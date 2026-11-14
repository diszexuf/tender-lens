"""Сопоставимость: слова-исключения, грубые выбросы, диапазоны КТРУ, приоритет контрактов."""

from __future__ import annotations

import uuid
from decimal import Decimal
from types import SimpleNamespace

from app.calc.stats import CalcRules, PositionStatus, PriceItem, calculate_position
from app.domain.reasons import Reason
from app.domain.rules import Rules
from app.domain.spec import AttrType, PositionSpec, SpecAttribute
from app.matching.comparators import CompareContext, compare
from app.matching.prefilter import excluded_word
from app.matching.text_values import values_from_text

PEN = PositionSpec(ordinal=1, name="Ручка шариковая синяя", quantity=Decimal(500))
WORDS = Rules().excluded_words


def test_souvenir_and_gift_items_are_excluded() -> None:
    assert (
        excluded_word(PEN, "Ручка подарочная шариковая Basel", WORDS) == "в названии «подарочная»"
    )
    assert excluded_word(PEN, "Ручка Parker сувенирная в футляре", WORDS) is not None
    assert excluded_word(PEN, "Ручка шариковая с логотипом компании", WORDS) is not None
    assert excluded_word(PEN, "Ручка шариковая синяя 0,7 мм", WORDS) is None
    assert excluded_word(PEN, "Ручка б/у", WORDS) == "в названии «б/у»"
    assert excluded_word(PEN, "Клубная ручка", WORDS) is None


def test_excluded_word_not_applied_when_position_asks_for_it() -> None:
    gift = PositionSpec(ordinal=1, name="Ручка подарочная", quantity=Decimal(1))
    assert excluded_word(gift, "Ручка подарочная Basel", WORDS) is None


def test_position_words_exclude_polymer_coated_clips() -> None:
    clips = PositionSpec(
        ordinal=1,
        name="Скрепки металлические 28 мм",
        quantity=Decimal(1),
        exclude_words=["полимерн"],
    )
    title = "Скрепки канцелярские 28 мм Attache металлические с полимерным покрытием"
    assert excluded_word(clips, title, clips.exclude_words) == "в названии «полимерным»"
    assert excluded_word(clips, "Скрепки 28 мм никелированные", clips.exclude_words) is None


def _items(*prices: int) -> list[PriceItem]:
    return [PriceItem(str(i), Decimal(p)) for i, p in enumerate(prices)]


def test_price_far_from_median_is_excluded_even_in_small_sample() -> None:
    res = calculate_position(_items(14, 15, 18, 1880), Decimal(1), CalcRules())
    assert [e.reason for e in res.excluded] == [Reason.OUTLIER_RATIO]
    assert res.status == PositionStatus.OK
    assert res.unit_price == Decimal("15.67")


def test_ratio_filter_keeps_majority_and_can_be_disabled() -> None:
    # Половина выборки «далеко» — это не выбросы, а две группы цен
    res = calculate_position(_items(10, 11, 100, 110), Decimal(1), CalcRules())
    assert not res.excluded and res.status == PositionStatus.HETEROGENEOUS
    off = calculate_position(
        _items(14, 15, 18, 1880), Decimal(1), CalcRules(max_price_ratio=Decimal(0))
    )
    assert not off.excluded


def test_ktru_min_max_is_a_range_containing_required_value() -> None:
    attr = SpecAttribute(name="Длина", type=AttrType.NUMBER, value_number=Decimal(28), unit="мм")
    text = (
        "Скрепки металлические (28 мм). Количество штук в упаковке ≥ 100 , "
        "Длина, max ≤ 30 Миллиметр , Длина, min ≥ 26 Миллиметр"
    )
    value = values_from_text([attr], text)["Длина"]
    assert (value.min, value.max) == (Decimal(26), Decimal(30))
    assert compare(attr, value, CompareContext()).status == "match"
    wrong = SpecAttribute(name="Длина", type=AttrType.NUMBER, value_number=Decimal(50), unit="мм")
    assert compare(wrong, value, CompareContext()).status == "mismatch"


def _draft(source: str, price: str):  # type: ignore[no-untyped-def]
    from app.pipeline.evaluate_stage import Draft

    d = Draft(offer=SimpleNamespace(id=uuid.uuid4()), source_type=source)  # type: ignore[arg-type]
    d.normalized = Decimal(price)
    return d


def test_contracts_first_only_when_contracts_give_a_price() -> None:
    from app.pipeline.evaluate_stage import _prefer_contracts

    rules = Rules()
    # Контракты однородны — цены магазинов не нужны
    ok = [_draft("eis", p) for p in ("10", "11", "12")] + [_draft("html", "30")]
    _prefer_contracts(ok, rules, Decimal(1))
    assert ok[-1].reason == Reason.CONTRACTS_SUFFICIENT
    assert "цена рассчитана по 3 исполненным контрактам" in ok[-1].text
    # Контракты неоднородны (CV > 33 %) — в расчёт идут и цены магазинов
    mixed = [_draft("eis", p) for p in ("18", "23", "70")] + [_draft("html", "70")]
    _prefer_contracts(mixed, rules, Decimal(1))
    assert all(d.included for d in mixed)
