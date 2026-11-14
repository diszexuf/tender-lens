from decimal import Decimal as D

import pytest

from app.domain.offer import ExtractedValue
from app.domain.quote_parser import locate, parse_quantity, parse_quote
from app.domain.spec import AttrType, Operator, SpecAttribute
from app.llm.tasks import (
    LLMAttr,
    LLMPosition,
    LLMSpec,
    LLMValue,
    _offer_value,
    llm_spec_to_domain,
)
from app.matching.text_values import ground_llm_value


@pytest.mark.parametrize(
    ("name", "quote", "kind", "op", "value", "unit"),
    [
        (
            "Ресурс",
            "ресурс не менее 1600 страниц",
            AttrType.NUMBER,
            Operator.GTE,
            D(1600),
            "страниц",
        ),
        ("Плотность", "80 г/м²", AttrType.NUMBER, Operator.EQ, D(80), "г/м²"),
        ("Белизна", "белизна не менее 146% CIE", AttrType.NUMBER, Operator.GTE, D(146), "%"),
        ("Нагрузка", "не более 120 кг", AttrType.NUMBER, Operator.LTE, D(120), "кг"),
        ("Толщина линии", "толщина линии 0,7 мм", AttrType.NUMBER, Operator.EQ, D("0.7"), "мм"),
        (
            "Количество в упаковке",
            "500 листов в пачке",
            AttrType.NUMBER,
            Operator.EQ,
            D(500),
            "листов",
        ),
        ("Количество жил", "3х2,5 мм²", AttrType.NUMBER, Operator.EQ, D(3), None),
        ("Сечение жилы", "3х2,5 мм²", AttrType.NUMBER, Operator.EQ, D("2.5"), "мм²"),
        ("Количество листов", "1 600 листов", AttrType.NUMBER, Operator.EQ, D(1600), "листов"),
    ],
)
def test_numbers(name, quote, kind, op, value, unit) -> None:
    a = parse_quote(name, quote, True)
    assert (a.type, a.operator, a.value_number, a.unit) == (kind, op, value, unit)


def test_range() -> None:
    a = parse_quote("Диагональ", "от 15 до 16 дюймов", True)
    assert (a.type, a.value_min, a.value_max, a.unit) == (AttrType.RANGE, D(15), D(16), "дюймов")


@pytest.mark.parametrize(
    ("name", "quote", "values"),
    [
        ("Цвет", "цвет чёрный", ["чёрный"]),
        ("Тип", "оригинальный или совместимый", ["оригинальный", "совместимый"]),
        ("Марка кабеля", "ВВГнг(А)-LS", ["ВВГнг(А)-LS"]),
        ("Формат", "А4", ["А4"]),  # код, а не число
        ("Цоколь", "E27", ["E27"]),
        ("Принтер", "HP LaserJet Pro P1102/M1132", ["HP LaserJet Pro P1102/M1132"]),
    ],
)
def test_enums(name: str, quote: str, values: list[str]) -> None:
    a = parse_quote(name, quote, True)
    assert a.type == AttrType.ENUM and a.values == values


def test_bool() -> None:
    assert parse_quote("Подлокотники", "наличие подлокотников", True).value_bool is True
    assert parse_quote("Подлокотники", "без подлокотников", True).value_bool is False


def test_quantity() -> None:
    assert parse_quantity("Количество — 500 м.") == (D(500), "м")
    assert parse_quantity("200 пачек") == (D(200), "пачек")
    assert parse_quantity("12 месяцев") == (D(12), "месяцев")
    assert parse_quantity(None) is None


def test_locate_tolerates_case_and_yo() -> None:
    text = "Картридж: цвет чёрный, ресурс не менее 1600 страниц."
    assert locate("Цвет черный", text) == "цвет чёрный"
    assert locate("ресурс не менее 1600 страниц", text) == "ресурс не менее 1600 страниц"
    assert locate("ресурс 2000 страниц", text) is None


def test_llm_spec_to_domain_grounds_values() -> None:
    text = "Бумага А4, 1600 листов, средняя плотность, 1000 упаковок"
    spec = LLMSpec(
        positions=[
            LLMPosition(
                name="Бумага А4",
                quantity_quote="1000 упаковок",
                search_query="бумага а4",
                attributes=[
                    LLMAttr(name="Количество в упаковке", quote="1600 листов", required=True),
                    LLMAttr(name="Плотность", quote="160 листов", required=True),  # нет в тексте
                    LLMAttr(name="Формат", quote="А4", required=True),
                ],
            )
        ]
    )
    out = llm_spec_to_domain(spec, text)
    p = out.positions[0]
    assert (p.quantity, p.okei_code) == (D(1000), "778")
    assert [(a.name, a.value_number or a.values) for a in p.attributes] == [
        ("Количество в упаковке", D(1600)),
        ("Формат", ["А4"]),
    ]
    assert any("160 листов" in w for w in out.warnings)


def test_integers_without_exponent() -> None:
    assert str(parse_quote("Количество листов", "1600 листов", True).value_number) == "1600"


def _v(name: str, **kw: object) -> LLMValue:
    base: dict[str, object] = dict.fromkeys(("number", "min", "max", "text", "flag", "unit"))
    return LLMValue(name=name, **(base | kw))


def _ground(attr: SpecAttribute, v: LLMValue, text: str) -> ExtractedValue:
    return ground_llm_value(attr, _offer_value(v, attr.type), text)


def test_offer_values_grounded_in_text() -> None:
    qty = SpecAttribute(
        name="Количество в упаковке", type=AttrType.NUMBER, value_number=D(100), unit="шт"
    )
    # «1» модель взяла из единицы «упак» — в тексте его нет
    assert _ground(qty, _v(qty.name, number=1), "Скрепки оцинкованные 28 мм").number is None
    section = SpecAttribute(
        name="Сечение жилы", type=AttrType.NUMBER, value_number=D("2.5"), unit="мм²"
    )
    got = _ground(section, _v(section.name, number=2.5, unit="мм²"), "Кабель ВВГнг(А)-LS 3х2,5ок")
    assert got.number == D("2.5")
    length = SpecAttribute(name="Длина", type=AttrType.NUMBER, value_number=D(28), unit="мм")
    assert _ground(length, _v("Длина", text="28 мм"), "Скрепки 28 мм").number == D(28)
    cores = SpecAttribute(name="Процессор", type=AttrType.NUMBER, value_number=D(8), unit="ядер")
    # «Ryzen 7» — не «7 ядер»; «8-ядерный» — да
    assert (
        _ground(cores, _v(cores.name, number=7, unit="ядер"), "Ноутбук (Ryzen 7, 16 ГБ)").number
        is None
    )
    assert _ground(
        cores, _v(cores.name, number=8, unit="ядер"), "Intel Core 5, 8-ядерный"
    ).number == D(8)
    kind = SpecAttribute(name="Тип питания", type=AttrType.ENUM, values=["сетевой"])
    # «аккумуляторный» в тексте нет — модель додумала
    assert (
        _ground(kind, _v(kind.name, text="аккумуляторный"), "Перфоратор П-800, 800 Вт").text is None
    )
