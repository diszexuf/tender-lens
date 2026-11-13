"""Готовность системы: /api/health как чёрный ящик (NFR-06.7)."""

from __future__ import annotations

import re
import time
from typing import Any

import httpx

EXPECTED_COMPONENTS = {"database", "redis", "worker", "storage", "llm", "search"}
STATUSES = {"ok", "degraded", "down"}


def test_liveness(client: httpx.Client) -> None:
    resp = client.get("/api/health/live")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_health_report_shape(client: httpx.Client) -> None:
    resp = client.get("/api/health")
    assert resp.status_code in (200, 503), resp.text
    assert resp.headers["content-type"].startswith("application/json")

    report = resp.json()
    assert report["status"] in STATUSES
    assert re.fullmatch(r"\d+\.\d+\.\d+", report["version"]), report["version"]

    names = [c["name"] for c in report["components"]]
    assert len(names) == len(set(names)), f"дубли компонентов: {names}"
    assert set(names) == EXPECTED_COMPONENTS

    for component in report["components"]:
        assert component["status"] in STATUSES
        assert isinstance(component["latency_ms"], int)
        assert component["latency_ms"] >= 0
        assert component["detail"] is None or isinstance(component["detail"], str)


def test_overall_status_is_consistent(client: httpx.Client) -> None:
    """Общий статус — худший из статусов компонентов; при down — HTTP 503."""
    resp = client.get("/api/health")
    report = resp.json()
    statuses = {c["status"] for c in report["components"]}
    expected = "down" if "down" in statuses else "degraded" if "degraded" in statuses else "ok"
    assert report["status"] == expected
    assert resp.status_code == (503 if expected == "down" else 200)


def test_all_components_ready(client: httpx.Client, allow_degraded: set[str]) -> None:
    """Штатное состояние: все зависимости готовы (кроме явно разрешённых)."""
    report = client.get("/api/health").json()
    problems = {
        c["name"]: f"{c['status']}: {c['detail']}"
        for c in report["components"]
        if c["status"] != "ok" and not (c["status"] == "degraded" and c["name"] in allow_degraded)
    }
    assert not problems, f"компоненты не готовы: {problems}"


def test_database_migrations_applied(client: httpx.Client) -> None:
    db = _component(client, "database")
    assert db["status"] == "ok", db["detail"]
    assert "миграция" in (db["detail"] or "")


def test_worker_heartbeat_is_fresh(client: httpx.Client) -> None:
    worker = _component(client, "worker")
    assert worker["status"] == "ok", worker["detail"]


def test_health_responds_quickly(client: httpx.Client) -> None:
    """Проверки идут параллельно с таймаутом 3 с — ответ не должен быть дольше."""
    started = time.perf_counter()
    client.get("/api/health")
    assert time.perf_counter() - started < 4.0


def _component(client: httpx.Client, name: str) -> dict[str, Any]:
    report = client.get("/api/health").json()
    return next(c for c in report["components"] if c["name"] == name)
