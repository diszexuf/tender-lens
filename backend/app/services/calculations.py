"""Чтение расчётов и сборка отчёта (FR-09)."""

from __future__ import annotations

import uuid
from collections import defaultdict
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas import (
    CalculationDetail,
    CalculationList,
    CalculationSummary,
    OfferReport,
    PositionReport,
    PositionResultReport,
    Report,
    SourceRunReport,
    VersionReport,
    VersionSummary,
)
from app.db import models as m
from app.domain.reasons import REASON_TITLES
from app.domain.spec import PositionSpec
from app.pipeline.collect_stage import position_spec
from app.pipeline.evaluate_stage import status_label


def _title(text: str, limit: int = 120) -> str:
    first = " ".join(text.split())
    return first if len(first) <= limit else first[: limit - 1] + "…"


async def list_calculations(
    session: AsyncSession, q: str | None, limit: int, offset: int
) -> CalculationList:
    base = select(m.Calculation)
    if q:
        base = base.where(m.Calculation.input_text.ilike(f"%{q}%"))
    total = (await session.execute(select(func.count()).select_from(base.subquery()))).scalar_one()
    calcs = (
        (
            await session.execute(
                base.order_by(m.Calculation.created_at.desc()).limit(limit).offset(offset)
            )
        )
        .scalars()
        .all()
    )
    ids = [c.id for c in calcs]
    counts = dict(
        (
            await session.execute(
                select(m.Position.calculation_id, func.count())
                .where(m.Position.calculation_id.in_(ids))
                .group_by(m.Position.calculation_id)
            )
        ).all()
    )
    latest = {
        v.calculation_id: v
        for v in (
            await session.execute(
                select(m.CalculationVersion)
                .where(m.CalculationVersion.calculation_id.in_(ids))
                .order_by(m.CalculationVersion.version_no)
            )
        ).scalars()
    }
    return CalculationList(
        total=total,
        items=[
            CalculationSummary(
                id=c.id,
                created_at=c.created_at,
                title=_title(c.input_text),
                status=c.status,  # type: ignore[arg-type]
                positions=counts.get(c.id, 0),
                nmck_total=latest[c.id].nmck_total if c.id in latest else None,
                outcome=latest[c.id].outcome if c.id in latest else None,
            )
            for c in calcs
        ],
    )


async def get_detail(session: AsyncSession, calc: m.Calculation) -> CalculationDetail:
    # updated_at обновляется на стороне БД: после commit атрибут устаревает,
    # а ленивая догрузка в async-коде недопустима
    await session.refresh(calc)
    positions = (
        (
            await session.execute(
                select(m.Position)
                .where(m.Position.calculation_id == calc.id)
                .order_by(m.Position.ordinal)
            )
        )
        .scalars()
        .all()
    )
    versions = (
        (
            await session.execute(
                select(m.CalculationVersion)
                .where(m.CalculationVersion.calculation_id == calc.id)
                .order_by(m.CalculationVersion.version_no)
            )
        )
        .scalars()
        .all()
    )
    return CalculationDetail(
        id=calc.id,
        created_at=calc.created_at,
        updated_at=calc.updated_at,
        status=calc.status,  # type: ignore[arg-type]
        stage=calc.stage,
        error=calc.error,
        input_text=calc.input_text,
        input_params=calc.input_params,
        auto_run=calc.auto_run,
        spec=[position_spec(p) for p in positions],
        spec_warnings=list((calc.spec_extracted or {}).get("warnings", [])),
        spec_edited=calc.spec_edited,
        versions=[_version_summary(v) for v in versions],
    )


def _version_summary(v: m.CalculationVersion) -> VersionSummary:
    return VersionSummary(
        id=v.id,
        version_no=v.version_no,
        created_at=v.created_at,
        trigger=v.trigger,
        outcome=v.outcome,
        nmck_total=v.nmck_total,
    )


async def build_report(
    session: AsyncSession, calc: m.Calculation, version_no: int | None
) -> Report:
    detail = await get_detail(session, calc)
    version_q = select(m.CalculationVersion).where(m.CalculationVersion.calculation_id == calc.id)
    version_q = (
        version_q.where(m.CalculationVersion.version_no == version_no)
        if version_no
        else version_q.order_by(m.CalculationVersion.version_no.desc()).limit(1)
    )
    version = (await session.execute(version_q)).scalar_one_or_none()

    positions = (
        (
            await session.execute(
                select(m.Position)
                .where(m.Position.calculation_id == calc.id)
                .order_by(m.Position.ordinal)
            )
        )
        .scalars()
        .all()
    )
    runs = (
        (
            await session.execute(
                select(m.SourceRun)
                .where(m.SourceRun.calculation_id == calc.id)
                .order_by(m.SourceRun.started_at)
            )
        )
        .scalars()
        .all()
    )
    runs_by_pos: dict[uuid.UUID, list[m.SourceRun]] = defaultdict(list)
    for run_row in runs:
        runs_by_pos[run_row.position_id].append(run_row)

    offers = (
        (
            await session.execute(
                select(m.Offer)
                .where(m.Offer.position_id.in_([p.id for p in positions]))
                .order_by(m.Offer.id)
            )
        )
        .scalars()
        .all()
    )
    evaluations: dict[uuid.UUID, m.OfferEvaluation] = {}
    results: dict[uuid.UUID, m.PositionResult] = {}
    version_report = None
    if version is not None:
        evaluations = {
            e.offer_id: e
            for e in (
                await session.execute(
                    select(m.OfferEvaluation).where(m.OfferEvaluation.version_id == version.id)
                )
            ).scalars()
        }
        results = {
            r.position_id: r
            for r in (
                await session.execute(
                    select(m.PositionResult).where(m.PositionResult.version_id == version.id)
                )
            ).scalars()
        }
        settings = await session.get(m.SettingsVersion, version.settings_version_id)
        version_report = VersionReport(
            **_version_summary(version).model_dump(),
            settings_version_id=version.settings_version_id,
            rules=settings.rules if settings else {},
            llm_model=version.llm_model,
            prompt_versions=version.prompt_versions,
        )

    offers_by_pos: dict[uuid.UUID, list[OfferReport]] = defaultdict(list)
    for o in offers:
        e = evaluations.get(o.id)
        offers_by_pos[o.position_id].append(
            OfferReport(
                id=o.id,
                source_name=o.source_name,
                url=o.url,
                title=o.title,
                price=o.price,
                currency=o.currency,
                vat_status=o.vat_status,
                unit_raw=o.unit_raw,
                pack_qty=o.pack_qty,
                supplier_name=o.supplier_name,
                supplier_inn=o.supplier_inn,
                price_date=o.price_date,
                fetched_at=o.fetched_at,
                extraction_method=o.extraction_method,
                snapshot_ids=o.snapshot_ids,
                decision=e.decision if e else "pending",
                reason_code=e.reason_code if e else None,
                reason_text=e.reason_text if e else "",
                relevance=e.relevance if e else None,
                text_score=e.text_score if e else None,
                attribute_matches=e.attribute_matches if e else [],
                normalization_steps=e.normalization_steps if e else [],
                normalized_price=e.normalized_price if e else None,
                flags=e.flags if e else list(o.flags),
                is_manual=e.is_manual if e else False,
                duplicate_of=e.duplicate_of if e else None,
            )
        )

    position_reports = []
    for p in positions:
        res = results.get(p.id)
        offers_sorted = sorted(
            offers_by_pos[p.id],
            key=lambda x: (
                x.decision != "included",
                -(x.relevance or Decimal(0)),
                -(x.text_score or Decimal(0)),
            ),
        )
        position_reports.append(
            PositionReport(
                id=p.id,
                spec=position_spec(p),
                result=PositionResultReport(
                    status=res.status,
                    status_label=status_label(res.status),
                    unit_price=res.unit_price,
                    total_price=res.total_price,
                    stats=res.stats,
                    filter_log=res.filter_log,
                    hints=res.hints,
                    flags=res.flags,
                    outlier_bounds=res.outlier_bounds,
                )
                if res
                else None,
                sources=[
                    SourceRunReport(
                        source_name=run.source_name,
                        source_type=run.source_type,
                        status=run.status,
                        error=run.error,
                        offers_found=run.offers_found,
                        duration_ms=run.duration_ms,
                        requests=len(run.requests),
                    )
                    for run in runs_by_pos[p.id]
                ],
                offers=offers_sorted,
            )
        )

    return Report(
        calculation=detail,
        version=version_report,
        positions=position_reports,
        reasons={k.value: v for k, v in REASON_TITLES.items()},
    )


def spec_positions(update: list[PositionSpec]) -> list[PositionSpec]:
    """Нормализует порядковые номера после редактирования."""
    return [p.model_copy(update={"ordinal": i}) for i, p in enumerate(update, start=1)]
