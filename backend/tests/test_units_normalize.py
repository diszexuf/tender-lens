from decimal import Decimal as D

import pytest

from app.calc.normalize import normalize_price
from app.calc.units import DEFAULT_REGISTRY as R
from app.domain.reasons import Flag, Reason


@pytest.mark.parametrize(
    ("raw", "code"),
    [
        ("шт", "796"),
        ("шт.", "796"),
        ("Штука", "796"),
        ("упак.", "778"),
        ("кг", "166"),
        ("руб./м2", "055"),
        ("за 1 кг", "166"),
        ("ГБ", "x-byte-gb"),
        ("стр.", "x-page"),
        ("г/м2", "x-gsm"),
    ],
)
def test_resolve_aliases(raw: str, code: str) -> None:
    unit = R.resolve(raw)
    assert unit is not None and unit.code == code


def test_unknown_unit() -> None:
    assert R.resolve("попугай") is None


def test_convert_attribute_units() -> None:
    assert R.convert(D(2), "ТБ", "ГБ") == D(2048)
    assert R.convert(D(1), "кг", "г") == D(1000)
    assert R.convert(D(1), "кг", "м") is None


def test_same_unit_no_steps() -> None:
    res = normalize_price(
        price=D(100),
        offer_unit_raw="шт",
        pack_qty=None,
        position_okei="796",
        position_unit_raw="шт",
    )
    assert res.price == D(100) and res.steps == []


def test_pack_divided_by_quantity() -> None:
    # UC-02: ручки по 50 шт в упаковке
    res = normalize_price(
        price=D(920),
        offer_unit_raw="упак",
        pack_qty=D(50),
        position_okei="796",
        position_unit_raw="шт",
    )
    assert res.price == D("18.4")
    assert res.steps[0].op == "pack"


def test_pack_without_quantity_is_incompatible() -> None:
    res = normalize_price(
        price=D(920),
        offer_unit_raw="упак",
        pack_qty=None,
        position_okei="796",
        position_unit_raw="шт",
    )
    assert not res.ok and res.reason == Reason.UNIT_INCOMPATIBLE


def test_mass_conversion() -> None:
    res = normalize_price(
        price=D(500), offer_unit_raw="кг", pack_qty=None, position_okei="163", position_unit_raw="г"
    )
    assert res.price == D("0.5")


def test_missing_unit_assumed() -> None:
    res = normalize_price(
        price=D(100),
        offer_unit_raw=None,
        pack_qty=None,
        position_okei="796",
        position_unit_raw="шт",
    )
    assert res.price == D(100)
    assert Flag.UNIT_ASSUMED in res.flags


def test_different_dimensions_incompatible() -> None:
    res = normalize_price(
        price=D(100),
        offer_unit_raw="кг",
        pack_qty=None,
        position_okei="796",
        position_unit_raw="шт",
    )
    assert res.reason == Reason.UNIT_INCOMPATIBLE


def test_unrecognized_offer_unit_incompatible() -> None:
    res = normalize_price(
        price=D(100),
        offer_unit_raw="бухта",
        pack_qty=None,
        position_okei="796",
        position_unit_raw="шт",
    )
    assert res.reason == Reason.UNIT_INCOMPATIBLE


def test_sale_unit_with_quantity() -> None:
    # Бухта кабеля 100 м за 16 704 ₽ → 167,04 ₽/м
    res = normalize_price(
        price=D(16704),
        offer_unit_raw="бухта 100 м",
        pack_qty=None,
        position_okei="006",
        position_unit_raw="м",
    )
    assert res.price == D("167.04")
    assert res.steps[0].op == "pack"


def test_sale_unit_pack_of_pieces() -> None:
    res = normalize_price(
        price=D(920),
        offer_unit_raw="упаковка 50 шт",
        pack_qty=None,
        position_okei="796",
        position_unit_raw="шт",
    )
    assert res.price == D("18.4")


@pytest.mark.parametrize(
    ("title", "okei", "expected"),
    [
        ("Кабель силовой ВВГ-Пнг(А)-LS 3x2,5ок(N,PE)-0,66 100м ТРТС - 100 м", "006", D("16704")),
        ("Кабель силовой ВВГ-Пнг(А)-LS 3x2,5ок(N,PE)-0,66 барабан ТРТС", "006", D("16704")),
        ("Ручка шариковая синяя, упаковка 50 шт", "796", D("16704")),
        ("Бумага А4 80 г/м2 500 листов", "796", D("16704")),  # г/м2 и листы — не фасовка штук
    ],
)
def test_pack_from_title(title: str, okei: str, expected: D) -> None:
    res = normalize_price(
        price=D(16704),
        offer_unit_raw=None,
        pack_qty=None,
        position_okei=okei,
        position_unit_raw=None,
        title=title,
    )
    assert res.price == expected


def test_plain_llm_unit_does_not_override_title_pack() -> None:
    """LLM ответил «м» и подтвердил фасовку 100 — делим."""
    res = normalize_price(
        price=D(16704),
        offer_unit_raw=None,
        pack_qty=None,
        position_okei="006",
        position_unit_raw="м",
        title="Кабель ВВГнг 3x2,5 100м",
        hint_unit="м",
        hint_pack_qty=D(100),
    )
    assert res.price == D("167.04")


def test_llm_sale_unit_with_quantity_used() -> None:
    res = normalize_price(
        price=D(8601), offer_unit_raw=None, pack_qty=None, position_okei="006",
        position_unit_raw="м", title="Кабель ВВГнг 3x2,5", hint_unit="бухта 50 м",
    )  # fmt: skip
    assert res.price == D("172.02")


def test_structured_unit_wins_over_hints() -> None:
    res = normalize_price(
        price=D(181), offer_unit_raw="м", pack_qty=None, position_okei="006",
        position_unit_raw="м", title="Кабель 100м", hint_unit="бухта 100 м",
    )  # fmt: skip
    assert res.price == D(181)


def test_title_pack_without_confirmation_is_ambiguous() -> None:
    from app.domain.reasons import Flag

    res = normalize_price(
        price=D("194.83"),
        offer_unit_raw=None,
        pack_qty=None,
        position_okei="006",
        position_unit_raw="м",
        title="Кабель ВВГ-Пнг(А) 3х2,5 мм² 100м",
    )
    assert res.price == D("194.83")
    assert res.pack_candidate == D(100)
    assert Flag.PACK_AMBIGUOUS in res.flags


def test_resolve_pack_by_peers() -> None:
    from app.calc.normalize import resolve_pack_ambiguity

    prices = {"a": D("172"), "b": D("181.8"), "coil": D(16704), "meter": D("194.83")}
    chosen = resolve_pack_ambiguity(prices, {"coil": D(100), "meter": D(100)})
    assert set(chosen) == {"coil"}
    assert chosen["coil"][0] == D("167.04")
    assert resolve_pack_ambiguity({"x": D(100), "y": D(5)}, {"y": D(10)}) == {}


def test_energy_and_pack_aliases() -> None:
    assert R.convert(D("3.2"), "Дж", "мДж") == D(3200)
    unit = R.resolve("упаковок")
    assert unit is not None and unit.code == "778"


def test_llm_pack_division_is_reversible() -> None:
    """Деление по данным LLM («бухта 100 м» из названия) помечается как обратимое."""
    res = normalize_price(
        price=D("171.52"), offer_unit_raw=None, pack_qty=None, position_okei="006",
        position_unit_raw="м", title="Кабель ВВГнг(А)-LS 3х2.5 бухта 100м", hint_unit="бухта 100 м",
    )  # fmt: skip
    assert res.price == D("1.7152")
    assert res.pack_candidate == D("0.01")


def test_structured_pack_is_not_reversible() -> None:
    res = normalize_price(
        price=D(920), offer_unit_raw="упаковка 50 шт", pack_qty=None,
        position_okei="796", position_unit_raw="шт",
    )  # fmt: skip
    assert res.pack_candidate is None


def test_peers_undo_wrong_llm_division() -> None:
    from app.calc.normalize import resolve_pack_ambiguity

    prices = {"a": D("155.96"), "b": D("181.58"), "c": D("124"), "coil": D("1.7152")}
    chosen = resolve_pack_ambiguity(prices, {"coil": D("0.01")})
    assert chosen["coil"][0] == D("171.52")
    assert "отменено" in chosen["coil"][1]
    # верное деление по LLM остаётся
    prices["coil"] = D("167.04")
    assert resolve_pack_ambiguity(prices, {"coil": D("0.01")}) == {}


def test_pack_position_set_in_title_is_ambiguous() -> None:
    """ЭТМ: «Бумага … 500л … - 5 упак», позиция в пачках — цена может быть за 5 пачек."""
    res = normalize_price(
        price=D("3685.55"), offer_unit_raw=None, pack_qty=None, position_okei="728",
        position_unit_raw="пачка", title="Бумага офисная А4, 80г/м2, 500л, марка А - 5 упак",
        hint_unit="упак",
    )  # fmt: skip
    assert res.price == D("3685.55")
    assert res.pack_candidate == D(5)
    assert Flag.PACK_AMBIGUOUS in res.flags


def test_piece_accepted_for_pack_position() -> None:
    res = normalize_price(
        price=D("332.70"), offer_unit_raw="ШТ", pack_qty=None,
        position_okei="728", position_unit_raw="пачка",
    )  # fmt: skip
    assert res.price == D("332.70")
    assert Flag.UNIT_ASSUMED in res.flags
