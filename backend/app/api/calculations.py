"""API расчётов (docs/03-architecture.md, §8)."""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import AsyncIterator
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import HTMLResponse, Response, StreamingResponse
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app import __version__
from app.api.schemas import (
    AuditTrail,
    CalculationCreate,
    CalculationDetail,
    CalculationList,
    ManualDecisionIn,
    Report,
    RunRequest,
    SpecUpdate,
    VersionComparison,
)
from app.core.config import get_settings
from app.db import models as m
from app.db.session import get_session
from app.domain.spec import PositionSpec, ProcurementSpec
from app.pipeline.deps import attrs_hash
from app.pipeline.progress import Progress, channel, history_key
from app.services import calculations as svc

router = APIRouter(prefix="/calculations", tags=["calculations"])
Session = Annotated[AsyncSession, Depends(get_session)]

FINAL_EVENTS = {"done", "failed", "cancelled"}
EDITABLE = {"spec_ready", "failed", "interrupted", "completed"}
RUNNABLE = {"spec_ready", "failed", "interrupted"}
ACTIVE = {"extracting", "collecting", "evaluating"}
PDF_TIMEOUT_S = 180


async def _get(session: AsyncSession, calc_id: uuid.UUID) -> m.Calculation:
    calc = await session.get(m.Calculation, calc_id)
    if calc is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Расчёт не найден")
    return calc


def _conflict(message: str) -> HTTPException:
    return HTTPException(status.HTTP_409_CONFLICT, message)


async def _new_run(request: Request, calc_id: uuid.UUID) -> None:
    """Новый запуск: история событий прошлого запуска («done») не должна попасть в SSE."""
    await Progress(request.app.state.redis, str(calc_id)).reset()


@router.post(
    "",
    response_model=CalculationDetail,
    status_code=status.HTTP_201_CREATED,
    summary="Создать расчёт и запустить разбор описания закупки",
)
async def create_calculation(body: CalculationCreate, session: Session) -> CalculationDetail:
    from app.worker import calc_extract

    calc = m.Calculation(
        input_text=body.text.strip(),
        input_params=body.params.model_dump(mode="json"),
        auto_run=body.auto_run,
        status="draft",
        app_version=__version__,
    )
    session.add(calc)
    await session.commit()
    await calc_extract.kiq(str(calc.id))
    return await svc.get_detail(session, calc)


@router.get("", response_model=CalculationList, summary="История расчётов")
async def list_calculations(
    session: Session,
    q: Annotated[str | None, Query(max_length=200, description="Поиск по тексту закупки")] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> CalculationList:
    return await svc.list_calculations(session, q, limit, offset)


@router.get("/{calc_id}", response_model=CalculationDetail, summary="Состояние и спецификация")
async def get_calculation(calc_id: uuid.UUID, session: Session) -> CalculationDetail:
    return await svc.get_detail(session, await _get(session, calc_id))


@router.put(
    "/{calc_id}/spec",
    response_model=CalculationDetail,
    summary="Сохранить спецификацию: до запуска поиска или после расчёта (перерасчёт, UC-05)",
    responses={409: {"description": "Спецификацию нельзя менять в текущем статусе"}},
)
async def update_spec(
    calc_id: uuid.UUID, body: SpecUpdate, session: Session, request: Request
) -> CalculationDetail:
    calc = await _get(session, calc_id)
    if calc.status not in EDITABLE:
        raise _conflict(f"Спецификацию нельзя менять в статусе «{calc.status}»")
    positions = svc.spec_positions(body.positions)
    completed = calc.status == "completed"
    need_collect = False
    if completed:
        # Правка после расчёта (UC-05): собранные данные сохраняются у позиций с тем же
        # наименованием, запросом и КТРУ; новые и изменённые позиции собираются заново
        need_collect = await _update_positions(session, calc_id, positions)
    else:
        await session.execute(delete(m.SourceRun).where(m.SourceRun.calculation_id == calc_id))
        await session.execute(delete(m.Position).where(m.Position.calculation_id == calc_id))
        for p in positions:
            session.add(_position_row(calc_id, p))
    calc.spec_final = ProcurementSpec(positions=positions).model_dump(mode="json")
    calc.spec_edited = calc.spec_final != {
        k: v for k, v in (calc.spec_extracted or {}).items() if k == "positions" or k == "region"
    }
    if not completed:
        calc.status, calc.error = "spec_ready", None
        await session.commit()
        return await svc.get_detail(session, calc)

    from app.worker import calc_recalculate, calc_run

    calc.error = None
    calc.status, calc.stage = (
        ("collecting", "collect") if need_collect else ("evaluating", "evaluate")
    )
    await session.commit()
    await _new_run(request, calc_id)
    if need_collect:
        await calc_run.kiq(str(calc_id), only_missing=True, trigger="spec_edit")
    else:
        await calc_recalculate.kiq(str(calc_id), trigger="spec_edit")
    return await svc.get_detail(session, calc)


def _position_row(calc_id: uuid.UUID, p: PositionSpec) -> m.Position:
    attrs = [a.model_dump(mode="json") for a in p.attributes]
    return m.Position(
        calculation_id=calc_id,
        ordinal=p.ordinal,
        name=p.name,
        quantity=p.quantity,
        unit_raw=p.unit_raw,
        okei_code=p.okei_code,
        ktru_code=p.ktru_code,
        ktru_confirmed=p.ktru_confirmed,
        search_query=p.search_query,
        attributes=attrs,
        exclude_words=list(p.exclude_words),
        attrs_hash=attrs_hash(attrs),
    )


async def _update_positions(
    session: AsyncSession, calc_id: uuid.UUID, positions: list[PositionSpec]
) -> bool:
    """Обновляет позиции на месте; True — есть позиции, для которых нужен новый сбор."""
    old = {
        p.ordinal: p
        for p in (
            await session.execute(select(m.Position).where(m.Position.calculation_id == calc_id))
        ).scalars()
    }
    keep = {
        p.ordinal
        for p in positions
        if (row := old.get(p.ordinal)) is not None
        and (row.name, row.search_query, row.ktru_code) == (p.name, p.search_query, p.ktru_code)
    }
    for ordinal, row in old.items():
        if ordinal not in keep:
            await session.delete(row)  # офферы и прогоны источников удаляются каскадно
    await session.flush()
    for p in positions:
        if p.ordinal not in keep:
            session.add(_position_row(calc_id, p))
            continue
        row = old[p.ordinal]
        attrs = [a.model_dump(mode="json") for a in p.attributes]
        row.quantity, row.unit_raw, row.okei_code = p.quantity, p.unit_raw, p.okei_code
        row.exclude_words = list(p.exclude_words)
        row.ktru_confirmed, row.attributes, row.attrs_hash = (
            p.ktru_confirmed,
            attrs,
            attrs_hash(attrs),
        )
    return len(keep) < len(positions)


@router.post(
    "/{calc_id}/run",
    response_model=CalculationDetail,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Запустить сбор предложений и расчёт",
    responses={409: {"description": "Расчёт уже выполняется или завершён"}},
)
async def run_calculation(
    calc_id: uuid.UUID, session: Session, request: Request, body: RunRequest | None = None
) -> CalculationDetail:
    from app.worker import calc_run

    calc = await _get(session, calc_id)
    if calc.status not in RUNNABLE:
        raise _conflict(f"Запуск невозможен в статусе «{calc.status}»")
    detail = await svc.get_detail(session, calc)
    if not detail.spec:
        raise _conflict("В спецификации нет позиций")
    # Повторный запуск после сбоя: собранные ранее данные удаляются
    await session.execute(delete(m.SourceRun).where(m.SourceRun.calculation_id == calc_id))
    calc.status, calc.stage, calc.error = "collecting", "collect", None
    await session.commit()
    await _new_run(request, calc_id)
    await calc_run.kiq(str(calc_id), use_cache=(body or RunRequest()).use_cache)
    return await svc.get_detail(session, calc)


@router.post(
    "/{calc_id}/recalculate",
    response_model=CalculationDetail,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Перерасчёт на собранных данных с текущими настройками (новая версия)",
    responses={409: {"description": "Расчёт ещё не завершён"}},
)
async def recalculate(calc_id: uuid.UUID, session: Session, request: Request) -> CalculationDetail:
    from app.worker import calc_recalculate

    calc = await _get(session, calc_id)
    if calc.status != "completed":
        raise _conflict("Перерасчёт доступен только для завершённого расчёта")
    calc.status, calc.stage = "evaluating", "evaluate"
    await session.commit()
    await _new_run(request, calc_id)
    await calc_recalculate.kiq(str(calc_id))
    return await svc.get_detail(session, calc)


@router.post(
    "/{calc_id}/cancel",
    response_model=CalculationDetail,
    summary="Отменить выполняющийся расчёт",
    responses={409: {"description": "Расчёт не выполняется"}},
)
async def cancel(calc_id: uuid.UUID, session: Session) -> CalculationDetail:
    calc = await _get(session, calc_id)
    if calc.status not in {"draft", *ACTIVE}:
        raise _conflict("Расчёт не выполняется")
    calc.status, calc.error = "cancelled", "Отменено пользователем"
    await session.commit()
    return await svc.get_detail(session, calc)


# ---------------------------------------------------------------- ручные решения (FR-06.9)


@router.put(
    "/{calc_id}/offers/{offer_id}/decision",
    response_model=CalculationDetail,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Ручное включение или исключение предложения (новая версия расчёта)",
    responses={409: {"description": "Расчёт не завершён"}, 404: {"description": "Не найдено"}},
)
async def set_decision(
    calc_id: uuid.UUID,
    offer_id: uuid.UUID,
    body: ManualDecisionIn,
    session: Session,
    request: Request,
) -> CalculationDetail:
    calc = await _get(session, calc_id)
    await _offer_of(session, calc_id, offer_id)
    if calc.status != "completed":
        raise _conflict("Ручные решения доступны для завершённого расчёта")
    row = (
        await session.execute(
            select(m.ManualDecision).where(
                m.ManualDecision.calculation_id == calc_id, m.ManualDecision.offer_id == offer_id
            )
        )
    ).scalar_one_or_none()
    if row is None:
        session.add(
            m.ManualDecision(
                calculation_id=calc_id,
                offer_id=offer_id,
                decision=body.decision,
                comment=body.comment.strip(),
            )
        )
    else:
        row.decision, row.comment = body.decision, body.comment.strip()
    return await _recalc_after_decision(session, request, calc)


@router.delete(
    "/{calc_id}/offers/{offer_id}/decision",
    response_model=CalculationDetail,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Отменить ручное решение (новая версия расчёта)",
    responses={409: {"description": "Расчёт не завершён"}, 404: {"description": "Не найдено"}},
)
async def delete_decision(
    calc_id: uuid.UUID, offer_id: uuid.UUID, session: Session, request: Request
) -> CalculationDetail:
    calc = await _get(session, calc_id)
    if calc.status != "completed":
        raise _conflict("Ручные решения доступны для завершённого расчёта")
    row = (
        await session.execute(
            select(m.ManualDecision).where(
                m.ManualDecision.calculation_id == calc_id, m.ManualDecision.offer_id == offer_id
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Ручного решения нет")
    await session.delete(row)
    return await _recalc_after_decision(session, request, calc)


async def _offer_of(session: AsyncSession, calc_id: uuid.UUID, offer_id: uuid.UUID) -> m.Offer:
    offer = (
        await session.execute(
            select(m.Offer)
            .join(m.Position, m.Offer.position_id == m.Position.id)
            .where(m.Offer.id == offer_id, m.Position.calculation_id == calc_id)
        )
    ).scalar_one_or_none()
    if offer is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Предложение не найдено в расчёте")
    return offer


async def _recalc_after_decision(
    session: AsyncSession, request: Request, calc: m.Calculation
) -> CalculationDetail:
    from app.worker import calc_recalculate

    calc.status, calc.stage = "evaluating", "evaluate"
    await session.commit()
    await _new_run(request, calc.id)
    await calc_recalculate.kiq(str(calc.id), trigger="manual")
    return await svc.get_detail(session, calc)


# ---------------------------------------------------------------- отчёт, версии, выгрузки


@router.get(
    "/{calc_id}/report",
    response_model=Report,
    summary="Отчёт: выборка, исключения, расчёт",
)
async def report(
    calc_id: uuid.UUID,
    session: Session,
    version: Annotated[
        int | None, Query(ge=1, description="Номер версии; по умолчанию последняя")
    ] = None,
) -> Report:
    calc = await _get(session, calc_id)
    report = await svc.build_report(session, calc, version)
    if version is not None and report.version is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Версия не найдена")
    return report


@router.get(
    "/{calc_id}/versions",
    response_model=list[VersionComparison],
    summary="Итоги всех версий расчёта по позициям (сравнение версий)",
)
async def versions(calc_id: uuid.UUID, session: Session) -> list[VersionComparison]:
    return await svc.compare_versions(session, await _get(session, calc_id))


@router.get(
    "/{calc_id}/audit",
    response_model=AuditTrail,
    summary="Полный след расчёта: отчёт, запросы к источникам, вызовы LLM (FR-09.5)",
)
async def audit(
    calc_id: uuid.UUID,
    session: Session,
    version: Annotated[int | None, Query(ge=1)] = None,
) -> AuditTrail:
    return await svc.audit_trail(session, await _get(session, calc_id), version)


@router.get(
    "/{calc_id}/export",
    summary="Выгрузка отчёта: xlsx (с формулами), pdf или html (FR-09.2)",
    response_class=Response,
    responses={
        200: {
            "content": {
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": {},
                "application/pdf": {},
                "text/html": {},
            }
        },
        404: {"description": "Расчёт или версия не найдены"},
        503: {"description": "Воркер выгрузки PDF недоступен"},
    },
)
async def export(
    calc_id: uuid.UUID,
    session: Session,
    format: Annotated[Literal["xlsx", "pdf", "html"], Query()] = "xlsx",
    version: Annotated[int | None, Query(ge=1)] = None,
) -> Response:
    from app.reports.export import build_xlsx, file_name, render_html

    calc = await _get(session, calc_id)
    report = await svc.build_report(session, calc, version)
    if report.version is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Версия расчёта не найдена")
    if format == "html":
        return HTMLResponse(render_html(report))
    if format == "xlsx":
        return Response(
            build_xlsx(report),
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers=_attachment(file_name(report, "xlsx")),
        )
    await session.rollback()  # соединение не держим, пока воркер рисует PDF
    data = await _pdf(calc_id, report.version.version_no)
    return Response(
        data, media_type="application/pdf", headers=_attachment(file_name(report, "pdf"))
    )


def _attachment(name: str) -> dict[str, str]:
    return {"Content-Disposition": f'attachment; filename="{name}"'}


async def _pdf(calc_id: uuid.UUID, version_no: int) -> bytes:
    from app.storage.s3 import s3_client
    from app.worker import export_pdf

    settings = get_settings()
    try:
        async with asyncio.timeout(PDF_TIMEOUT_S):
            task = await export_pdf.kiq(str(calc_id), version_no)
            result = await task.wait_result()
    except TimeoutError as exc:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "PDF не готов: истекло время ожидания воркера"
        ) from exc
    if result.is_err:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Воркер не смог сформировать PDF")
    async with s3_client(settings) as s3:
        obj = await s3.get_object(Bucket=settings.s3_bucket_reports, Key=result.return_value)
        async with obj["Body"] as stream:
            data: bytes = await stream.read()
    return data


@router.get(
    "/{calc_id}/events",
    summary="Прогресс расчёта (Server-Sent Events)",
    response_class=StreamingResponse,
    responses={200: {"content": {"text/event-stream": {}}}},
)
async def events(calc_id: uuid.UUID, request: Request, session: Session) -> StreamingResponse:
    await _get(session, calc_id)
    redis = request.app.state.redis

    async def stream() -> AsyncIterator[str]:
        pubsub = redis.pubsub()
        await pubsub.subscribe(channel(str(calc_id)))
        try:
            # История — для подписавшихся после начала расчёта
            for raw in await redis.lrange(history_key(str(calc_id)), 0, -1):
                yield f"data: {raw}\n\n"
            idle = 0.0
            while not await request.is_disconnected():
                message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
                if message is None:
                    idle += 1
                    if idle >= 15:
                        idle = 0
                        yield ": ping\n\n"
                    continue
                idle = 0
                data = message["data"]
                yield f"data: {data}\n\n"
                if json.loads(data).get("event") in FINAL_EVENTS:
                    break
        except asyncio.CancelledError:
            pass
        finally:
            await pubsub.unsubscribe()
            await pubsub.aclose()

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
