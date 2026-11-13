"""Входная точка системы: nginx отдаёт SPA и проксирует /api."""

from __future__ import annotations

import re

import httpx
import pytest


def test_index_served(client: httpx.Client) -> None:
    resp = client.get("/")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/html")
    assert '<div id="app">' in resp.text


@pytest.mark.parametrize("path", ["/status", "/calculations/new", "/sources", "/no/such/page"])
def test_spa_deep_links_return_index(client: httpx.Client, path: str) -> None:
    """Клиентские маршруты при прямом заходе отдают index.html, а не 404."""
    resp = client.get(path)
    assert resp.status_code == 200
    assert '<div id="app">' in resp.text


def test_static_assets_cached(client: httpx.Client) -> None:
    index = client.get("/").text
    assets = re.findall(r'(?:src|href)="(/assets/[^"]+)"', index)
    assert assets, "в index.html нет ссылок на /assets"
    for asset in assets:
        resp = client.get(asset)
        assert resp.status_code == 200, asset
        assert "immutable" in resp.headers.get("cache-control", ""), asset


def test_missing_asset_is_404(client: httpx.Client) -> None:
    assert client.get("/assets/does-not-exist.js").status_code == 404


def test_unknown_api_route_is_json_404(client: httpx.Client) -> None:
    """Неизвестный путь под /api уходит в API (JSON 404), а не в SPA."""
    resp = client.get("/api/does-not-exist")
    assert resp.status_code == 404
    assert resp.headers["content-type"].startswith("application/json")
    assert resp.json() == {"detail": "Not Found"}


def test_wrong_method_is_405(client: httpx.Client) -> None:
    assert client.post("/api/health").status_code == 405


def test_swagger_ui_available(client: httpx.Client) -> None:
    resp = client.get("/api/docs")
    assert resp.status_code == 200
    assert "swagger" in resp.text.lower()
