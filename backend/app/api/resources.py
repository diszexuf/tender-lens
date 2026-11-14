"""Снапшоты страниц источников и справочник источников."""

from __future__ import annotations

import asyncio
import gzip
import time
import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import Response
from pydantic import BaseModel, ValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.db import models as m
from app.db.session import get_session
from app.domain.rules import Rules
from app.services import sources as svc
from app.sources import eis_adapter, html_adapter, searxng_adapter  # noqa: F401  — регистрация типов
from app.sources.base import ADAPTERS, RunStatus, adapter_types
from app.sources.fetcher import FetchError, assert_public_url
from app.storage.s3 import s3_client

router = APIRouter(tags=["resources"])
Session = Annotated[AsyncSession, Depends(get_session)]

# Снапшот — чужая страница: показываем без скриптов, форм и внешних подключений кроме стилей/картинок
SNAPSHOT_CSP = (
    "sandbox; default-src 'none'; img-src * data:; style-src * 'unsafe-inline'; font-src *"
)


@router.get(
    "/snapshots/{snapshot_id}",
    summary="Сохранённая копия страницы источника (FR-04.5)",
    response_class=Response,
    responses={200: {"content": {"text/html": {}}}, 404: {"description": "Снапшот не найден"}},
)
async def get_snapshot(snapshot_id: uuid.UUID, session: Session) -> Response:
    snap = await session.get(m.Snapshot, snapshot_id)
    if snap is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Снапшот не найден")
    settings = get_settings()
    async with s3_client(settings) as s3:
        try:
            obj = await s3.get_object(Bucket=settings.s3_bucket_snapshots, Key=snap.s3_key)
            async with obj["Body"] as stream:
                data = await stream.read()
        except Exception as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Файл снапшота недоступен") from exc
    body = gzip.decompress(data) if data[:2] == b"\x1f\x8b" else data
    return Response(
        content=body,
        media_type="text/html; charset=utf-8",
        headers={
            "Content-Security-Policy": SNAPSHOT_CSP,
            "X-Content-Type-Options": "nosniff",
            "X-Snapshot-Url": snap.url.encode("ascii", "ignore").decode()[:1000],
            "X-Snapshot-Sha256": snap.sha256,
            "X-Snapshot-Fetched-At": snap.fetched_at.isoformat(),
        },
    )


class SourceOut(BaseModel):
    id: uuid.UUID
    name: str
    type: str
    role: str
    enabled: bool
    priority: int
    config: dict[str, Any]
    okpd2_prefixes: list[str]


class SourceType(BaseModel):
    type: str
    title: str
    description: str
    config_schema: dict[str, Any]


@router.get("/sources", response_model=list[SourceOut], summary="Источники данных")
async def list_sources(session: Session) -> list[SourceOut]:
    rows = (
        await session.execute(select(m.Source).order_by(m.Source.priority, m.Source.name))
    ).scalars()
    return [SourceOut.model_validate(row, from_attributes=True) for row in rows]


@router.get(
    "/source-types",
    response_model=list[SourceType],
    summary="Типы адаптеров и JSON Schema их настроек",
)
async def source_types() -> list[SourceType]:
    types = [SourceType(**t) for t in adapter_types()]
    for adapter in types:
        for name, field in ADAPTERS[adapter.type].config_model.model_fields.items():
            if field.default_factory:
                adapter.config_schema["properties"][name]["default"] = field.get_default(
                    call_default_factory=True
                )
    return types


async def _check_urls(config: dict) -> None:
    for key in ("base_url", "search_url"):
        if url := config.get(key):
            try:
                await asyncio.wait_for(assert_public_url(url.replace("{query}", "test")), 5)
            except (FetchError, TimeoutError, ValueError) as exc:
                raise HTTPException(
                    422, f"config.{key}: укажите доступный публичный HTTP(S)-адрес"
                ) from exc


class Enabled(BaseModel):
    enabled: bool


async def _source(session: Session, source_id: uuid.UUID, *, lock: bool = False) -> m.Source:
    row = await session.get(m.Source, source_id, with_for_update=lock)
    if row is None:
        raise HTTPException(404, "Источник не найден")
    return row


async def _save(session: Session, row: m.Source) -> SourceOut:
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(409, "Название источника уже используется") from exc
    return SourceOut.model_validate(row, from_attributes=True)


@router.post("/sources", status_code=201, responses={409: {"description": "Название уже занято"}})
async def create_source(body: svc.SourceInput, session: Session) -> SourceOut:
    await _check_urls(body.config)
    row = m.Source(**body.model_dump(mode="json"))
    session.add(row)
    return await _save(session, row)


async def _probe(session: Session, type_: str, config: dict, query: str) -> svc.ProbeResult:
    rules = await session.scalar(
        select(m.SettingsVersion.rules).order_by(m.SettingsVersion.id.desc()).limit(1)
    )
    timeout_s = Rules.model_validate(rules or {}).source_timeout_s
    await session.rollback()  # освободить соединение на время сетевого запроса
    if not (config.get("render_js") or config.get("card_render_js")):
        return await svc.probe(type_, config, query, timeout_s)
    from app.worker import probe_with_browser

    started = time.monotonic()
    try:
        async with asyncio.timeout(timeout_s):
            task = await probe_with_browser.kiq(type_, config, query, timeout_s)
            result = await task.wait_result()
            if result.is_err:
                raise RuntimeError
            report = svc.ProbeResult.model_validate(result.return_value)
            report.duration_ms = round((time.monotonic() - started) * 1000)
            return report
    except TimeoutError:
        return svc.ProbeResult(
            status=RunStatus.TIMEOUT,
            duration_ms=round((time.monotonic() - started) * 1000),
            error="Истекло время ожидания воркера",
        )
    except Exception as exc:
        raise HTTPException(503, "Воркер проверки недоступен") from exc


@router.post("/sources/probe")
async def probe_draft(body: svc.DraftProbe, session: Session) -> svc.ProbeResult:
    return await _probe(session, body.type, body.config, body.query)


@router.post("/sources/{source_id}/probe")
async def probe_saved(
    source_id: uuid.UUID, body: svc.ProbeQuery, session: Session
) -> svc.ProbeResult:
    row = await _source(session, source_id)
    try:
        settings = svc.AdapterInput(type=row.type, config=row.config)
    except ValidationError as exc:
        raise HTTPException(422, "Проверьте сохранённые настройки источника") from exc
    return await _probe(session, settings.type, settings.config, body.query)


@router.get("/sources/{source_id}")
async def get_source(source_id: uuid.UUID, session: Session) -> SourceOut:
    return SourceOut.model_validate(await _source(session, source_id), from_attributes=True)


@router.put("/sources/{source_id}")
async def update_source(source_id: uuid.UUID, body: svc.SourceInput, session: Session) -> SourceOut:
    row = await _source(session, source_id, lock=True)
    await _check_urls(body.config)
    for field, value in body.model_dump(mode="json").items():
        setattr(row, field, value)
    return await _save(session, row)


@router.patch("/sources/{source_id}/enabled")
async def toggle_source(source_id: uuid.UUID, body: Enabled, session: Session) -> SourceOut:
    row = await _source(session, source_id, lock=True)
    row.enabled = body.enabled
    return await _save(session, row)


@router.delete(
    "/sources/{source_id}",
    status_code=204,
    responses={
        409: {"description": "Источник использовался в расчёте"},
        404: {"description": "Источник не найден"},
    },
)
async def delete_source(source_id: uuid.UUID, session: Session) -> Response:
    row = await _source(session, source_id, lock=True)
    # Офферы ссылаются на источник через source_run.
    if await session.scalar(
        select(m.SourceRun.id).where(m.SourceRun.source_id == source_id).limit(1)
    ):
        raise HTTPException(409, "Источник использовался в расчёте. Его можно отключить.")
    await session.delete(row)
    await session.commit()
    return Response(status_code=204)
