"""Разбор требования из дословной цитаты описания закупки (FR-02.2).

LLM находит в тексте характеристику и возвращает цитату («ресурс не менее 1600 страниц»),
а тип, оператор, число, диапазон и единицу определяет этот модуль. Числа берутся из
текста как есть — модель не может их исказить.
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

from rapidfuzz import fuzz

from app.calc.units import DEFAULT_REGISTRY, UnitRegistry
from app.domain.spec import AttrType, Operator, SpecAttribute

_SP = "[   ]"
# Число, не приклеенное к буквам слева («А4», «E27», «P1102» — коды, а не числа)
_NUM = rf"(?<![\w.,])(\d{{1,3}}(?:{_SP}\d{{3}})+(?:[.,]\d+)?|\d+(?:[.,]\d+)?)(?![\d])"
_NUMBER = re.compile(_NUM)
_UNIT_AFTER = re.compile(rf"^{_SP}*([%\"″]|[a-zа-яё²³/.]+(?:{_SP}?[a-zа-яё²³/]+)?)", re.I)
_RANGE = re.compile(rf"(?:от{_SP}+)?{_NUM}{_SP}*(?:-|–|—|до){_SP}*{_NUM}", re.I)
_CROSS = re.compile(rf"{_NUM}{_SP}*[xх×*]{_SP}*(\d+(?:[.,]\d+)?)", re.I)  # «3х2,5»: жилы × сечение
_GTE = re.compile(
    r"(не\s+менее|не\s+ниже|не\s+меньше|минимум|от|≥|>=|(?<!не )более|свыше|(?<!не )выше)\s*$",
    re.I,
)
_LTE = re.compile(
    r"(не\s+более|не\s+выше|не\s+больше|максимум|до|≤|<=|(?<!не )менее|(?<!не )ниже)\s*$",
    re.I,
)
_BOOL_NO = re.compile(r"^\s*(без|отсутств|нет)\b", re.I)
_BOOL_YES = re.compile(r"^\s*(наличие|налич|с|есть|имеется|оснащ)\b", re.I)
_SPLIT = re.compile(r"\s+или\s+|,\s*|;\s*", re.I)
_DIGITS = re.compile(r"\d+")
TEXT_MIN_WORDS = 6  # длиннее — текстовое требование, а не перечисление


def clean_decimal(d: Decimal) -> Decimal:
    """1600 → 1600, а не 1.6E+3; 0,70 → 0.7."""
    return d.quantize(Decimal(1)) if d == d.to_integral_value() else d.normalize()


def to_decimal(raw: str) -> Decimal | None:
    try:
        d = Decimal(re.sub(_SP, "", raw).replace(",", "."))
    except InvalidOperation:
        return None
    return clean_decimal(d)


def locate(quote: str, text: str) -> str | None:
    """Находит цитату в тексте: дословно или ближайший фрагмент (модель могла поправить регистр,
    пробелы, ё). None — цитаты в тексте нет."""
    q = quote.strip().strip("«»\"'.")
    if not q:
        return None
    if q in text:
        return q
    norm_text = text.replace("ё", "е").replace("Ё", "Е")
    norm_q = q.replace("ё", "е").replace("Ё", "Е")
    idx = norm_text.lower().find(norm_q.lower())
    if idx >= 0:
        return text[idx : idx + len(q)]
    hit = fuzz.partial_ratio_alignment(norm_q.lower(), norm_text.lower())
    if hit is None or hit.score < 90:
        return None
    found = text[hit.dest_start : hit.dest_end]
    # Неточное совпадение допустимо в словах, но не в числах: «160 листов» ≠ «1600 листов»
    return found if _DIGITS.findall(found) == _DIGITS.findall(q) else None


def _unit_after(tail: str, units: UnitRegistry) -> str | None:
    m = _UNIT_AFTER.match(tail)
    if not m:
        return None
    raw = m.group(1).strip(" .")
    # «146% CIE», «80 г/м²», «1600 страниц» — берём то, что знает справочник, иначе первое слово
    for candidate in (raw, raw.split()[0] if raw.split() else raw):
        if units.resolve(candidate) is not None:
            return candidate
    return raw.split()[0] if raw.split() else None


def value_part(name: str, quote: str) -> str:
    """Цитата без названия характеристики: «цвет чёрный» → «чёрный»."""
    words = [w for w in re.split(r"\s+", name.strip()) if len(w) > 2]
    value = quote
    for w in words:
        value = re.sub(rf"\b{re.escape(w[: max(4, len(w) - 2)])}\w*\b", "", value, flags=re.I)
    value = re.sub(r"^[\s:—–-]+|[\s:.]+$", "", value)
    return value or quote


def parse_quote(
    name: str, quote: str, required: bool, units: UnitRegistry = DEFAULT_REGISTRY
) -> SpecAttribute:
    """Требование из цитаты. Бросает ValueError, если значение не выделить."""
    name = name.strip()[:200]
    q = re.sub(r"\s+", " ", quote).strip()

    if m := _RANGE.search(q):
        lo, hi = to_decimal(m.group(1)), to_decimal(m.group(2))
        if lo is not None and hi is not None and lo < hi:
            return SpecAttribute(
                name=name, type=AttrType.RANGE, value_min=lo, value_max=hi,
                unit=_unit_after(q[m.end():], units), required=required,
            )  # fmt: skip
    if m := _CROSS.search(q):
        first, second = to_decimal(m.group(1)), to_decimal(m.group(2))
        cores = "жил" in name.lower() and "сечен" not in name.lower()  # «Количество жил»
        value = first if cores else second
        unit = None if cores else _unit_after(q[m.end() :], units)
        if value is not None:
            return SpecAttribute(
                name=name, type=AttrType.NUMBER, value_number=value, unit=unit, required=required
            )
    if m := _NUMBER.search(q):
        value = to_decimal(m.group(1))
        if value is not None:
            before = q[: m.start()]
            operator = (
                Operator.GTE if _GTE.search(before)
                else Operator.LTE if _LTE.search(before)
                else Operator.EQ
            )  # fmt: skip
            return SpecAttribute(
                name=name, type=AttrType.NUMBER, operator=operator, value_number=value,
                unit=_unit_after(q[m.end():], units), required=required,
            )  # fmt: skip

    text_value = value_part(name, q)
    if _BOOL_NO.search(q):
        return SpecAttribute(name=name, type=AttrType.BOOL, value_bool=False, required=required)
    if _BOOL_YES.search(q) and len(text_value.split()) <= 3:
        return SpecAttribute(name=name, type=AttrType.BOOL, value_bool=True, required=required)
    if len(text_value.split()) >= TEXT_MIN_WORDS:
        return SpecAttribute(
            name=name, type=AttrType.TEXT, value_text=text_value, required=required
        )
    values = [v.strip(" .") for v in _SPLIT.split(text_value) if v.strip(" .")]
    return SpecAttribute(name=name, type=AttrType.ENUM, values=values, required=required)


_ANY_NUMBER = re.compile(rf"\d{{1,3}}(?:{_SP}\d{{3}})+(?:[.,]\d+)?|\d+(?:[.,]\d+)?")


def numbers_in(text: str) -> set[Decimal]:
    """Все числа текста, включая приклеенные к буквам («3х2,5», «500л», «28мм»)."""
    return {d for raw in _ANY_NUMBER.findall(text) if (d := to_decimal(raw)) is not None}


def parse_quantity(
    quote: str | None, units: UnitRegistry = DEFAULT_REGISTRY
) -> tuple[Decimal, str] | None:
    """«Количество — 500 м», «200 пачек», «12 месяцев» → (число, единица)."""
    if not quote:
        return None
    q = re.sub(r"\s+", " ", quote)
    for m in _NUMBER.finditer(q):
        value = to_decimal(m.group(1))
        unit = _unit_after(q[m.end() :], units)
        if value is not None and value > 0:
            return value, (unit or "шт")
    return None
