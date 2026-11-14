from decimal import Decimal as D

import pytest

from app.domain.spec import AttrType, PositionSpec, SpecAttribute
from app.matching.prefilter import other_product
from app.matching.text_values import values_from_text


def _num(name: str, value: str, unit: str | None) -> SpecAttribute:
    return SpecAttribute(name=name, type=AttrType.NUMBER, value_number=D(value), unit=unit)


def _enum(name: str, *values: str) -> SpecAttribute:
    return SpecAttribute(name=name, type=AttrType.ENUM, values=list(values))


PAPER = [
    _num("Плотность", "80", "г/м²"),
    _num("Белизна", "146", "%"),
    _num("Количество в упаковке", "500", "листов"),
]


def test_paper_title() -> None:
    got = values_from_text(PAPER, "Бумага офисная А4, 80г/м2, 500л, марка С, 146%(CIE)")
    assert got["Плотность"].number == D(80)
    assert got["Белизна"].number == D(146)
    assert got["Количество в упаковке"].number == D(500)


def test_table_cells_and_named_fragments() -> None:
    text = (
        "Перфоратор П-800\nОписание | Мощность, Вт | 800 | Тип патрона | SDS-plus\n"
        "Цвет чернил: Синий; Толщина линии письма: 0,35 Миллиметр"
    )
    attrs = [
        _num("Мощность", "800", "Вт"),
        _enum("Цвет", "синяя"),
        _num("Толщина линии", "0.7", "мм"),
    ]
    got = values_from_text(attrs, text)
    assert got["Мощность"].number == D(800)
    assert got["Цвет"].text == "Синий"
    assert got["Толщина линии"].number == D("0.35")


def test_cable_cross_section() -> None:
    attrs = [_num("Количество жил", "3", None), _num("Сечение жилы", "2.5", "мм²")]
    got = values_from_text(attrs, "Кабель силовой ВВГнг(А)-LS 3х2,5ок(N,PE)-0,66 100м")
    assert got["Количество жил"].number == D(3)
    assert got["Сечение жилы"].number == D("2.5")


@pytest.mark.parametrize(
    "text",
    [
        "Ручка шариковая, узел 0,7 мм, линия 0,35 мм",  # два значения в мм — неоднозначно
        "Толщина линии письма: ≥ 0,3 и < 0,5 Миллиметр",  # диапазон с условиями
    ],
)
def test_ambiguous_numbers_left_to_llm(text: str) -> None:
    assert "Толщина линии" not in values_from_text([_num("Толщина линии", "0.7", "мм")], text)


def test_navigation_words_are_not_values() -> None:
    text = "Скрепки 28 мм\nХарактеристики | Сертификаты и материалы | Аналоги и замены"
    assert "Материал" not in values_from_text([_enum("Материал", "металлические")], text)


def test_description_numbers_ignored() -> None:
    # «сечениями до 240 кв.мм» в описании — не сечение этого кабеля
    text = "Кабель силовой ВВГнг(А)-LS\nКабели на напряжение 0,66 кВ сечениями до 240 кв.мм"
    assert "Сечение жилы" not in values_from_text([_num("Сечение жилы", "2.5", "мм²")], text)


@pytest.mark.parametrize(
    ("title", "text", "other"),
    [
        ("Сумка для ноутбука Geek 15.6''", "Сумка для ноутбука", True),
        ("Модуль памяти 16 ГБ SO-DIMM", "Модуль памяти ADATA DDR4", True),
        ("Ноутбук Acer Aspire Lite 15", "Ноутбук Acer", False),
        ("Azerty RB-1561", "Лучшие ноутбуки года: Azerty", False),
    ],
)
def test_other_product(title: str, text: str, other: bool) -> None:
    position = PositionSpec(ordinal=1, name="Ноутбук", quantity=D(1), unit_raw="шт")
    assert bool(other_product(position, title, text)) is other


def test_other_product_skips_general_names() -> None:
    position = PositionSpec(ordinal=1, name="Поставка канцтоваров", quantity=D(1), unit_raw="шт")
    assert other_product(position, "Ручка шариковая", "Ручка") is None


def test_superscript_unit_in_table() -> None:
    text = "Кабель ВВГнг(А)-LS\nСечение жилы, мм\n2\n2,5\nИсполнение жилы"
    got = values_from_text([_num("Сечение жилы", "2.5", "мм²")], text)
    assert got["Сечение жилы"].number == D("2.5")


def test_label_cells_are_not_values() -> None:
    text = "Кресло руководителя КВ-9 экокожа черная\nЦвет:\nПроизводитель:\nРоссия"
    assert values_from_text([_enum("Цвет", "чёрный")], text)["Цвет"].text == "чёрный"


def test_name_inside_label() -> None:
    text = "Кресло офисное\nВид материала обивки спинки: Кожа натуральная"
    assert values_from_text([_enum("Обивка", "экокожа")], text)["Обивка"].text == "Кожа натуральная"


def test_designation_from_text() -> None:
    attr = _enum("Обозначение", "SDS-plus")
    assert values_from_text([attr], "Перфоратор П-800\nТип патрона\nSDS Plus")["Обозначение"].text
    assert "Обозначение" not in values_from_text([attr], "Перфоратор SDS-max 1300Вт")
