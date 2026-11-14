"""Схемы запросов и ответов API расчётов."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.domain.spec import PositionSpec

CalculationStatus = Literal[
    "draft",
    "extracting",
    "spec_ready",
    "collecting",
    "evaluating",
    "completed",
    "failed",
    "cancelled",
    "interrupted",
]


class CalculationParams(BaseModel):
    region: str | None = Field(None, max_length=200, description="Регион поставки")


class CalculationCreate(BaseModel):
    text: str = Field(min_length=10, max_length=20_000, description="Описание закупки")
    params: CalculationParams = Field(default_factory=CalculationParams)
    auto_run: bool = Field(
        True, description="Сразу запускать поиск после разбора (без шага подтверждения)"
    )


class SpecUpdate(BaseModel):
    positions: list[PositionSpec] = Field(min_length=1, max_length=50)


class RunRequest(BaseModel):
    use_cache: bool = Field(True, description="Использовать кэш ответов источников (24 ч)")


class VersionSummary(BaseModel):
    id: uuid.UUID
    version_no: int
    created_at: datetime
    trigger: str
    outcome: str
    nmck_total: Decimal | None


class CalculationSummary(BaseModel):
    id: uuid.UUID
    created_at: datetime
    title: str
    status: CalculationStatus
    positions: int
    nmck_total: Decimal | None
    outcome: str | None


class CalculationList(BaseModel):
    items: list[CalculationSummary]
    total: int


class CalculationDetail(BaseModel):
    id: uuid.UUID
    created_at: datetime
    updated_at: datetime
    status: CalculationStatus
    stage: str | None
    error: str | None
    input_text: str
    input_params: dict[str, Any]
    auto_run: bool
    spec: list[PositionSpec]
    spec_warnings: list[str]
    spec_edited: bool
    versions: list[VersionSummary]


# ---------------------------------------------------------------- отчёт


class SourceRunReport(BaseModel):
    source_name: str
    source_type: str
    status: str
    error: str | None
    offers_found: int
    duration_ms: int
    requests: int


class OfferReport(BaseModel):
    id: uuid.UUID
    source_name: str
    url: str
    title: str
    price: Decimal | None
    currency: str
    vat_status: str
    unit_raw: str | None
    pack_qty: Decimal | None
    supplier_name: str | None
    supplier_inn: str | None
    price_date: date | None
    fetched_at: datetime
    extraction_method: str
    snapshot_ids: list[uuid.UUID]
    decision: str
    reason_code: str | None
    reason_text: str
    relevance: Decimal | None
    text_score: Decimal | None
    attribute_matches: list[dict[str, Any]]
    normalization_steps: list[dict[str, Any]]
    normalized_price: Decimal | None
    flags: list[str]
    is_manual: bool
    manual_decision: str | None = Field(None, description="Ручное решение: included / excluded")
    manual_comment: str | None = None
    duplicate_of: uuid.UUID | None
    region: str | None = None


class PositionResultReport(BaseModel):
    status: str
    status_label: str
    unit_price: Decimal | None
    total_price: Decimal | None
    stats: dict[str, Any] | None
    filter_log: list[str]
    hints: list[str]
    flags: list[str]
    outlier_bounds: list[str] | None


class PositionReport(BaseModel):
    id: uuid.UUID
    spec: PositionSpec
    result: PositionResultReport | None
    sources: list[SourceRunReport]
    offers: list[OfferReport]


class VersionReport(VersionSummary):
    settings_version_id: int
    rules: dict[str, Any]
    llm_model: str | None
    prompt_versions: dict[str, str]


class Report(BaseModel):
    calculation: CalculationDetail
    version: VersionReport | None
    positions: list[PositionReport]
    reasons: dict[str, str]


# ---------------------------------------------------------------- ручные решения, версии


class ManualDecisionIn(BaseModel):
    decision: Literal["included", "excluded"]
    comment: str = Field(min_length=3, max_length=2000, description="Обоснование решения")


class PositionVersionResult(BaseModel):
    ordinal: int
    name: str
    status: str
    status_label: str
    n: int | None
    cv: Decimal | None
    unit_price: Decimal | None
    total_price: Decimal | None


class VersionComparison(VersionSummary):
    settings_version_id: int
    positions: list[PositionVersionResult]
    manual_decisions: int


# ---------------------------------------------------------------- audit trail


class LLMCallReport(BaseModel):
    id: uuid.UUID
    created_at: datetime
    purpose: str
    model: str
    prompt_version: str
    request_hash: str
    duration_ms: int
    tokens_in: int | None
    tokens_out: int | None
    error: str | None


class SourceRequestReport(BaseModel):
    position: int
    source_name: str
    status: str
    requests: list[dict[str, Any]]


class AuditTrail(BaseModel):
    report: Report
    source_requests: list[SourceRequestReport]
    llm_calls: list[LLMCallReport]
