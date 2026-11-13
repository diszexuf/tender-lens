"""Taskiq: брокер и фоновые задачи.

Запуск воркера:  taskiq worker app.worker:broker
"""

from __future__ import annotations

import asyncio
import contextlib
from datetime import UTC, datetime

from redis.asyncio import Redis
from taskiq import TaskiqEvents, TaskiqState
from taskiq_redis import RedisAsyncResultBackend, RedisStreamBroker

from app import __version__
from app.core.config import get_settings
from app.core.logging import configure_logging, get_logger
from app.services.health import WORKER_HEARTBEAT_KEY

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
    log.info("worker.started", version=__version__)


@broker.on_event(TaskiqEvents.WORKER_SHUTDOWN)
async def on_shutdown(state: TaskiqState) -> None:
    state.heartbeat.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await state.heartbeat
    await state.redis.aclose()
    log.info("worker.stopped")


@broker.task(task_name="system.ping")
async def ping(message: str = "ping") -> str:
    """Проверочная задача: подтверждает, что очередь и воркер работают."""
    return f"pong: {message}"
