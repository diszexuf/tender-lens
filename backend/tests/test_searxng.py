from app.sources.searxng_adapter import SearxngSourceConfig, select_urls

RESULTS = [
    {"url": "https://www.ozon.ru/category/kabel-vvgng/"},
    {"url": "https://www.vseinstrumenti.ru/tag-page/kabel-vvg-12447/"},
    {"url": "https://xcabel.ru/products/vvgng-a-ls-3x2-5"},
    {"url": "https://www.etm.ru/cat/nn/6427418"},
    {"url": "https://www.etm.ru/cat/nn/9729229"},
    {"url": "https://shop.ru/"},
    {"url": "https://www.optimant.ru/catalog/kabel-vvgng-ls/kabel-vvgng-ls-3h2-5/"},
]


def test_select_urls_filters_listings_marketplaces_and_duplicates() -> None:
    urls = select_urls(RESULTS, SearxngSourceConfig(), limit=10)
    assert urls == [
        "https://xcabel.ru/products/vvgng-a-ls-3x2-5",
        "https://www.etm.ru/cat/nn/6427418",
        "https://www.optimant.ru/catalog/kabel-vvgng-ls/kabel-vvgng-ls-3h2-5/",
    ]


def test_allowlist() -> None:
    cfg = SearxngSourceConfig(allow_domains=["etm.ru"], one_per_domain=False)
    assert select_urls(RESULTS, cfg, limit=10) == [
        "https://www.etm.ru/cat/nn/6427418",
        "https://www.etm.ru/cat/nn/9729229",
    ]
