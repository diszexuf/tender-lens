"""Дедупликация и «одна цена на поставщика» (FR-05)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from rapidfuzz import fuzz

from app.domain.reasons import Reason
from app.matching.text import norm

TRACKING_PARAMS = {
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "yclid", "gclid", "fbclid", "_openstat", "from", "ref", "sessionid", "sid",
}  # fmt: skip
FUZZY_DUP_THRESHOLD = 92
SAME_PRODUCT_THRESHOLD = 88  # «тот же товар» у одного поставщика (разные контракты, фасовки)


@dataclass
class DedupItem:
    offer_id: str
    url: str
    title: str
    price: Decimal | None
    supplier_name: str | None
    supplier_inn: str | None
    external_id: str | None
    price_date: date | None
    fetched_at: datetime


@dataclass(frozen=True)
class DedupDecision:
    offer_id: str
    reason: Reason
    duplicate_of: str
    detail: str


def canonical_url(url: str) -> str:
    parts = urlsplit(url.strip())
    query = sorted(
        (k, v)
        for k, v in parse_qsl(parts.query, keep_blank_values=True)
        if k.lower() not in TRACKING_PARAMS
    )
    path = parts.path.rstrip("/") or "/"
    host = parts.netloc.lower().removeprefix("www.")
    return urlunsplit((parts.scheme.lower(), host, path, urlencode(query), ""))


_NUMBERS = re.compile(r"\d+(?:[.,]\d+)?")


_PACKAGING = re.compile(
    r"(?<![\d.,x×х])\d+(?:[.,]\d+)?\s?(?:метров|штук|листов|упак|лист|пар|шт|мл|кг|уп|м|л|г)(?![а-яё])\.?"
)


def same_product(a: str, b: str) -> bool:
    """Тот же товар: похожее название и одинаковые числа модели (сечения, артикулы, объёмы).

    Фасовка не учитывается: бухты 50 м и 100 м одного кабеля — один товар поставщика.
    """
    na, nb = _PACKAGING.sub(" ", norm(a)), _PACKAGING.sub(" ", norm(b))
    if set(_NUMBERS.findall(na)) != set(_NUMBERS.findall(nb)):
        return False
    return fuzz.token_sort_ratio(na, nb) >= SAME_PRODUCT_THRESHOLD


def supplier_key(item: DedupItem) -> str | None:
    if item.supplier_inn:
        return f"inn:{item.supplier_inn}"
    if item.supplier_name:
        name = norm(item.supplier_name)
        for form in ("ооо", "ао", "пао", "зао", "оао", "ип", "фгуп", "муп"):
            name = name.removeprefix(form + " ")
        return f"name:{name.strip(' «»')}"
    return None


def deduplicate(items: list[DedupItem]) -> list[DedupDecision]:
    """Возвращает решения об исключении. Порядок входа важен: первый оффер — основной."""
    decisions: list[DedupDecision] = []
    kept: list[DedupItem] = []
    by_url: dict[str, str] = {}
    by_external: dict[str, str] = {}

    for item in items:
        url_key = canonical_url(item.url)
        if item.external_id and item.external_id in by_external:
            decisions.append(
                _dup(item, by_external[item.external_id], f"тот же номер {item.external_id}")
            )
            continue
        if url_key in by_url and not item.external_id:
            decisions.append(_dup(item, by_url[url_key], "та же ссылка"))
            continue
        fuzzy = next((k for k in kept if _fuzzy_same(item, k)), None)
        if fuzzy is not None:
            decisions.append(_dup(item, fuzzy.offer_id, "то же наименование, поставщик и цена"))
            continue
        kept.append(item)
        by_url.setdefault(url_key, item.offer_id)
        if item.external_id:
            by_external[item.external_id] = item.offer_id

    # Одна цена поставщика на один товар: оставляем самую свежую (FR-05.5).
    # Разные товары одного магазина — независимые предложения, их не трогаем.
    by_supplier: dict[str, list[DedupItem]] = {}
    for item in kept:
        key = supplier_key(item)
        if key:
            by_supplier.setdefault(key, []).append(item)
    groups: list[list[DedupItem]] = []
    for items_of_supplier in by_supplier.values():
        clusters: list[list[DedupItem]] = []
        for item in items_of_supplier:
            target = next(
                (c for c in clusters if same_product(c[0].title, item.title)),
                None,
            )
            if target is None:
                clusters.append([item])
            else:
                target.append(item)
        groups.extend(c for c in clusters if len(c) > 1)
    for group in groups:
        group.sort(key=lambda i: (i.price_date or i.fetched_at.date(), i.fetched_at), reverse=True)
        latest = group[0]
        for older in group[1:]:
            decisions.append(
                DedupDecision(
                    older.offer_id,
                    Reason.SAME_SUPPLIER,
                    latest.offer_id,
                    f"у поставщика есть более свежая цена ({latest.price_date or latest.fetched_at.date()})",
                )
            )
    return decisions


def _dup(item: DedupItem, original: str, why: str) -> DedupDecision:
    return DedupDecision(item.offer_id, Reason.DUPLICATE, original, why)


def _fuzzy_same(a: DedupItem, b: DedupItem) -> bool:
    if a.price is None or a.price != b.price:
        return False
    # Без известного поставщика одинаковая цена на разных площадках — разные предложения
    key = supplier_key(a)
    if key is None or key != supplier_key(b):
        return False
    return fuzz.token_sort_ratio(norm(a.title), norm(b.title)) >= FUZZY_DUP_THRESHOLD
