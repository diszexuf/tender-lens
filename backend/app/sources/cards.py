"""Извлечение предложения с карточки товара — общее для HTML-источников и веб-поиска.

Цепочка (FR-04.9): JSON-LD → microdata → селекторы → LLM. Цену от LLM принимаем, только если
она буквально есть в тексте страницы (защита от галлюцинаций).
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from urllib.parse import urlsplit

from app.domain.offer import ExtractionMethod, RawOffer, VatStatus
from app.domain.reasons import Flag
from app.extraction.html import (
    ProductData,
    SelectorConfig,
    compress_text,
    page_text,
    page_title,
    parse_price,
    price_in_text,
    selector_product,
    structured_products,
)
from app.llm.provider import LLMError
from app.llm.tasks import extract_page_offer
from app.sources.base import AdapterContext
from app.sources.fetcher import FetchError, Page


@dataclass
class CardExtractor:
    selectors: SelectorConfig = field(default_factory=SelectorConfig)
    render_js: bool = False
    llm_fallback: bool = True
    vat_status: VatStatus = VatStatus.INCLUDED
    supplier_name: str | None = None
    supplier_inn: str | None = None
    supplier_from_domain: bool = False

    async def extract(self, url: str, query_text: str, ctx: AdapterContext) -> RawOffer | None:
        page = await ctx.fetcher.get(
            url, render_js=self.render_js, use_cache=ctx.use_cache, request_log=ctx.requests
        )
        products = [p for p in structured_products(page.text, page.final_url) if p.price]
        if products:
            return self.offer(products[0], page, url)
        if self.selectors.price:
            product = selector_product(page.text, self.selectors)
            if product:
                return self.offer(product, page, url)
        if self.llm_fallback and ctx.llm is not None:
            return await self._llm_offer(page, query_text, ctx, url)
        return None

    async def extract_many(
        self, urls: list[str], query_text: str, ctx: AdapterContext
    ) -> tuple[list[RawOffer], list[str]]:
        results = await asyncio.gather(
            *(self.extract(u, query_text, ctx) for u in urls), return_exceptions=True
        )
        offers: list[RawOffer] = []
        errors: list[str] = []
        for result in results:
            if isinstance(result, RawOffer):
                offers.append(result)
            elif isinstance(result, FetchError):
                errors.append(f"{result.kind}: {result}")
            elif isinstance(result, BaseException):
                errors.append(f"{type(result).__name__}: {result}")
        return offers, errors

    async def _llm_offer(
        self, page: Page, query_text: str, ctx: AdapterContext, url: str
    ) -> RawOffer | None:
        full_text = page_text(page.text, self.selectors.attributes)
        text = compress_text(full_text, 2500, keywords=tuple(query_text.split()))
        assert ctx.llm is not None
        try:
            data, _ = await extract_page_offer(ctx.llm, url, text)
        except LLMError:
            return None
        price = parse_price(data.price)
        if price is None or not price_in_text(price, page_text(page.text)):
            return None
        product = ProductData(
            name=data.name or page_title(page.text),
            price=price,
            currency=data.currency,
            unit=data.sale_unit,
            description=data.characteristics,
            method=ExtractionMethod.LLM,
        )
        offer = self.offer(product, page, url)
        if data.pack_qty:
            offer.pack_qty = Decimal(str(data.pack_qty))
        offer.flags.append(Flag.LLM_EXTRACTED)
        return offer

    def offer(self, product: ProductData, page: Page, url: str) -> RawOffer:
        title = product.name or page_title(page.text) or url
        attributes_text = (
            page_text(page.text, self.selectors.attributes)
            if self.selectors.attributes
            else compress_text(page_text(page.text), 2500)
        )
        supplier = product.seller or self.supplier_name
        if supplier is None and self.supplier_from_domain:
            supplier = urlsplit(page.final_url).netloc.removeprefix("www.")
        return RawOffer(
            url=product.url if product.url and product.url.startswith("http") else url,
            title=title[:500],
            price=product.price,
            currency=(product.currency or "RUB").upper()[:3],
            vat_status=self.vat_status,
            unit_raw=product.unit,
            attributes=product.attributes,
            attributes_raw=f"{product.description or ''}\n{attributes_text}".strip()[:6000],
            supplier_name=supplier,
            supplier_inn=self.supplier_inn,
            supplier_kind="legal" if self.supplier_inn and len(self.supplier_inn) == 10 else None,
            price_date=page.fetched_at.date() if page.fetched_at else date.today(),
            external_id=product.sku,
            extraction_method=product.method,
            snapshot_keys=[page.s3_key],
            fetched_at=page.fetched_at,
        )
