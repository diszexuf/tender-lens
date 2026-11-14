"""Универсальный HTML-источник: выдача поиска → карточки товаров → цепочка извлечения.

Новый магазин подключается из UI заданием шаблона URL поиска; селекторы необязательны (FR-03.3).
Извлечение с карточки (FR-04.9): JSON-LD → microdata → селекторы → LLM (с проверкой цены по тексту).
"""

from __future__ import annotations

from typing import Literal
from urllib.parse import quote_plus, urlsplit

from pydantic import BaseModel, Field, field_validator

from app.domain.offer import VatStatus
from app.extraction.html import (
    SelectorConfig,
    itemlist_links,
    page_text,
    pattern_links,
    selector_links,
    structured_products,
)
from app.sources.base import (
    AdapterContext,
    AdapterResult,
    RunStatus,
    SourceAdapter,
    SourceQuery,
    register_adapter,
)
from app.sources.cards import CardExtractor
from app.sources.fetcher import FetchError, FetchErrorKind, Page


class HtmlSelectors(BaseModel):
    name: str | None = Field(
        None, description="CSS-селектор названия на карточке", title="Название товара"
    )
    price: str | None = Field(None, description="CSS-селектор цены на карточке", title="Цена")
    unit: str | None = Field(
        None, description="CSS-селектор единицы продажи", title="Единица продажи"
    )


class HtmlSourceConfig(BaseModel):
    search_url: str = Field(
        description="Откройте поиск на сайте, найдите любой товар и скопируйте адрес из "
        "браузера, заменив искомое слово на {query}: https://shop.ru/search?q={query}",
        examples=["https://shop.example.ru/search?q={query}"],
        title="Адрес поиска на сайте",
        pattern=r"^https?://.*\{query\}",
        json_schema_extra={
            "x-basic": True,
            "x-pattern-message": "Нужен адрес http(s)://… с {query} на месте искомого слова",
        },
    )
    render_js: bool = Field(
        False,
        description="Страница выдачи требует JavaScript (Playwright)",
        title="JavaScript в поиске",
    )
    card_render_js: bool = Field(
        False, description="Карточка товара требует JavaScript", title="JavaScript в карточках"
    )
    links_from: Literal["auto", "itemlist", "pattern", "selector"] = Field(
        "auto",
        description="Как искать ссылки на товары в выдаче",
        title="Поиск ссылок",
    )
    card_url_pattern: str | None = Field(
        None,
        description="Регулярное выражение пути карточки товара, например /product/\\d+",
        title="Шаблон пути карточки",
    )
    card_link_selector: str | None = Field(
        None, description="CSS-селектор ссылок на карточки", title="Селектор ссылок"
    )
    max_cards: int = Field(
        8, ge=1, le=30, description="Сколько карточек открывать", title="Лимит карточек"
    )
    selectors: HtmlSelectors = Field(default_factory=HtmlSelectors, title="Селекторы карточки")
    attributes_selector: str | None = Field(
        None,
        description="CSS-селектор блока характеристик на карточке",
        title="Блок характеристик",
    )
    llm_fallback: bool = Field(
        True,
        description="Извлекать данные LLM, если других данных нет",
        title="Разбор локальной LLM",
    )
    vat_status: VatStatus = Field(
        VatStatus.INCLUDED,
        description="Как на сайте указаны цены",
        title="НДС в ценах",
        json_schema_extra={
            "x-basic": True,
            "x-enum-titles": {
                "included": "С НДС",
                "excluded": "Без НДС",
                "not_applicable": "НДС не облагается",
                "unknown": "Неизвестно",
            },
        },
    )
    supplier_name: str | None = Field(
        None,
        description="Если сайт — один магазин: его название попадёт в отчёт",
        title="Название продавца",
        json_schema_extra={"x-basic": True},
    )
    supplier_inn: str | None = Field(None, description="ИНН продавца", title="ИНН продавца")

    @field_validator("search_url")
    @classmethod
    def _has_query(cls, v: str) -> str:
        if "{query}" not in v:
            raise ValueError("шаблон должен содержать {query}")
        if urlsplit(v.replace("{query}", "x")).scheme not in ("http", "https"):
            raise ValueError("нужен адрес http(s)")
        return v


@register_adapter
class HtmlAdapter(SourceAdapter):
    type = "html"
    title = "Сайт магазина или дистрибьютора"
    description = (
        "Поиск на сайте по шаблону URL, затем разбор карточек товаров: структурированные "
        "данные schema.org, селекторы или LLM."
    )
    config_model = HtmlSourceConfig
    config: HtmlSourceConfig

    def _extractor(self) -> CardExtractor:
        cfg = self.config
        return CardExtractor(
            selectors=SelectorConfig(
                cfg.selectors.name, cfg.selectors.price, cfg.selectors.unit, cfg.attributes_selector
            ),
            render_js=cfg.card_render_js,
            llm_fallback=cfg.llm_fallback,
            vat_status=cfg.vat_status,
            supplier_name=cfg.supplier_name,
            supplier_inn=cfg.supplier_inn,
        )

    async def search(self, query: SourceQuery, ctx: AdapterContext) -> AdapterResult:
        cfg = self.config
        url = cfg.search_url.replace("{query}", quote_plus(query.query))
        try:
            page = await ctx.fetcher.get(
                url, render_js=cfg.render_js, use_cache=ctx.use_cache, request_log=ctx.requests
            )
        except FetchError as exc:
            return error_result(exc)

        extractor = self._extractor()
        links = self._links(page)[: min(cfg.max_cards, query.max_offers)]
        if not links:
            # Ссылок нет: либо выдача сама содержит товары, либо ничего не найдено,
            # либо изменилась вёрстка
            products = [p for p in structured_products(page.text, page.final_url) if p.price]
            if products:
                return AdapterResult(
                    RunStatus.OK, [extractor.offer(p, page, page.final_url) for p in products]
                )
            text = page_text(page.text).lower()
            if any(m in text for m in ("ничего не найдено", "не найдено", "нет результатов")):
                return AdapterResult(RunStatus.EMPTY)
            return AdapterResult(
                RunStatus.STRUCTURE_CHANGED, error="на странице выдачи не найдены ссылки на товары"
            )

        offers, errors = await extractor.extract_many(links, query.query, ctx)
        if not offers:
            if errors and len(errors) == len(links):
                return AdapterResult(RunStatus.ERROR, error=f"карточки не загружены: {errors[0]}")
            return AdapterResult(
                RunStatus.STRUCTURE_CHANGED,
                error="на карточках товаров не найдены цены ни одним методом",
            )
        if errors:
            ctx.notes.append(f"не загружено карточек: {len(errors)}")
        return AdapterResult(RunStatus.OK, offers)

    def _links(self, page: Page) -> list[str]:
        cfg = self.config
        html, base = page.text, page.final_url
        mode = cfg.links_from
        if mode == "selector" and cfg.card_link_selector:
            return selector_links(html, base, cfg.card_link_selector)
        if mode == "pattern" and cfg.card_url_pattern:
            return pattern_links(html, base, cfg.card_url_pattern)
        if mode == "itemlist":
            return itemlist_links(html, base)
        links = itemlist_links(html, base)
        if not links and cfg.card_link_selector:
            links = selector_links(html, base, cfg.card_link_selector)
        if not links and cfg.card_url_pattern:
            links = pattern_links(html, base, cfg.card_url_pattern)
        return links


def error_result(exc: FetchError) -> AdapterResult:
    status = {
        FetchErrorKind.TIMEOUT: RunStatus.TIMEOUT,
        FetchErrorKind.ROBOTS: RunStatus.BLOCKED,
    }.get(exc.kind, RunStatus.ERROR)
    return AdapterResult(status, error=f"{exc.kind}: {exc}")
