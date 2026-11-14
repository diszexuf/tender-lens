"""Правила расчёта — содержимое версии настроек (FR-12.1, FR-12.2)."""

from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel, Field

from app.calc.stats import CalcRules, OutlierMethod
from app.matching.scoring import ScoringRules


class Rules(BaseModel):
    # Расчёт и выбросы (FR-08)
    outlier_method: OutlierMethod = OutlierMethod.MAD
    mad_threshold: Decimal = Field(Decimal("3.5"), gt=0)
    iqr_k: Decimal = Field(Decimal("1.5"), gt=0)
    min_n_for_outliers: int = Field(5, ge=3)
    outlier_max_share: Decimal = Field(Decimal("0.25"), gt=0, le=1)
    cv_threshold: Decimal = Field(Decimal("33"), gt=0)
    min_sample: int = Field(3, ge=1)
    trimming_enabled: bool = False
    trimming_max_share: Decimal = Field(Decimal("0.2"), ge=0, le=Decimal("0.5"))
    # Сопоставимость (FR-06)
    top_k: int = Field(15, ge=1, le=100)
    relevance_threshold: Decimal = Field(Decimal("0.6"), ge=0, le=1)
    required_weight: Decimal = Field(Decimal(3), gt=0)
    desired_weight: Decimal = Field(Decimal(1), gt=0)
    llm_batch_size: int = Field(4, ge=1, le=10)
    # Приоритет источников: цены исполненных контрактов (ЕИС) — основные; коммерческие
    # предложения магазинов берутся, только если контрактных цен меньше min_sample
    contracts_first: bool = True
    contract_source_types: list[str] = Field(default_factory=lambda: ["eis"])
    # Сбор (FR-04)
    max_offers_per_source: int = Field(10, ge=1, le=50)
    source_timeout_s: int = Field(120, ge=10, le=600)
    # Нормализация (FR-07)
    max_price_age_days: dict[str, int] = Field(
        default_factory=lambda: {"eis": 3 * 365, "default": 183},
        description="Максимальный возраст цены по типу источника, дней",
    )

    def calc_rules(self) -> CalcRules:
        return CalcRules(
            outlier_method=self.outlier_method,
            mad_threshold=self.mad_threshold,
            iqr_k=self.iqr_k,
            min_n_for_outliers=self.min_n_for_outliers,
            outlier_max_share=self.outlier_max_share,
            cv_threshold=self.cv_threshold,
            min_sample=self.min_sample,
            trimming_enabled=self.trimming_enabled,
            trimming_max_share=self.trimming_max_share,
        )

    def scoring_rules(self) -> ScoringRules:
        return ScoringRules(
            required_weight=self.required_weight,
            desired_weight=self.desired_weight,
            relevance_threshold=self.relevance_threshold,
        )

    def max_age(self, source_type: str) -> int:
        return self.max_price_age_days.get(source_type, self.max_price_age_days.get("default", 183))
