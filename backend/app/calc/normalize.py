"""Приведение цены оффера к единице позиции (FR-07.1, FR-07.2, FR-07.6).

Каждое преобразование записывается шагом: что сделано, с каким коэффициентом,
цена до и после. Шаги выводятся в отчёте.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from app.calc.units import (
    DEFAULT_REGISTRY,
    Unit,
    UnitRegistry,
    pack_from_title,
    parse_sale_unit,
)
from app.domain.reasons import Flag, Reason

PIECE_DIMENSIONS = {"count", "sheet"}
PACK_DIMENSIONS = {"pack", "set"}


@dataclass(frozen=True)
class NormalizationStep:
    op: str
    factor: Decimal
    before: Decimal
    after: Decimal
    reason: str

    def as_dict(self) -> dict[str, str]:
        return {
            "op": self.op,
            "factor": str(self.factor),
            "before": str(self.before),
            "after": str(self.after),
            "reason": self.reason,
        }


@dataclass
class NormalizationResult:
    price: Decimal | None
    steps: list[NormalizationStep] = field(default_factory=list)
    reason: Reason | None = None
    detail: str | None = None
    flags: list[Flag] = field(default_factory=list)
    # Альтернативный делитель цены, если фасовка не из структурированных данных:
    # > 1 — цена взята как есть, но может быть за фасовку (фасовка в названии);
    # < 1 — цена поделена на фасовку по данным LLM, но может быть уже за единицу
    # (LLM читает то же название). Выбор делает этап оценки по ценам других предложений
    # (resolve_pack_ambiguity).
    pack_candidate: Decimal | None = None

    @property
    def ok(self) -> bool:
        return self.price is not None


def normalize_price(
    *,
    price: Decimal,
    offer_unit_raw: str | None,
    pack_qty: Decimal | None,
    position_okei: str | None,
    position_unit_raw: str | None,
    registry: UnitRegistry = DEFAULT_REGISTRY,
    title: str | None = None,
    hint_unit: str | None = None,
    hint_pack_qty: Decimal | None = None,
) -> NormalizationResult:
    """Цена за единицу позиции.

    Приоритет данных: единица и фасовка из структурированных данных источника →
    единица продажи с количеством от LLM («бухта 100 м») → фасовка из названия →
    количество в упаковке от LLM, если это число есть в названии → цена за единицу позиции.
    """
    res = _normalize(
        price=price, offer_unit_raw=offer_unit_raw, pack_qty=pack_qty,
        position_okei=position_okei, position_unit_raw=position_unit_raw, registry=registry,
        title=title, hint_unit=hint_unit, hint_pack_qty=hint_pack_qty,
    )  # fmt: skip
    # Позиция в пачках/упаковках, а товар продаётся набором: «Бумага … - 5 упак».
    # Единица из данных источника не указана — цена может быть и за пачку, и за набор.
    pos_unit = registry.by_code(position_okei) or registry.resolve(position_unit_raw)
    if (
        res.price is not None
        and res.pack_candidate is None
        and not offer_unit_raw
        and title
        and pos_unit is not None
        and pos_unit.dimension in PACK_DIMENSIONS
        and (qty := pack_from_title(title, pos_unit, registry)) is not None
    ):
        res.pack_candidate = qty
        res.flags.append(Flag.PACK_AMBIGUOUS)
    return res


def _normalize(
    *,
    price: Decimal,
    offer_unit_raw: str | None,
    pack_qty: Decimal | None,
    position_okei: str | None,
    position_unit_raw: str | None,
    registry: UnitRegistry = DEFAULT_REGISTRY,
    title: str | None = None,
    hint_unit: str | None = None,
    hint_pack_qty: Decimal | None = None,
) -> NormalizationResult:
    from_hint = False  # единица/фасовка взяты из ответа LLM, а не из данных источника
    if not offer_unit_raw and hint_unit and parse_sale_unit(hint_unit, registry):
        offer_unit_raw, from_hint = hint_unit, True
    if (
        not offer_unit_raw
        and not pack_qty
        and hint_pack_qty
        and hint_pack_qty > 1
        and title
        and f"{hint_pack_qty.normalize():f}" in title
        and hint_unit
        and (hu := registry.resolve(hint_unit)) is not None
        and hu.dimension in PACK_DIMENSIONS
    ):
        offer_unit_raw, pack_qty, from_hint = hint_unit, hint_pack_qty, True
    pos_unit = registry.by_code(position_okei) or registry.resolve(position_unit_raw)
    off_unit = registry.resolve(offer_unit_raw)
    flags: list[Flag] = []
    steps: list[NormalizationStep] = []

    if pos_unit is None:
        return NormalizationResult(
            price=price,
            flags=[Flag.UNIT_ASSUMED],
            steps=[
                _step(
                    "unit",
                    Decimal(1),
                    price,
                    price,
                    "единица позиции не распознана — цена без пересчёта",
                )
            ],
        )

    if off_unit is None and (parsed := parse_sale_unit(offer_unit_raw, registry)):
        # «бухта 100 м»: цена за фасовку из qty единиц
        inner_unit, qty = parsed
        per_inner = price / qty
        steps.append(
            _step(
                "pack",
                qty,
                price,
                per_inner,
                f"цена за «{offer_unit_raw}» / {qty} {inner_unit.short}",
            )
        )
        price, off_unit = per_inner, inner_unit
        if off_unit.code == pos_unit.code:
            return NormalizationResult(
                price=price, steps=steps, flags=flags, pack_candidate=_undo(qty, from_hint)
            )

    # Единица не указана, но фасовка есть в названии: «Кабель … 100м». Магазины пишут фасовку
    # в названии и при цене за бухту, и при цене за метр — делим, только если извлечение
    # подтвердило то же количество; иначе выбор делает этап оценки по ценам других предложений.
    title_qty = (
        pack_from_title(title, pos_unit, registry)
        if off_unit is None
        and not offer_unit_raw
        and title
        and pos_unit.dimension not in PACK_DIMENSIONS
        else None
    )
    if title_qty is not None and hint_pack_qty is not None and hint_pack_qty == title_qty:
        per_unit = price / title_qty
        steps.append(
            _step(
                "pack",
                title_qty,
                price,
                per_unit,
                f"фасовка {_fmt(title_qty)} {pos_unit.short} (в названии, подтверждена "
                f"извлечением): цена / {_fmt(title_qty)}",
            )
        )
        return NormalizationResult(
            price=per_unit, steps=steps, flags=flags, pack_candidate=_undo(title_qty, True)
        )

    if off_unit is None:
        if offer_unit_raw:
            return NormalizationResult(
                price=None,
                reason=Reason.UNIT_INCOMPATIBLE,
                detail=f"единица «{offer_unit_raw}» не распознана",
            )
        # Магазины обычно не пишут единицу: цена за штуку/единицу товара
        flags.append(Flag.UNIT_ASSUMED)
        off_unit = pos_unit
        steps.append(
            _step(
                "unit", Decimal(1), price, price, f"единица не указана — принята «{pos_unit.short}»"
            )
        )
        if title_qty is not None:
            flags.append(Flag.PACK_AMBIGUOUS)
            return NormalizationResult(
                price=price, steps=steps, flags=flags, pack_candidate=title_qty
            )

    # Цена за упаковку → цена за штуку
    if off_unit.dimension in PACK_DIMENSIONS and pos_unit.dimension in PIECE_DIMENSIONS:
        if not pack_qty or pack_qty <= 0:
            return NormalizationResult(
                price=None,
                reason=Reason.UNIT_INCOMPATIBLE,
                detail=f"цена за {off_unit.short}, количество в упаковке не указано",
            )
        per_piece = price / pack_qty
        steps.append(
            _step(
                "pack",
                pack_qty,
                price,
                per_piece,
                f"цена {off_unit.short} / {pack_qty} {pos_unit.short} в упаковке",
            )
        )
        return NormalizationResult(
            price=per_piece, steps=steps, flags=flags, pack_candidate=_undo(pack_qty, from_hint)
        )

    if off_unit.code == pos_unit.code:
        return NormalizationResult(price=price, steps=steps, flags=flags)

    # Позиция в пачках/рулонах, а цена «за штуку» (в контрактах ЕИС пачку часто считают штукой)
    if pos_unit.dimension in PACK_DIMENSIONS and off_unit.code == "796":
        flags.append(Flag.UNIT_ASSUMED)
        steps.append(
            _step(
                "unit",
                Decimal(1),
                price,
                price,
                f"«{off_unit.short}» принята за 1 {pos_unit.short}",
            )
        )
        return NormalizationResult(price=price, steps=steps, flags=flags)

    factor = registry.factor(off_unit, pos_unit)
    if factor is None or factor == 0:
        return NormalizationResult(
            price=None,
            reason=Reason.UNIT_INCOMPATIBLE,
            detail=f"«{off_unit.short}» нельзя привести к «{pos_unit.short}»",
            flags=flags,
        )
    converted = price / factor
    steps.append(
        _step(
            "unit",
            factor,
            price,
            converted,
            f"1 {off_unit.short} = {_fmt(factor)} {pos_unit.short}: цена / {_fmt(factor)}",
        )
    )
    return NormalizationResult(price=converted, steps=steps, flags=flags)


def _undo(qty: Decimal, from_hint: bool) -> Decimal | None:
    """Делитель, отменяющий деление на фасовку, которую назвала только LLM."""
    return Decimal(1) / qty if from_hint and qty > 1 else None


def _step(
    op: str, factor: Decimal, before: Decimal, after: Decimal, reason: str
) -> NormalizationStep:
    return NormalizationStep(op=op, factor=factor, before=before, after=after, reason=reason)


def _fmt(value: Decimal) -> str:
    return format(value.normalize(), "f")


def unit_label(unit: Unit | None) -> str:
    return unit.short if unit else "?"


def resolve_pack_ambiguity(
    prices: dict[str, Decimal], candidates: dict[str, Decimal]
) -> dict[str, tuple[Decimal, str]]:
    """Выбор для неоднозначной фасовки по ценам других предложений.

    prices — нормализованные цены «как есть» (за единицу), candidates — фасовка из названия для
    неоднозначных. Для каждого неоднозначного выбирается вариант (цена или цена / фасовка),
    ближе к медиане однозначных цен в логарифмической шкале. Нужно не меньше двух однозначных
    цен, иначе выбор не делается. Возвращает только предложения, где выбрано деление.
    """
    peers = sorted(p for oid, p in prices.items() if oid not in candidates and p > 0)
    if len(peers) < 2:
        return {}
    mid = len(peers) // 2
    median = peers[mid] if len(peers) % 2 else (peers[mid - 1] + peers[mid]) / 2
    chosen: dict[str, tuple[Decimal, str]] = {}
    for oid, qty in candidates.items():
        as_is = prices[oid]
        alt = as_is / qty
        if abs((alt / median).ln()) < abs((as_is / median).ln()):
            near = f"ближе к медиане других предложений ({median.quantize(Decimal('0.01'))})"
            if qty > 1:
                why = f"фасовка {_fmt(qty)} из названия: цена / {_fmt(qty)} — {near}"
            else:
                undo = _fmt((Decimal(1) / qty).quantize(Decimal("0.001")))
                why = f"цена уже за единицу, деление на фасовку {undo} отменено — {near}"
            chosen[oid] = (alt, why)
    return chosen
