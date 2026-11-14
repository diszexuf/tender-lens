from datetime import UTC, date, datetime
from decimal import Decimal as D

import pytest
from pydantic import ValidationError

from app.domain.offer import ExtractedValue, MatchStatus
from app.domain.reasons import Reason
from app.domain.spec import AttrType, Operator, PositionSpec, SpecAttribute
from app.matching.comparators import CompareContext, compare
from app.matching.dedup import DedupItem, canonical_url, deduplicate
from app.matching.prefilter import rank
from app.matching.scoring import ScoringRules, score
from app.matching.text import DEFAULT_SYNONYMS

CTX = CompareContext()


def num(
    name: str, op: Operator, value: str, unit: str | None = None, required: bool = True
) -> SpecAttribute:
    return SpecAttribute(
        name=name,
        type=AttrType.NUMBER,
        operator=op,
        value_number=D(value),
        unit=unit,
        required=required,
    )


def enum(name: str, *values: str, required: bool = True) -> SpecAttribute:
    return SpecAttribute(name=name, type=AttrType.ENUM, values=list(values), required=required)


# ---------- спецификация ----------


def test_spec_validation_requires_value_for_type() -> None:
    with pytest.raises(ValidationError):
        SpecAttribute(name="ресурс", type=AttrType.NUMBER)
    with pytest.raises(ValidationError):
        SpecAttribute(name="цвет", type=AttrType.ENUM, values=[" "])


def test_enum_operator_derived_from_values() -> None:
    assert enum("цвет", "черный").operator == Operator.EQ
    assert enum("тип", "оригинальный", "совместимый").operator == Operator.IN


def test_describe() -> None:
    assert num("ресурс", Operator.GTE, "1600", "стр").describe() == "ресурс ≥ 1600 стр"


# ---------- компараторы ----------


@pytest.mark.parametrize(
    ("value", "status"),
    [
        (ExtractedValue(name="ресурс", number=D(1600), unit="стр"), MatchStatus.MATCH),
        (ExtractedValue(name="ресурс", number=D(2000), unit="страниц"), MatchStatus.MATCH),
        (ExtractedValue(name="ресурс", number=D(1000), unit="стр"), MatchStatus.MISMATCH),
        (ExtractedValue(name="ресурс", number=D(1600)), MatchStatus.MATCH),  # единица не указана
        (ExtractedValue(name="ресурс"), MatchStatus.UNKNOWN),
        (None, MatchStatus.UNKNOWN),
    ],
)
def test_number_gte(value: ExtractedValue | None, status: MatchStatus) -> None:
    assert compare(num("ресурс", Operator.GTE, "1600", "стр"), value, CTX).status == status


def test_number_unit_conversion() -> None:
    attr = num("накопитель", Operator.GTE, "512", "ГБ")
    assert (
        compare(attr, ExtractedValue(name="x", number=D(1), unit="ТБ"), CTX).status
        == MatchStatus.MATCH
    )
    assert (
        compare(attr, ExtractedValue(name="x", number=D(256), unit="ГБ"), CTX).status
        == MatchStatus.MISMATCH
    )


def test_number_eq_tolerance() -> None:
    attr = num("плотность", Operator.EQ, "80", "г/м2")
    assert (
        compare(attr, ExtractedValue(name="x", number=D("80"), unit="г/м²"), CTX).status
        == MatchStatus.MATCH
    )
    assert (
        compare(attr, ExtractedValue(name="x", number=D("90"), unit="г/м2"), CTX).status
        == MatchStatus.MISMATCH
    )


def test_range() -> None:
    attr = SpecAttribute(
        name="диагональ", type=AttrType.RANGE, value_min=D(14), value_max=D(16), unit="дюйм"
    )
    assert (
        compare(attr, ExtractedValue(name="x", number=D("15.6"), unit="дюйм"), CTX).status
        == MatchStatus.MATCH
    )
    assert (
        compare(attr, ExtractedValue(name="x", number=D("17.3")), CTX).status
        == MatchStatus.MISMATCH
    )


@pytest.mark.parametrize(
    ("actual", "status"),
    [
        ("черный", MatchStatus.MATCH),
        ("Black", MatchStatus.MATCH),
        ("чёрный", MatchStatus.MATCH),
        ("голубой", MatchStatus.MISMATCH),
        ("cyan", MatchStatus.MISMATCH),
    ],
)
def test_enum_with_synonyms(actual: str, status: MatchStatus) -> None:
    assert (
        compare(enum("цвет", "черный"), ExtractedValue(name="цвет", text=actual), CTX).status
        == status
    )


def test_enum_any_of_list() -> None:
    attr = enum("совместимость", "HP LaserJet Pro P1102", "HP LaserJet Pro M1132")
    value = ExtractedValue(name="x", text="HP LaserJet P1005, P1102, Pro M1132 MFP")
    assert compare(attr, value, CTX).status == MatchStatus.MATCH


def test_enum_unrelated_value_mismatch() -> None:
    attr = enum("совместимость", "HP LaserJet Pro P1102")
    value = ExtractedValue(name="x", text="Canon i-SENSYS MF3010")
    assert compare(attr, value, CTX).status == MatchStatus.MISMATCH


def test_bool() -> None:
    attr = SpecAttribute(name="подсветка", type=AttrType.BOOL, value_bool=True)
    assert compare(attr, ExtractedValue(name="x", text="есть"), CTX).status == MatchStatus.MATCH
    assert (
        compare(attr, ExtractedValue(name="x", boolean=False), CTX).status == MatchStatus.MISMATCH
    )
    assert (
        compare(attr, ExtractedValue(name="x", text="возможно"), CTX).status == MatchStatus.UNKNOWN
    )


def test_text() -> None:
    attr = SpecAttribute(name="материал", type=AttrType.TEXT, value_text="нержавеющая сталь")
    assert (
        compare(attr, ExtractedValue(name="x", text="сталь нержавеющая AISI 304"), CTX).status
        == MatchStatus.MATCH
    )
    assert (
        compare(attr, ExtractedValue(name="x", text="пластик"), CTX).status == MatchStatus.MISMATCH
    )


# ---------- скоринг ----------


def test_required_mismatch_excludes() -> None:
    m = [
        compare(num("ресурс", Operator.GTE, "1600"), ExtractedValue(name="x", number=D(1000)), CTX),
        compare(enum("цвет", "черный"), ExtractedValue(name="x", text="черный"), CTX),
    ]
    verdict = score(m, ScoringRules())
    assert not verdict.included and verdict.reason == Reason.REQUIRED_ATTR_MISMATCH


def test_unknown_lowers_relevance() -> None:
    m = [
        compare(num("ресурс", Operator.GTE, "1600"), None, CTX),
        compare(enum("цвет", "черный"), None, CTX),
    ]
    verdict = score(m, ScoringRules())
    assert verdict.relevance == D("0.50")
    assert verdict.reason == Reason.LOW_RELEVANCE


def test_desired_mismatch_does_not_exclude() -> None:
    m = [
        compare(enum("цвет", "черный"), ExtractedValue(name="x", text="черный"), CTX),
        compare(
            enum("тип", "оригинальный", required=False),
            ExtractedValue(name="x", text="совместимый"),
            CTX,
        ),
    ]
    verdict = score(m, ScoringRules())
    assert verdict.included and verdict.relevance == D("0.75")


# ---------- дедупликация ----------


def _item(oid: str, **kw: object) -> DedupItem:
    base: dict = dict(
        url=f"https://shop.ru/p/{oid}", title="Картридж HP 85A", price=D(1000),
        supplier_name=None, supplier_inn=None, external_id=None, price_date=None,
        fetched_at=datetime(2026, 11, 14, tzinfo=UTC),
    )  # fmt: skip
    base.update(kw)
    return DedupItem(offer_id=oid, **base)


def test_canonical_url_strips_tracking() -> None:
    assert (
        canonical_url("https://WWW.Shop.ru/p/1/?utm_source=x&b=2&a=1#top")
        == "https://shop.ru/p/1?a=1&b=2"
    )


def test_dedup_by_url_and_external_id() -> None:
    decisions = deduplicate(
        [
            _item("a", url="https://shop.ru/p/1?utm_source=y"),
            _item("b", url="https://shop.ru/p/1"),
            _item("c", external_id="0123:1"),
            _item("d", external_id="0123:1", url="https://other.ru/x"),
        ]
    )
    dups = {d.offer_id: d.duplicate_of for d in decisions if d.reason == Reason.DUPLICATE}
    assert dups == {"b": "a", "d": "c"}


def test_same_supplier_different_products_kept() -> None:
    """Разные товары одного магазина — независимые предложения."""
    decisions = deduplicate(
        [
            _item("a", supplier_inn="7700000000", title="Кабель ВВГнг 3x1,5 100 м", price=D(10930)),
            _item("b", supplier_inn="7700000000", title="Кабель ВВГнг 3x2,5 100 м", price=D(16704)),
        ]
    )
    assert decisions == []


def test_text_long_description_is_unknown_not_mismatch() -> None:
    attr = SpecAttribute(name="тип изоляции", type=AttrType.TEXT, value_text="LS")
    long = "ПВХ пластикат пониженной пожароопасности с низким дымо- и газовыделением"
    assert compare(attr, ExtractedValue(name="x", text=long), CTX).status == MatchStatus.UNKNOWN


def test_same_supplier_keeps_latest() -> None:
    decisions = deduplicate(
        [
            _item(
                "old",
                supplier_inn="7700000000",
                price_date=date(2025, 1, 1),
                price=D(900),
                title="Картридж A",
            ),
            _item(
                "new",
                supplier_inn="7700000000",
                price_date=date(2026, 6, 1),
                price=D(950),
                title="Картридж B",
            ),
            _item("other", supplier_inn="7800000000", price=D(990)),
        ]
    )
    assert [(d.offer_id, d.reason, d.duplicate_of) for d in decisions] == [
        ("old", Reason.SAME_SUPPLIER, "new")
    ]


# ---------- предфильтр ----------


def test_prefilter_ranks_relevant_first() -> None:
    position = PositionSpec(ordinal=1, name="Картридж HP CE285A черный", quantity=D(50))
    offers = {
        "good": "Картридж лазерный HP 85A CE285A черный, ресурс 1600 стр.",
        "similar": "Картридж совместимый для HP CE285A",
        "bad": "Бумага офисная А4 80 г/м2 500 листов",
    }
    top, rest = rank(position, offers, top_k=2, synonyms=DEFAULT_SYNONYMS)
    assert [r.offer_id for r in top] == ["good", "similar"]
    assert [r.offer_id for r in rest] == ["bad"]


def test_same_product_ignores_packaging() -> None:
    from app.matching.dedup import same_product

    assert same_product(
        "Кабель силовой ВВГ-Пнг(А)-LS 3x2,5ок(N,PE)-0,66 100м ТРТС - 100 м",
        "Кабель силовой ВВГ-Пнг(А)-LS 3x2,5ок(N,PE)-0,66 50м ТРТС - 50 м",
    )
    assert not same_product("Кабель ВВГнг 3x1,5 100м", "Кабель ВВГнг 3x2,5 100м")


def test_value_in_title_beats_wrong_extraction() -> None:
    attr = enum("тип кабеля", "ВВГнг(А)-LS")
    wrong = ExtractedValue(name="тип кабеля", text="круглый")
    title = "Кабель ВВГнг(А)-LS 3х2,5 кв.мм ГОСТ круглый"
    assert compare(attr, wrong, CTX, title=title).status == MatchStatus.MATCH
    assert compare(attr, wrong, CTX, title="Кабель ВВГ 3х2,5").status != MatchStatus.MATCH


@pytest.mark.parametrize(
    ("actual", "status"),
    [
        ("ВВГнг(А)-LS", MatchStatus.MATCH),
        ("ВВГнг(А)-LS 3х2,5 ГОСТ 31996", MatchStatus.MATCH),
        ("ВВГнг(А)", MatchStatus.MISMATCH),
        ("КГВВнг(А)-LS", MatchStatus.MISMATCH),
        ("ВВГ", MatchStatus.MISMATCH),
    ],
)
def test_enum_requires_all_tokens_of_expected(actual: str, status: MatchStatus) -> None:
    attr = enum("тип кабеля", "ВВГнг(А)-LS")
    assert compare(attr, ExtractedValue(name="x", text=actual), CTX).status == status


def test_word_forms_match() -> None:
    attr = enum("материал жилы", "медный")
    assert compare(attr, ExtractedValue(name="x", text="Медь"), CTX).status == MatchStatus.MATCH


@pytest.mark.parametrize(
    ("title", "status"),
    [
        ("Кабель силовой гибкий КГВВнг(А)-LS 3х2,5 медный ГОСТ", MatchStatus.MISMATCH),
        ("Кабель силовой ВВГнг(А) 3х2.5 (N, PE) плоский", MatchStatus.MISMATCH),
        ("Кабель ВВГ 3х2,5 (ож)", MatchStatus.MISMATCH),
        ("Кабель силовой ВВГнг(А)-LS 3х2,5 медный круглый", MatchStatus.MATCH),
        ("Кабель силовой медный 3х2,5", MatchStatus.UNKNOWN),  # кодов в названии нет — не судим
    ],
)
def test_title_conflict_for_codes(title: str, status: MatchStatus) -> None:
    attr = enum("тип кабеля", "ВВГнг(А)-LS")
    assert compare(attr, None, CTX, title=title).status == status


def test_title_conflict_not_applied_to_plain_words() -> None:
    attr = enum("цвет", "черный")
    assert compare(attr, None, CTX, title="Картридж голубой").status == MatchStatus.UNKNOWN


def test_title_conflict_overrides_extraction() -> None:
    attr = enum("тип кабеля", "ВВГнг(А)-LS")
    extracted = ExtractedValue(name="x", text="ВВГнг(А)-LS")  # модель взяла из текста страницы
    title = "Кабель силовой ВВГ-Пнг(А)-LS 3х2,5 медный плоский"
    assert compare(attr, extracted, CTX, title=title).status == MatchStatus.MISMATCH


def test_abbreviated_model_list_in_title_is_not_conflict() -> None:
    attr = enum("совместимость", "HP LaserJet Pro P1102")
    value = ExtractedValue(name="x", text="HP LaserJet P1102, M1132")
    result = compare(attr, value, CTX, title="Картридж для HP P1102 черный")
    assert result.status == MatchStatus.MATCH


def test_printer_models_without_required_is_mismatch() -> None:
    attr = SpecAttribute(
        name="Принтер", type=AttrType.TEXT, value_text="HP LaserJet Pro P1102/M1132", required=True
    )
    other = ExtractedValue(
        name="Принтер", text="HP CLJ Pro 4203cdZ/4203dZ/4203dw/4303dw/4303fdZ/4303fdw"
    )
    assert compare(attr, other, CompareContext()).status == MatchStatus.MISMATCH
    same = ExtractedValue(name="Принтер", text="HP LaserJet P1102, P1102w, M1132, M1212nf, M1217")
    assert compare(attr, same, CompareContext()).status == MatchStatus.MATCH


@pytest.mark.parametrize(
    ("required", "actual", "status"),
    [
        (["синяя"], "син", MatchStatus.MATCH),  # сокращение в названии магазина
        (["синяя"], "синие", MatchStatus.MATCH),
        (["металлические"], "оцинкованные", MatchStatus.MATCH),  # частное выполняет общее
        (["металлические"], "цинковое", MatchStatus.MATCH),
        (["оцинкованные"], "металлические", MatchStatus.MISMATCH),  # обратное — нет
        (["металлические"], "пластиковые", MatchStatus.MISMATCH),
    ],
)
def test_enum_abbreviation_and_kind(required: list[str], actual: str, status: MatchStatus) -> None:
    attr = SpecAttribute(name="Значение", type=AttrType.ENUM, values=required)
    result = compare(attr, ExtractedValue(name="Значение", text=actual), CompareContext())
    assert result.status == status, result.explanation


def test_unit_abbreviation_not_liters() -> None:
    attr = SpecAttribute(
        name="Количество в упаковке", type=AttrType.NUMBER, value_number=D(500), unit="листов"
    )
    value = ExtractedValue(name="Количество в упаковке", number=D(100), unit="л")
    assert compare(attr, value, CompareContext()).status == MatchStatus.MISMATCH
