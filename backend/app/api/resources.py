"""Снапшоты страниц источников и справочник источников."""

from __future__ import annotations

import asyncio
import gzip
import html
import re
import time
import uuid
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
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
from app.sources.breaker import FAILURES, SUCCESSES, breaker_state, reset_breaker
from app.sources.fetcher import FetchError, assert_public_url
from app.storage.s3 import s3_client

router = APIRouter(tags=["resources"])
Session = Annotated[AsyncSession, Depends(get_session)]

# Снапшот — чужая страница: показываем без скриптов, форм и внешних подключений кроме стилей/картинок
SNAPSHOT_CSP = (
    "sandbox; default-src 'none'; img-src * data:; style-src * 'unsafe-inline'; font-src *"
)


_CHARSET = re.compile(rb"""<meta[^>]+charset=["']?([\w-]+)""", re.I)
_META_CHARSET = re.compile(rb"<meta[^>]*charset[^>]*>", re.I)
_HEAD = re.compile(rb"<head[^>]*>", re.I)
_BODY = re.compile(rb"<body[^>]*>", re.I)


def snapshot_page(
    body: bytes, url: str, content_type: str | None, fetched_at: datetime, sha256: str
) -> bytes:
    """Копия страницы для просмотра: в исходной кодировке, со стилями и картинками
    с сайта-источника (<base href>) и плашкой «откуда и когда сохранено»."""
    charset = None
    if content_type and "charset=" in content_type.lower():
        charset = content_type.lower().split("charset=", 1)[1].split(";")[0].strip(" \"'")
    if not charset and (m_ := _CHARSET.search(body[:4096])):
        charset = m_.group(1).decode("ascii", "ignore")
    try:
        text = body.decode(charset or "utf-8", errors="replace")
    except LookupError:
        text = body.decode("utf-8", errors="replace")
    if not re.search(r"<html|<body|<div|<head", text[:20000], re.I):
        text = f"<pre style='white-space:pre-wrap'>{html.escape(text)}</pre>"
    raw = text.encode("utf-8")
    raw = _META_CHARSET.sub(b"", raw)
    base = f'<meta charset="utf-8"><base href="{html.escape(url, quote=True)}">'.encode()
    raw = (
        _HEAD.sub(lambda m_: m_.group(0) + base, raw, count=1) if _HEAD.search(raw) else base + raw
    )
    banner = (
        '<div style="all:initial;display:block;position:sticky;top:0;z-index:2147483647;'
        "background:#fff8e1;border-bottom:1px solid #e0c060;padding:8px 12px;"
        'font:14px/1.4 system-ui,sans-serif;color:#333">'
        f"Копия страницы, сохранённая Tender Lens {fetched_at:%d.%m.%Y %H:%M} UTC. "
        f'Адрес: <a href="{html.escape(url, quote=True)}" target="_blank" rel="noopener">'
        f"{html.escape(url[:120])}</a> (ссылки и кнопки на копии не работают). "
        f'<span style="color:#888">SHA-256: {sha256[:16]}…</span></div>'
    ).encode()
    if m_ := _BODY.search(raw):
        return raw[: m_.end()] + banner + raw[m_.end() :]
    return banner + raw


@router.get(
    "/snapshots/{snapshot_id}",
    summary="Сохранённая копия страницы источника (FR-04.5)",
    response_class=Response,
    responses={
        200: {"content": {"text/html": {}}},
        404: {"description": "Сохранённая копия страницы не найдена"},
    },
)
async def get_snapshot(snapshot_id: uuid.UUID, session: Session) -> Response:
    snap = await session.get(m.Snapshot, snapshot_id)
    if snap is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Сохранённая копия страницы не найдена")
    settings = get_settings()
    async with s3_client(settings) as s3:
        try:
            obj = await s3.get_object(Bucket=settings.s3_bucket_snapshots, Key=snap.s3_key)
            async with obj["Body"] as stream:
                data = await stream.read()
        except Exception as exc:
            raise HTTPException(
                status.HTTP_404_NOT_FOUND, "Файл сохранённой копии страницы недоступен"
            ) from exc
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


HEALTH_WINDOW = 20  # последних обращений для доли ошибок


class SourceHealth(BaseModel):
    source_id: uuid.UUID
    last_run_at: datetime | None
    last_success_at: datetime | None
    runs: int
    error_share: float | None
    consecutive_failures: int
    circuit_open: bool
    retry_in_s: int | None


@router.get(
    "/sources/health",
    response_model=list[SourceHealth],
    summary="Состояние источников: последний успех, доля ошибок, circuit breaker (FR-03.6)",
)
async def sources_health(session: Session, request: Request) -> list[SourceHealth]:
    sources = (await session.execute(select(m.Source.id))).scalars().all()
    out = []
    for source_id in sources:
        runs = (
            await session.execute(
                select(m.SourceRun.status, m.SourceRun.started_at)
                .where(m.SourceRun.source_id == source_id)
                .order_by(m.SourceRun.started_at.desc())
                .limit(HEALTH_WINDOW)
            )
        ).all()
        last_success = await session.scalar(
            select(m.SourceRun.started_at)
            .where(
                m.SourceRun.source_id == source_id,
                m.SourceRun.status.in_([str(x) for x in SUCCESSES]),
            )
            .order_by(m.SourceRun.started_at.desc())
            .limit(1)
        )
        counted = [r for r in runs if r.status in FAILURES or r.status in SUCCESSES]
        state = await breaker_state(request.app.state.redis, str(source_id))
        out.append(
            SourceHealth(
                source_id=source_id,
                last_run_at=runs[0].started_at if runs else None,
                last_success_at=last_success,
                runs=len(counted),
                error_share=round(sum(r.status in FAILURES for r in counted) / len(counted), 2)
                if counted
                else None,
                consecutive_failures=state.failures,
                circuit_open=state.open,
                retry_in_s=state.retry_in_s,
            )
        )
    return out


@router.post(
    "/sources/{source_id}/breaker/reset",
    status_code=204,
    summary="Снова включить источник, отключённый circuit breaker",
)
async def breaker_reset(source_id: uuid.UUID, session: Session, request: Request) -> Response:
    await _source(session, source_id)
    await reset_breaker(request.app.state.redis, str(source_id))
    return Response(status_code=204)


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
