"""Провайдеры LLM за единым интерфейсом (FR-11.1).

В MVP — только локальный Ollama. Внешний провайдер обязан идти через PIIGuard (FR-11.4).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from dataclasses import dataclass, field
from typing import Any, Protocol

import httpx
from pydantic import BaseModel, ValidationError

from app.core.config import Settings
from app.core.logging import get_logger
from app.llm.pii import PIIMasker
from app.llm.prompts import RenderedPrompt

log = get_logger(__name__)


class LLMError(RuntimeError):
    def __init__(self, message: str, raw: str | None = None) -> None:
        super().__init__(message)
        self.raw = raw


@dataclass
class LLMResult[T: BaseModel]:
    value: T
    raw: str
    model: str
    prompt_version: str
    request_hash: str
    duration_ms: int
    tokens_in: int | None = None
    tokens_out: int | None = None
    attempts: int = 1


@dataclass
class LLMCallRecord:
    """Запись для журнала llm_call (сохраняется пайплайном)."""

    purpose: str
    model: str
    prompt_version: str
    request_hash: str
    request: dict[str, Any]
    response: dict[str, Any] | None
    error: str | None
    duration_ms: int
    tokens_in: int | None
    tokens_out: int | None


class LLMProvider(Protocol):
    @property
    def name(self) -> str: ...
    @property
    def model(self) -> str: ...
    @property
    def is_external(self) -> bool: ...

    async def complete_json[T: BaseModel](
        self, prompt: RenderedPrompt, schema: type[T], *, json_schema: dict[str, Any] | None = None
    ) -> LLMResult[T]: ...


# Один GPU — одна генерация одновременно на процесс (NFR-01.3)
_gpu_semaphores: dict[int, asyncio.Semaphore] = {}


def _gpu_semaphore(limit: int) -> asyncio.Semaphore:
    if limit not in _gpu_semaphores:
        _gpu_semaphores[limit] = asyncio.Semaphore(limit)
    return _gpu_semaphores[limit]


@dataclass
class OllamaProvider:
    settings: Settings
    max_attempts: int = 3
    timeout_s: float = 300.0
    records: list[LLMCallRecord] = field(default_factory=list)
    name: str = "ollama"
    is_external: bool = False

    @property
    def model(self) -> str:
        return self.settings.ollama_model

    async def complete_json[T: BaseModel](
        self, prompt: RenderedPrompt, schema: type[T], *, json_schema: dict[str, Any] | None = None
    ) -> LLMResult[T]:
        fmt = json_schema or schema.model_json_schema()
        body: dict[str, Any] = {
            "model": self.model,
            "stream": False,
            "think": False,
            "format": fmt,
            "options": {
                "temperature": 0,
                "seed": 42,
                "num_ctx": self.settings.ollama_num_ctx,
                "num_predict": self.settings.ollama_num_predict,
            },
            "messages": [
                {"role": "system", "content": prompt.system},
                {"role": "user", "content": prompt.user},
            ],
        }
        request_hash = hashlib.sha256(
            json.dumps(body, ensure_ascii=False, sort_keys=True).encode()
        ).hexdigest()

        last_error = "нет ответа"
        raw = ""
        started = time.perf_counter()
        for attempt in range(1, self.max_attempts + 1):
            try:
                async with (
                    _gpu_semaphore(self.settings.llm_max_concurrency),
                    httpx.AsyncClient(timeout=self.timeout_s) as client,
                ):
                    resp = await client.post(f"{self.settings.ollama_url}/api/chat", json=body)
                resp.raise_for_status()
                data = resp.json()
                raw = data.get("message", {}).get("content", "")
                value = schema.model_validate_json(raw)
                duration = int((time.perf_counter() - started) * 1000)
                result = LLMResult(
                    value=value,
                    raw=raw,
                    model=self.model,
                    prompt_version=prompt.version,
                    request_hash=request_hash,
                    duration_ms=duration,
                    tokens_in=data.get("prompt_eval_count"),
                    tokens_out=data.get("eval_count"),
                    attempts=attempt,
                )
                self._record(prompt, body, request_hash, raw, None, result)
                return result
            except ValidationError as exc:
                last_error = f"ответ не соответствует схеме: {exc.errors()[:3]}"
            except (httpx.HTTPError, json.JSONDecodeError) as exc:
                last_error = f"{type(exc).__name__}: {exc}"
            log.warning(
                "llm.attempt_failed", purpose=prompt.name, attempt=attempt, error=last_error[:300]
            )
        self._record(prompt, body, request_hash, raw, last_error, None)
        raise LLMError(f"LLM ({prompt.name}): {last_error}", raw=raw)

    def _record(
        self,
        prompt: RenderedPrompt,
        body: dict[str, Any],
        request_hash: str,
        raw: str,
        error: str | None,
        result: LLMResult[Any] | None,
    ) -> None:
        try:
            response = json.loads(raw) if raw else None
        except json.JSONDecodeError:
            response = {"raw": raw[:5000]}
        self.records.append(
            LLMCallRecord(
                purpose=prompt.name,
                model=self.model,
                prompt_version=prompt.version,
                request_hash=request_hash,
                request={"system": prompt.system, "user": prompt.user, "options": body["options"]},
                response=response,
                error=error,
                duration_ms=result.duration_ms if result else 0,
                tokens_in=result.tokens_in if result else None,
                tokens_out=result.tokens_out if result else None,
            )
        )


@dataclass
class PIIGuard:
    """Обёртка для внешних провайдеров: маскирует ПДн до отправки (FR-11.4).

    В MVP внешних провайдеров нет; обёртка и самопроверка готовы к подключению.
    """

    inner: LLMProvider
    masker: PIIMasker = field(default_factory=PIIMasker)

    @property
    def name(self) -> str:
        return f"{self.inner.name}+pii"

    @property
    def model(self) -> str:
        return self.inner.model

    @property
    def is_external(self) -> bool:
        return self.inner.is_external

    async def complete_json[T: BaseModel](
        self, prompt: RenderedPrompt, schema: type[T], *, json_schema: dict[str, Any] | None = None
    ) -> LLMResult[T]:
        if not self.masker.self_check():
            raise LLMError("маскирование ПДн не прошло самопроверку — запрос заблокирован")
        masked = RenderedPrompt(
            name=prompt.name,
            version=prompt.version,
            system=self.masker.mask(prompt.system).text,
            user=self.masker.mask(prompt.user).text,
        )
        return await self.inner.complete_json(masked, schema, json_schema=json_schema)


def build_provider(settings: Settings) -> LLMProvider:
    provider: LLMProvider = OllamaProvider(settings)
    return PIIGuard(provider) if provider.is_external else provider
