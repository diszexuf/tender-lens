"""Circuit breaker источников в Redis (NFR-02.3, FR-03.6).

После N ошибок подряд источник пропускается на время паузы; первый успешный ответ
после паузы сбрасывает счётчик. Состояние общее для всех воркеров.
"""

from __future__ import annotations

from dataclasses import dataclass

from redis.asyncio import Redis

from app.sources.base import RunStatus

FAILURES = {RunStatus.ERROR, RunStatus.TIMEOUT, RunStatus.STRUCTURE_CHANGED}
SUCCESSES = {RunStatus.OK, RunStatus.EMPTY}
FAILS_TTL_S = 24 * 3600


def _fails(source_id: str) -> str:
    return f"tl:cb:{source_id}:fails"


def _open(source_id: str) -> str:
    return f"tl:cb:{source_id}:open"


@dataclass(frozen=True)
class BreakerState:
    failures: int
    open: bool
    retry_in_s: int | None


class CircuitBreaker:
    def __init__(self, redis: Redis, threshold: int, cooldown_s: int) -> None:
        self.redis = redis
        self.threshold = threshold
        self.cooldown_s = cooldown_s

    async def is_open(self, source_id: str) -> int | None:
        """Секунд до повторной попытки, если источник отключён; иначе None."""
        ttl = await self.redis.ttl(_open(source_id))
        return int(ttl) if ttl and ttl > 0 else None

    async def record(self, source_id: str, status: RunStatus) -> None:
        if status in SUCCESSES:
            await self.redis.delete(_fails(source_id))
        elif status in FAILURES:
            async with self.redis.pipeline(transaction=True) as pipe:
                pipe.incr(_fails(source_id))
                pipe.expire(_fails(source_id), FAILS_TTL_S)
                failures, _ = await pipe.execute()
            if int(failures) >= self.threshold:
                await self.redis.set(_open(source_id), str(failures), ex=self.cooldown_s)


async def breaker_state(redis: Redis, source_id: str) -> BreakerState:
    fails = await redis.get(_fails(source_id))
    ttl = await redis.ttl(_open(source_id))
    return BreakerState(
        failures=int(fails or 0),
        open=bool(ttl and ttl > 0),
        retry_in_s=int(ttl) if ttl and ttl > 0 else None,
    )


async def reset_breaker(redis: Redis, source_id: str) -> None:
    await redis.delete(_fails(source_id), _open(source_id))
