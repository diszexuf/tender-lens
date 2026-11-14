"""Справочник причин исключения (docs/01-requirements.md, §4.13)."""

from __future__ import annotations

from enum import StrEnum


class Reason(StrEnum):
    DUPLICATE = "duplicate"
    SAME_SUPPLIER = "same_supplier"
    MISSING_PRICE = "missing_price"
    MISSING_UNIT_PRICE = "missing_unit_price"
    MISSING_DATA = "missing_data"
    CURRENCY_UNSUPPORTED = "currency_unsupported"
    STALE_PRICE = "stale_price"
    CONTRACT_PENALTIES = "contract_penalties"
    LOW_TEXT_SIMILARITY = "low_text_similarity"
    REQUIRED_ATTR_MISMATCH = "required_attr_mismatch"
    REGION_MISMATCH = "region_mismatch"
    LOW_RELEVANCE = "low_relevance"
    UNIT_INCOMPATIBLE = "unit_incompatible"
    OUTLIER_MAD = "outlier_mad"
    OUTLIER_IQR = "outlier_iqr"
    CV_TRIMMING = "cv_trimming"
    MANUAL = "manual"
    CONTRACTS_SUFFICIENT = "contracts_sufficient"
    OTHER_PRODUCT = "other_product"


REASON_TITLES: dict[Reason, str] = {
    Reason.DUPLICATE: "Дубликат предложения",
    Reason.SAME_SUPPLIER: "Более ранняя цена того же поставщика",
    Reason.MISSING_PRICE: "Цена не указана",
    Reason.MISSING_UNIT_PRICE: "Нельзя выделить цену за единицу позиции",
    Reason.MISSING_DATA: "Недостаточно данных для сравнения",
    Reason.CURRENCY_UNSUPPORTED: "Цена не в рублях",
    Reason.STALE_PRICE: "Цена старше допустимого возраста",
    Reason.CONTRACT_PENALTIES: "По контракту начислены неустойки",
    Reason.LOW_TEXT_SIMILARITY: "Не вошло в число самых похожих на позицию",
    Reason.REQUIRED_ATTR_MISMATCH: "Не совпадает обязательная характеристика",
    Reason.REGION_MISMATCH: "Несопоставимый регион",
    Reason.LOW_RELEVANCE: "Релевантность ниже порога",
    Reason.UNIT_INCOMPATIBLE: "Единицу измерения нельзя привести к единице позиции",
    Reason.OUTLIER_MAD: "Выброс по правилу MAD",
    Reason.OUTLIER_IQR: "Выброс по правилу IQR",
    Reason.CV_TRIMMING: "Исключено при подрезке выборки",
    Reason.MANUAL: "Исключено пользователем",
    Reason.CONTRACTS_SUFFICIENT: "Достаточно цен из исполненных контрактов",
    Reason.OTHER_PRODUCT: "Другой вид товара",
}


class Flag(StrEnum):
    """Информационные флаги: не исключают оффер, но видны в отчёте."""

    PENALTIES_UNKNOWN = "penalties_unknown"
    VAT_UNKNOWN = "vat_unknown"
    KTRU_UNCONFIRMED = "ktru_unconfirmed"
    OUTLIER_FILTER_SKIPPED_SMALL_N = "outlier_filter_skipped_small_n"
    UNIT_ASSUMED = "unit_assumed"
    PACK_AMBIGUOUS = "pack_ambiguous"  # фасовка в названии, цена может быть за фасовку
    PACK_BY_PEERS = "pack_by_peers"  # фасовка выбрана по ценам других предложений
    LLM_EXTRACTED = "llm_extracted"
