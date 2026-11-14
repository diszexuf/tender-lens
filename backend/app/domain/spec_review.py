"""Проверка правдоподобия характеристик, извлечённых LLM из описания закупки.

Мелкая модель иногда приписывает число не той характеристике («Плотность ≥ 1600 листов»)
или делает обязательным размытое пожелание («средняя плотность»). Обязательная характеристика
исключает несовпадающие предложения, поэтому такая ошибка обнуляет выборку. Подозрительные
характеристики не удаляются (решение за пользователем), а переводятся в желательные
с предупреждением — они влияют только на релевантность.
"""

from __future__ import annotations

import re

from app.calc.units import DEFAULT_REGISTRY, UnitRegistry
from app.domain.spec import AttrType, SpecAttribute
from app.matching.text import norm

# Основа слова в названии характеристики → допустимые размерности единицы (app.calc.units)
_EXPECTED_DIMENSIONS: tuple[tuple[str, frozenset[str]], ...] = (
    ("плотн", frozenset({"density"})),
    ("мощн", frozenset({"power"})),
    ("напряж", frozenset({"voltage"})),
    ("масс", frozenset({"mass"})),
    ("вес", frozenset({"mass"})),
    ("длин", frozenset({"length"})),
    ("ширин", frozenset({"length"})),
    ("высот", frozenset({"length"})),
    ("глубин", frozenset({"length"})),
    ("толщин", frozenset({"length"})),
    ("диаметр", frozenset({"length"})),
    ("диагонал", frozenset({"inch", "length"})),
    ("объем", frozenset({"volume", "data", "charge"})),
    ("емкост", frozenset({"volume", "data", "charge"})),
    ("памят", frozenset({"data"})),
    ("накопител", frozenset({"data"})),
    ("частот", frozenset({"frequency"})),
    ("температур", frozenset({"temperature"})),
    ("площад", frozenset({"area"})),
    ("сила тока", frozenset({"current"})),
    ("световой поток", frozenset({"luminous"})),
    ("энерги", frozenset({"energy", "energy_kwh"})),
    ("белизн", frozenset({"percent"})),
)

# Оценочные слова без измеримого содержания
_VAGUE = re.compile(
    r"\b(средн|хорош|качествен|стандартн|обычн|оптимальн|надежн|достаточн|нормальн|"
    r"приемлем|современн|удобн|высок\w*\s+качеств)",
    re.I,
)
_MEASURABLE = re.compile(r"[0-9a-z]", re.I)


def _expected_dimensions(name: str) -> frozenset[str] | None:
    text = name.lower().replace("ё", "е")
    found: set[str] = set()
    for stem, dims in _EXPECTED_DIMENSIONS:
        if stem in text:
            found |= dims
    return frozenset(found) if found else None


def _value_text(attr: SpecAttribute) -> str | None:
    if attr.type == AttrType.TEXT:
        return attr.value_text
    if attr.type == AttrType.ENUM:
        return " ".join(attr.values)
    return None


def unit_mismatch(attr: SpecAttribute, units: UnitRegistry = DEFAULT_REGISTRY) -> str | None:
    """Единица явно не подходит к характеристике: «Плотность» в листах, «Мощность» в кг."""
    if attr.type not in (AttrType.NUMBER, AttrType.RANGE) or not attr.unit:
        return None
    expected = _expected_dimensions(attr.name)
    unit = units.resolve(attr.unit)
    if expected is None or unit is None or unit.dimension in expected:
        return None
    return f"единица «{attr.unit}» не подходит к характеристике"


def is_vague(attr: SpecAttribute) -> bool:
    """Значение — оценка без измеримого содержания («средняя плотность», «хорошее качество»)."""
    text = _value_text(attr)
    if not text:
        return False
    text = text.lower().replace("ё", "е")
    return bool(_VAGUE.search(text)) and not _MEASURABLE.search(text) and len(text.split()) <= 4


# Альтернативы, покрывающие все варианты: «оригинальный или совместимый» ничего не ограничивает
_EXHAUSTIVE = (
    frozenset({"оригинальный", "совместимый"}),
    frozenset({"оригинал", "аналог"}),
    frozenset({"новый", "восстановленный"}),
)


def is_unrestrictive(attr: SpecAttribute) -> bool:
    """Перечисление допускает все варианты признака — требование не сужает выборку."""
    if attr.type != AttrType.ENUM or len(attr.values) < 2:
        return False
    values = {v.lower().replace("ё", "е").strip() for v in attr.values}
    return any(pair <= values for pair in _EXHAUSTIVE)


def review_attributes(
    ordinal: int,
    attrs: list[SpecAttribute],
    units: UnitRegistry = DEFAULT_REGISTRY,
) -> tuple[list[SpecAttribute], list[str]]:
    """Возвращает характеристики (подозрительные — желательными) и предупреждения."""
    out: list[SpecAttribute] = []
    warnings: list[str] = []
    for attr in attrs:
        problem = unit_mismatch(attr, units)
        if problem is None and is_vague(attr):
            problem = "нечёткое требование без измеримого значения"
        if problem is None and is_unrestrictive(attr):
            problem = "допускаются все варианты — требование не сужает выборку"
        if problem and attr.required:
            attr = attr.model_copy(update={"required": False})
            warnings.append(
                f"Позиция {ordinal}, «{attr.name}»: {problem} — сделана желательной, проверьте"
            )
        out.append(attr)
    return out, warnings


_CODE = re.compile(r"[\w()/.-]+")
DESIGNATION = "Обозначение"


def codes_from_name(
    ordinal: int, name: str, attrs: list[SpecAttribute]
) -> tuple[list[SpecAttribute], list[str]]:
    """Код в наименовании позиции («Перфоратор SDS-plus», «Лампа E27»), которого нет среди
    требований, становится обязательной характеристикой «Обозначение»: иначе SDS-max и
    SDS-plus неотличимы — сравниваются только характеристики."""
    covered = norm(" ".join(
        " ".join([*a.values, a.value_text or "", a.name]) for a in attrs
    ))  # fmt: skip
    codes = [
        c.strip("()/.,")
        for c in _CODE.findall(name)
        if re.search(r"[A-Za-z]", c) and re.search(r"[\d-]", c) and len(c.strip("()/.,")) >= 3
    ]
    new = [c for c in codes if norm(c) not in covered]
    if not new:
        return attrs, []
    attr = SpecAttribute(name=DESIGNATION, type=AttrType.ENUM, values=new, required=True)
    return [*attrs, attr], [
        f"Позиция {ordinal}: «{', '.join(new)}» из наименования добавлено как обязательная характеристика"
    ]
