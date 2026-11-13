from __future__ import annotations

import asyncio

from app.services.health import _Degraded, _timed


async def test_timed_ok() -> None:
    async def ok() -> str:
        return "fine"

    result = await _timed("x", ok)
    assert result.status == "ok"
    assert result.detail == "fine"


async def test_timed_degraded() -> None:
    async def degraded() -> str:
        raise _Degraded("модель не загружена")

    result = await _timed("x", degraded)
    assert result.status == "degraded"
    assert result.detail == "модель не загружена"


async def test_timed_down_on_error() -> None:
    async def broken() -> str:
        raise ConnectionError("refused")

    result = await _timed("x", broken)
    assert result.status == "down"
    assert "ConnectionError" in (result.detail or "")


async def test_timed_down_on_timeout(monkeypatch) -> None:
    monkeypatch.setattr("app.services.health.CHECK_TIMEOUT_SECONDS", 0.01)

    async def slow() -> str:
        await asyncio.sleep(1)
        return "late"

    result = await _timed("x", slow)
    assert result.status == "down"
    assert result.detail == "таймаут проверки"
