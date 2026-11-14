"""Единицы измерения: справочник ОКЕИ, синонимы и пересчёт.

Используется в двух местах:
- единица позиции и оффера (шт, упак, кг, м², мес) — для нормализации цены (FR-07.1);
- единицы характеристик (стр, ГБ, мм, Вт) — для сравнения числовых атрибутов (FR-06.4).

Пересчёт возможен только внутри одной размерности: значение × factor = значение в базовой
единице размерности. Справочник загружается в БД при первом запуске и редактируется через UI
(этап 3); здесь — значения по умолчанию.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class Unit:
    code: str  # код ОКЕИ или внутренний код для единиц характеристик (префикс «x-»)
    name: str
    short: str
    dimension: str
    factor: Decimal  # множитель к базовой единице размерности
    aliases: tuple[str, ...] = ()


D = Decimal

# fmt: off
DEFAULT_UNITS: tuple[Unit, ...] = (
    # Штучные
    Unit("796", "штука", "шт", "count", D(1), ("шт", "штука", "штуки", "штук", "pcs", "pc", "ед", "единица", "единиц")),
    Unit("715", "пара", "пар", "count", D(2), ("пара", "пар", "пары")),
    Unit("641", "дюжина", "дюжина", "count", D(12), ("дюжина",)),
    Unit("797", "сто штук", "100 шт", "count", D(100), ("100 шт", "сто штук")),
    Unit("798", "тысяча штук", "1000 шт", "count", D(1000), ("1000 шт", "тыс шт", "тыс. шт")),
    # Фасовки: количество в упаковке задаётся отдельно (pack_qty)
    Unit("778", "упаковка", "упак", "pack", D(1), ("упак", "упаковка", "уп", "упаковки", "упаковок", "уп.", "pack")),
    Unit("728", "пачка", "пач", "pack", D(1), ("пачка", "пач", "пачки", "пачек")),
    Unit("736", "рулон", "рул", "pack", D(1), ("рулон", "рул", "рулона")),
    Unit("704", "набор", "набор", "set", D(1), ("набор", "наб", "наборы")),
    Unit("839", "комплект", "компл", "set", D(1), ("комплект", "компл", "кмпл", "к-т", "комплекта")),
    # Масса (база — грамм)
    Unit("163", "грамм", "г", "mass", D(1), ("г", "гр", "грамм", "g")),
    Unit("166", "килограмм", "кг", "mass", D(1000), ("кг", "килограмм", "kg")),
    Unit("168", "тонна", "т", "mass", D(1_000_000), ("т", "тонна", "тонн")),
    # Объём (база — миллилитр)
    Unit("111", "миллилитр", "мл", "volume", D(1), ("мл", "миллилитр", "ml", "см3", "куб. см")),
    Unit("112", "литр", "л", "volume", D(1000), ("л", "литр", "литров", "l")),
    Unit("113", "кубический метр", "м³", "volume", D(1_000_000), ("м3", "м³", "куб. м", "куб.м", "кубометр")),
    # Длина (база — миллиметр)
    Unit("003", "миллиметр", "мм", "length", D(1), ("мм", "миллиметр", "mm")),
    Unit("004", "сантиметр", "см", "length", D(10), ("см", "сантиметр", "cm")),
    Unit("006", "метр", "м", "length", D(1000), ("м", "метр", "метров", "м.", "m", "пог. м", "п.м", "пм")),
    Unit("008", "километр", "км", "length", D(1_000_000), ("км", "километр")),
    # Площадь (база — квадратный метр)
    Unit("055", "квадратный метр", "м²", "area", D(1), ("м2", "м²", "кв. м", "кв.м", "квм")),
    Unit("x-mm2", "квадратный миллиметр", "мм²", "area", D("0.000001"), ("мм2", "мм²", "кв. мм", "кв.мм", "квмм")),
    # Время (база — час)
    Unit("356", "час", "ч", "time", D(1), ("ч", "час", "часов", "чел.-ч", "чел-час")),
    Unit("359", "сутки", "сут", "time", D(24), ("сут", "сутки", "день", "дней")),
    Unit("362", "месяц", "мес", "month", D(1), ("мес", "месяц", "месяцев")),
    Unit("366", "год", "г.", "month", D(12), ("год", "лет")),
    # Услуги и прочее
    Unit("876", "условная единица", "усл. ед", "service", D(1), ("усл. ед", "усл ед", "услуга", "работа")),
    Unit("625", "лист", "лист", "sheet", D(1), ("лист", "листов", "листа")),

    # ----- единицы характеристик (не ОКЕИ, используются в атрибутах) -----
    Unit("x-page", "страница", "стр", "pages", D(1), ("стр", "страниц", "страницы", "страница", "с.", "pages", "page", "отпечатков", "копий")),
    Unit("x-byte-mb", "мегабайт", "МБ", "data", D(1), ("мб", "mb", "мегабайт")),
    Unit("x-byte-gb", "гигабайт", "ГБ", "data", D(1024), ("гб", "gb", "гигабайт", "гбайт")),
    Unit("x-byte-tb", "терабайт", "ТБ", "data", D(1_048_576), ("тб", "tb", "терабайт", "тбайт")),
    Unit("x-w", "ватт", "Вт", "power", D(1), ("вт", "w", "ватт")),
    Unit("x-kw", "киловатт", "кВт", "power", D(1000), ("квт", "kw", "киловатт")),
    Unit("x-hz", "герц", "Гц", "frequency", D(1), ("гц", "hz", "герц")),
    Unit("x-khz", "килогерц", "кГц", "frequency", D(1000), ("кгц", "khz")),
    Unit("x-mhz", "мегагерц", "МГц", "frequency", D(1_000_000), ("мгц", "mhz")),
    Unit("x-ghz", "гигагерц", "ГГц", "frequency", D(1_000_000_000), ("ггц", "ghz")),
    Unit("x-v", "вольт", "В", "voltage", D(1), ("в", "v", "вольт")),
    Unit("x-gsm", "грамм на кв. метр", "г/м²", "density", D(1), ("г/м2", "г/м²", "г/кв.м", "gsm", "гр/м2", "гр/м²", "гр/кв.м", "г/м", "гр/м", "г/кв. м", "г./кв.м", "гр./м.кв", "г/м.кв", "г/м кв")),
    Unit("x-inch", "дюйм", "\"", "inch", D(1), ("дюйм", "дюймов", "дюйма", "\"", "''", "″", "”", "inch", "in")),
    Unit("x-percent", "процент", "%", "percent", D(1), ("%", "процент", "процентов")),
    Unit("x-db", "децибел", "дБ", "db", D(1), ("дб", "db", "децибел")),
    Unit("x-mj", "миллиджоуль", "мДж", "energy", D(1), ("мдж", "mj", "миллиджоуль")),
    Unit("x-j", "джоуль", "Дж", "energy", D(1000), ("дж", "j", "джоуль", "джоулей")),
    Unit("x-kj", "килоджоуль", "кДж", "energy", D(1_000_000), ("кдж", "kj")),
    Unit("x-k", "кельвин", "К", "temperature", D(1), ("к", "k", "кельвин")),
    Unit("x-lm", "люмен", "лм", "luminous", D(1), ("лм", "lm", "люмен")),
    Unit("x-a", "ампер", "А", "current", D(1), ("а", "a", "ампер")),
    Unit("x-mah", "миллиампер-час", "мА·ч", "charge", D(1), ("мач", "mah", "ма·ч", "ма ч")),
    Unit("x-ah", "ампер-час", "А·ч", "charge", D(1000), ("ач", "ah", "а·ч")),
    Unit("x-kwh", "киловатт-час", "кВт·ч", "energy_kwh", D(1), ("квт·ч", "квтч", "kwh")),
)
# fmt: on


def normalize_unit_text(raw: str) -> str:
    text = raw.strip().lower().replace("ё", "е")
    text = re.sub(r"\s+", " ", text)
    return text.rstrip(".") if text not in ("г.", "с.", "м.", "уп.") else text


@dataclass
class UnitRegistry:
    units: tuple[Unit, ...] = DEFAULT_UNITS
    _by_code: dict[str, Unit] = field(init=False, default_factory=dict)
    _by_alias: dict[str, Unit] = field(init=False, default_factory=dict)

    def __post_init__(self) -> None:
        for unit in self.units:
            self._by_code[unit.code] = unit
            for alias in (unit.short, unit.name, *unit.aliases):
                self._by_alias.setdefault(normalize_unit_text(alias), unit)

    def by_code(self, code: str | None) -> Unit | None:
        return self._by_code.get(code) if code else None

    def resolve(self, raw: str | None) -> Unit | None:
        """Ищет единицу по тексту: «шт.», «упак», «Кг», «руб./м2» → м²."""
        if not raw:
            return None
        text = normalize_unit_text(raw)
        if text in self._by_alias:
            return self._by_alias[text]
        # «руб./шт», «за 1 кг», «цена за упаковку» — берём хвост после «/» или «за»
        tail = re.split(r"/|\bза\b", text)[-1].strip(" .1")
        if tail != text and tail in self._by_alias:
            return self._by_alias[tail]
        stripped = text.rstrip(".")
        return self._by_alias.get(stripped)

    def factor(self, src: Unit, dst: Unit) -> Decimal | None:
        """Множитель: 1 src = factor dst. None — если размерности разные."""
        if src.dimension != dst.dimension:
            return None
        return src.factor / dst.factor

    def convert(self, value: Decimal, src_raw: str | None, dst_raw: str | None) -> Decimal | None:
        """Пересчёт значения характеристики между единицами («2 ТБ» → 2048 ГБ)."""
        if not src_raw or not dst_raw:
            return None
        src, dst = self.resolve(src_raw), self.resolve(dst_raw)
        if src is None or dst is None:
            return None
        f = self.factor(src, dst)
        return value * f if f is not None else None


_QTY_UNIT = re.compile(r"(\d+(?:[.,]\d+)?)\s*([^\d\s][^\d]*)$")


def parse_sale_unit(raw: str | None, registry: UnitRegistry) -> tuple[Unit, Decimal] | None:
    """«бухта 100 м» → (метр, 100); «упаковка 50 шт» → (штука, 50). None — если не разобрать."""
    if not raw:
        return None
    match = _QTY_UNIT.search(normalize_unit_text(raw))
    if not match:
        return None
    unit = registry.resolve(match.group(2))
    if unit is None:
        return None
    qty = Decimal(match.group(1).replace(",", "."))
    return (unit, qty) if qty > 0 else None


_TITLE_QTY = re.compile(r"(?<![\d.,x×х])(\d+(?:[.,]\d+)?)\s?([a-zа-яё²³]+\.?)", re.IGNORECASE)


def pack_from_title(title: str, position_unit: Unit, registry: UnitRegistry) -> Decimal | None:
    """Количество единиц позиции в товаре по названию: «Кабель … 100м» → 100 (для позиции в м).

    Берутся числа с единицей той же размерности, что у позиции; при нескольких разных
    значениях результат неоднозначен — None.
    """
    found: set[Decimal] = set()
    for number, unit_text in _TITLE_QTY.findall(title.lower().replace("ё", "е")):
        unit = registry.resolve(unit_text)
        if unit is None or unit.dimension != position_unit.dimension:
            continue
        factor = registry.factor(unit, position_unit)
        if factor is None:
            continue
        found.add(Decimal(number.replace(",", ".")) * factor)
    if len(found) != 1:
        return None
    qty = found.pop()
    return qty if qty > 1 else None


DEFAULT_REGISTRY = UnitRegistry()
