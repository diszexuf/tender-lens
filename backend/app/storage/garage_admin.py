"""Идемпотентная инициализация Garage через Admin API v2.

Шаги: назначить layout единственному узлу → импортировать ключ S3 из .env →
создать бакеты и выдать ключу права на них. Повторный запуск ничего не меняет.
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx

from app.core.config import Settings
from app.core.logging import get_logger

log = get_logger(__name__)


class GarageAdminError(RuntimeError):
    pass


class GarageAdmin:
    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None) -> None:
        self._settings = settings
        self._client = client or httpx.AsyncClient(
            base_url=settings.garage_admin_url,
            headers={"Authorization": f"Bearer {settings.garage_admin_token.get_secret_value()}"},
            timeout=10.0,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _call(
        self,
        method: str,
        endpoint: str,
        *,
        params: dict[str, str] | None = None,
        json: Any = None,
        allow_404: bool = False,
    ) -> Any:
        resp = await self._client.request(method, f"/v2/{endpoint}", params=params, json=json)
        if allow_404 and resp.status_code in (400, 404):
            return None
        if resp.status_code >= 400:
            raise GarageAdminError(f"{endpoint}: HTTP {resp.status_code} {resp.text[:500]}")
        return resp.json() if resp.content else None

    async def wait_ready(self, max_wait_seconds: float = 60.0) -> None:
        try:
            async with asyncio.timeout(max_wait_seconds):
                while True:
                    try:
                        resp = await self._client.get("/health")
                        # 200 — кластер готов; 503 — запущен, но layout ещё не назначен
                        if resp.status_code in (200, 503):
                            return
                    except httpx.HTTPError:
                        pass
                    await asyncio.sleep(1)
        except TimeoutError as exc:
            raise GarageAdminError("Garage admin API не отвечает") from exc

    async def ensure_layout(self) -> None:
        status = await self._call("GET", "GetClusterStatus")
        nodes = status.get("nodes", [])
        if not nodes:
            raise GarageAdminError("В кластере Garage нет узлов")
        if all(node.get("role") for node in nodes):
            log.info("garage.layout.exists", version=status.get("layoutVersion"))
            return

        capacity = self._settings.garage_capacity_gb * 1024**3
        roles = [
            {"id": node["id"], "zone": "dc1", "capacity": capacity, "tags": ["tender-lens"]}
            for node in nodes
            if not node.get("role")
        ]
        await self._call("POST", "UpdateClusterLayout", json={"roles": roles})
        layout = await self._call("GET", "GetClusterLayout")
        new_version = int(layout["version"]) + 1
        await self._call("POST", "ApplyClusterLayout", json={"version": new_version})
        log.info("garage.layout.applied", version=new_version, nodes=len(roles))

    async def ensure_key(self) -> None:
        key_id = self._settings.s3_access_key_id
        existing = await self._call("GET", "GetKeyInfo", params={"id": key_id}, allow_404=True)
        if existing:
            log.info("garage.key.exists", key_id=key_id)
            return
        await self._call(
            "POST",
            "ImportKey",
            json={
                "accessKeyId": key_id,
                "secretAccessKey": self._settings.s3_secret_access_key.get_secret_value(),
                "name": "tender-lens",
            },
        )
        log.info("garage.key.imported", key_id=key_id)

    async def ensure_bucket(self, alias: str) -> None:
        bucket = await self._call(
            "GET", "GetBucketInfo", params={"globalAlias": alias}, allow_404=True
        )
        if bucket is None:
            bucket = await self._call("POST", "CreateBucket", json={"globalAlias": alias})
            log.info("garage.bucket.created", bucket=alias)
        await self._call(
            "POST",
            "AllowBucketKey",
            json={
                "bucketId": bucket["id"],
                "accessKeyId": self._settings.s3_access_key_id,
                "permissions": {"read": True, "write": True, "owner": True},
            },
        )

    async def bootstrap(self) -> None:
        await self.wait_ready()
        await self.ensure_layout()
        await self.ensure_key()
        for alias in (self._settings.s3_bucket_snapshots, self._settings.s3_bucket_reports):
            await self.ensure_bucket(alias)
        log.info("garage.bootstrap.done")
