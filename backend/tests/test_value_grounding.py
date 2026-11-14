"""Привязка ответа модели к тексту, происхождение значений и состав запроса к модели."""

from __future__ import annotations

from decimal import Decimal

from app.domain.offer import ExtractedValue
from app.domain.spec import AttrType, PositionSpec, SpecAttribute
from app.llm.tasks import _batch_schema, missing_attributes
from app.matching.comparators import CompareContext, compare
from app.matching.text_values import ground_llm_value, values_from_text

MATERIAL = SpecAttribute(name="Материал", type=AttrType.ENUM, values=["металлические"])
COLOR = SpecAttribute(name="Цвет", type=AttrType.ENUM, values=["синяя"])
LENGTH = SpecAttribute(name="Длина", type=AttrType.NUMBER, value_number=Decimal(28), unit="мм")

STAFF_TITLE = "Скрепки STAFF EVERYDAY, 28 мм, металлические, 100 шт., в картонной коробке"
STAFF_TEXT = (
    f"{STAFF_TITLE}\nСкрепки длиной 28 мм обеспечивают надежное скрепление бумаг.\n"
    "Описание\nСертификаты и материалы\nДекларация о соответствии\nPDF\nОтзывы"
)


def test_value_in_title_is_found_next_to_punctuation() -> None:
    # «…28 мм, металлические, 100 шт.» — запятая после значения не мешает
    m = compare(MATERIAL, None, CompareContext(), title=STAFF_TITLE)
    assert (m.status, m.source) == ("match", "title")
    cable = SpecAttribute(name="Марка", type=AttrType.ENUM, values=["ВВГнг(А)-LS"])
    assert (
        compare(cable, None, CompareContext(), title="Кабель ВВГнг(А)-LS 3х2,5.").status == "match"
    )
    assert compare(cable, None, CompareContext(), title="Кабель ВВГнг(А) 3х2,5").status != "match"


def test_unrelated_word_from_llm_is_dropped() -> None:
    # «PDF» есть в тексте, но в разделе «Сертификаты и материалы» — это не материал скрепок
    got = ground_llm_value(MATERIAL, ExtractedValue(name="Материал", text="PDF"), STAFF_TEXT)
    assert got.text is None and got.source == "llm"


def test_contradicting_value_kept_when_tied_to_attribute() -> None:
    text = "Скрепки канцелярские 28 мм\nМатериал: пластик\nВ упаковке 100 шт."
    got = ground_llm_value(MATERIAL, ExtractedValue(name="Материал", text="пластик"), text)
    assert got.text == "пластик" and got.evidence == "Материал: пластик"
    cell = "Скрепки канцелярские 28 мм\nМатериал\nПластик\nДлина\n28 мм"
    got = ground_llm_value(MATERIAL, ExtractedValue(name="Материал", text="пластик"), cell)
    assert got.text == "пластик"


def test_value_in_running_text_is_kept() -> None:
    # Связный текст — не ячейка таблицы: противоречие требованию «синяя» остаётся
    text = "Ручка шариковая 0,7 мм\nКорпус прозрачный, чернила красные, колпачок с клипом"
    red = ground_llm_value(COLOR, ExtractedValue(name="Цвет", text="красные"), text)
    assert red.text == "красные" and red.evidence == "чернила красные"
    # Ячейка под короткой подписью характеристики («Основной материал») — её значение
    cell = "Скрепки канцелярские 28 мм\nОсновной материал\nПластик\nДлина\n28 мм"
    got = ground_llm_value(MATERIAL, ExtractedValue(name="Материал", text="пластик"), cell)
    assert got.text == "пластик"
    # Ячейка под чужой подписью — не значение этой характеристики
    cell = "Скрепки канцелярские 28 мм\nСтрана\nПластик\nДлина\n28 мм"
    got = ground_llm_value(MATERIAL, ExtractedValue(name="Материал", text="пластик"), cell)
    assert got.text is None


def test_code_values_carry_source_and_evidence() -> None:
    text = "Скрепки (28 мм). Длина, max ≤ 30 Миллиметр , Длина, min ≥ 26 Миллиметр"
    value = values_from_text([LENGTH], text)["Длина"]
    assert value.source == "code" and "26" in (value.evidence or "") and "30" in value.evidence
    m = compare(LENGTH, value, CompareContext())
    assert (m.status, m.source) == ("match", "code") and m.evidence == value.evidence


def test_llm_is_asked_only_for_attributes_code_did_not_find() -> None:
    position = PositionSpec(
        ordinal=1, name="Скрепки", quantity=Decimal(1), attributes=[LENGTH, MATERIAL]
    )
    length = {"Длина": ExtractedValue(name="Длина", number=Decimal(28))}
    both = {**length, "Материал": ExtractedValue(name="Материал", text="металл")}
    assert [a.name for a in missing_attributes(position, [length, both])] == ["Материал"]
    assert missing_attributes(position, [both, both]) == []
    # Нечего спрашивать — модель возвращает только единицу и фасовку
    schema = _batch_schema(["1"], [])
    assert schema["$defs"]["LLMOfferValues"]["properties"]["values"]["maxItems"] == 0


def test_value_of_another_attribute_is_dropped() -> None:
    mains = SpecAttribute(name="Сетевой", type=AttrType.ENUM, values=["сетевой"])
    chuck = SpecAttribute(name="Патрон", type=AttrType.ENUM, values=["SDS-plus"])
    text = "Перфоратор П-32-1000К SDS Plus\nМощность 1000 Вт"
    wrong = ExtractedValue(name="Сетевой", text="SDS Plus")
    assert ground_llm_value(mains, wrong, text, others=[mains, chuck]).text is None
    assert ground_llm_value(mains, wrong, text).text == "SDS Plus"  # без списка — как раньше
    own = ExtractedValue(name="Патрон", text="SDS Plus")
    assert ground_llm_value(chuck, own, text, others=[mains, chuck]).text == "SDS Plus"


def test_nominal_with_tolerance_is_a_range() -> None:
    density = SpecAttribute(
        name="Плотность", type=AttrType.NUMBER, value_number=Decimal(80), unit="г/м²"
    )
    text = (
        "Бумага офисная А4, 80 г/м2, 500 л.\nПлотность: 81 +/-2 г/м2\nБелизна по CIE: 162 +/- 2 %"
    )
    value = values_from_text([density], text)["Плотность"]
    assert (value.min, value.max) == (Decimal(79), Decimal(83))
    assert compare(density, value, CompareContext()).status == "match"
    white = SpecAttribute(
        name="Белизна", type=AttrType.NUMBER, operator="gte", value_number=Decimal(146), unit="%"
    )
    value = values_from_text([white], text)["Белизна"]
    assert (value.min, value.max) == (Decimal(160), Decimal(164))
    assert compare(white, value, CompareContext()).status == "match"
