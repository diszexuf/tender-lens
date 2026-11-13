from fastapi.testclient import TestClient

from app.main import app


def test_live() -> None:
    with TestClient(app) as client:
        resp = client.get("/api/health/live")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_openapi_available() -> None:
    with TestClient(app) as client:
        resp = client.get("/api/openapi.json")
    assert resp.status_code == 200
    assert "/api/health" in resp.json()["paths"]
