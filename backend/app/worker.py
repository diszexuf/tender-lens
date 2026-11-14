"""Taskiq: брокер и фоновые задачи.

Запуск воркера:  taskiq worker app.worker:broker
"""

from __future__ import annotations

import asyncio
import contextlib
import uuid
from datetime import UTC, datetime

from redis.asyncio import Redis
from taskiq import Context, TaskiqDepends, TaskiqEvents, TaskiqState
from taskiq_redis import RedisAsyncResultBackend, RedisStreamBroker

from app import __version__
from app.core.config import get_settings
from app.core.logging import configure_logging, get_logger
from app.pipeline import orchestrator
from app.services.health import WORKER_HEARTBEAT_KEY
from app.sources.browser import BrowserPool

HEARTBEAT_INTERVAL_SECONDS = 10
HEARTBEAT_TTL_SECONDS = 30

_settings = get_settings()

broker = RedisStreamBroker(url=_settings.redis_url).with_result_backend(
    RedisAsyncResultBackend(redis_url=_settings.redis_url, result_ex_time=3600)
)

log = get_logger(__name__)


async def _heartbeat(redis: Redis) -> None:
    while True:
        try:
            await redis.set(
                WORKER_HEARTBEAT_KEY,
                datetime.now(UTC).isoformat(timespec="seconds"),
                ex=HEARTBEAT_TTL_SECONDS,
            )
        except Exception as exc:  # Redis недоступен — пробуем дальше
            log.warning("worker.heartbeat.failed", error=str(exc))
        await asyncio.sleep(HEARTBEAT_INTERVAL_SECONDS)


@broker.on_event(TaskiqEvents.WORKER_STARTUP)
async def on_startup(state: TaskiqState) -> None:
    configure_logging(_settings.log_level, _settings.log_json)
    state.redis = Redis.from_url(_settings.redis_url, decode_responses=True)
    state.heartbeat = asyncio.create_task(_heartbeat(state.redis))
    state.browser = BrowserPool()
    async with orchestrator.pipeline_deps(_settings, state.redis, None) as deps:
        interrupted = await orchestrator.mark_interrupted(deps)
    log.info("worker.started", version=__version__, interrupted=interrupted)


@broker.on_event(TaskiqEvents.WORKER_SHUTDOWN)
async def on_shutdown(state: TaskiqState) -> None:
    state.heartbeat.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await state.heartbeat
    await state.browser.close()
    await state.redis.aclose()
    log.info("worker.stopped")


@broker.task(task_name="system.ping")
async def ping(message: str = "ping") -> str:
    """Проверочная задача: подтверждает, что очередь и воркер работают."""
    return f"pong: {message}"


# ---------------------------------------------------------------- расчёты
# Длинные задачи: тайм-аут большой, повторов нет — состояние хранится в БД (NFR-02.5).


@broker.task(task_name="calc.extract", timeout=1800)
async def calc_extract(calculation_id: str, context: Context = TaskiqDepends()) -> None:
    state = context.state
    async with orchestrator.pipeline_deps(_settings, state.redis, state.browser) as deps:
        await orchestrator.extract(deps, uuid.UUID(calculation_id))


@broker.task(task_name="calc.run", timeout=3600)
async def calc_run(
    calculation_id: str, use_cache: bool = True, context: Context = TaskiqDepends()
) -> None:
    state = context.state
    async with orchestrator.pipeline_deps(_settings, state.redis, state.browser) as deps:
        await orchestrator.run(deps, uuid.UUID(calculation_id), use_cache=use_cache)


@broker.task(task_name="calc.recalculate", timeout=1800)
async def calc_recalculate(
    calculation_id: str, trigger: str = "recalc", context: Context = TaskiqDepends()
) -> None:
    state = context.state
    async with orchestrator.pipeline_deps(_settings, state.redis, state.browser) as deps:
        await orchestrator.recalculate(deps, uuid.UUID(calculation_id), trigger)


@broker.task(task_name="sources.probe")
async def probe_with_browser(
    type_: str, config: dict, query: str, timeout_s: int, context: Context = TaskiqDepends()
) -> dict:
    from app.services.sources import probe

    result = await probe(type_, config, query, timeout_s, context.state.browser)
    return result.model_dump(mode="json")
