"""Сравнение региона поставки с регионом предложения (FR-06.7)."""

from __future__ import annotations

import re

from app.matching.text import norm

# Слова-типы и предлоги не различают регионы: «г. Москва» = «Москва»
_GENERIC = {
    "г", "город", "гор", "обл", "область", "край", "республика", "респ", "ао", "автономный",
    "округ", "федеральный", "и", "в", "по", "на", "рф", "россия", "российская", "федерация",
}  # fmt: skip
_WORD = re.compile(r"[a-zа-я0-9]+")


def _stems(text: str) -> set[str]:
    # 5 букв: «Москва» и «Московская» различаются (москв / моско), окончания — нет
    return {w[:5] for w in _WORD.findall(norm(text)) if w not in _GENERIC and len(w) >= 3}


def same_region(required: str, actual: str) -> bool | None:
    """True/False — совпадает ли регион; None — сравнить нельзя (пустые названия)."""
    want, got = _stems(required), _stems(actual)
    if not want or not got:
        return None
    return want <= got or got <= want
