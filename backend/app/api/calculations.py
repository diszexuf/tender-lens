"""API расчётов (docs/03-architecture.md, §8)."""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from app import __version__
from app.api.schemas import (
    CalculationCreate,
    CalculationDetail,
    CalculationList,
    Report,
    RunRequest,
    SpecUpdate,
)
from app.db import models as m
from app.db.session import get_session
from app.domain.spec import ProcurementSpec
from app.pipeline.deps import attrs_hash
from app.pipeline.progress import channel, history_key
from app.services import calculations as svc

router = APIRouter(prefix="/calculations", tags=["calculations"])
Session = Annotated[AsyncSession, Depends(get_session)]

FINAL_EVENTS = {"done", "failed", "cancelled"}
EDITABLE = {"spec_ready", "failed", "interrupted"}
RUNNABLE = {"spec_ready", "failed", "interrupted"}
ACTIVE = {"extracting", "collecting", "evaluating"}


async def _get(session: AsyncSession, calc_id: uuid.UUID) -> m.Calculation:
    calc = await session.get(m.Calculation, calc_id)
    if calc is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Расчёт не найден")
    return calc


def _conflict(message: str) -> HTTPException:
    return HTTPException(status.HTTP_409_CONFLICT, message)


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
    summary="Сохранить отредактированную спецификацию (до запуска поиска)",
    responses={409: {"description": "Спецификацию нельзя менять в текущем статусе"}},
)
async def update_spec(calc_id: uuid.UUID, body: SpecUpdate, session: Session) -> CalculationDetail:
    calc = await _get(session, calc_id)
    if calc.status not in EDITABLE:
        raise _conflict(f"Спецификацию нельзя менять в статусе «{calc.status}»")
    positions = svc.spec_positions(body.positions)
    await session.execute(delete(m.SourceRun).where(m.SourceRun.calculation_id == calc_id))
    await session.execute(delete(m.Position).where(m.Position.calculation_id == calc_id))
    for p in positions:
        attrs = [a.model_dump(mode="json") for a in p.attributes]
        session.add(
            m.Position(
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
                attrs_hash=attrs_hash(attrs),
            )
        )
    calc.spec_final = ProcurementSpec(positions=positions).model_dump(mode="json")
    calc.spec_edited = calc.spec_final != {
        k: v for k, v in (calc.spec_extracted or {}).items() if k == "positions" or k == "region"
    }
    calc.status, calc.error = "spec_ready", None
    await session.commit()
    return await svc.get_detail(session, calc)


@router.post(
    "/{calc_id}/run",
    response_model=CalculationDetail,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Запустить сбор предложений и расчёт",
    responses={409: {"description": "Расчёт уже выполняется или завершён"}},
)
async def run_calculation(
    calc_id: uuid.UUID, session: Session, body: RunRequest | None = None
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
    await calc_run.kiq(str(calc_id), use_cache=(body or RunRequest()).use_cache)
    return await svc.get_detail(session, calc)


@router.post(
    "/{calc_id}/recalculate",
    response_model=CalculationDetail,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Перерасчёт на собранных данных с текущими настройками (новая версия)",
    responses={409: {"description": "Расчёт ещё не завершён"}},
)
async def recalculate(calc_id: uuid.UUID, session: Session) -> CalculationDetail:
    from app.worker import calc_recalculate

    calc = await _get(session, calc_id)
    if calc.status != "completed":
        raise _conflict("Перерасчёт доступен только для завершённого расчёта")
    calc.status, calc.stage = "evaluating", "evaluate"
    await session.commit()
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
    if calc.status not in ACTIVE:
        raise _conflict("Расчёт не выполняется")
    calc.status, calc.error = "cancelled", "Отменено пользователем"
    await session.commit()
    return await svc.get_detail(session, calc)


@router.get(
    "/{calc_id}/report",
    response_model=Report,
    summary="Отчёт: выборка, исключения, расчёт (он же audit trail в JSON)",
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
