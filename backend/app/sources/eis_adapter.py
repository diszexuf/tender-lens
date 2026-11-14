"""ЕИС: реестр контрактов (zakupki.gov.ru), исполненные контракты за N лет (FR-04.3).

Шаги: поиск контрактов (несколько страниц выдачи) → отбор по сходству объекта закупки
из выдачи → карточка «Объекты закупки» (цена за единицу позиции) → «Характеристики ТРУ»
(формат, плотность и т. п.) → «Общая информация» (поставщик, ИНН, дата) →
«Исполнение» (неустойки).

Разбор опирается на заголовки таблиц и текстовые метки, а не на CSS-классы: вёрстка ЕПЗ
меняется. Адреса и селекторы вынесены в настройки источника.

ЕПЗ доступна только с российских IP; сертификат выдан НУЦ Минцифры (см. fetcher.RU_ROOT_CA).
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from urllib.parse import urlencode, urljoin

from pydantic import BaseModel, Field
from rapidfuzz import fuzz
from selectolax.lexbor import LexborHTMLParser

from app.domain.offer import ExtractionMethod, RawOffer, VatStatus
from app.domain.reasons import Flag
from app.extraction.html import parse_price
from app.matching.text import norm
from app.sources.base import (
    AdapterContext,
    AdapterResult,
    RunStatus,
    SourceAdapter,
    SourceQuery,
    register_adapter,
)
from app.sources.fetcher import FetchError, FetchErrorKind

_REESTR_RE = re.compile(r"reestrNumber=(\d{19})")
_INN_RE = re.compile(r"ИНН\s*:?\s*(\d{10}|\d{12})\b")
_DATE_RE = re.compile(r"\b(\d{2})\.(\d{2})\.(\d{4})\b")
_PENALTY_RE = re.compile(r"(неустойк|штраф|пен[иья])", re.I)
_SUBJECT_RE = re.compile(r"contractSubjectId=(\d+)")
_MORE_RE = re.compile(r"Посмотреть все\s*\((\d+)\)")
_PAGES_RE = re.compile(r"pageNumber=(\d+)")
# Служебный хвост названия позиции в карточке контракта
_NAME_TAIL_RE = re.compile(
    r"\.?\s*(?:Товарный знак|Страна происхождения|Номер реестровой записи)", re.I
)


class EisSourceConfig(BaseModel):
    base_url: str = Field("https://zakupki.gov.ru", description="Адрес ЕПЗ", title="Базовый URL")
    search_path: str = Field("/epz/contract/search/results.html", title="Путь поиска")
    objects_path: str = Field(
        "/epz/contract/contractCard/payment-info-and-target-of-order.html",
        title="Путь к объектам закупки",
    )
    common_path: str = Field(
        "/epz/contract/contractCard/common-info.html", title="Путь к сведениям о контракте"
    )
    execution_path: str = Field(
        "/epz/contract/contractCard/process-info.html", title="Путь к исполнению контракта"
    )
    characteristics_path: str = Field(
        "/epz/contract/contractCard/ktru-characteristics.html",
        title="Путь к характеристикам ТРУ",
    )
    period_years: int = Field(
        3, ge=1, le=10, description="Глубина поиска, лет", title="Период поиска"
    )
    max_contracts: int = Field(
        15, ge=1, le=50, description="Сколько контрактов разбирать", title="Лимит контрактов"
    )
    max_pages: int = Field(
        4,
        ge=1,
        le=20,
        description="Сколько страниц выдачи просматривать (по 50 контрактов)",
        title="Страниц выдачи",
    )
    min_entry_similarity: int = Field(
        50,
        ge=0,
        le=100,
        description="Порог сходства объекта закупки в выдаче с позицией, %",
        title="Сходство в выдаче",
    )
    min_row_similarity: int = Field(
        55, ge=0, le=100, description="Порог сходства позиции, %", title="Минимальное сходство"
    )
    load_characteristics: bool = Field(
        True,
        description="Загружать «Характеристики ТРУ» позиции контракта",
        title="Характеристики ТРУ",
    )
    query_by_name: bool = Field(
        False,
        description="Сначала искать по наименованию позиции, затем по поисковой фразе "
        "(по умолчанию наоборот: фраза с маркой точнее, наименование — запасной вариант)",
        title="Сначала по наименованию",
    )
    check_penalties: bool = Field(
        True, description="Проверять неустойки по разделу исполнения", title="Проверка неустоек"
    )
    entry_selector: str = Field(
        ".search-registry-entry-block",
        description="Блок контракта в выдаче",
        title="Селектор контракта",
    )


@register_adapter
class EisAdapter(SourceAdapter):
    type = "eis"
    title = "ЕИС: реестр контрактов"
    description = (
        "Исполненные контракты по 44-ФЗ за последние годы: цена за единицу позиции, "
        "поставщик, дата, неустойки."
    )
    config_model = EisSourceConfig
    config: EisSourceConfig

    def search_texts(self, query: SourceQuery) -> list[str]:
        """Формулировки поиска по порядку: фраза с маркой точнее, наименование шире."""
        texts = [query.query, query.position.name]
        if self.config.query_by_name:
            texts.reverse()
        return list(dict.fromkeys(t.strip() for t in texts if t and t.strip()))

    def search_url(self, query: SourceQuery, page: int = 1, text: str | None = None) -> str:
        cfg = self.config
        since = datetime.now(UTC).date() - timedelta(days=365 * cfg.period_years)
        params = {
            "searchString": text if text is not None else self.search_texts(query)[0],
            "morphology": "on",
            "fz44": "on",
            "contractStageList_1": "on",  # исполнение завершено
            "contractStageList": "1",
            "contractDateFrom": since.strftime("%d.%m.%Y"),
            "sortBy": "UPDATE_DATE",
            "sortDirection": "false",
            "recordsPerPage": "_50",
            "pageNumber": str(page),
        }
        if query.position.ktru_code:
            params["ktruCodes"] = query.position.ktru_code
        return f"{cfg.base_url}{cfg.search_path}?{urlencode(params)}"

    async def search(self, query: SourceQuery, ctx: AdapterContext) -> AdapterResult:
        cfg = self.config
        limit = min(cfg.max_contracts, query.max_offers)
        similar: dict[str, SearchEntry] = {}
        scanned = pages = 0
        problem: AdapterResult | None = None
        for text in self.search_texts(query):
            result = await self._scan(text, query, ctx, similar, limit)
            if isinstance(result, AdapterResult):
                problem = problem or result
            else:
                scanned, pages = scanned + result[0], pages + result[1]
            if len(similar) >= limit:
                break
        if not similar and problem is not None and problem.status != RunStatus.EMPTY:
            return problem
        candidates = sorted(similar.values(), key=lambda e: e.score, reverse=True)[:limit]
        ctx.notes.append(
            f"ЕИС: просмотрено контрактов {scanned} на {pages} стр., похожих {len(similar)}, "
            f"разобрано {len(candidates)}"
        )
        if not candidates:
            return AdapterResult(RunStatus.EMPTY)

        results = await asyncio.gather(
            *(self._contract(e.reestr, query, ctx) for e in candidates), return_exceptions=True
        )
        offers = [r for r in results if isinstance(r, RawOffer)]
        failed = [r for r in results if isinstance(r, BaseException)]
        if failed:
            ctx.notes.append(f"контрактов не разобрано: {len(failed)} ({failed[0]})")
        if not offers:
            return AdapterResult(
                RunStatus.EMPTY if not failed else RunStatus.ERROR,
                error=str(failed[0]) if failed else None,
            )
        return AdapterResult(RunStatus.OK, offers)

    async def _scan(
        self,
        text: str,
        query: SourceQuery,
        ctx: AdapterContext,
        similar: dict[str, SearchEntry],
        limit: int,
    ) -> tuple[int, int] | AdapterResult:
        """Просматривает страницы выдачи по одной формулировке, пополняя `similar`.

        Выдача отсортирована по дате, а не по релевантности: смотрим несколько страниц и
        отбираем контракты по сходству объекта закупки ещё до загрузки карточек.
        """
        cfg = self.config
        target = norm(query.position.query_text())
        scanned, pages, last_page = 0, 0, 1
        while pages < min(cfg.max_pages, last_page):
            try:
                page = await ctx.fetcher.get(
                    self.search_url(query, pages + 1, text),
                    use_cache=ctx.use_cache,
                    request_log=ctx.requests,
                )
            except FetchError as exc:
                if pages:  # результаты прошлых страниц уже есть
                    ctx.notes.append(f"ЕИС «{text}», страница {pages + 1}: {exc}")
                    break
                status = (
                    RunStatus.TIMEOUT if exc.kind == FetchErrorKind.TIMEOUT else RunStatus.ERROR
                )
                return AdapterResult(status, error=f"{exc.kind}: {exc}")
            pages += 1
            found = parse_search_entries(page.text, cfg.entry_selector)
            if not found:
                if pages > 1:
                    break
                lower = norm(LexborHTMLParser(page.text).text())
                if "ничего не найдено" in lower or "не найдено" in lower:
                    return AdapterResult(RunStatus.EMPTY)
                return AdapterResult(
                    RunStatus.STRUCTURE_CHANGED, error="в выдаче не найдены контракты"
                )
            scanned += len(found)
            for entry in found:
                entry.score = fuzz.token_set_ratio(target, norm(entry.objects))
                # В выдаче виден только первый объект: у многопозиционных контрактов порог ниже
                threshold = cfg.min_entry_similarity - (15 if entry.more else 0)
                if not entry.objects or entry.score >= threshold:
                    similar.setdefault(entry.reestr, entry)
            if len(similar) >= limit * 2:
                break
            last_page = max(last_page, last_page_number(page.text))
        return scanned, pages

    async def _contract(
        self, reestr: str, query: SourceQuery, ctx: AdapterContext
    ) -> RawOffer | None:
        cfg = self.config
        objects_url = f"{cfg.base_url}{cfg.objects_path}?reestrNumber={reestr}"
        common_url = f"{cfg.base_url}{cfg.common_path}?reestrNumber={reestr}"
        objects_page, common_page = await asyncio.gather(
            ctx.fetcher.get(objects_url, use_cache=ctx.use_cache, request_log=ctx.requests),
            ctx.fetcher.get(common_url, use_cache=ctx.use_cache, request_log=ctx.requests),
        )
        rows = parse_object_rows(objects_page.text)
        row = best_row(rows, query.position.query_text(), cfg.min_row_similarity)
        if row is None:
            return None
        characteristics = row.get("characteristics") or ""
        extra_keys: list[str] = []
        if cfg.load_characteristics and row.get("subject_id"):
            try:
                ch_page = await ctx.fetcher.get(
                    f"{cfg.base_url}{cfg.characteristics_path}"
                    f"?contractSubjectId={row['subject_id']}",
                    use_cache=ctx.use_cache,
                    request_log=ctx.requests,
                )
                if full := parse_characteristics(ch_page.text):
                    characteristics = full
                    extra_keys.append(ch_page.s3_key)
            except FetchError:
                pass  # остаются сокращённые характеристики из карточки

        info = parse_common_info(common_page.text)
        flags: list[str] = []
        snapshot_keys = [objects_page.s3_key, common_page.s3_key, *extra_keys]
        if cfg.check_penalties:
            try:
                exec_page = await ctx.fetcher.get(
                    f"{cfg.base_url}{cfg.execution_path}?reestrNumber={reestr}",
                    use_cache=ctx.use_cache,
                    request_log=ctx.requests,
                )
                snapshot_keys.append(exec_page.s3_key)
                penalties = has_penalties(exec_page.text)
                if penalties is None:
                    flags.append(Flag.PENALTIES_UNKNOWN)
                elif penalties:
                    flags.append("contract_penalties")
            except FetchError:
                flags.append(Flag.PENALTIES_UNKNOWN)
        else:
            flags.append(Flag.PENALTIES_UNKNOWN)

        return RawOffer(
            url=urljoin(cfg.base_url, f"{cfg.common_path}?reestrNumber={reestr}"),
            title=row["name"][:500],
            price=row["unit_price"],
            currency="RUB",
            vat_status=VatStatus.INCLUDED,  # цена, уплаченная заказчиком
            unit_raw=row.get("unit"),
            attributes_raw=f"{row['name']}. {characteristics}".strip(". "),
            supplier_name=info.get("supplier_name"),
            supplier_inn=info.get("supplier_inn"),
            supplier_kind="ip" if len(info.get("supplier_inn") or "") == 12 else "legal",
            price_date=info.get("contract_date"),
            region=info.get("region"),
            external_id=f"{reestr}:{row['index']}",
            extraction_method=ExtractionMethod.SELECTORS,
            snapshot_keys=snapshot_keys,
            fetched_at=objects_page.fetched_at,
            flags=flags,
        )


# ---------------------------------------------------------------- разбор страниц


@dataclass
class SearchEntry:
    reestr: str
    objects: str = ""  # первый объект закупки из выдачи
    more: int = 0  # «Посмотреть все (N)»: объектов в контракте больше одного
    score: float = 0.0


def parse_search_entries(html: str, entry_selector: str) -> list[SearchEntry]:
    """Контракты выдачи: реестровый номер и объект закупки (по метке «Объекты закупки»)."""
    tree = LexborHTMLParser(html)
    blocks = tree.css(entry_selector)
    if not blocks:
        return [SearchEntry(n) for n in parse_search(html, entry_selector)]
    out: dict[str, SearchEntry] = {}
    for block in blocks:
        number = next(
            (m.group(1) for a in block.css("a[href]")
             if (m := _REESTR_RE.search(a.attributes.get("href") or ""))),
            None,
        )  # fmt: skip
        if number is None or number in out:
            continue
        text = re.sub(r"\s+", " ", block.text(separator=" "))
        objects = ""
        if (idx := text.find("Объекты закупки")) >= 0:
            tail = text[idx + len("Объекты закупки") :]
            objects = re.split(r"Посмотреть все|Цена контракта|Товарный знак", tail)[0]
        more = int(m.group(1)) if (m := _MORE_RE.search(text)) else 0
        out[number] = SearchEntry(number, objects.strip(" .")[:500], more)
    return list(out.values())


def last_page_number(html: str) -> int:
    """Номер последней страницы выдачи по пагинатору (1, если пагинатора нет)."""
    tree = LexborHTMLParser(html)
    numbers = [1]
    for node in tree.css(".paginator a, .paginator-block a, [data-pagenumber]"):
        value = node.attributes.get("data-pagenumber") or ""
        if value.isdigit():
            numbers.append(int(value))
        elif m := _PAGES_RE.search(node.attributes.get("href") or ""):
            numbers.append(int(m.group(1)))
        elif (t := node.text(strip=True)).isdigit():
            numbers.append(int(t))
    return max(numbers)


def parse_search(html: str, entry_selector: str) -> list[str]:
    """Реестровые номера контрактов из выдачи (по ссылкам reestrNumber=…)."""
    tree = LexborHTMLParser(html)
    blocks = tree.css(entry_selector) or ([tree.body] if tree.body else [])
    numbers: dict[str, None] = {}
    for block in blocks:
        for a in block.css("a[href]"):
            if m := _REESTR_RE.search(a.attributes.get("href") or ""):
                numbers.setdefault(m.group(1), None)
    return list(numbers)


def parse_characteristics(html: str) -> str:
    """«Характеристики ТРУ» → «Формат: А4; Цвет: Белый; Масса бумаги площадью 1м2, г: 80 Грамм»."""
    tree = LexborHTMLParser(html)
    raw: list[tuple[str, str]] = []
    # Вёрстка ЕПЗ: <span class="section__title">Формат</span><span class="section__info">А4</span>
    for title in tree.css(".section__title"):
        info = title.next
        while info is not None and info.tag == "-text":
            info = info.next
        if info is not None and "section__info" in (info.attributes.get("class") or ""):
            raw.append((title.text(strip=True), info.text(separator=" ", strip=True)))
    if not raw:  # запасной вариант — таблица из двух колонок
        for tr in tree.css("tr"):
            cells = [c.text(separator=" ", strip=True) for c in tr.css("td")]
            cells = [c for c in cells if c]
            if len(cells) >= 2:
                raw.append((cells[0], " ".join(cells[1:])))
    pairs: list[str] = []
    for name, value in raw:
        name, value = re.sub(r"\s+", " ", name), re.sub(r"\s+", " ", value)
        if name and value and len(name) < 200:
            pairs.append(f"{name}: {re.sub(r'(\d)([А-ЯA-Zа-я])', r'\1 \2', value)}")
    return "; ".join(pairs)[:2000]


def clean_name(raw: str) -> str:
    """«1. Бумага для офисной техники. Товарный знак: …» → «Бумага для офисной техники»."""
    text = re.sub(r"\s+", " ", raw).strip()
    text = re.sub(r"^\d+\.\s*", "", text)
    return _NAME_TAIL_RE.split(text, maxsplit=1)[0].strip(" .")


def clean_unit(raw: str | None) -> str | None:
    """«640 пач», «2 000 ШТ» → «пач», «ШТ» (в ЕПЗ количество и единица в одной колонке)."""
    if not raw:
        return None
    text = re.sub(r"[\d\s\u00a0.,]+", " ", raw).strip()
    return text or None


_HEADERS = {
    "name": ("наименование", "объект закупки", "товар"),
    "unit_price": ("цена за ед", "цена единицы"),
    "quantity": ("количество",),
    "unit": ("единица измерения", "ед. изм"),
    "total": ("сумма", "стоимость"),
}


def parse_object_rows(html: str) -> list[dict]:
    """Позиции контракта из таблицы «Объекты закупки»: колонки ищутся по заголовкам."""
    tree = LexborHTMLParser(html)
    rows_out: list[dict] = []
    for table in tree.css("table"):
        header_cells = table.css("thead th") or (
            table.css("tr")[0].css("th, td") if table.css("tr") else []
        )
        headers = [norm(c.text(strip=True)) for c in header_cells]
        cols: dict[str, int] = {}
        for key, markers in _HEADERS.items():
            for i, h in enumerate(headers):
                if key not in cols and any(m in h for m in markers):
                    cols[key] = i
        if "name" not in cols or "unit_price" not in cols:
            continue
        body_rows = table.css("tbody tr") or table.css("tr")[1:]
        for index, tr in enumerate(body_rows, start=1):
            tds = tr.css("td")
            cells = [c.text(separator=" ", strip=True) for c in tds]
            if len(cells) <= max(cols.values()):
                continue
            price = parse_price(cells[cols["unit_price"]])
            name = clean_name(cells[cols["name"]])
            if not price or not name:
                continue
            subject_id = next(
                (m.group(1) for a in tds[cols["name"]].css("a[href]")
                 if (m := _SUBJECT_RE.search(a.attributes.get("href") or ""))),
                None,
            )  # fmt: skip
            # Сокращённые характеристики идут после «Страна происхождения: … (643)»
            full = re.sub(r"\s+", " ", cells[cols["name"]])
            characteristics = ""
            if (idx := full.find("Страна происхождения")) >= 0 and ")" in full[idx:]:
                characteristics = full[idx:].split(")", 1)[1]
                characteristics = characteristics.replace("Все характеристики ТРУ", "")
                characteristics = characteristics.strip(" .,")
            rows_out.append(
                {
                    "index": index,
                    "name": name,
                    "unit_price": price,
                    "quantity": parse_price(cells[cols["quantity"]])
                    if "quantity" in cols
                    else None,
                    "unit": clean_unit(cells[cols["unit"]]) if "unit" in cols else None,
                    "characteristics": characteristics,
                    "subject_id": subject_id,
                }
            )
    return rows_out


def best_row(rows: list[dict], query: str, threshold: int) -> dict | None:
    if not rows:
        return None
    q = norm(query)
    scored = [
        (fuzz.token_set_ratio(q, norm(f"{r['name']} {r.get('characteristics') or ''}")), r)
        for r in rows
    ]
    score, row = max(scored, key=lambda x: x[0])
    return row if score >= threshold else None


def parse_common_info(html: str) -> dict:
    """Поставщик, ИНН, дата заключения из «Общей информации»."""
    text = LexborHTMLParser(html).text(separator="\n")
    info: dict = {}
    supplier_block = text
    idx = text.lower().find("информация о поставщик")
    if idx >= 0:
        supplier_block = text[idx : idx + 3000]
        lines = [ln.strip() for ln in supplier_block.split("\n") if ln.strip()]
        for ln in lines[1:8]:
            if re.search(r"(ООО|АО|ПАО|ЗАО|ИП|Общество|Индивидуальный)", ln):
                info["supplier_name"] = ln[:300]
                break
    if m := _INN_RE.search(supplier_block):
        info["supplier_inn"] = m.group(1)
    date_idx = text.lower().find("дата заключения контракта")
    if date_idx >= 0 and (m := _DATE_RE.search(text[date_idx : date_idx + 200])):
        info["contract_date"] = date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
    return info


def has_penalties(html: str) -> bool | None:
    """True — неустойки начислены; False — раздел есть, неустоек нет; None — нельзя определить."""
    text = LexborHTMLParser(html).text(separator="\n")
    lower = text.lower()
    if "неустойк" not in lower and "штраф" not in lower:
        return None
    for line in text.split("\n"):
        if _PENALTY_RE.search(line) and (amount := parse_price(line)) and amount > Decimal(0):
            return True
    if "отсутству" in lower or "не начислял" in lower:
        return False
    return None
