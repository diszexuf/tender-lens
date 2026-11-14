"""Правила расчёта — содержимое версии настроек (FR-12.1, FR-12.2).

Названия и описания полей показываются в форме настроек (UI строит её по JSON Schema).
"""

from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel, Field

from app.calc.stats import CalcRules, OutlierMethod
from app.matching.scoring import ScoringRules

DEFAULT_EXCLUDED_WORDS = (
    "сувенир",
    "подароч",
    "подарок",
    "имиджев",
    "vip",
    "с логотипом",
    "с гравировкой",
    "в футляре",
    "б/у",
    "уценк",
)


class Rules(BaseModel):
    # Расчёт и выбросы (FR-08)
    outlier_method: OutlierMethod = Field(
        OutlierMethod.MAD,
        title="Правило выбросов",
        description="mad — модифицированный z-score, iqr — межквартильный размах, none — без фильтра",
    )
    mad_threshold: Decimal = Field(Decimal("3.5"), gt=0, title="Порог MAD (|z|)")
    iqr_k: Decimal = Field(Decimal("1.5"), gt=0, title="Коэффициент k для IQR")
    min_n_for_outliers: int = Field(
        5,
        ge=3,
        title="Минимальный n для фильтра выбросов",
        description="При меньшей выборке фильтр не применяется",
    )
    outlier_max_share: Decimal = Field(
        Decimal("0.25"),
        gt=0,
        le=1,
        title="Предельная доля выбросов",
        description="Если правило отмечает больше — это неоднородность, цены не исключаются",
    )
    max_price_ratio: Decimal = Field(
        Decimal(4),
        ge=0,
        title="Допустимое отличие от медианы, раз",
        description="Цена, которая в столько раз выше или ниже медианы выборки, исключается "
        "при любом размере выборки (сувенирная ручка за 1880 ₽ среди ручек по 15 ₽). "
        "0 — не проверять",
    )
    cv_threshold: Decimal = Field(Decimal("33"), gt=0, title="Порог коэффициента вариации, %")
    min_sample: int = Field(3, ge=1, title="Минимальный размер выборки")
    trimming_enabled: bool = Field(
        False,
        title="Подрезка выборки",
        description="Исключать самые удалённые цены, пока CV выше порога",
    )
    trimming_max_share: Decimal = Field(
        Decimal("0.2"), ge=0, le=Decimal("0.5"), title="Предельная доля подрезки"
    )
    # Сопоставимость (FR-06)
    top_k: int = Field(
        15,
        ge=1,
        le=100,
        title="Top-K предфильтра",
        description="Сколько самых похожих предложений проверять по характеристикам",
    )
    relevance_threshold: Decimal = Field(Decimal("0.6"), ge=0, le=1, title="Порог релевантности")
    required_weight: Decimal = Field(Decimal(3), gt=0, title="Вес обязательной характеристики")
    desired_weight: Decimal = Field(Decimal(1), gt=0, title="Вес желательной характеристики")
    llm_batch_size: int = Field(
        4,
        ge=1,
        le=10,
        title="Размер пачки LLM",
        description="Предложений в одном вызове модели; меньше — надёжнее на CPU",
    )
    region_required: bool = Field(
        False,
        title="Регион обязателен",
        description="Предложения из другого региона исключаются; иначе — только пометка в отчёте",
    )
    # Приоритет источников: цены исполненных контрактов (ЕИС) — основные; коммерческие
    # предложения магазинов берутся, только если контрактных цен меньше min_sample
    contracts_first: bool = Field(
        True,
        title="Приоритет контрактов",
        description="Если цен из исполненных контрактов достаточно и они однородны, цены "
        "магазинов не используются; иначе в расчёт идут все сопоставимые цены",
    )
    excluded_words: list[str] = Field(
        default_factory=lambda: list(DEFAULT_EXCLUDED_WORDS),
        title="Слова-исключения в названии",
        description="Предложение исключается, если в названии есть слово, начинающееся так, "
        "а в наименовании позиции его нет. Свои слова для отдельной позиции задаются "
        "в спецификации",
    )
    contract_source_types: list[str] = Field(
        default_factory=lambda: ["eis"], title="Типы источников с ценами контрактов"
    )
    # Сбор (FR-04)
    max_offers_per_source: int = Field(10, ge=1, le=50, title="Предложений с источника на позицию")
    source_timeout_s: int = Field(120, ge=10, le=600, title="Таймаут источника, с")
    breaker_threshold: int = Field(
        3,
        ge=1,
        le=20,
        title="Ошибок подряд до отключения источника",
        description="Circuit breaker (NFR-02.3)",
    )
    breaker_cooldown_s: int = Field(
        900, ge=30, le=86400, title="Пауза после отключения источника, с"
    )
    # Нормализация (FR-07)
    vat_rate: Decimal = Field(
        Decimal(22),
        ge=0,
        le=100,
        title="Ставка НДС, %",
        description="Цены без НДС приводятся к цене с НДС",
    )
    indexation_enabled: bool = Field(
        True,
        title="Индексация по ИПЦ",
        description="Цены прошлых месяцев приводятся к текущему уровню",
    )
    max_price_age_days: dict[str, int] = Field(
        default_factory=lambda: {"eis": 3 * 365, "default": 183},
        title="Максимальный возраст цены, дней",
        description="По типу источника; default — для остальных",
    )

    def calc_rules(self) -> CalcRules:
        return CalcRules(
            outlier_method=self.outlier_method,
            mad_threshold=self.mad_threshold,
            iqr_k=self.iqr_k,
            min_n_for_outliers=self.min_n_for_outliers,
            outlier_max_share=self.outlier_max_share,
            cv_threshold=self.cv_threshold,
            max_price_ratio=self.max_price_ratio,
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
