from decimal import Decimal

from app.domain.spec import AttrType, Operator, SpecAttribute
from app.domain.spec_review import is_vague, review_attributes, unit_mismatch


def num(name: str, value: str, unit: str | None, required: bool = True) -> SpecAttribute:
    return SpecAttribute(
        name=name,
        type=AttrType.NUMBER,
        operator=Operator.GTE,
        value_number=Decimal(value),
        unit=unit,
        required=required,
    )


def text(name: str, value: str) -> SpecAttribute:
    return SpecAttribute(name=name, type=AttrType.TEXT, value_text=value, required=True)


def test_unit_mismatch_density_in_sheets() -> None:
    assert unit_mismatch(num("Плотность", "1600", "листов"))
    assert unit_mismatch(num("Плотность", "80", "г/м²")) is None


def test_unit_mismatch_unknown_name_or_unit_is_ignored() -> None:
    assert unit_mismatch(num("Ресурс", "1600", "стр")) is None
    assert unit_mismatch(num("Сечение жилы", "2.5", "мм²")) is None
    assert unit_mismatch(num("Мощность", "800", "попугаев")) is None


def test_memory_volume_in_gb_is_fine() -> None:
    assert unit_mismatch(num("Объём оперативной памяти", "16", "ГБ")) is None
    assert unit_mismatch(num("Объём", "5", "л")) is None


def test_vague_values() -> None:
    assert is_vague(text("Тип бумаги", "средняя плотность"))
    assert is_vague(text("Качество", "хорошее качество печати"))
    assert not is_vague(text("Цвет", "чёрный"))
    assert not is_vague(text("Класс", "средний класс A"))  # есть измеримое: код
    assert not is_vague(text("Описание", "плотная бумага для принтеров и копиров большого объёма"))


def test_review_demotes_and_warns() -> None:
    attrs = [
        num("Плотность", "1600", "листов"),
        text("Тип бумаги", "средняя плотность"),
        num("Белизна", "146", "%"),
    ]
    out, warnings = review_attributes(1, attrs)
    assert [a.required for a in out] == [False, False, True]
    assert len(warnings) == 2
    assert "Плотность" in warnings[0] and "листов" in warnings[0]


def test_review_keeps_optional_without_warning() -> None:
    out, warnings = review_attributes(1, [num("Плотность", "1600", "листов", required=False)])
    assert not out[0].required and warnings == []


def test_exhaustive_alternatives_are_not_required() -> None:
    attr = SpecAttribute(
        name="Тип", type=AttrType.ENUM, values=["оригинальный", "совместимый"], required=True
    )
    out, warnings = review_attributes(1, [attr])
    assert out[0].required is False and "все варианты" in warnings[0]


def test_codes_from_name() -> None:
    from app.domain.spec_review import codes_from_name

    power = SpecAttribute(
        name="Мощность", type=AttrType.NUMBER, value_number=Decimal(800), unit="Вт"
    )
    attrs, warnings = codes_from_name(1, "Перфоратор SDS-plus", [power])
    assert attrs[-1].values == ["SDS-plus"] and attrs[-1].required and warnings
    # код уже есть в требованиях — не дублируется
    base = SpecAttribute(name="Цоколь", type=AttrType.ENUM, values=["E27"])
    assert codes_from_name(1, "Лампа светодиодная E27", [base])[0] == [base]
    assert codes_from_name(1, "Бумага офисная", [])[0] == []
