"""Релевантность оффера по результатам сравнения характеристик (FR-06.5, FR-06.6)."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from app.domain.offer import AttributeMatch, MatchStatus
from app.domain.reasons import Reason


@dataclass(frozen=True)
class ScoringRules:
    required_weight: Decimal = Decimal(3)
    desired_weight: Decimal = Decimal(1)
    unknown_share: Decimal = Decimal("0.5")
    relevance_threshold: Decimal = Decimal("0.6")


@dataclass(frozen=True)
class Verdict:
    relevance: Decimal
    included: bool
    reason: Reason | None
    explanation: str


def score(matches: list[AttributeMatch], rules: ScoringRules) -> Verdict:
    mismatched_required = [m for m in matches if m.required and m.status == MatchStatus.MISMATCH]

    total = earned = Decimal(0)
    for m in matches:
        weight = rules.required_weight if m.required else rules.desired_weight
        total += weight
        if m.status == MatchStatus.MATCH:
            earned += weight
        elif m.status == MatchStatus.UNKNOWN:
            earned += weight * rules.unknown_share
    relevance = (earned / total).quantize(Decimal("0.01")) if total else Decimal(1)

    if mismatched_required:
        details = "; ".join(f"{m.requirement} — {m.explanation}" for m in mismatched_required)
        return Verdict(relevance, False, Reason.REQUIRED_ATTR_MISMATCH, details)

    if relevance < rules.relevance_threshold:
        unknown = [m.name for m in matches if m.status == MatchStatus.UNKNOWN]
        return Verdict(
            relevance,
            False,
            Reason.LOW_RELEVANCE,
            f"релевантность {relevance} ниже порога {rules.relevance_threshold}; "
            f"не подтверждены: {', '.join(unknown) or '—'}",
        )

    matched = sum(1 for m in matches if m.status == MatchStatus.MATCH)
    unknown_n = sum(1 for m in matches if m.status == MatchStatus.UNKNOWN)
    text = f"совпадает {matched} из {len(matches)} характеристик"
    if unknown_n:
        text += f", не указано в предложении: {unknown_n}"
    if not matches:
        text = "в спецификации нет характеристик — сопоставление по наименованию"
    return Verdict(relevance, True, None, text)
