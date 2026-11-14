"""Статистика выборки, выбросы и расчёт цены позиции (FR-08).

Только чистый Python и Decimal: расчёт детерминирован и воспроизводится вручную в XLSX.
σ — выборочное стандартное отклонение (n − 1), как в методических рекомендациях № 567.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal, localcontext
from enum import StrEnum

from app.domain.reasons import Flag, Reason

KOPECK = Decimal("0.01")


class OutlierMethod(StrEnum):
    MAD = "mad"
    IQR = "iqr"
    NONE = "none"


class PositionStatus(StrEnum):
    OK = "ok"
    OK_ADJUSTED = "ok_adjusted"
    HETEROGENEOUS = "heterogeneous"
    INSUFFICIENT_DATA = "insufficient_data"


@dataclass(frozen=True)
class CalcRules:
    """Параметры расчёта (часть версии настроек)."""

    outlier_method: OutlierMethod = OutlierMethod.MAD
    mad_threshold: Decimal = Decimal("3.5")
    iqr_k: Decimal = Decimal("1.5")
    min_n_for_outliers: int = 5
    # Если правило хочет исключить большую долю — это не выбросы, а неоднородная выборка
    outlier_max_share: Decimal = Decimal("0.25")
    cv_threshold: Decimal = Decimal("33")
    min_sample: int = 3
    trimming_enabled: bool = False
    trimming_max_share: Decimal = Decimal("0.2")


@dataclass(frozen=True)
class Stats:
    n: int
    min: Decimal
    max: Decimal
    mean: Decimal
    median: Decimal
    stdev: Decimal | None  # None при n < 2
    cv: Decimal | None  # %, None при n < 2 или среднем 0

    def as_dict(self) -> dict[str, str | int | None]:
        return {
            "n": self.n,
            "min": str(self.min),
            "max": str(self.max),
            "mean": str(_q(self.mean)),
            "median": str(_q(self.median)),
            "stdev": str(_q(self.stdev)) if self.stdev is not None else None,
            "cv": str(self.cv.quantize(KOPECK)) if self.cv is not None else None,
        }


@dataclass
class PriceItem:
    """Цена из выборки с идентификатором оффера."""

    offer_id: str
    price: Decimal


@dataclass
class Exclusion:
    offer_id: str
    reason: Reason
    detail: str


@dataclass
class PositionCalculation:
    status: PositionStatus
    included: list[PriceItem]
    excluded: list[Exclusion]
    stats: Stats | None
    unit_price: Decimal | None
    total_price: Decimal | None
    flags: list[Flag] = field(default_factory=list)
    log: list[str] = field(default_factory=list)  # протокол шагов для отчёта
    hints: list[str] = field(default_factory=list)
    outlier_bounds: tuple[Decimal, Decimal] | None = None


def _q(value: Decimal) -> Decimal:
    return value.quantize(KOPECK, rounding=ROUND_HALF_UP)


def median(values: list[Decimal]) -> Decimal:
    s = sorted(values)
    n = len(s)
    mid = n // 2
    return s[mid] if n % 2 else (s[mid - 1] + s[mid]) / 2


def quantile(values: list[Decimal], q: Decimal) -> Decimal:
    """Квантиль с линейной интерполяцией (как QUARTILE.INC в Excel)."""
    s = sorted(values)
    pos = (len(s) - 1) * q
    lo = int(pos)
    hi = min(lo + 1, len(s) - 1)
    return s[lo] + (s[hi] - s[lo]) * (pos - lo)


def describe(values: list[Decimal]) -> Stats:
    if not values:
        raise ValueError("пустая выборка")
    with localcontext() as ctx:
        ctx.prec = 28
        n = len(values)
        mean = sum(values, Decimal(0)) / n
        stdev: Decimal | None = None
        cv: Decimal | None = None
        if n >= 2:
            variance = sum(((v - mean) ** 2 for v in values), Decimal(0)) / (n - 1)
            stdev = variance.sqrt()
            if mean != 0:
                cv = stdev / mean * 100
        return Stats(
            n=n,
            min=min(values),
            max=max(values),
            mean=mean,
            median=median(values),
            stdev=stdev,
            cv=cv,
        )


def find_outliers(
    items: list[PriceItem], rules: CalcRules
) -> tuple[list[PriceItem], list[Exclusion], tuple[Decimal, Decimal] | None]:
    """Возвращает (оставшиеся, исключённые, границы)."""
    prices = [i.price for i in items]
    if rules.outlier_method == OutlierMethod.NONE:
        return items, [], None

    if rules.outlier_method == OutlierMethod.IQR:
        q1, q3 = quantile(prices, Decimal("0.25")), quantile(prices, Decimal("0.75"))
        iqr = q3 - q1
        lo, hi = q1 - rules.iqr_k * iqr, q3 + rules.iqr_k * iqr
        keep = [i for i in items if lo <= i.price <= hi]
        out = [
            Exclusion(
                i.offer_id,
                Reason.OUTLIER_IQR,
                f"цена {_q(i.price)} вне границ [{_q(lo)}; {_q(hi)}] "
                f"(Q1 = {_q(q1)}, Q3 = {_q(q3)}, k = {rules.iqr_k})",
            )
            for i in items
            if not (lo <= i.price <= hi)
        ]
        return keep, out, (lo, hi)

    # MAD: модифицированный z-score |0,6745·(x − медиана) / MAD| > порога
    med = median(prices)
    mad = median([abs(p - med) for p in prices])
    if mad == 0:
        # Больше половины цен совпадают — MAD не определён, фильтр не применяется
        return items, [], None
    k = Decimal("0.6745")
    bound = rules.mad_threshold * mad / k
    lo, hi = med - bound, med + bound
    keep, out = [], []
    for item in items:
        z = abs(k * (item.price - med) / mad)
        if z > rules.mad_threshold:
            out.append(
                Exclusion(
                    item.offer_id,
                    Reason.OUTLIER_MAD,
                    f"цена {_q(item.price)}: |z| = {z.quantize(KOPECK)} > {rules.mad_threshold} "
                    f"(медиана {_q(med)}, MAD {_q(mad)})",
                )
            )
        else:
            keep.append(item)
    return keep, out, (lo, hi)


def calculate_position(
    items: list[PriceItem], quantity: Decimal, rules: CalcRules
) -> PositionCalculation:
    """Выбросы → CV → проверка минимального n → цена (FR-08.1 – FR-08.9)."""
    log: list[str] = []
    flags: list[Flag] = []
    excluded: list[Exclusion] = []
    bounds = None

    if len(items) < rules.min_sample:
        log.append(f"В выборке {len(items)} предложений при минимуме {rules.min_sample}.")
        return PositionCalculation(
            status=PositionStatus.INSUFFICIENT_DATA,
            included=items,
            excluded=[],
            stats=describe([i.price for i in items]) if items else None,
            unit_price=None,
            total_price=None,
            log=log,
            hints=[
                f"Найдено {len(items)} сопоставимых предложений из {rules.min_sample} "
                "необходимых. Запросите коммерческие предложения, ослабьте требования "
                "спецификации или подключите дополнительные источники."
            ],
        )

    current = list(items)
    if len(current) < rules.min_n_for_outliers:
        flags.append(Flag.OUTLIER_FILTER_SKIPPED_SMALL_N)
        log.append(
            f"Фильтр выбросов не применялся: n = {len(current)} < {rules.min_n_for_outliers}."
        )
    else:
        kept, outliers, bounds = find_outliers(current, rules)
        if rules.outlier_method == OutlierMethod.NONE:
            log.append("Фильтр выбросов отключён в настройках.")
        elif bounds is None:
            log.append("Фильтр выбросов не применён: MAD = 0 (большинство цен совпадает).")
        elif outliers and Decimal(len(outliers)) / len(current) > rules.outlier_max_share:
            log.append(
                f"Правило {rules.outlier_method.upper()} отметило {len(outliers)} из "
                f"{len(current)} цен (> {rules.outlier_max_share:.0%}) — это не выбросы, "
                "а неоднородность выборки; цены не исключались."
            )
            bounds = None
        else:
            current = kept
            excluded.extend(outliers)
            log.append(
                f"Правило {rules.outlier_method.upper()}: границы [{_q(bounds[0])}; "
                f"{_q(bounds[1])}], исключено {len(outliers)}."
            )

    if len(current) < rules.min_sample:
        log.append(f"После исключения выбросов осталось {len(current)} < {rules.min_sample}.")
        return PositionCalculation(
            status=PositionStatus.INSUFFICIENT_DATA,
            included=current,
            excluded=excluded,
            stats=describe([i.price for i in current]) if current else None,
            unit_price=None,
            total_price=None,
            flags=flags,
            log=log,
            outlier_bounds=bounds,
            hints=["После исключения выбросов в выборке недостаточно предложений."],
        )

    stats = describe([i.price for i in current])
    status = PositionStatus.OK
    hints: list[str] = []

    if stats.cv is not None and stats.cv > rules.cv_threshold:
        log.append(f"CV = {stats.cv.quantize(KOPECK)} % > порога {rules.cv_threshold} %.")
        if rules.trimming_enabled:
            current, trimmed, stats = _trim(current, rules, log)
            excluded.extend(trimmed)
            if stats.cv is not None and stats.cv <= rules.cv_threshold:
                status = PositionStatus.OK_ADJUSTED
            else:
                status = PositionStatus.HETEROGENEOUS
        else:
            status = PositionStatus.HETEROGENEOUS
        if status == PositionStatus.HETEROGENEOUS:
            hints.extend(cluster_hints([i.price for i in current]))
            hints.append(
                "Выборка неоднородна: уточните существенные характеристики в спецификации "
                "или исключите несопоставимые предложения вручную."
            )
    else:
        cv_text = f"{stats.cv.quantize(KOPECK)} %" if stats.cv is not None else "не определён"
        log.append(f"CV = {cv_text} ≤ {rules.cv_threshold} % — выборка однородна.")

    unit_price = total = None
    if status in (PositionStatus.OK, PositionStatus.OK_ADJUSTED):
        unit_price = _q(stats.mean)
        total = _q(unit_price * quantity)
        log.append(
            f"Цена за единицу = среднее {stats.n} цен = {unit_price}; "
            f"цена позиции = {unit_price} × {quantity} = {total}."
        )

    return PositionCalculation(
        status=status,
        included=current,
        excluded=excluded,
        stats=stats,
        unit_price=unit_price,
        total_price=total,
        flags=flags,
        log=log,
        hints=hints,
        outlier_bounds=bounds,
    )


def _trim(
    items: list[PriceItem], rules: CalcRules, log: list[str]
) -> tuple[list[PriceItem], list[Exclusion], Stats]:
    """Подрезка выборки (FR-08.5): не больше доли и не ниже минимального n."""
    max_remove = int(len(items) * rules.trimming_max_share)
    current = list(items)
    removed: list[Exclusion] = []
    stats = describe([i.price for i in current])
    step = 0
    while (
        stats.cv is not None
        and stats.cv > rules.cv_threshold
        and len(removed) < max_remove
        and len(current) - 1 >= rules.min_sample
    ):
        med = stats.median
        worst = max(current, key=lambda i: abs(i.price - med))
        current.remove(worst)
        step += 1
        removed.append(
            Exclusion(
                worst.offer_id,
                Reason.CV_TRIMMING,
                f"шаг {step}: цена {_q(worst.price)} дальше всех от медианы {_q(med)}",
            )
        )
        stats = describe([i.price for i in current])
        cv_text = f"{stats.cv.quantize(KOPECK)} %" if stats.cv is not None else "—"
        log.append(f"Подрезка, шаг {step}: исключена цена {_q(worst.price)}, CV = {cv_text}.")
    return current, removed, stats


def cluster_hints(prices: list[Decimal]) -> list[str]:
    """Ищет разрыв в отсортированных ценах, который делит выборку на группы."""
    if len(prices) < 4:
        return []
    s = sorted(prices)
    gaps = [(s[i + 1] / s[i] if s[i] > 0 else Decimal(0), i) for i in range(len(s) - 1)]
    ratio, idx = max(gaps)
    if ratio < 2 or idx == 0 or idx == len(s) - 2:
        return []
    low, high = s[: idx + 1], s[idx + 1 :]
    return [
        f"В выборке две группы цен: {_q(low[0])}–{_q(low[-1])} ({len(low)} шт.) и "
        f"{_q(high[0])}–{_q(high[-1])} ({len(high)} шт.). Вероятно, в выборку попали "
        "товары разного класса — уточните характеристики."
    ]
