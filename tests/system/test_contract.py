"""Соответствие API зафиксированному контракту (contracts/openapi.json).

Контракт — источник типов фронтенда. Если тест падает, API изменился без обновления
контракта: выполните "npm run gen:api" в frontend/ и закоммитьте изменения.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

pytestmark = pytest.mark.contract

HINT = "контракт устарел: выполните 'npm run gen:api' в frontend/"


@pytest.fixture(scope="module")
def live_spec(client: httpx.Client) -> dict[str, Any]:
    resp = client.get("/api/openapi.json")
    assert resp.status_code == 200
    return resp.json()


def test_api_version_matches(live_spec: dict, contract: dict) -> None:
    assert live_spec["info"]["version"] == contract["info"]["version"], HINT


def test_same_endpoints(live_spec: dict, contract: dict) -> None:
    live = _operations(live_spec)
    expected = _operations(contract)
    assert live - expected == set(), f"в API есть операции не из контракта; {HINT}"
    assert expected - live == set(), f"операции из контракта пропали из API; {HINT}"


def test_same_schemas(live_spec: dict, contract: dict) -> None:
    live = live_spec.get("components", {}).get("schemas", {})
    expected = contract.get("components", {}).get("schemas", {})
    assert live.keys() == expected.keys(), HINT
    for name in expected:
        assert live[name] == expected[name], f"схема {name} отличается; {HINT}"


def test_spec_identical(live_spec: dict, contract: dict) -> None:
    """Итоговая проверка: спецификация совпадает целиком (описания, коды ответов и т. д.)."""
    assert live_spec == contract, HINT


def test_contract_endpoints_respond(client: httpx.Client, contract: dict) -> None:
    """Каждая GET-операция без параметров из контракта отвечает документированным кодом."""
    checked = 0
    for path, methods in contract["paths"].items():
        op = methods.get("get")
        if op is None or "{" in path or op.get("parameters"):
            continue
        resp = client.get(path)
        documented = {int(code) for code in op["responses"] if code.isdigit()}
        assert resp.status_code in documented, f"GET {path}: {resp.status_code} не в {documented}"
        checked += 1
    assert checked > 0


def _operations(spec: dict) -> set[tuple[str, str]]:
    return {
        (method.upper(), path)
        for path, methods in spec["paths"].items()
        for method in methods
        if method in {"get", "post", "put", "patch", "delete"}
    }
