"""Настройки правил и справочники (FR-12), состояние источников (FR-03.6)."""

from __future__ import annotations

import httpx


def test_rules_versioned(client: httpx.Client) -> None:
    current = client.get("/api/settings").json()
    assert current["rules"]["cv_threshold"]
    assert "cv_threshold" in current["rules_schema"]["properties"]
    # Те же правила — без новой версии
    same = client.put("/api/settings", json={"rules": current["rules"]}).json()
    assert same["id"] == current["id"]
    bad = client.put("/api/settings", json={"rules": {**current["rules"], "min_sample": 0}})
    assert bad.status_code == 422
    changed = dict(current["rules"], cv_threshold="34")
    new = client.put("/api/settings", json={"rules": changed, "comment": "системный тест"})
    assert new.status_code == 200, new.text
    assert new.json()["id"] > current["id"]
    restored = client.put("/api/settings", json={"rules": current["rules"], "comment": "возврат"})
    assert restored.json()["rules"]["cv_threshold"] == current["rules"]["cv_threshold"]
    versions = client.get("/api/settings/versions").json()
    assert versions[0]["id"] == restored.json()["id"]


def test_units_crud(client: httpx.Client) -> None:
    units = client.get("/api/units").json()
    assert any(u["code"] == "796" for u in units)
    unit = {
        "code": "TST1",
        "name": "тестовая",
        "short": "тст",
        "dimension": "count",
        "factor": "10",
        "aliases": ["ТСТ", "тест."],
    }
    saved = client.put("/api/units/TST1", json=unit)
    assert saved.status_code == 200, saved.text
    assert saved.json()["aliases"] == ["тест.", "тст"]
    assert client.put("/api/units/OTHER", json=unit).status_code == 422
    assert client.delete("/api/units/TST1").status_code == 204
    assert client.delete("/api/units/TST1").status_code == 404


def test_synonyms_crud(client: httpx.Client) -> None:
    created = client.post("/api/synonyms", json={"words": ["тестслово", "Тестслово2"]})
    assert created.status_code == 201, created.text
    group = created.json()
    assert group["words"] == ["тестслово", "тестслово2"]
    assert client.post("/api/synonyms", json={"words": ["одно", "Одно"]}).status_code == 422
    upd = client.put(f"/api/synonyms/{group['id']}", json={"words": ["тестслово", "тс3"]})
    assert upd.json()["words"] == ["тестслово", "тс3"]
    assert client.delete(f"/api/synonyms/{group['id']}").status_code == 204


def test_price_indexes_roundtrip(client: httpx.Client) -> None:
    items = client.get("/api/price-indexes").json()
    assert items, "таблица ИПЦ заполняется при первом запуске"
    assert all(i["period"].endswith("-01") for i in items)
    dup = client.put("/api/price-indexes", json=[items[0], items[0]])
    assert dup.status_code == 422
    saved = client.put("/api/price-indexes", json=items)
    assert saved.status_code == 200 and len(saved.json()) == len(items)


def test_sources_health(client: httpx.Client) -> None:
    sources = client.get("/api/sources").json()
    health = {h["source_id"]: h for h in client.get("/api/sources/health").json()}
    assert set(health) == {s["id"] for s in sources}
    for h in health.values():
        assert h["circuit_open"] in {True, False}
        assert h["error_share"] is None or 0 <= h["error_share"] <= 1
    assert client.post(f"/api/sources/{sources[0]['id']}/breaker/reset").status_code == 204
