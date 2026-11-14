"""Настройки правил и справочники (FR-12): версии правил, ОКЕИ, синонимы, индексы цен."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import Response
from pydantic import BaseModel, Field, ValidationError, field_validator
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import models as m
from app.db.session import get_session
from app.domain.rules import Rules
from app.pipeline.deps import latest_settings

router = APIRouter(tags=["settings"])
Session = Annotated[AsyncSession, Depends(get_session)]


# ---------------------------------------------------------------- правила


class SettingsOut(BaseModel):
    id: int
    created_at: datetime
    comment: str | None
    rules: dict[str, Any]
    rules_schema: dict[str, Any]


class SettingsIn(BaseModel):
    rules: dict[str, Any]
    comment: str | None = Field(None, max_length=500)


class SettingsVersionOut(BaseModel):
    id: int
    created_at: datetime
    comment: str | None


def _settings_out(row: m.SettingsVersion, rules: Rules) -> SettingsOut:
    return SettingsOut(
        id=row.id,
        created_at=row.created_at,
        comment=row.comment,
        rules=rules.model_dump(mode="json"),
        rules_schema=Rules.model_json_schema(),
    )


@router.get("/settings", response_model=SettingsOut, summary="Текущие правила расчёта")
async def get_rules(session: Session) -> SettingsOut:
    row, rules = await latest_settings(session)
    await session.commit()
    return _settings_out(row, rules)


@router.put(
    "/settings",
    response_model=SettingsOut,
    summary="Сохранить правила: создаётся новая версия настроек (FR-12.2)",
    responses={422: {"description": "Недопустимые значения"}},
)
async def save_rules(body: SettingsIn, session: Session) -> SettingsOut:
    try:
        rules = Rules.model_validate(body.rules)
    except ValidationError as exc:
        errors = "; ".join(f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors())
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, errors) from exc
    current, current_rules = await latest_settings(session)
    if current_rules == rules:
        await session.commit()
        return _settings_out(current, current_rules)
    row = m.SettingsVersion(rules=rules.model_dump(mode="json"), comment=body.comment)
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return _settings_out(row, rules)


@router.get(
    "/settings/versions", response_model=list[SettingsVersionOut], summary="История версий настроек"
)
async def settings_versions(session: Session) -> list[SettingsVersionOut]:
    rows = (
        await session.execute(select(m.SettingsVersion).order_by(m.SettingsVersion.id.desc()))
    ).scalars()
    return [SettingsVersionOut.model_validate(r, from_attributes=True) for r in rows]


# ---------------------------------------------------------------- единицы ОКЕИ


class UnitIn(BaseModel):
    code: str = Field(min_length=1, max_length=20, description="Код ОКЕИ")
    name: str = Field(min_length=1, max_length=100)
    short: str = Field(min_length=1, max_length=20)
    dimension: str = Field(
        min_length=1,
        max_length=30,
        description="Размерность (length, mass, count, pack…): пересчёт только внутри одной",
    )
    factor: Decimal = Field(gt=0, description="Коэффициент к базовой единице размерности")
    aliases: list[str] = Field(default_factory=list, description="Написания в текстах")

    @field_validator("aliases")
    @classmethod
    def _clean(cls, v: list[str]) -> list[str]:
        return sorted({a.strip().lower() for a in v if a.strip()})


@router.get("/units", response_model=list[UnitIn], summary="Справочник единиц измерения")
async def list_units(session: Session) -> list[UnitIn]:
    rows = (
        await session.execute(select(m.Unit).order_by(m.Unit.dimension, m.Unit.factor))
    ).scalars()
    return [UnitIn.model_validate(r, from_attributes=True) for r in rows]


@router.put("/units/{code}", response_model=UnitIn, summary="Создать или изменить единицу")
async def save_unit(code: str, body: UnitIn, session: Session) -> UnitIn:
    if body.code != code:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "Код в адресе и в теле не совпадает"
        )
    row = await session.get(m.Unit, code)
    if row is None:
        row = m.Unit(code=code)
        session.add(row)
    for field, value in body.model_dump().items():
        setattr(row, field, value)
    await session.commit()
    return body


@router.delete("/units/{code}", status_code=204, summary="Удалить единицу")
async def delete_unit(code: str, session: Session) -> Response:
    row = await session.get(m.Unit, code)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Единица не найдена")
    await session.delete(row)
    await session.commit()
    return Response(status_code=204)


# ---------------------------------------------------------------- синонимы


class SynonymIn(BaseModel):
    words: list[str] = Field(min_length=2, description="Первое слово — каноническое")

    @field_validator("words")
    @classmethod
    def _clean(cls, v: list[str]) -> list[str]:
        out: list[str] = []
        for w in v:
            w = " ".join(w.split()).lower()
            if w and w not in out:
                out.append(w)
        if len(out) < 2:
            raise ValueError("нужно не меньше двух разных слов")
        return out


class SynonymOut(SynonymIn):
    id: int


@router.get("/synonyms", response_model=list[SynonymOut], summary="Справочник синонимов")
async def list_synonyms(session: Session) -> list[SynonymOut]:
    rows = (await session.execute(select(m.SynonymGroup).order_by(m.SynonymGroup.id))).scalars()
    return [SynonymOut(id=r.id, words=list(r.words)) for r in rows]


@router.post("/synonyms", response_model=SynonymOut, status_code=201, summary="Добавить группу")
async def create_synonym(body: SynonymIn, session: Session) -> SynonymOut:
    row = m.SynonymGroup(words=body.words)
    session.add(row)
    await session.commit()
    return SynonymOut(id=row.id, words=row.words)


@router.put("/synonyms/{group_id}", response_model=SynonymOut, summary="Изменить группу")
async def update_synonym(group_id: int, body: SynonymIn, session: Session) -> SynonymOut:
    row = await session.get(m.SynonymGroup, group_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Группа не найдена")
    row.words = body.words
    await session.commit()
    return SynonymOut(id=row.id, words=row.words)


@router.delete("/synonyms/{group_id}", status_code=204, summary="Удалить группу")
async def delete_synonym(group_id: int, session: Session) -> Response:
    row = await session.get(m.SynonymGroup, group_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Группа не найдена")
    await session.delete(row)
    await session.commit()
    return Response(status_code=204)


# ---------------------------------------------------------------- индексы цен


class PriceIndexItem(BaseModel):
    period: date = Field(description="Месяц (первое число)")
    value: Decimal = Field(
        gt=50, lt=200, description="ИПЦ, % к предыдущему месяцу (100,5 = +0,5 %)"
    )
    note: str | None = Field(None, max_length=300)

    @field_validator("period")
    @classmethod
    def _month(cls, v: date) -> date:
        return v.replace(day=1)


@router.get(
    "/price-indexes", response_model=list[PriceIndexItem], summary="Индексы потребительских цен"
)
async def list_indexes(session: Session) -> list[PriceIndexItem]:
    rows = (await session.execute(select(m.PriceIndex).order_by(m.PriceIndex.period))).scalars()
    return [PriceIndexItem.model_validate(r, from_attributes=True) for r in rows]


@router.put(
    "/price-indexes",
    response_model=list[PriceIndexItem],
    summary="Заменить таблицу индексов цен целиком",
)
async def save_indexes(body: list[PriceIndexItem], session: Session) -> list[PriceIndexItem]:
    periods = [i.period for i in body]
    if len(set(periods)) != len(periods):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Месяцы повторяются")
    await session.execute(delete(m.PriceIndex))
    for item in body:
        session.add(m.PriceIndex(**item.model_dump()))
    await session.commit()
    return sorted(body, key=lambda i: i.period)
