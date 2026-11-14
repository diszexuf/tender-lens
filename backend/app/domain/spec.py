"""Спецификация закупки: позиции и типизированные характеристики (FR-02.2)."""

from __future__ import annotations

from decimal import Decimal
from enum import StrEnum
from typing import Self

from pydantic import BaseModel, Field, model_validator


class AttrType(StrEnum):
    NUMBER = "number"
    RANGE = "range"
    ENUM = "enum"
    BOOL = "bool"
    TEXT = "text"


class Operator(StrEnum):
    EQ = "eq"  # =
    GTE = "gte"  # ≥
    LTE = "lte"  # ≤
    IN = "in"  # ∈ (одно из значений)
    BETWEEN = "between"  # в диапазоне [min; max]


OPERATOR_SYMBOLS = {
    Operator.EQ: "=",
    Operator.GTE: "≥",
    Operator.LTE: "≤",
    Operator.IN: "∈",
    Operator.BETWEEN: "∈",
}


class SpecAttribute(BaseModel):
    """Требование к характеристике позиции.

    Значение хранится в поле, соответствующем типу:
    number — value_number; range/between — value_min/value_max;
    enum — values; bool — value_bool; text — value_text.
    """

    name: str = Field(min_length=1, max_length=200)
    type: AttrType
    operator: Operator = Operator.EQ
    value_number: Decimal | None = None
    value_min: Decimal | None = None
    value_max: Decimal | None = None
    values: list[str] = Field(default_factory=list)
    value_bool: bool | None = None
    value_text: str | None = None
    unit: str | None = Field(default=None, max_length=50)
    required: bool = True

    @model_validator(mode="after")
    def _check_value(self) -> Self:
        match self.type:
            case AttrType.NUMBER:
                if self.value_number is None:
                    raise ValueError(f"{self.name}: для типа number нужно value_number")
                if self.operator not in (Operator.EQ, Operator.GTE, Operator.LTE):
                    raise ValueError(f"{self.name}: для number допустимы операторы eq/gte/lte")
            case AttrType.RANGE:
                if self.value_min is None and self.value_max is None:
                    raise ValueError(f"{self.name}: для range нужна хотя бы одна граница")
                self.operator = Operator.BETWEEN
            case AttrType.ENUM:
                self.values = [v.strip() for v in self.values if v and v.strip()]
                if not self.values:
                    raise ValueError(f"{self.name}: для enum нужно хотя бы одно значение")
                self.operator = Operator.IN if len(self.values) > 1 else Operator.EQ
            case AttrType.BOOL:
                if self.value_bool is None:
                    raise ValueError(f"{self.name}: для bool нужно value_bool")
                self.operator = Operator.EQ
            case AttrType.TEXT:
                if not (self.value_text and self.value_text.strip()):
                    raise ValueError(f"{self.name}: для text нужно value_text")
                self.operator = Operator.EQ
        return self

    def describe(self) -> str:
        """Человекочитаемое требование: «ресурс ≥ 1600 стр.»."""
        unit = f" {self.unit}" if self.unit else ""
        match self.type:
            case AttrType.NUMBER:
                return f"{self.name} {OPERATOR_SYMBOLS[self.operator]} {self.value_number}{unit}"
            case AttrType.RANGE:
                lo = self.value_min if self.value_min is not None else "…"
                hi = self.value_max if self.value_max is not None else "…"
                return f"{self.name} ∈ [{lo}; {hi}]{unit}"
            case AttrType.ENUM:
                if len(self.values) == 1:
                    return f"{self.name} = {self.values[0]}"
                return f"{self.name} ∈ {{{', '.join(self.values)}}}"
            case AttrType.BOOL:
                return f"{self.name}: {'да' if self.value_bool else 'нет'}"
            case AttrType.TEXT:
                return f"{self.name}: {self.value_text}"


class PositionSpec(BaseModel):
    ordinal: int = Field(ge=1)
    name: str = Field(min_length=1, max_length=500)
    quantity: Decimal = Field(gt=0)
    unit_raw: str = Field(default="шт", max_length=50)
    okei_code: str | None = Field(default=None, max_length=10)
    ktru_code: str | None = Field(default=None, max_length=40)
    ktru_confirmed: bool = False
    search_query: str = Field(default="", max_length=300)
    attributes: list[SpecAttribute] = Field(default_factory=list)

    @model_validator(mode="after")
    def _defaults(self) -> Self:
        if not self.search_query.strip():
            self.search_query = self.name
        return self

    def query_text(self) -> str:
        """Текст позиции для поиска и текстового сходства."""
        parts = [self.search_query or self.name]
        for attr in self.attributes:
            if attr.type == AttrType.NUMBER and attr.value_number is not None:
                parts.append(f"{attr.value_number} {attr.unit or ''}".strip())
            elif attr.type == AttrType.ENUM and len(attr.values) == 1:
                parts.append(attr.values[0])
        return " ".join(parts)


class ProcurementSpec(BaseModel):
    positions: list[PositionSpec] = Field(min_length=1, max_length=50)
    region: str | None = None
