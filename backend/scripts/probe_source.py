"""Проверка источника: запускает адаптер на тестовом запросе и печатает результат (FR-03.5).

python scripts/probe_source.py html '{"search_url": "https://shop.ru/search?q={query}"}' "кабель ВВГнг 3х2,5"
python scripts/probe_source.py --candidates "картридж CE285A"   # список кандидатов ниже
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


from app.core.logging import configure_logging
from app.domain.rules import Rules
from app.services.sources import probe as run_probe
from app.sources.browser import BrowserPool

CANDIDATES: dict[str, dict] = {
    "220-volt": {"search_url": "https://www.220-volt.ru/search/?query={query}"},
    "officemag": {"search_url": "https://www.officemag.ru/search/?q={query}"},
    "onlinetrade": {"search_url": "https://www.onlinetrade.ru/sitesearch.html?query={query}"},
    "regard": {"search_url": "https://www.regard.ru/catalog?search={query}"},
    "relefopt": {"search_url": "https://relefopt.ru/search/?q={query}"},
    "minimaks": {"search_url": "https://www.minimaks.ru/search/?q={query}"},
    "elektrika": {"search_url": "https://www.elektrika.ru/search/?q={query}"},
    "nix": {"search_url": "https://www.nix.ru/price/search_panel_ajax.html?search={query}"},
}


async def probe(name: str, type_: str, config: dict, query: str, browser: BrowserPool) -> None:
    result = await run_probe(type_, config, query, Rules().source_timeout_s, browser)
    print(name, result.status, result.duration_ms, result.error or "")
    for note in result.warnings:
        print(note)
    for offer in result.offers:
        print(offer.extraction_method, offer.price, offer.unit_raw or "", offer.title)


async def main() -> None:
    configure_logging("WARNING", json=False)
    browser = BrowserPool()
    try:
        if sys.argv[1] == "--candidates":
            for name, cfg in CANDIDATES.items():
                await probe(name, "html", cfg, sys.argv[2], browser)
        else:
            await probe("source", sys.argv[1], json.loads(sys.argv[2]), sys.argv[3], browser)
    finally:
        await browser.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except ValueError:
        sys.exit("Проверьте настройки и введите название товара без персональных данных")
