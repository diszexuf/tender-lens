"""Точка входа FastAPI."""

from __future__ import annotations

import contextlib
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from redis.asyncio import Redis

from app import __version__
from app.api.router import api_router
from app.core.config import get_settings
from app.core.logging import configure_logging, get_logger
from app.db.session import get_engine


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    app.state.redis = Redis.from_url(settings.redis_url, decode_responses=True)
    # Клиент брокера: API ставит задачи в очередь воркера
    from app.worker import broker

    log = get_logger(__name__)
    if not broker.is_worker_process:
        try:
            await broker.startup()
        except Exception as exc:  # Redis недоступен: API работает, постановка задач — ошибка
            log.warning("api.broker_unavailable", error=str(exc))
    log.info("api.started", version=__version__, env=settings.app_env)
    try:
        yield
    finally:
        if not broker.is_worker_process:
            with contextlib.suppress(Exception):
                await broker.shutdown()
        await app.state.redis.aclose()
        await get_engine().dispose()


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings.log_level, settings.log_json)

    app = FastAPI(
        title="Tender Lens API",
        version=__version__,
        description="Обоснование НМЦК методом сопоставимых рыночных цен",
        lifespan=lifespan,
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
        redoc_url=None,
        # Одна схема для запросов и ответов: клиент редактирует и отправляет тот же тип
        separate_input_output_schemas=False,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(api_router)
    return app


app = create_app()
