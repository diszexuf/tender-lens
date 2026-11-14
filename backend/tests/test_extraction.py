"""Извлечение на сохранённых реальных страницах (NFR-05.4)."""

from decimal import Decimal as D
from pathlib import Path

import pytest

from app.domain.offer import ExtractionMethod
from app.extraction.html import (
    compress_text,
    itemlist_links,
    page_text,
    page_title,
    parse_price,
    pattern_links,
    price_in_text,
    structured_products,
)

FIXTURES = Path(__file__).parent / "fixtures" / "html"


def load(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8", errors="ignore")


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("17 664,50 ₽", D("17664.50")),
        ("181.58", D("181.58")),
        (181.58, D("181.58")),
        ("от 1 290 руб.", D("1290")),
        ("Цена по запросу", None),
        (0, None),
        (None, None),
    ],
)
def test_parse_price(raw: object, expected: D | None) -> None:
    assert parse_price(raw) == expected


def test_etm_search_itemlist_links() -> None:
    links = itemlist_links(load("etm_search.html"), "https://www.etm.ru/catalog")
    assert len(links) == 24
    assert all(link.startswith("https://www.etm.ru/cat/nn/") for link in links)


def test_etm_card_jsonld_product() -> None:
    products = structured_products(load("etm_card_meter.html"), "https://www.etm.ru/cat/nn/4545156")
    assert len(products) == 1
    p = products[0]
    assert p.method == ExtractionMethod.JSONLD
    assert p.price == D("181.58") and p.currency == "RUB"
    assert p.name and "ВВГнг" in p.name


def test_etm_card_pack_price() -> None:
    p = structured_products(load("etm_card_pack.html"), "https://www.etm.ru/cat/nn/5480173")[0]
    assert p.price == D("17664")
    assert p.name and "100 м" in p.name


def test_komus_card_microdata() -> None:
    products = structured_products(load("komus_card.html"), "https://www.komus.ru/p/164585/")
    assert any(p.method == ExtractionMethod.MICRODATA and p.price == D("10494") for p in products)


def test_komus_card_title_and_text() -> None:
    html = load("komus_card.html")
    title = page_title(html)
    assert title and "CE285A" in title
    text = page_text(html)
    assert "<script" not in text and len(text) > 200


def test_pattern_links() -> None:
    html = '<a href="/katalog/x/p/123/">a</a><a href="/about">b</a><a href="https://other.ru/p/1">c</a>'
    assert pattern_links(html, "https://shop.ru/search", r"/p/\d+") == [
        "https://shop.ru/katalog/x/p/123/"
    ]


def test_compress_prefers_specifications() -> None:
    text = "Меню\n" * 500 + "Характеристики\nРесурс: 1600 стр.\nЦвет: черный\n" + "Отзывы\n" * 500
    out = compress_text(text, max_chars=300)
    assert out.startswith("Характеристики") and len(out) <= 300


def test_price_in_text_guards_hallucination() -> None:
    page = "Картридж HP 85A\nЦена: 10 494 ₽"
    assert price_in_text(D("10494"), page)
    assert not price_in_text(D("9990"), page)
