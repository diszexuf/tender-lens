"""Нормализация текста и словарь синонимов."""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field

_PUNCT = re.compile(r"[^\w\s./,+-]", re.UNICODE)
_SPACES = re.compile(r"\s+")
_WORDS = re.compile(r"[a-zа-я0-9]+")
# Латинские буквы, неотличимые от кириллических: «A4» и «А4», «ВВГнг(A)» и «ВВГнг(А)»
_LOOKALIKE = str.maketrans("aceopxkmtbhy", "асеорхкмтвну")
_LATIN = re.compile(r"[a-z]")


def _homoglyphs(word: re.Match[str]) -> str:
    w = word.group(0)
    latin = set(_LATIN.findall(w))
    return w.translate(_LOOKALIKE) if latin and latin <= set("aceopxkmtbhy") else w


def norm(text: str | None) -> str:
    """Нижний регистр, ё → е, без лишней пунктуации и пробелов."""
    if not text:
        return ""
    t = text.lower().replace("ё", "е")
    t = _PUNCT.sub(" ", t)
    t = _WORDS.sub(_homoglyphs, t)
    return _SPACES.sub(" ", t).strip()


@dataclass
class Synonyms:
    """Группы синонимов: любое слово группы приводится к каноническому (первому)."""

    groups: list[list[str]] = field(default_factory=list)
    _canon: dict[str, str] = field(init=False, default_factory=dict)

    def __post_init__(self) -> None:
        for group in self.groups:
            canonical = norm(group[0])
            for word in group:
                self._canon[norm(word)] = canonical

    def canonical(self, text: str | None) -> str:
        n = norm(text)
        return self._canon.get(n, n)

    def expand(self, text: str | None) -> set[str]:
        """Каноническая форма и все синонимы группы."""
        canon = self.canonical(text)
        return {w for w, c in self._canon.items() if c == canon} | {canon}

    def same(self, a: str | None, b: str | None) -> bool:
        return bool(a and b) and self.canonical(a) == self.canonical(b)

    @classmethod
    def from_pairs(cls, groups: Iterable[Iterable[str]]) -> Synonyms:
        return cls(groups=[list(g) for g in groups])


# Стартовые синонимы (загружаются в справочник, редактируются через UI на этапе 3)
DEFAULT_SYNONYM_GROUPS: list[list[str]] = [
    [
        "ресурс",
        "количество страниц",
        "ресурс печати",
        "yield",
        "ресурс картриджа",
        "кол-во страниц",
    ],
    ["цвет", "цвет печати", "color", "colour", "цвет чернил", "цвет тонера"],
    ["черный", "black", "bk", "чёрный", "монохромный", "ч/б"],
    ["голубой", "cyan", "синий", "c"],
    ["пурпурный", "magenta", "m", "малиновый"],
    ["желтый", "yellow", "y", "жёлтый"],
    ["белый", "white"],
    ["оригинальный", "original", "оригинал", "oem"],
    ["экокожа", "искусственная кожа", "кожа искусственная", "кожзам", "кожзаменитель", "иск. кожа"],
    ["совместимый", "compatible", "аналог", "неоригинальный"],
    ["формат", "размер листа", "формат бумаги"],
    ["плотность", "плотность бумаги", "масса бумаги"],
    ["белизна", "белизна cie"],
    ["количество листов", "листов в пачке", "листов"],
    ["толщина линии", "толщина линии письма", "толщина"],
    ["объем", "объём", "емкость", "ёмкость", "capacity"],
    ["оперативная память", "озу", "ram", "объем оперативной памяти"],
    ["накопитель", "ssd", "диск", "объем накопителя"],
    ["диагональ", "диагональ экрана", "размер экрана"],
    ["процессор", "cpu"],
    ["да", "есть", "yes", "true", "имеется"],
    ["нет", "no", "false", "отсутствует"],
]

DEFAULT_SYNONYMS = Synonyms(groups=DEFAULT_SYNONYM_GROUPS)

# Общее значение → основы частных: требование «металлические» выполняют «оцинкованные»,
# «стальные»; обратное (требуется «оцинкованные», предложены «металлические») — нет
HYPERNYMS: dict[str, tuple[str, ...]] = {
    "металл": ("оцинк", "цинк", "никел", "стал", "медн", "латун", "алюмин", "хромир", "желез"),
    "пластик": ("пластмас", "полипропилен", "полиэтилен", "пвх", "abs", "поликарбонат"),
}
