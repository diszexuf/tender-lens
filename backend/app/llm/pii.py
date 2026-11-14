"""Маскирование персональных данных перед отправкой во внешние сервисы (FR-11.2).

MVP: регулярные выражения. ФИО и адреса физлиц (NER) — вместе с внешним провайдером (FR-11.3).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# Порядок важен: более специфичные шаблоны раньше общих
PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("EMAIL", re.compile(r"\b[\w.+-]+@[\w-]+(?:\.[\w-]+)+\b")),
    ("CARD", re.compile(r"\b(?:\d{4}[ -]?){3}\d{4}\b")),
    ("ACCOUNT", re.compile(r"\b[34]\d{19}\b")),  # расчётный счёт: 20 цифр
    ("SNILS", re.compile(r"\b\d{3}-\d{3}-\d{3}[ -]\d{2}\b")),
    (
        "PASSPORT",
        re.compile(r"(?i)\bпаспорт\w*\s*(?:серии?\s*)?\d{2}\s?\d{2}\s*(?:№|номер)?\s*\d{6}\b"),
    ),
    ("INN", re.compile(r"(?i)\bИНН\s*:?\s*\d{12}\b")),  # 12 цифр — ИНН физлица/ИП
    (
        "PHONE",
        re.compile(r"(?<!\d)(?:\+7|8)[\s(-]*\d{3}[\s)-]*\d{3}[\s-]*\d{2}[\s-]*\d{2}(?!\d)"),
    ),
]


@dataclass
class MaskResult:
    text: str
    replacements: dict[str, str] = field(default_factory=dict)  # токен → исходное значение

    def unmask(self, text: str) -> str:
        for token, original in self.replacements.items():
            text = text.replace(token, original)
        return text


@dataclass
class PIIMasker:
    patterns: list[tuple[str, re.Pattern[str]]] = field(default_factory=lambda: list(PATTERNS))

    def mask(self, text: str) -> MaskResult:
        replacements: dict[str, str] = {}
        counters: dict[str, int] = {}
        seen: dict[str, str] = {}

        def make_sub(kind: str):  # type: ignore[no-untyped-def]
            def sub(match: re.Match[str]) -> str:
                original = match.group(0)
                if original in seen:
                    return seen[original]
                counters[kind] = counters.get(kind, 0) + 1
                token = f"[{kind}_{counters[kind]}]"
                seen[original] = token
                replacements[token] = original
                return token

            return sub

        for kind, pattern in self.patterns:
            text = pattern.sub(make_sub(kind), text)
        return MaskResult(text=text, replacements=replacements)

    def self_check(self) -> bool:
        """Контрольный пример: все типы данных должны маскироваться."""
        sample = (
            "Иванов, тел. +7 (912) 345-67-89, ivanov@example.ru, СНИЛС 112-233-445 95, "
            "паспорт 45 06 123456, ИНН 500100732259, карта 4276 1234 5678 9012"
        )
        masked = self.mask(sample).text
        return not any(
            leak in masked
            for leak in (
                "345-67-89",
                "@example.ru",
                "112-233-445",
                "123456",
                "500100732259",
                "4276",
            )
        )
