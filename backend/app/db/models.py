"""Модели предметной области (docs/03-architecture.md, §5)."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    ARRAY,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

Money = Numeric(18, 4)


def new_id() -> uuid.UUID:
    return uuid.uuid7()


class _Id:
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=new_id)


class _Created:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


# ---------------------------------------------------------------- настройки


class SettingsVersion(Base):
    """Версия правил расчёта (FR-12.2). Изменение настроек создаёт новую версию."""

    __tablename__ = "settings_version"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    rules: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    comment: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class Unit(Base):
    """Справочник единиц измерения: ОКЕИ и единицы характеристик (FR-07.1, FR-12.3)."""

    __tablename__ = "unit"

    code: Mapped[str] = mapped_column(String(20), primary_key=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    short: Mapped[str] = mapped_column(String(20), nullable=False)
    dimension: Mapped[str] = mapped_column(String(30), nullable=False)
    factor: Mapped[Decimal] = mapped_column(Numeric(30, 10), nullable=False)
    aliases: Mapped[list[str]] = mapped_column(ARRAY(String), nullable=False, default=list)


class SynonymGroup(Base):
    """Группа синонимов атрибутов и значений (FR-12.4). Первое слово — каноническое."""

    __tablename__ = "synonym_group"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    words: Mapped[list[str]] = mapped_column(ARRAY(String), nullable=False)


# ---------------------------------------------------------------- источники


class Source(_Id, _Created, Base):
    __tablename__ = "source"

    type: Mapped[str] = mapped_column(String(30), nullable=False)
    role: Mapped[str] = mapped_column(String(20), nullable=False, default="price")
    name: Mapped[str] = mapped_column(String(200), nullable=False, unique=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=100)
    config: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    okpd2_prefixes: Mapped[list[str]] = mapped_column(ARRAY(String), nullable=False, default=list)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


# ---------------------------------------------------------------- расчёт


class Calculation(_Id, _Created, Base):
    __tablename__ = "calculation"

    input_text: Mapped[str] = mapped_column(Text, nullable=False)
    input_params: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    auto_run: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="draft", index=True)
    stage: Mapped[str | None] = mapped_column(String(50))
    error: Mapped[str | None] = mapped_column(Text)
    spec_extracted: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    spec_final: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    spec_edited: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    app_version: Mapped[str] = mapped_column(String(30), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    positions: Mapped[list[Position]] = relationship(
        back_populates="calculation", order_by="Position.ordinal", cascade="all, delete-orphan"
    )
    versions: Mapped[list[CalculationVersion]] = relationship(
        back_populates="calculation",
        order_by="CalculationVersion.version_no",
        cascade="all, delete-orphan",
    )


class Position(_Id, Base):
    __tablename__ = "position"
    __table_args__ = (UniqueConstraint("calculation_id", "ordinal"),)

    calculation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("calculation.id", ondelete="CASCADE"), nullable=False, index=True
    )
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    quantity: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    unit_raw: Mapped[str] = mapped_column(String(50), nullable=False)
    okei_code: Mapped[str | None] = mapped_column(String(20))
    ktru_code: Mapped[str | None] = mapped_column(String(40))
    ktru_confirmed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    search_query: Mapped[str] = mapped_column(Text, nullable=False)
    attributes: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    attrs_hash: Mapped[str] = mapped_column(String(64), nullable=False)

    calculation: Mapped[Calculation] = relationship(back_populates="positions")


class SourceRun(_Id, Base):
    """Обращение к одному источнику по одной позиции (FR-04.8)."""

    __tablename__ = "source_run"

    calculation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("calculation.id", ondelete="CASCADE"), nullable=False, index=True
    )
    position_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("position.id", ondelete="CASCADE"), nullable=False
    )
    source_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("source.id", ondelete="SET NULL")
    )
    source_name: Mapped[str] = mapped_column(String(200), nullable=False)
    source_type: Mapped[str] = mapped_column(String(30), nullable=False)
    source_config: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(String(30), nullable=False)
    error: Mapped[str | None] = mapped_column(Text)
    requests: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    offers_found: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    duration_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class Snapshot(_Id, Base):
    """Сохранённый ответ источника на HTTP-запрос (FR-04.5)."""

    __tablename__ = "snapshot"

    source_run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("source_run.id", ondelete="SET NULL"), index=True
    )
    url: Mapped[str] = mapped_column(Text, nullable=False)
    http_status: Mapped[int] = mapped_column(Integer, nullable=False)
    s3_key: Mapped[str] = mapped_column(Text, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    content_type: Mapped[str | None] = mapped_column(String(100))
    size: Mapped[int] = mapped_column(Integer, nullable=False)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class Offer(_Id, Base):
    __tablename__ = "offer"

    position_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("position.id", ondelete="CASCADE"), nullable=False, index=True
    )
    source_run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("source_run.id", ondelete="CASCADE"), nullable=False
    )
    url: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    price: Mapped[Decimal | None] = mapped_column(Money)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="RUB")
    vat_status: Mapped[str] = mapped_column(String(20), nullable=False, default="unknown")
    unit_raw: Mapped[str | None] = mapped_column(String(50))
    pack_qty: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    attributes_raw: Mapped[str] = mapped_column(Text, nullable=False, default="")
    attributes: Mapped[dict[str, str]] = mapped_column(JSONB, nullable=False, default=dict)
    supplier_name: Mapped[str | None] = mapped_column(Text)
    supplier_inn: Mapped[str | None] = mapped_column(String(12))
    supplier_kind: Mapped[str | None] = mapped_column(String(10))
    price_date: Mapped[date | None] = mapped_column(Date)
    region: Mapped[str | None] = mapped_column(Text)
    external_id: Mapped[str | None] = mapped_column(String(100))
    extraction_method: Mapped[str] = mapped_column(String(20), nullable=False)
    snapshot_ids: Mapped[list[uuid.UUID]] = mapped_column(
        ARRAY(UUID(as_uuid=True)), nullable=False, default=list
    )
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    flags: Mapped[list[str]] = mapped_column(ARRAY(String), nullable=False, default=list)
    source_name: Mapped[str] = mapped_column(String(200), nullable=False)


class OfferAttributes(_Id, _Created, Base):
    """Значения характеристик, извлечённые из оффера (кэш LLM, переживает версии)."""

    __tablename__ = "offer_attributes"
    __table_args__ = (UniqueConstraint("offer_id", "attrs_hash"),)

    offer_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("offer.id", ondelete="CASCADE"), nullable=False
    )
    attrs_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    values: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)
    llm_call_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("llm_call.id", ondelete="SET NULL")
    )


class CalculationVersion(_Id, _Created, Base):
    __tablename__ = "calculation_version"
    __table_args__ = (UniqueConstraint("calculation_id", "version_no"),)

    calculation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("calculation.id", ondelete="CASCADE"), nullable=False
    )
    settings_version_id: Mapped[int] = mapped_column(
        ForeignKey("settings_version.id"), nullable=False
    )
    version_no: Mapped[int] = mapped_column(Integer, nullable=False)
    trigger: Mapped[str] = mapped_column(String(20), nullable=False, default="initial")
    outcome: Mapped[str] = mapped_column(String(20), nullable=False)
    nmck_total: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    llm_model: Mapped[str | None] = mapped_column(String(100))
    prompt_versions: Mapped[dict[str, str]] = mapped_column(JSONB, nullable=False, default=dict)

    calculation: Mapped[Calculation] = relationship(back_populates="versions")


class OfferEvaluation(_Id, Base):
    """Оценка оффера в версии расчёта: сравнение, нормализация, решение."""

    __tablename__ = "offer_evaluation"
    __table_args__ = (UniqueConstraint("version_id", "offer_id"),)

    version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("calculation_version.id", ondelete="CASCADE"), nullable=False, index=True
    )
    offer_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("offer.id", ondelete="CASCADE"), nullable=False
    )
    text_score: Mapped[Decimal | None] = mapped_column(Numeric(6, 2))
    attribute_matches: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list
    )
    relevance: Mapped[Decimal | None] = mapped_column(Numeric(4, 2))
    normalization_steps: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list
    )
    normalized_price: Mapped[Decimal | None] = mapped_column(Money)
    decision: Mapped[str] = mapped_column(String(20), nullable=False)
    reason_code: Mapped[str | None] = mapped_column(String(40))
    reason_text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    duplicate_of: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    is_manual: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    flags: Mapped[list[str]] = mapped_column(ARRAY(String), nullable=False, default=list)


class PositionResult(_Id, Base):
    __tablename__ = "position_result"
    __table_args__ = (UniqueConstraint("version_id", "position_id"),)

    version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("calculation_version.id", ondelete="CASCADE"), nullable=False
    )
    position_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("position.id", ondelete="CASCADE"), nullable=False
    )
    status: Mapped[str] = mapped_column(String(30), nullable=False)
    stats: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    filter_log: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    hints: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    flags: Mapped[list[str]] = mapped_column(ARRAY(String), nullable=False, default=list)
    outlier_bounds: Mapped[list[str] | None] = mapped_column(JSONB)
    unit_price: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    total_price: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))


class ManualDecision(_Id, _Created, Base):
    """Ручное включение/исключение оффера; действует во всех версиях расчёта (FR-06.9)."""

    __tablename__ = "manual_decision"
    __table_args__ = (UniqueConstraint("calculation_id", "offer_id"),)

    calculation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("calculation.id", ondelete="CASCADE"), nullable=False
    )
    offer_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("offer.id", ondelete="CASCADE"), nullable=False
    )
    decision: Mapped[str] = mapped_column(String(20), nullable=False)
    comment: Mapped[str] = mapped_column(Text, nullable=False)


class LLMCall(_Id, _Created, Base):
    """Журнал обращений к LLM: воспроизводимость по сохранённым ответам (NFR-03.2)."""

    __tablename__ = "llm_call"

    calculation_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("calculation.id", ondelete="CASCADE"), index=True
    )
    purpose: Mapped[str] = mapped_column(String(50), nullable=False)
    model: Mapped[str] = mapped_column(String(100), nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(50), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    request: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    response: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    error: Mapped[str | None] = mapped_column(Text)
    duration_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    tokens_in: Mapped[int | None] = mapped_column(Integer)
    tokens_out: Mapped[int | None] = mapped_column(Integer)
