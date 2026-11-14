"""HTTP-проверки реестра и адаптера HTML без сети, БД и очереди."""

from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI

from app.api import resources
from app.db.session import get_session
from app.services import sources
from app.sources.fetcher import FetchError, FetchErrorKind, Page

FIXTURES = Path(__file__).parent / "fixtures" / "html"
HTML = {"type": "html", "config": {"search_url": "https://www.etm.ru/catalog?q={query}"}}


@pytest.fixture
def session():
    db = AsyncMock()
    db.scalar.return_value = {}
    return db


@pytest.fixture
async def client(session):
    app = FastAPI()
    app.include_router(resources.router, prefix="/api")
    app.dependency_overrides[get_session] = lambda: session
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield client


async def test_probe_draft_cards(client, monkeypatch):
    listing = (FIXTURES / "etm_search.html").read_text(encoding="utf-8")
    card = (FIXTURES / "etm_card_meter.html").read_text(encoding="utf-8")

    async def get_page(self, url, **kwargs):
        html = listing if "/catalog?" in url else card
        return Page(
            url,
            url,
            200,
            "text/html",
            html,
            datetime.now(UTC),
            "abc",
            "key",
            len(html),
            False,
            False,
        )

    monkeypatch.setattr(sources.Fetcher, "get", get_page)
    monkeypatch.setattr(sources, "s3_client", MagicMock())
    response = await client.post("/api/sources/probe", json={**HTML, "query": "кабель"})
    assert response.status_code == 200
    result = response.json()
    assert result["status"] == "ok" and len(result["offers"]) == 5
    assert result["duration_ms"] >= 0
    assert all(
        o["extraction_method"] == "jsonld" and o["price"] == "181.58" for o in result["offers"]
    )


@pytest.mark.parametrize(
    ("kind", "status"), [(FetchErrorKind.ROBOTS, "blocked"), (FetchErrorKind.TIMEOUT, "timeout")]
)
async def test_probe_reports_fetch_failure(client, monkeypatch, kind, status):
    monkeypatch.setattr(sources, "s3_client", MagicMock())
    monkeypatch.setattr(sources.Fetcher, "get", AsyncMock(side_effect=FetchError(kind, "test")))
    result = (await client.post("/api/sources/probe", json={**HTML, "query": "кабель"})).json()
    assert result["status"] == status and result["error"] and not result["offers"]


@pytest.mark.parametrize(
    "config",
    [
        {},
        {"search_url": "https://example.org"},
        {**HTML["config"], "max_cards": 999},
        {**HTML["config"], "unknown": "x"},
    ],
)
async def test_config_errors(client, config):
    response = await client.post(
        "/api/sources", json={"name": "Магазин", "type": "html", "config": config}
    )
    assert response.status_code == 422
    assert "config." in response.json()["detail"][0]["msg"]


@pytest.mark.parametrize(
    "query", [" ", "user@example.org", "+7 (999) 123-45-67", "Иванов Иван Иванович"]
)
async def test_query_rejected_before_dispatch(client, query, session):
    assert (
        await client.post("/api/sources/probe", json={**HTML, "query": query})
    ).status_code == 422
    session.scalar.assert_not_awaited()


async def test_cannot_delete_referenced_source(client, session):
    session.scalar.return_value = uuid4()
    response = await client.delete(f"/api/sources/{uuid4()}")
    assert response.status_code == 409 and "отключить" in response.json()["detail"]
    session.delete.assert_not_awaited()


async def test_probe_saved_on_worker(client, session, monkeypatch):
    from app.worker import probe_with_browser

    config = {**HTML["config"], "card_render_js": True}
    session.get.return_value = MagicMock(type="html", config=config)
    session.scalar.return_value = {"source_timeout_s": 20}
    task = AsyncMock()
    task.wait_result.return_value = MagicMock(
        is_err=False, return_value={"status": "empty", "duration_ms": 12}
    )
    enqueue = AsyncMock(return_value=task)
    monkeypatch.setattr(probe_with_browser, "kiq", enqueue)
    response = await client.post(f"/api/sources/{uuid4()}/probe", json={"query": "кабель"})
    assert response.status_code == 200 and response.json()["status"] == "empty"
    enqueue.assert_awaited_once_with(
        "html", sources.AdapterInput(type="html", config=config).config, "кабель", 20
    )


async def test_reject_internal_source(client):
    response = await client.post(
        "/api/sources",
        json={
            "type": "html",
            "name": "test",
            "config": {"search_url": "http://127.0.0.1/search?q={query}"},
        },
    )
    assert response.status_code == 422 and "публичный" in response.json()["detail"]


async def test_reject_contact_in_search_suffix(client, session):
    response = await client.post(
        "/api/sources/probe",
        json={"type": "searxng", "config": {"query_suffix": "test@example.org"}, "query": "кабель"},
    )
    assert response.status_code == 422
    session.scalar.assert_not_awaited()
