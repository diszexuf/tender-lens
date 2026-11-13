"""Общие фикстуры системных тестов.

Тесты не импортируют код приложения и обращаются к системе только по HTTP —
через тот же вход, что и пользователь (nginx → SPA и /api).

Переменные окружения:
    TL_BASE_URL         адрес системы (по умолчанию http://localhost:8080)
    TL_CONTRACT_PATH    путь к контракту OpenAPI (по умолчанию ../../contracts/openapi.json)
    TL_READY_TIMEOUT    сколько секунд ждать готовности системы (по умолчанию 120)
    TL_ALLOW_DEGRADED   компоненты, которым разрешён статус degraded, через запятую
                        (например, "llm" — пока модель скачивается)
"""

from __future__ import annotations

import json
import os
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest

BASE_URL = os.environ.get("TL_BASE_URL", "http://localhost:8080").rstrip("/")
_contract_env = os.environ.get("TL_CONTRACT_PATH")
CONTRACT_PATH = (
    Path(_contract_env)
    if _contract_env
    else Path(__file__).resolve().parents[2] / "contracts" / "openapi.json"
)
READY_TIMEOUT = float(os.environ.get("TL_READY_TIMEOUT", "120"))
ALLOW_DEGRADED = {
    name.strip() for name in os.environ.get("TL_ALLOW_DEGRADED", "").split(",") if name.strip()
}


@pytest.fixture(scope="session")
def client() -> Iterator[httpx.Client]:
    with httpx.Client(base_url=BASE_URL, timeout=10.0, follow_redirects=False) as c:
        _wait_until_ready(c)
        yield c


def _wait_until_ready(c: httpx.Client) -> None:
    """Ждёт, пока API ответит через nginx: стек мог только что стартовать."""
    deadline = time.monotonic() + READY_TIMEOUT
    last_error = "нет ответа"
    while time.monotonic() < deadline:
        try:
            resp = c.get("/api/health/live")
            if resp.status_code == 200:
                return
            last_error = f"HTTP {resp.status_code}"
        except httpx.HTTPError as exc:
            last_error = f"{type(exc).__name__}: {exc}"
        time.sleep(2)
    pytest.exit(f"Система по адресу {BASE_URL} не готова за {READY_TIMEOUT:.0f} с: {last_error}", 2)


@pytest.fixture(scope="session")
def contract() -> dict[str, Any]:
    if not CONTRACT_PATH.exists():
        pytest.fail(f"Не найден контракт OpenAPI: {CONTRACT_PATH}")
    return json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))


@pytest.fixture(scope="session")
def allow_degraded() -> set[str]:
    return ALLOW_DEGRADED
