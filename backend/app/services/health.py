"""Проверка готовности зависимостей (NFR-06.7)."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from typing import Literal

import httpx
from pydantic import BaseModel
from redis.asyncio import Redis
from sqlalchemy import text

from app import __version__
from app.core.config import Settings
from app.db.session import get_engine
from app.storage.s3 import s3_client

WORKER_HEARTBEAT_KEY = "tl:worker:heartbeat"
CHECK_TIMEOUT_SECONDS = 3.0

Status = Literal["ok", "degraded", "down"]


class ComponentHealth(BaseModel):
    name: str
    status: Status
    latency_ms: int
    detail: str | None = None


class HealthReport(BaseModel):
    status: Status
    version: str
    components: list[ComponentHealth]


async def _timed(name: str, check: Callable[[], Awaitable[str | None]]) -> ComponentHealth:
    started = time.perf_counter()
    try:
        detail = await asyncio.wait_for(check(), timeout=CHECK_TIMEOUT_SECONDS)
        status: Status = "ok"
    except _Degraded as exc:
        status, detail = "degraded", str(exc)
    except TimeoutError:
        status, detail = "down", "таймаут проверки"
    except Exception as exc:
        status, detail = "down", f"{type(exc).__name__}: {exc}"[:300]
    latency = int((time.perf_counter() - started) * 1000)
    return ComponentHealth(name=name, status=status, latency_ms=latency, detail=detail)


class _Degraded(Exception):
    """Сервис доступен, но не полностью готов (например, модель ещё скачивается)."""


async def check_health(settings: Settings, redis: Redis) -> HealthReport:
    async def database() -> str | None:
        async with get_engine().connect() as conn:
            version = (await conn.execute(text("SHOW server_version"))).scalar_one()
            revision = (
                await conn.execute(
                    text(
                        "SELECT version_num FROM alembic_version "
                        "WHERE to_regclass('alembic_version') IS NOT NULL"
                    )
                )
            ).scalar_one_or_none()
        if revision is None:
            raise _Degraded(f"PostgreSQL {version}: миграции не применены")
        return f"PostgreSQL {version}, миграция {revision}"

    async def cache() -> str | None:
        await redis.ping()
        return None

    async def worker() -> str | None:
        beat = await redis.get(WORKER_HEARTBEAT_KEY)
        if beat is None:
            raise _Degraded("нет сигнала от воркера")
        return f"последний сигнал: {beat if isinstance(beat, str) else beat.decode()}"

    async def storage() -> str | None:
        async with s3_client(settings) as s3:
            for bucket in (settings.s3_bucket_snapshots, settings.s3_bucket_reports):
                await s3.head_bucket(Bucket=bucket)
        return None

    async def llm() -> str | None:
        async with httpx.AsyncClient(timeout=CHECK_TIMEOUT_SECONDS) as client:
            resp = await client.get(f"{settings.ollama_url}/api/tags")
            resp.raise_for_status()
        models = {m.get("name") for m in resp.json().get("models", [])}
        wanted = settings.ollama_model
        if wanted not in models and f"{wanted}:latest" not in models:
            raise _Degraded(f"модель {wanted} не загружена")
        return wanted

    async def search() -> str | None:
        async with httpx.AsyncClient(timeout=CHECK_TIMEOUT_SECONDS) as client:
            resp = await client.get(f"{settings.searxng_url}/healthz")
            resp.raise_for_status()
        return None

    components = await asyncio.gather(
        _timed("database", database),
        _timed("redis", cache),
        _timed("worker", worker),
        _timed("storage", storage),
        _timed("llm", llm),
        _timed("search", search),
    )
    statuses = {c.status for c in components}
    overall: Status = (
        "down" if "down" in statuses else "degraded" if "degraded" in statuses else "ok"
    )
    return HealthReport(status=overall, version=__version__, components=list(components))
