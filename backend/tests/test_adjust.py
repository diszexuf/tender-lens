"""НДС, индексация по ИПЦ, регион, circuit breaker."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from app.calc.adjust import default_price_indexes, index_factor, index_step, vat_step
from app.domain.spec import PositionSpec
from app.matching.region import same_region
from app.sources.base import RunStatus
from app.sources.breaker import CircuitBreaker, breaker_state


def test_vat_added_only_for_price_without_vat() -> None:
    step = vat_step(Decimal("100"), "excluded", Decimal("22"))
    assert step is not None
    assert step.after == Decimal("122.00")
    assert "НДС 22 %" in step.reason
    for status in ("included", "not_applicable", "unknown"):
        assert vat_step(Decimal("100"), status, Decimal("22")) is None
    assert vat_step(Decimal("100"), "excluded", Decimal(0)) is None


def test_index_multiplies_months_after_price_month() -> None:
    idx = {
        date(2025, 1, 1): Decimal("101"),
        date(2025, 2, 1): Decimal("102"),
        date(2025, 3, 1): Decimal("100.5"),
    }
    factor, start, end = index_factor(date(2025, 1, 20), idx) or (None, None, None)
    assert factor == Decimal("1.02") * Decimal("1.005")
    assert (start, end) == (date(2025, 1, 1), date(2025, 3, 1))
    # Цена последнего месяца таблицы и будущих — без индексации
    assert index_factor(date(2025, 3, 5), idx) is None
    step = index_step(Decimal(1000), date(2025, 2, 1), idx)
    assert step is not None and step.after == Decimal("1005.000")
    assert "февраль 2025 → март 2025" in step.reason
    assert index_step(Decimal(1000), None, idx) is None


def test_default_indexes_reproduce_annual_cpi() -> None:
    idx = default_price_indexes()
    assert len(idx) == 36
    year = Decimal(1)
    for month in range(1, 13):
        year *= idx[date(2024, month, 1)] / 100
    assert abs(year * 100 - Decimal("109.52")) < Decimal("0.01")


def test_same_region() -> None:
    assert same_region("г. Москва", "Москва")
    assert same_region("Москва", "город Москва и Московская область")
    assert not same_region("Москва", "Московская область")
    assert not same_region("Новосибирская область", "Республика Татарстан")
    assert same_region("Республика Татарстан", "Татарстан")
    assert same_region("г.", "Москва") is None


class FakeRedis:
    def __init__(self) -> None:
        self.data: dict[str, str] = {}
        self.ttls: dict[str, int] = {}

    async def ttl(self, key: str) -> int:
        return self.ttls.get(key, -2) if key in self.data else -2

    async def get(self, key: str) -> str | None:
        return self.data.get(key)

    async def set(self, key: str, value: str, ex: int | None = None) -> None:
        self.data[key] = value
        if ex:
            self.ttls[key] = ex

    async def delete(self, *keys: str) -> None:
        for k in keys:
            self.data.pop(k, None)
            self.ttls.pop(k, None)

    def pipeline(self, transaction: bool = True) -> FakePipe:
        return FakePipe(self)


class FakePipe:
    def __init__(self, redis: FakeRedis) -> None:
        self.redis = redis
        self.ops: list[tuple[str, str, int]] = []

    async def __aenter__(self) -> FakePipe:
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    def incr(self, key: str) -> None:
        self.ops.append(("incr", key, 0))

    def expire(self, key: str, ttl: int) -> None:
        self.ops.append(("expire", key, ttl))

    async def execute(self) -> list[int | bool]:
        out: list[int | bool] = []
        for op, key, ttl in self.ops:
            if op == "incr":
                value = int(self.redis.data.get(key, "0")) + 1
                self.redis.data[key] = str(value)
                out.append(value)
            else:
                self.redis.ttls[key] = ttl
                out.append(True)
        return out


async def test_breaker_opens_after_threshold_and_resets_on_success() -> None:
    redis = FakeRedis()
    cb = CircuitBreaker(redis, threshold=3, cooldown_s=600)  # type: ignore[arg-type]
    for _ in range(2):
        await cb.record("s1", RunStatus.TIMEOUT)
    assert await cb.is_open("s1") is None
    await cb.record("s1", RunStatus.ERROR)
    assert await cb.is_open("s1") == 600
    state = await breaker_state(redis, "s1")  # type: ignore[arg-type]
    assert state.open and state.failures == 3
    # Пропуски и robots.txt не считаются ни ошибкой, ни успехом
    await cb.record("s2", RunStatus.BLOCKED)
    assert (await breaker_state(redis, "s2")).failures == 0  # type: ignore[arg-type]
    await cb.record("s1", RunStatus.EMPTY)
    assert (await breaker_state(redis, "s1")).failures == 0  # type: ignore[arg-type]


def test_evaluate_normalize_applies_vat_then_index() -> None:
    from types import SimpleNamespace

    from app.calc.units import DEFAULT_REGISTRY
    from app.pipeline.evaluate_stage import Adjust, Draft, _normalize

    offer = SimpleNamespace(
        price=Decimal(1000), unit_raw="шт", pack_qty=None, title="Картридж", vat_status="excluded",
        price_date=date(2025, 1, 15), flags=[],
    )  # fmt: skip
    spec = PositionSpec(
        ordinal=1, name="Картридж", quantity=Decimal(1), unit_raw="шт", okei_code="796"
    )
    d = Draft(offer=offer, source_type="eis")  # type: ignore[arg-type]
    adjust = Adjust(vat_rate=Decimal(22), indexes={date(2025, 2, 1): Decimal(101)})
    _normalize(d, None, spec, DEFAULT_REGISTRY, adjust)
    assert d.normalized == Decimal("1232.2")
    assert [s["op"] for s in d.steps] == ["vat", "index"]
    assert {"vat_added", "price_indexed"} <= set(d.flags)


def test_region_check_flags_or_excludes() -> None:
    from types import SimpleNamespace

    from app.pipeline.evaluate_stage import Adjust, Draft, _region_ok

    offer = SimpleNamespace(region="Республика Татарстан", flags=[])
    soft = Draft(offer=offer, source_type="eis")  # type: ignore[arg-type]
    assert _region_ok(soft, Adjust(region="Москва"))
    assert "region_differs" in soft.flags
    hard = Draft(offer=offer, source_type="eis")  # type: ignore[arg-type]
    assert not _region_ok(hard, Adjust(region="Москва", region_required=True))
    assert hard.reason == "region_mismatch"
