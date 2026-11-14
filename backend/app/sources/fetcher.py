"""Загрузка страниц источников.

Каждый запрос:
- проверяется на SSRF (NFR-04.4) и разрешение robots.txt (NFR-04.3);
- выполняется с таймаутом и повторами на сетевые ошибки, 429 и 5xx (NFR-02.2);
- сохраняется снапшотом в S3 с SHA-256 (FR-04.5);
- кэшируется в Redis с исходным временем получения (FR-04.7).
"""

from __future__ import annotations

import asyncio
import gzip
import hashlib
import ipaddress
import json
import socket
import ssl
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser

import certifi
import httpx
from redis.asyncio import Redis
from tenacity import (
    AsyncRetrying,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential_jitter,
)

from app.core.config import Settings
from app.core.logging import get_logger
from app.sources.browser import BrowserPool

log = get_logger(__name__)

CACHE_PREFIX = "tl:http:"
ROBOTS_TOKEN = "TenderLens"
MAX_BODY_BYTES = 8 * 1024 * 1024

# Корневой сертификат НУЦ Минцифры (Russian Trusted Root CA): им подписаны сертификаты
# zakupki.gov.ru и других госсайтов. Источник — gu-st.ru (Госуслуги), отпечаток SHA-256
# D2:6D:2D:02:31:B7:C3:9F:92:CC:73:85:12:BA:54:10:35:19:E4:40:5D:68:B5:BD:70:3E:97:88:CA:8E:CF:31.
# Доверие ему ограничено доменами из настройки RU_TRUSTED_CA_HOSTS.
RU_ROOT_CA = Path(__file__).with_name("certs") / "russian_trusted_root_ca.pem"


def ru_ca_context() -> ssl.SSLContext:
    ctx = ssl.create_default_context(cafile=certifi.where())
    ctx.load_verify_locations(cafile=str(RU_ROOT_CA))
    return ctx


def host_matches(host: str, patterns: list[str]) -> bool:
    host = host.lower().rstrip(".")
    return any(host == p or host.endswith("." + p) for p in patterns)


class FetchErrorKind(StrEnum):
    NETWORK = "network"
    TIMEOUT = "timeout"
    HTTP = "http"
    ROBOTS = "robots"
    SSRF = "ssrf"


class FetchError(Exception):
    def __init__(self, kind: FetchErrorKind, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.kind = kind
        self.status = status


@dataclass
class Page:
    url: str
    final_url: str
    status: int
    content_type: str
    text: str
    fetched_at: datetime
    sha256: str
    s3_key: str
    size: int
    cached: bool
    rendered: bool


@dataclass
class RequestLog:
    url: str
    status: int | None
    duration_ms: int
    cached: bool = False
    rendered: bool = False
    s3_key: str | None = None
    sha256: str | None = None
    content_type: str | None = None
    size: int | None = None
    fetched_at: datetime | None = None
    final_url: str | None = None
    error: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            k: (v.isoformat() if isinstance(v, datetime) else v)
            for k, v in self.__dict__.items()
            if v is not None
        }


# ---------------------------------------------------------------- SSRF


def _is_public_ip(ip: str) -> bool:
    addr = ipaddress.ip_address(ip)
    return not (
        addr.is_private
        or addr.is_loopback
        or addr.is_link_local
        or addr.is_multicast
        or addr.is_reserved
        or addr.is_unspecified
    )


async def assert_public_url(url: str) -> None:
    """Запрещает обращения к внутренним адресам (localhost, сеть Docker, 169.254.*)."""
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise FetchError(FetchErrorKind.SSRF, f"недопустимый адрес: {url}")
    host = parts.hostname
    try:
        infos = await asyncio.get_running_loop().getaddrinfo(host, parts.port or 443)
    except socket.gaierror as exc:
        raise FetchError(FetchErrorKind.NETWORK, f"DNS: {host}: {exc}") from exc
    for info in infos:
        ip = str(info[4][0])
        if not _is_public_ip(ip):
            raise FetchError(FetchErrorKind.SSRF, f"адрес {host} ведёт во внутреннюю сеть ({ip})")


# ---------------------------------------------------------------- Fetcher


def _retryable(exc: BaseException) -> bool:
    if isinstance(exc, FetchError):
        return exc.kind in (FetchErrorKind.NETWORK, FetchErrorKind.TIMEOUT) or (
            exc.kind == FetchErrorKind.HTTP and exc.status is not None
            and (exc.status == 429 or exc.status >= 500)
        )  # fmt: skip
    return False


@dataclass
class Fetcher:
    settings: Settings
    redis: Redis
    s3: Any  # aiobotocore S3 client
    browser: BrowserPool | None = None
    cache_ttl: int = 24 * 3600
    respect_robots: bool = True
    attempts: int = 3
    _client: httpx.AsyncClient | None = None
    _ru_client: httpx.AsyncClient | None = None
    per_host_limit: int = 3
    _robots: dict[str, RobotFileParser | None] = field(default_factory=dict)
    _hosts: dict[str, asyncio.Semaphore] = field(default_factory=dict)

    async def __aenter__(self) -> Fetcher:
        common: dict[str, Any] = {
            "timeout": httpx.Timeout(self.settings.http_timeout_seconds, connect=10.0),
            "follow_redirects": True,
            "headers": {
                "User-Agent": self.settings.http_user_agent,
                "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
                "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.5",
            },
        }
        self._client = httpx.AsyncClient(**common)
        if self.settings.ru_trusted_ca_host_list:
            self._ru_client = httpx.AsyncClient(verify=ru_ca_context(), **common)
        return self

    async def __aexit__(self, *exc: object) -> None:
        for client in (self._client, self._ru_client):
            if client:
                await client.aclose()

    @property
    def client(self) -> httpx.AsyncClient:
        assert self._client is not None, "Fetcher используется вне async with"
        return self._client

    def client_for(self, url: str) -> httpx.AsyncClient:
        """Госсайты с сертификатами НУЦ Минцифры — через клиент с этим корневым сертификатом."""
        host = urlsplit(url).hostname or ""
        if self._ru_client and host_matches(host, self.settings.ru_trusted_ca_host_list):
            return self._ru_client
        return self.client

    async def get(
        self,
        url: str,
        *,
        render_js: bool = False,
        use_cache: bool = True,
        request_log: list[RequestLog] | None = None,
        wait_selector: str | None = None,
    ) -> Page:
        started = time.perf_counter()
        entry = RequestLog(url=url, status=None, duration_ms=0, rendered=render_js)
        try:
            page = await self._get(url, render_js, use_cache, wait_selector)
            entry.status, entry.cached = page.status, page.cached
            entry.s3_key, entry.sha256 = page.s3_key, page.sha256
            entry.content_type, entry.size = page.content_type, page.size
            entry.fetched_at, entry.final_url = page.fetched_at, page.final_url
            return page
        except FetchError as exc:
            entry.status, entry.error = exc.status, f"{exc.kind}: {exc}"
            raise
        finally:
            entry.duration_ms = int((time.perf_counter() - started) * 1000)
            if request_log is not None:
                request_log.append(entry)

    async def _get(
        self, url: str, render_js: bool, use_cache: bool, wait_selector: str | None
    ) -> Page:
        await assert_public_url(url)
        if self.respect_robots and not await self._allowed(url):
            raise FetchError(FetchErrorKind.ROBOTS, f"запрещено robots.txt: {url}")

        cache_key = CACHE_PREFIX + hashlib.sha256(f"{render_js}:{url}".encode()).hexdigest()
        if use_cache and (cached := await self._from_cache(cache_key, url, render_js)):
            return cached

        host = urlsplit(url).netloc
        sem = self._hosts.setdefault(host, asyncio.Semaphore(self.per_host_limit))
        async for attempt in AsyncRetrying(
            stop=stop_after_attempt(self.attempts),
            wait=wait_exponential_jitter(initial=1, max=8),
            retry=retry_if_exception(_retryable),
            reraise=True,
        ):
            with attempt:
                async with sem:
                    if render_js:
                        final_url, status, ctype, text = await self._render(url, wait_selector)
                    else:
                        final_url, status, ctype, text = await self._http(url)

        page = await self._store(url, final_url, status, ctype, text, render_js)
        await self.redis.set(
            cache_key,
            json.dumps(
                {
                    "final_url": page.final_url,
                    "status": page.status,
                    "content_type": page.content_type,
                    "fetched_at": page.fetched_at.isoformat(),
                    "sha256": page.sha256,
                    "s3_key": page.s3_key,
                    "size": page.size,
                }
            ),
            ex=self.cache_ttl,
        )
        return page

    async def _http(self, url: str) -> tuple[str, int, str, str]:
        try:
            resp = await self.client_for(url).get(url)
        except httpx.TimeoutException as exc:
            raise FetchError(FetchErrorKind.TIMEOUT, f"таймаут: {url}") from exc
        except httpx.HTTPError as exc:
            raise FetchError(FetchErrorKind.NETWORK, f"{type(exc).__name__}: {exc}") from exc
        if resp.status_code >= 400:
            raise FetchError(
                FetchErrorKind.HTTP, f"HTTP {resp.status_code}: {url}", resp.status_code
            )
        if len(resp.content) > MAX_BODY_BYTES:
            raise FetchError(FetchErrorKind.HTTP, f"ответ больше {MAX_BODY_BYTES} байт", 413)
        return str(resp.url), resp.status_code, resp.headers.get("content-type", ""), resp.text

    async def _render(self, url: str, wait_selector: str | None) -> tuple[str, int, str, str]:
        if self.browser is None:
            raise FetchError(FetchErrorKind.NETWORK, "рендеринг JS недоступен в этом процессе")
        try:
            return await self.browser.render(
                url,
                user_agent=self.settings.http_user_agent,
                timeout_s=self.settings.http_timeout_seconds + 15,
                wait_selector=wait_selector,
            )
        except TimeoutError as exc:
            raise FetchError(FetchErrorKind.TIMEOUT, f"таймаут рендеринга: {url}") from exc
        except FetchError:
            raise
        except Exception as exc:
            raise FetchError(FetchErrorKind.NETWORK, f"браузер: {exc}") from exc

    async def _store(
        self, url: str, final_url: str, status: int, ctype: str, text: str, rendered: bool
    ) -> Page:
        body = text.encode("utf-8")
        sha = hashlib.sha256(body).hexdigest()
        key = f"{datetime.now(UTC):%Y/%m/%d}/{sha}.html.gz"
        await self.s3.put_object(
            Bucket=self.settings.s3_bucket_snapshots,
            Key=key,
            Body=gzip.compress(body),
            ContentType="text/html; charset=utf-8",
            ContentEncoding="gzip",
            Metadata={"source-url": final_url[:1000].encode("ascii", "ignore").decode()},
        )
        now = datetime.now(UTC)
        return Page(url, final_url, status, ctype, text, now, sha, key, len(body), False, rendered)

    async def _from_cache(self, cache_key: str, url: str, rendered: bool) -> Page | None:
        raw = await self.redis.get(cache_key)
        if not raw:
            return None
        meta = json.loads(raw)
        try:
            obj = await self.s3.get_object(
                Bucket=self.settings.s3_bucket_snapshots, Key=meta["s3_key"]
            )
            async with obj["Body"] as stream:
                data = await stream.read()
        except Exception:
            return None  # снапшот потерян — загрузим заново
        text = gzip.decompress(data).decode("utf-8") if data[:2] == b"\x1f\x8b" else data.decode()
        fetched_at = datetime.fromisoformat(meta["fetched_at"])
        return Page(
            url, meta["final_url"], meta["status"], meta["content_type"], text, fetched_at,
            meta["sha256"], meta["s3_key"], meta["size"], True, rendered,
        )  # fmt: skip

    async def _allowed(self, url: str) -> bool:
        parts = urlsplit(url)
        origin = f"{parts.scheme}://{parts.netloc}"
        if origin not in self._robots:
            self._robots[origin] = await self._load_robots(origin)
        parser = self._robots[origin]
        return True if parser is None else parser.can_fetch(ROBOTS_TOKEN, url)

    async def _load_robots(self, origin: str) -> RobotFileParser | None:
        cache_key = f"{CACHE_PREFIX}robots:{origin}"
        cached = await self.redis.get(cache_key)
        content = cached.decode() if isinstance(cached, bytes) else cached
        if content is None:
            try:
                resp = await self.client_for(origin).get(f"{origin}/robots.txt", timeout=10)
                content = resp.text if resp.status_code == 200 else ""
            except httpx.HTTPError:
                content = ""  # robots.txt недоступен — ограничений нет
            await self.redis.set(cache_key, content, ex=self.cache_ttl)
        if not content:
            return None
        parser = RobotFileParser()
        parser.parse(content.splitlines())
        return parser
