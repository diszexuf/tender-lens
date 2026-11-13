"""Проверка совместимости зависимостей с текущим Python (этап 0 плана).

python scripts/check_deps.py            # импорт всех библиотек
python scripts/check_deps.py --browser  # плюс запуск Chromium через Playwright
"""

from __future__ import annotations

import asyncio
import importlib
import sys
from importlib.metadata import version

MODULES = {
    "fastapi": "fastapi",
    "uvicorn": "uvicorn",
    "pydantic": "pydantic",
    "pydantic_settings": "pydantic-settings",
    "sqlalchemy": "sqlalchemy",
    "asyncpg": "asyncpg",
    "alembic": "alembic",
    "taskiq": "taskiq",
    "taskiq_redis": "taskiq-redis",
    "redis": "redis",
    "httpx": "httpx",
    "aioboto3": "aioboto3",
    "tenacity": "tenacity",
    "selectolax": "selectolax",
    "lxml": "lxml",
    "extruct": "extruct",
    "playwright": "playwright",
    "rapidfuzz": "rapidfuzz",
    "openpyxl": "openpyxl",
    "jinja2": "jinja2",
    "structlog": "structlog",
}


async def _check_browser() -> str:
    from playwright.async_api import async_playwright

    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        page = await browser.new_page()
        await page.set_content("<h1>ok</h1>")
        pdf = await page.pdf()
        await browser.close()
    return f"Chromium OK, PDF {len(pdf)} байт"


def main() -> int:
    print(f"Python {sys.version.split()[0]}")
    failed = 0
    for module, dist in MODULES.items():
        try:
            importlib.import_module(module)
            print(f"  ok    {dist}=={version(dist)}")
        except Exception as exc:
            failed += 1
            print(f"  FAIL  {dist}: {type(exc).__name__}: {exc}")
    if "--browser" in sys.argv:
        try:
            print(f"  ok    {asyncio.run(_check_browser())}")
        except Exception as exc:
            failed += 1
            print(f"  FAIL  playwright browser: {type(exc).__name__}: {exc}")
    print("Итог:", "все зависимости совместимы" if not failed else f"ошибок: {failed}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
