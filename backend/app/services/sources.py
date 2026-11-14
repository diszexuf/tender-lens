"""Тестовый поиск по открытым источникам."""

import asyncio
import re
import time
from decimal import Decimal
from typing import Any
from urllib.parse import unquote

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator
from redis.asyncio import Redis

from app.core.config import get_settings
from app.domain.offer import ExtractionMethod
from app.domain.spec import PositionSpec
from app.llm.pii import PIIMasker
from app.llm.provider import OllamaProvider
from app.sources import eis_adapter, html_adapter, searxng_adapter  # noqa: F401
from app.sources.base import (
    ADAPTERS,
    AdapterContext,
    RunStatus,
    SourceQuery,
    SourceRole,
    get_adapter,
)
from app.sources.browser import BrowserPool
from app.sources.fetcher import Fetcher
from app.storage.s3 import s3_client

_CONTACT = re.compile(
    r"(?i)\b(?:фио|отчество)\b|\b(?:телефон|почта)\s*:"
    r"|\b[а-яё]+(?:ович|евич|овна|евна|ична|инична)\b"
    r"|\b[а-яё]+\s+[а-яё]\.\s*[а-яё]\.|\b[а-яё]\.\s*[а-яё]\.\s*[а-яё]+"
)


def _contains_contact(text: str) -> bool:
    return bool(PIIMasker().mask(text).replacements or _CONTACT.search(text))


class AdapterInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: str
    config: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_adapter(self):
        adapter = ADAPTERS.get(self.type)
        if adapter is None:
            raise ValueError("Неизвестный тип адаптера")
        # Настройки тоже попадают в исходящие запросы (URL-шаблон, суффикс поиска).
        if any(_contains_contact(unquote(str(value))) for value in self.config.values()):
            raise ValueError("Настройки источника содержат персональные данные")
        try:
            self.config = adapter.config_model.model_validate(
                self.config, extra="forbid"
            ).model_dump(mode="json")
        except ValidationError as exc:
            details = "; ".join(
                f"config.{'.'.join(map(str, issue['loc']))}: {issue['msg']}"
                for issue in exc.errors(include_input=False)
            )
            raise ValueError(details) from exc
        return self


class SourceInput(AdapterInput):
    name: str = Field(min_length=1, max_length=200)
    role: SourceRole = SourceRole.PRICE
    enabled: bool = True
    priority: int = Field(100, ge=0, le=2147483647)
    okpd2_prefixes: list[str] = Field(default_factory=list)

    @field_validator("name", mode="before")
    @classmethod
    def strip_name(cls, value: Any) -> Any:
        return value.strip() if isinstance(value, str) else value


class ProbeQuery(BaseModel):
    query: str = Field(min_length=1, max_length=300)

    @field_validator("query")
    @classmethod
    def product_query(cls, value: str) -> str:
        value = value.strip()
        if not value or _contains_contact(value):
            raise ValueError("Введите название товара без персональных данных")
        return value


class DraftProbe(AdapterInput, ProbeQuery):
    pass


class ProbeOffer(BaseModel):
    title: str
    price: Decimal | None
    currency: str
    unit_raw: str | None
    url: str
    extraction_method: ExtractionMethod


class ProbeResult(BaseModel):
    status: RunStatus
    duration_ms: int
    offers: list[ProbeOffer] = Field(default_factory=list)
    error: str | None = None
    warnings: list[str] = Field(default_factory=list)


async def probe(
    type_: str, config: dict, query: str, timeout_s: int, browser: BrowserPool | None = None
) -> ProbeResult:
    query = ProbeQuery(query=query).query
    validated = AdapterInput(type=type_, config=config)
    adapter = get_adapter(type_, validated.config)
    settings = get_settings()
    started = time.monotonic()
    result = ProbeResult(status=RunStatus.ERROR, duration_ms=0)
    try:
        async with (
            asyncio.timeout(timeout_s),
            Redis.from_url(settings.redis_url, decode_responses=True) as redis,
            s3_client(settings) as s3,
            Fetcher(settings, redis, s3, browser) as fetcher,
        ):
            ctx = AdapterContext(fetcher=fetcher, llm=OllamaProvider(settings), use_cache=False)
            response = await adapter.search(
                SourceQuery(
                    position=PositionSpec(ordinal=1, name=query, quantity=Decimal(1)),
                    query=query,
                    max_offers=5,
                ),
                ctx,
            )
            result.status = response.status
            result.error = response.error
            result.warnings = ctx.notes
            result.offers = [
                ProbeOffer.model_validate(o, from_attributes=True) for o in response.offers[:5]
            ]
    except TimeoutError:
        result.status = RunStatus.TIMEOUT
        result.error = "Истекло время проверки источника"
    except Exception:
        result.error = "Не удалось выполнить запрос к источнику"
    result.duration_ms = round((time.monotonic() - started) * 1000)
    return result
