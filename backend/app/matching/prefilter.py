"""Текстовый предфильтр: в дорогую LLM-оценку идут только top-K офферов (FR-06.2)."""

from __future__ import annotations

import re
from dataclasses import dataclass

from rapidfuzz import fuzz

from app.domain.spec import PositionSpec
from app.matching.text import Synonyms, norm

_NUMBER = re.compile(r"\d+(?:[.,]\d+)?")
_MODEL_CODE = re.compile(r"\b[a-zа-я]{0,4}\d{2,}[a-zа-я0-9-]*\b", re.IGNORECASE)


@dataclass(frozen=True)
class Ranked:
    offer_id: str
    score: float  # 0–100


def similarity(position: PositionSpec, offer_text: str, synonyms: Synonyms) -> float:
    """Сходство позиции и оффера: токены, ключевые числа и артикулы."""
    query = norm(position.query_text())
    text = norm(offer_text)
    if not text:
        return 0.0
    base = max(
        fuzz.token_set_ratio(query, text), fuzz.partial_token_set_ratio(norm(position.name), text)
    )

    # Совпадение кодов моделей (CE285A, P1102) — сильный сигнал
    codes = {c.lower() for c in _MODEL_CODE.findall(query)}
    if codes:
        hit = sum(1 for c in codes if c in text)
        base = base * 0.7 + 30 * hit / len(codes)

    numbers = set(_NUMBER.findall(query))
    if numbers:
        text_numbers = set(_NUMBER.findall(text))
        base += 5 * len(numbers & text_numbers) / len(numbers)
    return min(base, 100.0)


_SERVICE_WORDS = ("оказан", "услуг", "выполнен", "работ", "поставк", "закупк")
_WORD = re.compile(r"[a-zа-я0-9]+")


def product_stem(position: PositionSpec) -> str | None:
    """Основа вида товара — первое слово наименования: «Ноутбук», «Скрепки», «Кабель».
    Для услуг и общих наименований («Поставка …») — None: проверка не применяется."""
    words = _WORD.findall(norm(position.name))
    if not words or not words[0].isalpha() or len(words[0]) < 4:
        return None
    if words[0].startswith(_SERVICE_WORDS):
        return None
    return words[0][: max(4, len(words[0]) - 2)]


def other_product(position: PositionSpec, title: str, text: str) -> str | None:
    """Предложение — другой вид товара: вида позиции нет в тексте вовсе («Модуль памяти»
    для ноутбука) или он упомянут как назначение («Сумка для ноутбука», «Стержень для ручки»).
    Возвращает пояснение или None."""
    stem = product_stem(position)
    if stem is None:
        return None
    title_n, text_n = norm(title), norm(text)
    if stem not in text_n:
        return f"в предложении нет «{position.name.split()[0]}»"
    words = _WORD.findall(title_n)
    first = next((i for i, w in enumerate(words) if w.startswith(stem)), None)
    if (
        first is not None
        and first > 0
        and {"для", "к", "под"} & set(words[max(0, first - 3) : first])
    ):
        return f"«{' '.join(words[: first + 1])}» — товар для {position.name.split()[0].lower()}, а не он сам"
    return None


def rank(
    position: PositionSpec, offers: dict[str, str], top_k: int, synonyms: Synonyms
) -> tuple[list[Ranked], list[Ranked]]:
    """Возвращает (top-K, остальные) по убыванию сходства."""
    ranked = sorted(
        (
            Ranked(oid, round(similarity(position, text, synonyms), 1))
            for oid, text in offers.items()
        ),
        key=lambda r: r.score,
        reverse=True,
    )
    return ranked[:top_k], ranked[top_k:]
