"""События прогресса расчёта: Redis pub/sub + короткая история для поздних подписчиков (SSE)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from redis.asyncio import Redis

HISTORY_LIMIT = 300
HISTORY_TTL = 24 * 3600


def channel(calc_id: str) -> str:
    return f"tl:calc:{calc_id}:events"


def history_key(calc_id: str) -> str:
    return f"tl:calc:{calc_id}:history"


class Progress:
    def __init__(self, redis: Redis, calc_id: str) -> None:
        self.redis = redis
        self.calc_id = calc_id

    async def emit(self, event: str, **data: Any) -> None:
        payload = json.dumps(
            {"event": event, "at": datetime.now(UTC).isoformat(timespec="seconds"), **data},
            ensure_ascii=False,
            default=str,
        )
        key = history_key(self.calc_id)
        async with self.redis.pipeline(transaction=False) as pipe:
            pipe.rpush(key, payload)
            pipe.ltrim(key, -HISTORY_LIMIT, -1)
            pipe.expire(key, HISTORY_TTL)
            pipe.publish(channel(self.calc_id), payload)
            await pipe.execute()

    async def reset(self) -> None:
        await self.redis.delete(history_key(self.calc_id))
