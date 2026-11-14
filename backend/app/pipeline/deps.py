"""Зависимости пайплайна: БД, Redis, S3, LLM, браузер."""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.calc.units import DEFAULT_UNITS, Unit, UnitRegistry
from app.core.config import Settings
from app.db import models as m
from app.domain.rules import Rules
from app.llm.provider import LLMProvider, OllamaProvider
from app.matching.text import DEFAULT_SYNONYM_GROUPS, Synonyms
from app.sources.browser import BrowserPool


@dataclass
class Deps:
    settings: Settings
    sessions: async_sessionmaker[AsyncSession]
    redis: Redis
    s3: Any
    llm: LLMProvider
    browser: BrowserPool | None = None

    @asynccontextmanager
    async def session(self) -> AsyncIterator[AsyncSession]:
        async with self.sessions() as session:
            yield session

    async def flush_llm_calls(self, calc_id: uuid.UUID | None) -> dict[str, uuid.UUID]:
        """Сохраняет журнал вызовов LLM; возвращает request_hash → id записи."""
        records = getattr(self.llm, "records", None)
        if not records:
            return {}
        ids: dict[str, uuid.UUID] = {}
        async with self.session() as session:
            for rec in records:
                row = m.LLMCall(
                    calculation_id=calc_id,
                    purpose=rec.purpose,
                    model=rec.model,
                    prompt_version=rec.prompt_version,
                    request_hash=rec.request_hash,
                    request=rec.request,
                    response=rec.response,
                    error=rec.error,
                    duration_ms=rec.duration_ms,
                    tokens_in=rec.tokens_in,
                    tokens_out=rec.tokens_out,
                )
                session.add(row)
                await session.flush()
                ids[rec.request_hash] = row.id
            await session.commit()
        records.clear()
        return ids


def make_llm(settings: Settings) -> OllamaProvider:
    return OllamaProvider(settings)


def attrs_hash(attributes: list[dict[str, Any]]) -> str:
    return hashlib.sha256(
        json.dumps(attributes, ensure_ascii=False, sort_keys=True, default=str).encode()
    ).hexdigest()


async def latest_settings(session: AsyncSession) -> tuple[m.SettingsVersion, Rules]:
    row = (
        await session.execute(
            select(m.SettingsVersion).order_by(m.SettingsVersion.id.desc()).limit(1)
        )
    ).scalar_one_or_none()
    if row is None:
        row = m.SettingsVersion(rules=Rules().model_dump(mode="json"), comment="по умолчанию")
        session.add(row)
        await session.flush()
    return row, Rules.model_validate(row.rules)


async def load_dictionaries(session: AsyncSession) -> tuple[UnitRegistry, Synonyms]:
    """Справочники из БД; если пусто — значения по умолчанию."""
    units = (await session.execute(select(m.Unit))).scalars().all()
    groups = (
        (await session.execute(select(m.SynonymGroup).order_by(m.SynonymGroup.id))).scalars().all()
    )
    registry = (
        UnitRegistry(
            tuple(
                Unit(u.code, u.name, u.short, u.dimension, u.factor, tuple(u.aliases))
                for u in units
            )
        )
        if units
        else UnitRegistry(DEFAULT_UNITS)
    )
    synonyms = Synonyms(groups=[list(g.words) for g in groups] or DEFAULT_SYNONYM_GROUPS)
    return registry, synonyms
