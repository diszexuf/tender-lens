"""Веб-поиск через SearXNG: поисковики находят карточки товаров, цены берутся с карточек.

Подходит для новых категорий без готового адаптера (FR-03.4, NFR-05.3). Страницы поиска
магазинов обычно закрыты robots.txt, а карточки товаров открыты для индексации — поэтому
URL карточек находим через поисковые системы, а не через поиск на сайте магазина.
"""

from __future__ import annotations

import re
import time
from urllib.parse import urlsplit

import httpx
from pydantic import BaseModel, Field

from app.extraction.html import SelectorConfig
from app.sources.base import (
    AdapterContext,
    AdapterResult,
    RunStatus,
    SourceAdapter,
    SourceQuery,
    register_adapter,
)
from app.sources.cards import CardExtractor
from app.sources.fetcher import RequestLog

# Маркетплейсы с защитой от ботов и агрегаторы: карточки не загрузить или в них нет цены
DEFAULT_DENYLIST = [
    "ozon.ru", "market.yandex.ru", "wildberries.ru", "avito.ru", "megamarket.ru",
    "aliexpress.ru", "aliexpress.com", "youtube.com", "vk.com", "dzen.ru", "pulscen.ru",
    "tiu.ru", "satom.ru", "blizko.ru", "wikipedia.org", "otzovik.com", "irecommend.ru",
]  # fmt: skip

# Признаки страниц-списков (категории, теги, поиск) — не карточки товара
LISTING_PATH = re.compile(
    r"/(category|categories|catalog/?$|tag-page|tags?|search|brand|brands|collections?)(/|$)", re.I
)


class SearxngSourceConfig(BaseModel):
    query_suffix: str = Field(
        "купить цена", description="Добавляется к поисковой фразе", title="Дополнение к запросу"
    )
    language: str = Field("ru-RU", description="Язык поиска", title="Язык")
    allow_domains: list[str] = Field(
        default_factory=list,
        description="Искать только на этих сайтах, например officemag.ru (пусто — на любых, "
        "кроме запрещённых)",
        title="Разрешённые сайты",
        json_schema_extra={"x-basic": True},
    )
    deny_domains: list[str] = Field(
        default_factory=lambda: list(DEFAULT_DENYLIST),
        description="Не брать цены с этих сайтов (маркетплейсы, доски объявлений)",
        title="Запрещённые сайты",
    )
    max_cards: int = Field(
        8, ge=1, le=20, description="Сколько карточек открывать", title="Лимит карточек"
    )
    one_per_domain: bool = Field(
        True, description="Не больше одной карточки с домена", title="Одна карточка с домена"
    )
    llm_fallback: bool = Field(
        True, description="Извлекать данные LLM, если нет разметки", title="Разбор локальной LLM"
    )


def _domain(url: str) -> str:
    return urlsplit(url).netloc.lower().removeprefix("www.")


def _matches(domain: str, patterns: list[str]) -> bool:
    return any(domain == p or domain.endswith("." + p) for p in patterns)


def select_urls(results: list[dict], cfg: SearxngSourceConfig, limit: int) -> list[str]:
    """Отбор ссылок: разрешённые домены, похожие на карточку товара, по одной с домена."""
    picked: list[str] = []
    domains: set[str] = set()
    for item in results:
        url = item.get("url") or ""
        parts = urlsplit(url)
        if parts.scheme not in ("http", "https"):
            continue
        domain = _domain(url)
        if cfg.allow_domains and not _matches(domain, cfg.allow_domains):
            continue
        if _matches(domain, cfg.deny_domains):
            continue
        if LISTING_PATH.search(parts.path) or parts.path in ("", "/"):
            continue
        if cfg.one_per_domain and domain in domains:
            continue
        picked.append(url)
        domains.add(domain)
        if len(picked) >= limit:
            break
    return picked


@register_adapter
class SearxngAdapter(SourceAdapter):
    type = "searxng"
    title = "Веб-поиск (SearXNG)"
    description = (
        "Поиск карточек товаров через поисковые системы и разбор найденных страниц. "
        "Запасной источник для любых категорий."
    )
    config_model = SearxngSourceConfig
    config: SearxngSourceConfig

    async def search(self, query: SourceQuery, ctx: AdapterContext) -> AdapterResult:
        cfg = self.config
        settings = ctx.fetcher.settings
        q = f"{query.query} {cfg.query_suffix}".strip()
        started = time.perf_counter()
        entry = RequestLog(url=f"searxng: {q}", status=None, duration_ms=0)
        ctx.requests.append(entry)
        try:
            # SearXNG — внутренний сервис из настроек окружения, а не из UI: SSRF-проверка не нужна
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.get(
                    f"{settings.searxng_url}/search",
                    params={"q": q, "format": "json", "language": cfg.language, "safesearch": 0},
                )
            entry.status = resp.status_code
            resp.raise_for_status()
            data = resp.json()
        except (httpx.HTTPError, ValueError) as exc:
            entry.error = f"{type(exc).__name__}: {exc}"
            return AdapterResult(RunStatus.ERROR, error=f"SearXNG: {entry.error}")
        finally:
            entry.duration_ms = int((time.perf_counter() - started) * 1000)

        results = data.get("results") or []
        if unresponsive := data.get("unresponsive_engines"):
            ctx.notes.append(f"не ответили поисковики: {', '.join(e[0] for e in unresponsive)}")
        urls = select_urls(results, cfg, min(cfg.max_cards, query.max_offers))
        if not urls:
            return AdapterResult(
                RunStatus.EMPTY, error=f"найдено ссылок: {len(results)}, карточек нет"
            )

        extractor = CardExtractor(
            selectors=SelectorConfig(),
            llm_fallback=cfg.llm_fallback,
            supplier_from_domain=True,
        )
        offers, errors = await extractor.extract_many(urls, query.query, ctx)
        if errors:
            ctx.notes.append(f"карточек не загружено: {len(errors)} ({errors[0][:120]})")
        if not offers:
            return AdapterResult(RunStatus.EMPTY, error="на найденных страницах нет цен")
        return AdapterResult(RunStatus.OK, offers)
