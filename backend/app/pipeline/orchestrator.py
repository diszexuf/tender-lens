"""Оркестратор расчёта: последовательность этапов, статусы и обработка сбоев (NFR-02.5)."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from redis.asyncio import Redis
from sqlalchemy import CursorResult, update

from app.core.config import Settings
from app.core.logging import get_logger
from app.db import models as m
from app.db.session import get_sessionmaker
from app.pipeline.collect_stage import run_collect
from app.pipeline.deps import Deps, latest_settings, make_llm
from app.pipeline.evaluate_stage import run_evaluate
from app.pipeline.progress import Progress
from app.pipeline.spec_stage import run_extract
from app.sources.browser import BrowserPool
from app.storage.s3 import s3_client

log = get_logger(__name__)

ACTIVE_STATUSES = ("extracting", "collecting", "evaluating")


@asynccontextmanager
async def pipeline_deps(
    settings: Settings, redis: Redis, browser: BrowserPool | None
) -> AsyncIterator[Deps]:
    async with s3_client(settings) as s3:
        yield Deps(
            settings=settings,
            sessions=get_sessionmaker(),
            redis=redis,
            s3=s3,
            llm=make_llm(settings),
            browser=browser,
        )


async def _set_status(
    deps: Deps, calc_id: uuid.UUID, status: str, stage: str | None, error: str | None = None
) -> None:
    async with deps.session() as session:
        values: dict[str, object] = {"status": status, "stage": stage}
        if error is not None or status != "failed":
            values["error"] = error
        await session.execute(
            update(m.Calculation).where(m.Calculation.id == calc_id).values(**values)
        )
        await session.commit()


async def _is_cancelled(deps: Deps, calc_id: uuid.UUID) -> bool:
    async with deps.session() as session:
        calc = await session.get(m.Calculation, calc_id)
        return calc is None or calc.status == "cancelled"


async def extract(deps: Deps, calc_id: uuid.UUID) -> None:
    """Разбор закупки; при auto_run — сразу сбор и расчёт (FR-01.5)."""
    try:
        ok = await run_extract(deps, calc_id)
    except Exception as exc:
        log.exception("pipeline.extract_failed", calculation_id=str(calc_id))
        await _set_status(deps, calc_id, "failed", "spec", f"{type(exc).__name__}: {exc}")
        await Progress(deps.redis, str(calc_id)).emit("failed", message=str(exc))
        return
    async with deps.session() as session:
        calc = await session.get(m.Calculation, calc_id)
        auto = bool(calc and calc.auto_run)
    if ok and auto:
        await run(deps, calc_id)


async def run(deps: Deps, calc_id: uuid.UUID, *, use_cache: bool = True) -> None:
    """Сбор данных и оценка."""
    progress = Progress(deps.redis, str(calc_id))
    try:
        async with deps.session() as session:
            _, rules = await latest_settings(session)
            await session.commit()
        await _set_status(deps, calc_id, "collecting", "collect")
        await run_collect(deps, calc_id, rules, use_cache=use_cache)
        if await _is_cancelled(deps, calc_id):
            await progress.emit("cancelled")
            return
        await _set_status(deps, calc_id, "evaluating", "evaluate")
        await run_evaluate(deps, calc_id, trigger="initial")
        await progress.emit("done")
    except Exception as exc:
        log.exception("pipeline.run_failed", calculation_id=str(calc_id))
        await _set_status(deps, calc_id, "failed", None, f"{type(exc).__name__}: {exc}")
        await progress.emit("failed", message=str(exc))


async def recalculate(deps: Deps, calc_id: uuid.UUID, trigger: str = "recalc") -> None:
    """Перерасчёт на собранных данных: новая версия (FR-10.3)."""
    progress = Progress(deps.redis, str(calc_id))
    try:
        await _set_status(deps, calc_id, "evaluating", "evaluate")
        await run_evaluate(deps, calc_id, trigger=trigger)
        await progress.emit("done")
    except Exception as exc:
        log.exception("pipeline.recalc_failed", calculation_id=str(calc_id))
        await _set_status(deps, calc_id, "failed", None, f"{type(exc).__name__}: {exc}")
        await progress.emit("failed", message=str(exc))


async def mark_interrupted(deps: Deps) -> int:
    """После рестарта воркера незавершённые расчёты помечаются прерванными (NFR-02.5)."""
    async with deps.session() as session:
        result = await session.execute(
            update(m.Calculation)
            .where(m.Calculation.status.in_(ACTIVE_STATUSES))
            .values(status="interrupted", error="Расчёт прерван перезапуском воркера")
        )
        await session.commit()
        return result.rowcount if isinstance(result, CursorResult) else 0
