"""Управление источниками через публичный HTTP API."""

from uuid import uuid4

import pytest


def test_crud_source(client):
    body = {
        "name": f"system-{uuid4()}",
        "type": "searxng",
        "role": "price",
        "enabled": False,
        "priority": 70,
        "config": {"max_cards": 2},
        "okpd2_prefixes": ["27.3"],
    }
    created = client.post("/api/sources", json=body)
    assert created.status_code == 201, created.text
    source = created.json()
    url = f"/api/sources/{source['id']}"
    try:
        assert source["config"]["max_cards"] == 2
        assert source["okpd2_prefixes"] == ["27.3"]
        assert client.get(url).json() == source
        assert any(row["id"] == source["id"] for row in client.get("/api/sources").json())
        assert client.post("/api/sources", json=body).status_code == 409
        updated = client.put(
            url, json={**body, "role": "reference", "priority": 30, "okpd2_prefixes": []}
        )
        assert updated.status_code == 200 and updated.json()["role"] == "reference"
        assert updated.json()["priority"] == 30 and updated.json()["okpd2_prefixes"] == []
        for enabled in (True, False):
            changed = client.patch(f"{url}/enabled", json={"enabled": enabled})
            assert changed.status_code == 200 and changed.json()["enabled"] is enabled
    finally:
        assert client.delete(url).status_code == 204
    assert client.get(url).status_code == 404


def test_reject_invalid_config(client):
    response = client.post(
        "/api/sources", json={"name": "system-invalid", "type": "html", "config": {}}
    )
    assert response.status_code == 422 and "search_url" in response.text


@pytest.mark.slow
def test_probe_html(client):
    response = client.post(
        "/api/sources/probe",
        json={
            "type": "html",
            "config": {"search_url": "https://www.etm.ru/catalog?searchValue={query}"},
            "query": "кабель ВВГнг",
        },
        timeout=140,
    )
    assert response.status_code == 200
    result = response.json()
    assert result["duration_ms"] >= 0 and len(result["offers"]) <= 5
    if result["status"] == "ok":
        assert result["offers"]
        for offer in result["offers"]:
            assert offer["title"] and offer["url"].startswith("https://")
            assert offer["extraction_method"] in {"jsonld", "microdata", "selectors", "llm"}
    else:
        assert result["status"] == "empty" or result["error"]
