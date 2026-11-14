"""Интерфейс адаптеров источников и реестр типов (FR-03.8, NFR-05.1).

Новый тип источника — новый класс с декоратором @register_adapter; ядро пайплайна не меняется.
Настройки источника описываются Pydantic-моделью config_model: по её JSON Schema UI строит форму.
"""

from __future__ import annotations

import builtins
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, ClassVar

from pydantic import BaseModel

from app.domain.offer import RawOffer
from app.domain.spec import PositionSpec
from app.llm.provider import LLMProvider
from app.sources.fetcher import Fetcher, RequestLog


class SourceRole(StrEnum):
    PRICE = "price"
    REFERENCE = "reference"


class RunStatus(StrEnum):
    OK = "ok"
    EMPTY = "empty"
    ERROR = "error"
    TIMEOUT = "timeout"
    STRUCTURE_CHANGED = "structure_changed"
    BLOCKED = "blocked"  # robots.txt запрещает
    SKIPPED = "skipped"
    CIRCUIT_OPEN = "circuit_open"  # источник временно отключён после серии ошибок


class SourceQuery(BaseModel):
    position: PositionSpec
    query: str
    max_offers: int = 20
    region: str | None = None


@dataclass
class AdapterContext:
    fetcher: Fetcher
    llm: LLMProvider | None
    use_cache: bool = True
    requests: list[RequestLog] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


@dataclass
class AdapterResult:
    status: RunStatus
    offers: list[RawOffer] = field(default_factory=list)
    error: str | None = None


class SourceAdapter(ABC):
    type: ClassVar[str]
    title: ClassVar[str]
    description: ClassVar[str]
    config_model: ClassVar[builtins.type[BaseModel]]

    def __init__(self, config: dict[str, Any]) -> None:
        self.config = self.config_model.model_validate(config)

    @abstractmethod
    async def search(self, query: SourceQuery, ctx: AdapterContext) -> AdapterResult: ...


ADAPTERS: dict[str, builtins.type[SourceAdapter]] = {}


def register_adapter[A: builtins.type[SourceAdapter]](cls: A) -> A:
    ADAPTERS[cls.type] = cls
    return cls


def get_adapter(type_: str, config: dict[str, Any]) -> SourceAdapter:
    try:
        cls = ADAPTERS[type_]
    except KeyError as exc:
        raise ValueError(f"неизвестный тип источника: {type_}") from exc
    return cls(config)


def adapter_types() -> list[dict[str, Any]]:
    """Описание типов для UI: название, описание и JSON Schema настроек."""
    return [
        {
            "type": cls.type,
            "title": cls.title,
            "description": cls.description,
            "config_schema": cls.config_model.model_json_schema(),
        }
        for cls in ADAPTERS.values()
    ]
