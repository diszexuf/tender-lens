"""Пул Playwright/Chromium для страниц, которым нужен JavaScript (FR-03.3, render_js)."""

from __future__ import annotations

import asyncio
from typing import Any

from app.core.logging import get_logger

log = get_logger(__name__)

# Тяжёлые ресурсы не нужны для извлечения данных
BLOCKED_RESOURCES = {"image", "media", "font"}


class BrowserPool:
    """Один браузер на процесс воркера, отдельный контекст на каждую страницу."""

    def __init__(self, max_pages: int = 2) -> None:
        self._pw: Any = None
        self._browser: Any = None
        self._lock = asyncio.Lock()
        self._sem = asyncio.Semaphore(max_pages)

    async def _ensure(self) -> Any:
        async with self._lock:
            if self._browser is None or not self._browser.is_connected():
                from playwright.async_api import async_playwright

                self._pw = await async_playwright().start()
                self._browser = await self._pw.chromium.launch(args=["--disable-dev-shm-usage"])
                log.info("browser.started")
        return self._browser

    async def render(
        self,
        url: str,
        *,
        user_agent: str,
        timeout_s: float,
        wait_selector: str | None = None,
        settle_ms: int = 2500,
    ) -> tuple[str, int, str, str]:
        browser = await self._ensure()
        async with self._sem:
            context = await browser.new_context(locale="ru-RU", user_agent=user_agent)
            try:
                page = await context.new_page()
                await page.route(
                    "**/*",
                    lambda route: (
                        route.abort()
                        if route.request.resource_type in BLOCKED_RESOURCES
                        else route.continue_()
                    ),
                )
                resp = await page.goto(url, wait_until="domcontentloaded", timeout=timeout_s * 1000)
                if wait_selector:
                    await page.wait_for_selector(wait_selector, timeout=timeout_s * 1000)
                else:
                    await page.wait_for_timeout(settle_ms)
                html = await page.content()
                status = resp.status if resp else 200
                ctype = (resp.headers.get("content-type") if resp else None) or "text/html"
                if status >= 400:
                    from app.sources.fetcher import FetchError, FetchErrorKind

                    raise FetchError(FetchErrorKind.HTTP, f"HTTP {status}: {url}", status)
                return page.url, status, ctype, html
            finally:
                await context.close()

    async def pdf(self, html: str) -> bytes:
        """HTML-документ → PDF (выгрузка отчёта). Внешние ресурсы не загружаются."""
        browser = await self._ensure()
        async with self._sem:
            context = await browser.new_context(locale="ru-RU", java_script_enabled=False)
            try:
                page = await context.new_page()
                await page.route("**/*", lambda route: route.abort())
                await page.set_content(html, wait_until="domcontentloaded")
                data: bytes = await page.pdf(
                    format="A4", landscape=True, print_background=True, prefer_css_page_size=True
                )
                return data
            finally:
                await context.close()

    async def close(self) -> None:
        if self._browser is not None:
            await self._browser.close()
        if self._pw is not None:
            await self._pw.stop()
