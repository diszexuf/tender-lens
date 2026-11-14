"""Этап 2: сбор предложений из источников (FR-04).

Каждая пара «позиция × источник» выполняется независимо: ошибка или таймаут одного источника
фиксируется в source_run и не прерывает расчёт (NFR-02.1).
"""

from __future__ import annotations

import asyncio
import html
import time
import uuid
from datetime import UTC, datetime

from sqlalchemy import select

from app.core.logging import get_logger
from app.db import models as m
from app.domain.rules import Rules
from app.domain.spec import PositionSpec, SpecAttribute
from app.pipeline.deps import Deps
from app.pipeline.progress import Progress
from app.sources import eis_adapter, html_adapter, searxng_adapter  # noqa: F401  — регистрация адаптеров
from app.sources.base import (
    AdapterContext,
    AdapterResult,
    RunStatus,
    SourceQuery,
    SourceRole,
    get_adapter,
)
from app.sources.fetcher import Fetcher

log = get_logger(__name__)

MAX_PARALLEL_RUNS = 4


def unescape_text(text: str | None) -> str | None:
    """«&apos;&apos;», «&amp;nbsp;» из JSON-LD и разметки — в обычный текст."""
    if text is None:
        return None
    for _ in range(2):  # бывает двойное экранирование
        text = html.unescape(text)
    return text.replace(" ", " ")


def position_spec(row: m.Position) -> PositionSpec:
    return PositionSpec(
        ordinal=row.ordinal,
        name=row.name,
        quantity=row.quantity,
        unit_raw=row.unit_raw,
        okei_code=row.okei_code,
        ktru_code=row.ktru_code,
        ktru_confirmed=row.ktru_confirmed,
        search_query=row.search_query,
        attributes=[SpecAttribute.model_validate(a) for a in row.attributes],
    )


def source_applies(source: m.Source, position: PositionSpec) -> bool:
    if not source.okpd2_prefixes or not position.ktru_code:
        return True
    return any(position.ktru_code.startswith(p) for p in source.okpd2_prefixes)


async def run_collect(
    deps: Deps, calc_id: uuid.UUID, rules: Rules, *, use_cache: bool = True
) -> None:
    progress = Progress(deps.redis, str(calc_id))
    async with deps.session() as session:
        positions = (
            (
                await session.execute(
                    select(m.Position)
                    .where(m.Position.calculation_id == calc_id)
                    .order_by(m.Position.ordinal)
                )
            )
            .scalars()
            .all()
        )
        sources = (
            (
                await session.execute(
                    select(m.Source)
                    .where(m.Source.enabled.is_(True), m.Source.role == SourceRole.PRICE)
                    .order_by(m.Source.priority, m.Source.name)
                )
            )
            .scalars()
            .all()
        )
        pairs = [(p.id, position_spec(p), s) for p in positions for s in sources]

    await progress.emit(
        "stage",
        stage="collect",
        status="started",
        total=len(pairs),
        sources=[s.name for s in sources],
    )
    if not sources:
        await progress.emit(
            "stage", stage="collect", status="done", message="нет активных источников"
        )
        return

    semaphore = asyncio.Semaphore(MAX_PARALLEL_RUNS)
    async with Fetcher(deps.settings, deps.redis, deps.s3, deps.browser) as fetcher:

        async def run_one(position_id: uuid.UUID, spec: PositionSpec, source: m.Source) -> None:
            async with semaphore:
                await _run_source(
                    deps, progress, fetcher, calc_id, position_id, spec, source, rules, use_cache
                )

        await asyncio.gather(*(run_one(pid, spec, src) for pid, spec, src in pairs))
    await deps.flush_llm_calls(calc_id)
    await progress.emit("stage", stage="collect", status="done")


async def _run_source(
    deps: Deps,
    progress: Progress,
    fetcher: Fetcher,
    calc_id: uuid.UUID,
    position_id: uuid.UUID,
    spec: PositionSpec,
    source: m.Source,
    rules: Rules,
    use_cache: bool,
) -> None:
    started_at = datetime.now(UTC)
    started = time.perf_counter()
    ctx = AdapterContext(fetcher=fetcher, llm=deps.llm, use_cache=use_cache)
    await progress.emit("source", position=spec.ordinal, source=source.name, status="running")

    if not source_applies(source, spec):
        result = AdapterResult(RunStatus.SKIPPED, error="источник не применим к категории ОКПД2")
    else:
        try:
            adapter = get_adapter(source.type, source.config)
            query = SourceQuery(
                position=spec, query=spec.search_query, max_offers=rules.max_offers_per_source
            )
            async with asyncio.timeout(rules.source_timeout_s):
                result = await adapter.search(query, ctx)
        except TimeoutError:
            result = AdapterResult(
                RunStatus.TIMEOUT, error=f"источник не ответил за {rules.source_timeout_s} с"
            )
        except Exception as exc:  # адаптер не должен ронять расчёт
            log.exception("source.failed", source=source.name)
            result = AdapterResult(RunStatus.ERROR, error=f"{type(exc).__name__}: {exc}"[:1000])

    if result.status == RunStatus.OK and not result.offers:
        result.status = RunStatus.EMPTY
    duration_ms = int((time.perf_counter() - started) * 1000)

    async with deps.session() as session:
        run = m.SourceRun(
            calculation_id=calc_id,
            position_id=position_id,
            source_id=source.id,
            source_name=source.name,
            source_type=source.type,
            source_config=source.config,
            status=result.status,
            error="; ".join(filter(None, [result.error, *ctx.notes])) or None,
            requests=[r.as_dict() for r in ctx.requests],
            offers_found=len(result.offers),
            started_at=started_at,
            duration_ms=duration_ms,
        )
        session.add(run)
        await session.flush()

        snapshot_ids: dict[str, uuid.UUID] = {}
        for req in ctx.requests:
            if req.s3_key and req.s3_key not in snapshot_ids:
                snap = m.Snapshot(
                    source_run_id=run.id,
                    url=req.final_url or req.url,
                    http_status=req.status or 0,
                    s3_key=req.s3_key,
                    sha256=req.sha256 or "",
                    content_type=req.content_type,
                    size=req.size or 0,
                    fetched_at=req.fetched_at or started_at,
                )
                session.add(snap)
                await session.flush()
                snapshot_ids[req.s3_key] = snap.id

        for offer in result.offers:
            session.add(
                m.Offer(
                    position_id=position_id,
                    source_run_id=run.id,
                    source_name=source.name,
                    url=offer.url,
                    title=unescape_text(offer.title) or offer.title,
                    price=offer.price,
                    currency=offer.currency,
                    vat_status=offer.vat_status,
                    unit_raw=offer.unit_raw,
                    pack_qty=offer.pack_qty,
                    attributes_raw=unescape_text(offer.attributes_raw),
                    attributes=offer.attributes,
                    supplier_name=offer.supplier_name,
                    supplier_inn=offer.supplier_inn,
                    supplier_kind=offer.supplier_kind,
                    price_date=offer.price_date,
                    region=offer.region,
                    external_id=offer.external_id,
                    extraction_method=offer.extraction_method,
                    snapshot_ids=[
                        snapshot_ids[k] for k in offer.snapshot_keys if k in snapshot_ids
                    ],
                    fetched_at=offer.fetched_at,
                    flags=list(offer.flags),
                )
            )
        await session.commit()

    await progress.emit(
        "source",
        position=spec.ordinal,
        source=source.name,
        status=result.status,
        offers=len(result.offers),
        duration_ms=duration_ms,
        error=result.error,
    )
