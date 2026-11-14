"""Разбор страниц ЕПЗ: синтетические фикстуры (устойчивость к смене CSS-классов) и урезанные
реальные страницы реестра контрактов (fixtures/html/eis_*.html, ноябрь 2026)."""

from datetime import date
from decimal import Decimal as D
from pathlib import Path

from app.sources.eis_adapter import (
    best_row,
    clean_name,
    clean_unit,
    has_penalties,
    last_page_number,
    parse_characteristics,
    parse_common_info,
    parse_object_rows,
    parse_search,
    parse_search_entries,
)

SEARCH = """
<div class="search-registry-entry-block">
  <div class="registry-entry__header-mid__number">
    <a href="/epz/contract/contractCard/common-info.html?reestrNumber=2770123456723000123">№ 2770123456723000123</a>
  </div>
</div>
<div class="search-registry-entry-block">
  <a href="/epz/contract/contractCard/common-info.html?reestrNumber=3500987654324000045">№ 3500987654324000045</a>
  <a href="/epz/contract/contractCard/common-info.html?reestrNumber=3500987654324000045">дубль</a>
</div>
"""

OBJECTS = """
<table class="blockInfo__table tableBlock">
  <thead><tr>
    <th>№</th><th>Наименование объекта закупки</th><th>Код позиции</th>
    <th>Единица измерения</th><th>Количество</th><th>Цена за единицу, ₽</th><th>Сумма, ₽</th>
  </tr></thead>
  <tbody>
    <tr><td>1</td><td>Бумага для офисной техники белая А4</td><td>17.12.14.129</td>
        <td>Пачка</td><td>200</td><td>389,50</td><td>77 900,00</td></tr>
    <tr><td>2</td><td>Картридж лазерный HP 85A (CE285A) черный</td><td>20.59.12.120</td>
        <td>Штука</td><td>10</td><td>1 480,00</td><td>14 800,00</td></tr>
  </tbody>
</table>
"""

COMMON = """
<section><h2>Общая информация</h2>
<span>Дата заключения контракта</span><span>12.03.2025</span></section>
<section><h2>Информация о поставщиках</h2>
<div>Наименование</div><div>ООО «КАНЦСНАБ»</div><div>ИНН: 7701234567</div></section>
"""


def test_parse_search_unique_numbers() -> None:
    assert parse_search(SEARCH, ".search-registry-entry-block") == [
        "2770123456723000123",
        "3500987654324000045",
    ]


def test_parse_search_falls_back_to_whole_page() -> None:
    assert parse_search(SEARCH, ".changed-class") == ["2770123456723000123", "3500987654324000045"]


def test_object_rows_by_headers() -> None:
    rows = parse_object_rows(OBJECTS)
    assert [(r["index"], r["unit_price"], r["unit"]) for r in rows] == [
        (1, D("389.50"), "Пачка"),
        (2, D("1480.00"), "Штука"),
    ]


def test_best_row_matches_position() -> None:
    row = best_row(parse_object_rows(OBJECTS), "картридж hp ce285a черный", 55)
    assert row is not None and row["index"] == 2
    assert best_row(parse_object_rows(OBJECTS), "стул офисный", 55) is None


def test_common_info() -> None:
    info = parse_common_info(COMMON)
    assert info["supplier_inn"] == "7701234567"
    assert info["supplier_name"] == "ООО «КАНЦСНАБ»"
    assert info["contract_date"] == date(2025, 3, 12)


def test_penalties() -> None:
    assert has_penalties("<p>Начисленные неустойки (штрафы, пени): 12 500,00 ₽</p>") is True
    assert has_penalties("<p>Информация о неустойках: отсутствует</p>") is False
    assert has_penalties("<p>Исполнение завершено</p>") is None


# ---------------------------------------------------------------- реальные страницы ЕПЗ (11.2026)

FIXTURES = Path(__file__).parent / "fixtures" / "html"


def _fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def test_real_search_entries_and_pages() -> None:
    html = _fixture("eis_search.html")
    entries = parse_search_entries(html, ".search-registry-entry-block")
    assert [e.reestr for e in entries][:2] == ["1770420620126000088", "1773600054926000009"]
    assert entries[0].more == 39 and entries[0].objects.startswith("Лицевой счет")
    assert entries[1].more == 0 and entries[1].objects == "Бумага для офисной техники"
    assert last_page_number(html) == 20


def test_real_object_row() -> None:
    (row,) = parse_object_rows(_fixture("eis_objects.html"))
    assert row["name"] == "Бумага для офисной техники"
    assert row["unit_price"] == D("327.75") and row["quantity"] == D("640")
    assert row["unit"] == "пач"
    assert row["subject_id"] == "811551913"
    assert "Формат А4" in row["characteristics"]


def test_real_characteristics() -> None:
    text = parse_characteristics(_fixture("eis_ktru.html"))
    assert "Формат: А4" in text
    assert "Масса бумаги площадью 1м2, г: 80 Грамм" in text
    assert "Количество листов в пачке: 500 Штука" in text


def test_clean_name_and_unit() -> None:
    assert (
        clean_name("2. Картонная заготовка №1. Товарный знак: отсутствует")
        == "Картонная заготовка №1"
    )
    assert clean_unit(" 2 000\n   ШТ ") == "ШТ"
    assert clean_unit("640 пач") == "пач"
