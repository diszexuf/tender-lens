"""Извлечение данных со страниц: структурированные данные, ссылки, текст, цены.

Цепочка для карточки товара (FR-04.9): JSON-LD → microdata → селекторы → LLM.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import urljoin, urlsplit

import extruct
from selectolax.lexbor import LexborHTMLParser

from app.domain.offer import ExtractionMethod

_PRICE_RE = re.compile(r"(\d[\d\s  ]*(?:[.,]\d{1,2})?)")
_WS = re.compile(r"[ \t  ]+")
_BLANK_LINES = re.compile(r"\n\s*\n+")
NOISE_TAGS = ("script", "style", "noscript", "svg", "header", "footer", "nav", "iframe", "form")


def parse_price(value: Any) -> Decimal | None:
    """«17 664,50 ₽» → 17664.50; 181.58 → 181.58."""
    if value is None:
        return None
    if isinstance(value, int | float | Decimal):
        price = Decimal(str(value))
        return price if price > 0 else None
    match = _PRICE_RE.search(str(value))
    if not match:
        return None
    raw = re.sub(r"[\s  ]", "", match.group(1)).replace(",", ".")
    try:
        price = Decimal(raw)
    except InvalidOperation:
        return None
    return price if price > 0 else None


@dataclass
class ProductData:
    """Товар, найденный в структурированных данных или по селекторам."""

    name: str | None
    price: Decimal | None
    currency: str | None = None
    url: str | None = None
    description: str | None = None
    sku: str | None = None
    brand: str | None = None
    unit: str | None = None
    attributes: dict[str, str] = field(default_factory=dict)
    seller: str | None = None
    method: ExtractionMethod = ExtractionMethod.JSONLD


def _types(item: dict[str, Any]) -> set[str]:
    t = item.get("@type")
    values = t if isinstance(t, list) else [t]
    return {str(v).rsplit("/", 1)[-1] for v in values if v}


def _first(value: Any) -> Any:
    return value[0] if isinstance(value, list) and value else value


def _text(value: Any) -> str | None:
    value = _first(value)
    if isinstance(value, dict):
        value = value.get("name") or value.get("@value")
    return str(value).strip() if value not in (None, "") else None


def _walk(node: Any) -> list[dict[str, Any]]:
    """Все объекты внутри JSON-LD (в т. ч. @graph и вложенные)."""
    out: list[dict[str, Any]] = []
    if isinstance(node, dict):
        out.append(node)
        for value in node.values():
            out.extend(_walk(value))
    elif isinstance(node, list):
        for value in node:
            out.extend(_walk(value))
    return out


def _offer_price(offers: Any) -> tuple[Decimal | None, str | None, str | None, str | None]:
    """(цена, валюта, единица, продавец) из schema.org Offer/AggregateOffer."""
    candidates = offers if isinstance(offers, list) else [offers]
    for offer in candidates:
        if not isinstance(offer, dict):
            continue
        price = parse_price(offer.get("price") or offer.get("lowPrice"))
        spec = _first(offer.get("priceSpecification"))
        unit = None
        if isinstance(spec, dict):
            price = price or parse_price(spec.get("price"))
            unit = _text(spec.get("unitText")) or _text(spec.get("unitCode"))
            currency = _text(spec.get("priceCurrency"))
        currency = _text(offer.get("priceCurrency")) or (
            currency if isinstance(spec, dict) else None
        )
        seller = _text(offer.get("seller"))
        if price is not None:
            return price, currency, unit, seller
    return None, None, None, None


def structured_products(html: str, url: str) -> list[ProductData]:
    """Товары из JSON-LD и microdata страницы."""
    try:
        data = extruct.extract(html, base_url=url, syntaxes=["json-ld", "microdata"], uniform=True)
    except Exception:
        data = {"json-ld": [], "microdata": []}
    products: list[ProductData] = []
    for syntax, method in (
        ("json-ld", ExtractionMethod.JSONLD),
        ("microdata", ExtractionMethod.MICRODATA),
    ):
        for item in _walk(data.get(syntax, [])):
            if "Product" not in _types(item):
                continue
            price, currency, unit, seller = _offer_price(item.get("offers"))
            attrs: dict[str, str] = {}
            for prop in item.get("additionalProperty") or []:
                if isinstance(prop, dict) and prop.get("name") and prop.get("value") is not None:
                    unit_text = _text(prop.get("unitText"))
                    attrs[str(prop["name"])] = (
                        f"{prop['value']}{' ' + unit_text if unit_text else ''}"
                    )
            products.append(
                ProductData(
                    name=_text(item.get("name")),
                    price=price,
                    currency=currency,
                    url=_text(item.get("url")),
                    description=_text(item.get("description")),
                    sku=_text(item.get("sku")) or _text(item.get("mpn")),
                    brand=_text(item.get("brand")),
                    unit=unit,
                    attributes=attrs,
                    seller=seller,
                    method=method,
                )
            )
    return products


def itemlist_links(html: str, url: str) -> list[str]:
    """Ссылки на товары из JSON-LD ItemList (страницы выдачи)."""
    links: list[str] = []
    for match in re.finditer(
        r"<script[^>]*application/ld\+json[^>]*>(.*?)</script>", html, re.S | re.I
    ):
        try:
            data = json.loads(match.group(1))
        except json.JSONDecodeError:
            continue
        for container in _walk(data):
            # BreadcrumbList тоже состоит из ListItem — берём только ItemList
            if "ItemList" not in _types(container):
                continue
            for item in container.get("itemListElement") or []:
                if not isinstance(item, dict):
                    continue
                target = item.get("url") or item.get("item")
                if isinstance(target, dict):
                    target = target.get("url") or target.get("@id")
                if isinstance(target, str):
                    links.append(urljoin(url, target))
    return _unique(links)


def pattern_links(html: str, url: str, pattern: str) -> list[str]:
    """Ссылки, путь которых соответствует регулярному выражению источника."""
    regex = re.compile(pattern)
    tree = LexborHTMLParser(html)
    host = urlsplit(url).netloc
    links = []
    for a in tree.css("a[href]"):
        href = urljoin(url, a.attributes.get("href") or "")
        parts = urlsplit(href)
        if parts.netloc == host and regex.search(parts.path):
            links.append(href.split("#")[0])
    return _unique(links)


def selector_links(html: str, url: str, selector: str) -> list[str]:
    tree = LexborHTMLParser(html)
    return _unique(
        urljoin(url, n.attributes.get("href") or "")
        for n in tree.css(selector)
        if n.attributes.get("href")
    )


def _unique(links: Any) -> list[str]:
    seen: dict[str, None] = {}
    for link in links:
        seen.setdefault(link, None)
    return list(seen)


def page_title(html: str) -> str | None:
    tree = LexborHTMLParser(html)
    for selector in ('meta[property="og:title"]', "h1", "title"):
        node = tree.css_first(selector)
        if node is None:
            continue
        text = node.attributes.get("content") if node.tag == "meta" else node.text(strip=True)
        if text:
            return _WS.sub(" ", text).strip()
    return None


def page_text(html: str, selector: str | None = None) -> str:
    """Видимый текст страницы (или блока по селектору) без меню, скриптов и подвалов."""
    tree = LexborHTMLParser(html)
    for tag in NOISE_TAGS:
        for node in tree.css(tag):
            node.decompose()
    root = (tree.css_first(selector) if selector else None) or tree.css_first("main") or tree.body
    if root is None:
        return ""
    text = root.text(separator="\n", strip=True)
    text = _WS.sub(" ", text)
    return _BLANK_LINES.sub("\n", text).strip()


SPEC_MARKERS = ("характеристик", "спецификац", "описание", "параметр", "технические")


def compress_text(text: str, max_chars: int = 3000, keywords: tuple[str, ...] = ()) -> str:
    """Сжимает текст до лимита: блок характеристик и строки с ключевыми словами в приоритете."""
    if len(text) <= max_chars:
        return text
    lower = text.lower()
    start = min((i for m in SPEC_MARKERS if (i := lower.find(m)) >= 0), default=-1)
    if start >= 0:
        chunk = text[start : start + max_chars]
        if len(chunk) >= max_chars * 0.6:
            return chunk
    lines = text.split("\n")
    keys = tuple(k.lower() for k in keywords if k)
    picked = [ln for ln in lines if ":" in ln or "₽" in ln or any(k in ln.lower() for k in keys)]
    result = "\n".join(picked)
    if len(result) < max_chars // 3:
        result = text
    return result[:max_chars]


@dataclass
class SelectorConfig:
    name: str | None = None
    price: str | None = None
    unit: str | None = None
    attributes: str | None = None


def selector_product(html: str, cfg: SelectorConfig) -> ProductData | None:
    tree = LexborHTMLParser(html)

    def first_text(selector: str | None) -> str | None:
        if not selector:
            return None
        node = tree.css_first(selector)
        if node is None:
            return None
        return (node.attributes.get("content") or node.text(strip=True) or "").strip() or None

    price = parse_price(first_text(cfg.price))
    if price is None:
        return None
    return ProductData(
        name=first_text(cfg.name),
        price=price,
        unit=first_text(cfg.unit),
        description=page_text(html, cfg.attributes) if cfg.attributes else None,
        method=ExtractionMethod.SELECTORS,
    )


def price_in_text(price: Decimal, text: str) -> bool:
    """Проверка против галлюцинаций: цена, извлечённая LLM, должна быть в тексте страницы."""
    normalized = re.sub(r"[\s  ]", "", text).replace(",", ".")
    candidates = {
        f"{price:.2f}",
        f"{price.normalize():f}",
        f"{int(price)}" if price == int(price) else f"{price:.2f}",
    }
    return any(c in normalized for c in candidates)
