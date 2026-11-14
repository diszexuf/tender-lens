"""API расчётов как чёрный ящик: создание, спецификация, запуск, отчёт, ошибки.

Быстрые проверки используют только разбор описания (LLM, ~10 с).
Полный расчёт с обращением к внешним источникам помечен `slow`:
    pytest -m slow            # только полный прогон
    pytest -m "not slow"      # без него
"""

from __future__ import annotations

import time
import uuid
from typing import Any

import httpx
import pytest

CABLE = (
    "Кабель силовой ВВГнг(А)-LS 3х2,5 мм², медный, количество жил 3, "
    "сечение жилы 2,5 мм². Количество — 500 м."
)
FINAL = {"completed", "failed", "cancelled", "interrupted"}


def wait_status(
    client: httpx.Client, calc_id: str, targets: set[str], timeout: float
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        detail = client.get(f"/api/calculations/{calc_id}").json()
        if detail["status"] in targets:
            return detail
        time.sleep(2)
    pytest.fail(f"расчёт {calc_id} не дошёл до {targets} за {timeout:.0f} с")


@pytest.fixture(scope="module")
def draft(client: httpx.Client) -> dict[str, Any]:
    """Расчёт с проверкой спецификации: останавливается в spec_ready."""
    resp = client.post("/api/calculations", json={"text": CABLE, "auto_run": False})
    assert resp.status_code == 201, resp.text
    return wait_status(client, resp.json()["id"], {"spec_ready", "failed"}, timeout=180)


# ---------------------------------------------------------------- валидация и ошибки


@pytest.mark.parametrize("text", ["", "коротко", "x" * 20_001])
def test_create_validates_text(client: httpx.Client, text: str) -> None:
    assert client.post("/api/calculations", json={"text": text}).status_code == 422


def test_unknown_calculation_is_404(client: httpx.Client) -> None:
    missing = uuid.uuid4()
    for path in ("", "/report"):
        resp = client.get(f"/api/calculations/{missing}{path}")
        assert resp.status_code == 404
        assert resp.json()["detail"] == "Расчёт не найден"


def test_invalid_id_is_422(client: httpx.Client) -> None:
    assert client.get("/api/calculations/not-a-uuid").status_code == 422


def test_unknown_snapshot_is_404(client: httpx.Client) -> None:
    assert client.get(f"/api/snapshots/{uuid.uuid4()}").status_code == 404


# ---------------------------------------------------------------- справочники


def test_sources_and_types(client: httpx.Client) -> None:
    types = {t["type"]: t for t in client.get("/api/source-types").json()}
    assert {"eis", "html", "searxng"} <= types.keys()
    for t in types.values():
        assert t["config_schema"]["type"] == "object"
    sources = client.get("/api/sources").json()
    assert sources, "нет источников по умолчанию"
    assert all(s["type"] in types for s in sources)


# ---------------------------------------------------------------- спецификация


def test_spec_extracted(draft: dict[str, Any]) -> None:
    assert draft["status"] == "spec_ready", draft["error"]
    assert draft["auto_run"] is False
    spec = draft["spec"]
    assert len(spec) == 1
    position = spec[0]
    assert float(position["quantity"]) == 500
    assert position["okei_code"] == "006"  # метр
    names = {a["name"].lower() for a in position["attributes"]}
    assert any("сечени" in n for n in names), names
    for attr in position["attributes"]:
        assert attr["type"] in {"number", "range", "enum", "bool", "text"}


def test_spec_update_validates(client: httpx.Client, draft: dict[str, Any]) -> None:
    bad = {"positions": [{"ordinal": 1, "name": "x", "quantity": 0}]}
    assert client.put(f"/api/calculations/{draft['id']}/spec", json=bad).status_code == 422
    bad_attr = {
        "positions": [
            {
                "ordinal": 1,
                "name": "Кабель",
                "quantity": 1,
                "attributes": [{"name": "сечение", "type": "number"}],  # нет значения
            }
        ]
    }
    resp = client.put(f"/api/calculations/{draft['id']}/spec", json=bad_attr)
    assert resp.status_code == 422


def test_spec_update_saves_edits(client: httpx.Client, draft: dict[str, Any]) -> None:
    spec = draft["spec"]
    spec[0]["search_query"] = "кабель ВВГнг(А)-LS 3х2,5"
    resp = client.put(f"/api/calculations/{draft['id']}/spec", json={"positions": spec})
    assert resp.status_code == 200, resp.text
    detail = resp.json()
    assert detail["spec"][0]["search_query"] == "кабель ВВГнг(А)-LS 3х2,5"
    assert detail["spec_edited"] is True
    assert detail["status"] == "spec_ready"


def test_recalculate_requires_completed(client: httpx.Client, draft: dict[str, Any]) -> None:
    assert client.post(f"/api/calculations/{draft['id']}/recalculate").status_code == 409


def test_cancel_requires_active(client: httpx.Client, draft: dict[str, Any]) -> None:
    assert client.post(f"/api/calculations/{draft['id']}/cancel").status_code == 409


def test_events_stream_returns_history(client: httpx.Client, draft: dict[str, Any]) -> None:
    with client.stream("GET", f"/api/calculations/{draft['id']}/events", timeout=5) as resp:
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/event-stream")
        first = next(line for line in resp.iter_lines() if line.startswith("data:"))
    assert '"event": "stage"' in first


def test_history_lists_and_searches(client: httpx.Client, draft: dict[str, Any]) -> None:
    page = client.get("/api/calculations", params={"limit": 5}).json()
    assert page["total"] >= 1 and len(page["items"]) <= 5
    found = client.get("/api/calculations", params={"q": "ВВГнг", "limit": 100}).json()
    assert any(item["id"] == draft["id"] for item in found["items"])


# ---------------------------------------------------------------- полный расчёт


@pytest.mark.slow
def test_full_run_produces_traceable_report(client: httpx.Client, draft: dict[str, Any]) -> None:
    resp = client.post(f"/api/calculations/{draft['id']}/run", json={"use_cache": True})
    assert resp.status_code == 202, resp.text
    assert client.post(f"/api/calculations/{draft['id']}/run").status_code == 409

    detail = wait_status(client, draft["id"], FINAL, timeout=900)
    assert detail["status"] == "completed", detail["error"]

    report = client.get(f"/api/calculations/{draft['id']}/report").json()
    version = report["version"]
    assert version["outcome"] in {"full", "partial", "none"}
    assert version["rules"]["cv_threshold"]
    position = report["positions"][0]
    assert position["sources"], "нет записей об обращении к источникам"
    result = position["result"]
    assert result["status"] in {"ok", "ok_adjusted", "heterogeneous", "insufficient_data"}
    assert result["filter_log"]

    for offer in position["offers"]:
        assert offer["url"].startswith("http")
        assert offer["decision"] in {"included", "excluded"}
        assert offer["reason_text"], f"нет объяснения решения: {offer['title']}"
        if offer["decision"] == "excluded":
            assert offer["reason_code"] in report["reasons"]

    included = [o for o in position["offers"] if o["decision"] == "included"]
    if result["status"] in {"ok", "ok_adjusted"}:
        # Цена воспроизводится по выборке: среднее нормализованных цен × количество
        prices = [float(o["normalized_price"]) for o in included]
        mean = sum(prices) / len(prices)
        assert abs(float(result["unit_price"]) - mean) < 0.01
        quantity = float(position["spec"]["quantity"])
        assert abs(float(result["total_price"]) - round(mean, 2) * quantity) < 0.01
    else:
        assert result["unit_price"] is None, "цена не должна формироваться без надёжных данных"

    # Каждая цена связана с источником: снапшот доступен
    with_snapshots = [o for o in position["offers"] if o["snapshot_ids"]]
    assert with_snapshots
    snap = client.get(f"/api/snapshots/{with_snapshots[0]['snapshot_ids'][0]}")
    assert snap.status_code == 200
    assert "sandbox" in snap.headers["content-security-policy"]

    # Перерасчёт на тех же данных — новая версия
    assert client.post(f"/api/calculations/{draft['id']}/recalculate").status_code == 202
    wait_status(client, draft["id"], FINAL, timeout=600)
    versions = client.get(f"/api/calculations/{draft['id']}").json()["versions"]
    assert [v["version_no"] for v in versions][-2:] == [1, 2]
