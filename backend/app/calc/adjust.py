"""Приведение цены к сопоставимым условиям: НДС (FR-07.3) и индексация (FR-07.5).

Как и нормализация единиц, каждая поправка возвращается шагом для отчёта.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from app.calc.normalize import NormalizationStep

HUNDRED = Decimal(100)
MONTHS = (
    "январь", "февраль", "март", "апрель", "май", "июнь",
    "июль", "август", "сентябрь", "октябрь", "ноябрь", "декабрь",
)  # fmt: skip


def month_start(d: date) -> date:
    return d.replace(day=1)


def _month(d: date) -> str:
    return f"{MONTHS[d.month - 1]} {d.year}"


def vat_step(price: Decimal, vat_status: str, rate: Decimal) -> NormalizationStep | None:
    """Цена без НДС → цена с НДС по ставке из настроек; остальные статусы — без изменений."""
    if vat_status != "excluded" or rate <= 0:
        return None
    factor = 1 + rate / HUNDRED
    return NormalizationStep(
        op="vat",
        factor=factor,
        before=price,
        after=price * factor,
        reason=f"цена без НДС: × {factor.normalize():f} (НДС {rate.normalize():f} %)",
    )


def index_factor(
    price_date: date, indexes: dict[date, Decimal]
) -> tuple[Decimal, date, date] | None:
    """Коэффициент от месяца цены до последнего месяца таблицы ИПЦ.

    indexes — ИПЦ месяца в % к предыдущему месяцу. Цена месяца M уже отражает уровень
    цен M, поэтому перемножаются индексы месяцев после M. None — индексировать нечем.
    """
    start = month_start(price_date)
    later = sorted(p for p in indexes if p > start)
    if not later:
        return None
    factor = Decimal(1)
    for p in later:
        factor *= indexes[p] / HUNDRED
    return factor, start, later[-1]


def index_step(
    price: Decimal, price_date: date | None, indexes: dict[date, Decimal]
) -> NormalizationStep | None:
    if price_date is None or not indexes:
        return None
    found = index_factor(price_date, indexes)
    if found is None:
        return None
    factor, start, end = found
    factor = factor.quantize(Decimal("0.000001"))
    if factor == 1:
        return None
    return NormalizationStep(
        op="index",
        factor=factor,
        before=price,
        after=price * factor,
        reason=(
            f"индексация по ИПЦ: цена за {_month(start)} → {_month(end)}, × {factor.normalize():f}"
        ),
    )


# Стартовая таблица ИПЦ (FR-07.5): годовой ИПЦ Росстата «декабрь к декабрю», разнесённый
# поровну по месяцам. Это приближение — помесячные значения уточняются в «Настройках».
ANNUAL_CPI: dict[int, Decimal] = {
    2023: Decimal("107.42"),
    2024: Decimal("109.52"),
    2025: Decimal("105.59"),
}
SEED_NOTE = "годовой ИПЦ Росстата (дек./дек.), поровну по месяцам — проверьте и уточните"


def default_price_indexes() -> dict[date, Decimal]:
    out: dict[date, Decimal] = {}
    for year, annual in ANNUAL_CPI.items():
        monthly = (annual / HUNDRED) ** (Decimal(1) / 12) * HUNDRED
        for month in range(1, 13):
            out[date(year, month, 1)] = monthly.quantize(Decimal("0.0001"))
    return out
