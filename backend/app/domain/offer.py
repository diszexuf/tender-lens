"""Предложения (офферы), результаты сравнения и решения по ним."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, Field


class VatStatus(StrEnum):
    INCLUDED = "included"
    EXCLUDED = "excluded"
    NOT_APPLICABLE = "not_applicable"
    UNKNOWN = "unknown"


class ExtractionMethod(StrEnum):
    JSONLD = "jsonld"
    MICRODATA = "microdata"
    SELECTORS = "selectors"
    LLM = "llm"
    API = "api"


class RawOffer(BaseModel):
    """Предложение в том виде, в каком его вернул адаптер источника (FR-04.4)."""

    url: str
    title: str
    price: Decimal | None = None
    currency: str = "RUB"
    vat_status: VatStatus = VatStatus.UNKNOWN
    unit_raw: str | None = None
    pack_qty: Decimal | None = None
    attributes_raw: str = ""
    attributes: dict[str, str] = Field(default_factory=dict)
    supplier_name: str | None = None
    supplier_inn: str | None = None
    supplier_kind: str | None = None  # legal | ip
    price_date: date | None = None
    region: str | None = None
    external_id: str | None = None  # например, «реестровый номер контракта:позиция»
    extraction_method: ExtractionMethod
    snapshot_keys: list[str] = Field(default_factory=list)
    fetched_at: datetime
    flags: list[str] = Field(default_factory=list)

    def text_for_matching(self) -> str:
        attrs = " ".join(f"{k} {v}" for k, v in self.attributes.items())
        return f"{self.title} {attrs} {self.attributes_raw}".strip()


class MatchStatus(StrEnum):
    MATCH = "match"
    MISMATCH = "mismatch"
    UNKNOWN = "unknown"


class ExtractedValue(BaseModel):
    """Значение характеристики, извлечённое из текста оффера."""

    name: str
    number: Decimal | None = None
    min: Decimal | None = None
    max: Decimal | None = None
    text: str | None = None
    boolean: bool | None = None
    unit: str | None = None

    @property
    def is_empty(self) -> bool:
        return all(v is None for v in (self.number, self.min, self.max, self.text, self.boolean))


class AttributeMatch(BaseModel):
    name: str
    required: bool
    status: MatchStatus
    requirement: str
    actual: str | None = None
    explanation: str
