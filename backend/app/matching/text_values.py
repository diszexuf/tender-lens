"""Значения характеристик из текста предложения кодом — до LLM (FR-06.3).

Названия и карточки обычно содержат нужное явно: «А4, 80г/м2, 500л, 146%(CIE)»,
«Количество штук в упаковке: 100». Такие значения берутся из текста детерминированно,
LLM вызывается только для того, что код однозначно не нашёл.

Правила консервативные — сомнение означает не значение, а вопрос к LLM:
- фрагмент, начинающийся с названия характеристики: «Цвет чернил: синий»,
  «белизна CIE 146 %», ячейки таблицы «Материал жилы | Медь»;
  разные значения в разных фрагментах или несколько чисел в одном — неоднозначно;
- если фрагментов нет — по названию товара: единственное число в единице требования
  («80г/м2»), «3х2,5» для жил и сечения, требуемое значение перечисления («синяя»);
- да/нет и свободный текст (совместимость с моделями) — всегда LLM.
"""

from __future__ import annotations

import re
from decimal import Decimal
from functools import lru_cache

from app.calc.units import DEFAULT_REGISTRY, UnitRegistry
from app.domain.offer import ExtractedValue
from app.domain.quote_parser import clean_decimal, numbers_in, parse_quote, to_decimal, value_part
from app.domain.spec import AttrType, SpecAttribute
from app.domain.spec_review import DESIGNATION
from app.matching.comparators import CompareContext, _covers, _tokens, compare
from app.matching.text import DEFAULT_SYNONYMS, Synonyms, norm

_SEGMENTS = re.compile(r"\s*(?:[\n|;]+|,\s+|\.\s+(?=[А-ЯA-Z]))\s*")
_NUM_UNIT = re.compile(
    r"(?<![\w.,])(\d+(?:[.,]\d+)?)\s*(%|\"|″|[a-zа-яё²³/.]+(?:\s?[a-zа-яё²³/.]+)?)", re.I
)
_NUMBER = re.compile(r"(?<![\w.,])\d+(?:[.,]\d+)?")
_CROSS = re.compile(r"(?<![\w.,])\d+\s*[xх×*]\s*\d+(?:[.,]\d+)?", re.I)
_SEPARATOR = re.compile(r"^.*?[:—–=]\s*|^.*?\s-\s+")
_WORD = re.compile(r"[a-zа-яё0-9]+", re.I)
MAX_ENUM_WORDS = 4
MAX_EVIDENCE = 160


@lru_cache(maxsize=256)
def _split(text: str) -> tuple[str, ...]:
    """Фрагменты текста: строки, ячейки таблиц, части перечислений. Один текст разбирается
    для каждой характеристики и каждого числа ответа модели — разбиение кэшируется."""
    return tuple(s for s in _SEGMENTS.split(text) if s)


def _evidence(fragment: str) -> str:
    return " ".join(fragment.split())[:MAX_EVIDENCE]


def _stems(name: str) -> list[str]:
    words = [w.replace("ё", "е") for w in _WORD.findall(name.lower()) if len(w) >= 3]
    return [w[: max(4, len(w) - 2)] for w in words]


def _anchor(segment: str, stems: list[str]) -> int | None:
    """Позиция названия характеристики в начале слова; None — не названа."""
    low = segment.lower().replace("ё", "е")
    positions = []
    for stem in stems:
        m = re.search(rf"(?<![а-яa-z]){re.escape(stem)}", low)
        if m is None:
            return None
        positions.append(m.start())
    return min(positions) if positions else None


def _compatible(unit: str | None, wanted: str, units: UnitRegistry) -> Decimal | None:
    """Множитель перевода unit → wanted; None — единица другая."""
    if not unit:
        return None
    src, dst = units.resolve(unit), units.resolve(wanted)
    if src is not None and dst is not None and (f := units.factor(src, dst)) is not None:
        return f
    # «500 л» при требовании в листах — сокращение словесной единицы (не «м» для «мм²»)
    u, w = norm(unit).rstrip("."), norm(wanted).rstrip(".")
    if not (u.isalpha() and w.isalpha()):
        return None
    # «л» → «листов», «ядерный» → «ядер»
    same = (len(w) >= 4 and w.startswith(u)) or (len(w) >= 3 and u.startswith(w))
    return Decimal(1) if same else None


_TOLERANCE = re.compile(r"(\d+(?:[.,]\d+)?)\s*(?:\+\s*/\s*-|±|\+-)\s*(\d+(?:[.,]\d+)?)")


def _numeric(attr: SpecAttribute, fragment: str, units: UnitRegistry) -> ExtractedValue | None:
    if (m := _TOLERANCE.search(fragment)) and len(_NUMBER.findall(fragment)) == 2:
        # «Плотность: 81 +/-2 г/м2» — номинал с допуском: диапазон [79; 83]
        nominal, delta = to_decimal(m.group(1)), to_decimal(m.group(2))
        base = _numeric(attr, fragment[: m.start()] + m.group(1) + fragment[m.end() :], units)
        if base is None or base.number is None or nominal is None or delta is None:
            return None
        return base.model_copy(
            update={
                "number": None,
                "min": clean_decimal(nominal - delta),
                "max": clean_decimal(nominal + delta),
                "evidence": _evidence(fragment),
            }
        )
    if len(_NUMBER.findall(fragment)) > 1 and not _CROSS.search(fragment):
        return None  # «≥ 0,3 и < 0,5», «узел 0,7 мм, линия 0,35 мм» — к LLM
    try:
        parsed = parse_quote(attr.name, fragment, True, units)
    except ValueError:
        return None
    if parsed.type not in (AttrType.NUMBER, AttrType.RANGE):
        return None
    if attr.unit and parsed.unit and _compatible(parsed.unit, attr.unit, units) is None:
        return None
    return ExtractedValue(
        name=attr.name, number=parsed.value_number, min=parsed.value_min,
        max=parsed.value_max, unit=parsed.unit or attr.unit,
        source="code", evidence=_evidence(fragment),
    )  # fmt: skip


_LOWER_BOUND = re.compile(
    r"(?<![a-zа-я])(?:min|мин\w*|не менее|не меньше|от)(?![a-zа-я])|≥|>=", re.I
)
_UPPER_BOUND = re.compile(
    r"(?<![a-zа-я])(?:max|макс\w*|не более|не больше|до)(?![a-zа-я])|≤|<=", re.I
)


def _bound(value: ExtractedValue | None, fragment: str) -> ExtractedValue | None:
    """«Длина, min ≥ 26 мм» — нижняя граница, «Длина, max ≤ 30 мм» — верхняя."""
    if value is None or value.number is None:
        return value
    lower, upper = bool(_LOWER_BOUND.search(fragment)), bool(_UPPER_BOUND.search(fragment))
    if lower == upper:
        return value
    if lower:
        return value.model_copy(update={"number": None, "min": value.number})
    return value.model_copy(update={"number": None, "max": value.number})


def _enum_value(raw: str) -> ExtractedValue | None:
    value = raw.strip(" .:-—")
    words = value.split()
    if not 0 < len(words) <= MAX_ENUM_WORDS:
        return None
    return ExtractedValue(name="", text=value, source="code")


def _from_segments(
    attr: SpecAttribute, text: str, units: UnitRegistry
) -> list[ExtractedValue] | None:
    """Значения из фрагментов, где названа характеристика. None — характеристика названа,
    но значение неоднозначно («≥ 0,3 и < 0,5»): угадывать по названию товара нельзя."""
    stems = _stems(attr.name)
    numeric = attr.type in (AttrType.NUMBER, AttrType.RANGE)
    segments = _split(text)
    found: dict[tuple[object, ...], ExtractedValue] = {}
    ambiguous = False
    for i, seg in enumerate(segments):
        pos = _anchor(seg, stems)
        if pos is None:
            continue
        fragment = seg[pos:]
        value: ExtractedValue | None = None
        if numeric:
            # ячейка таблицы «Мощность, Вт | 800»: число в одном из следующих фрагментов
            j = i + 1
            while not re.search(r"\d", fragment) and j < min(i + 3, len(segments)):
                fragment, j = f"{fragment} {segments[j]}", j + 1
            value = _bound(_numeric(attr, fragment, units), fragment)
            ambiguous = ambiguous or (value is None and bool(re.search(r"\d", fragment)))
        elif pos == 0 or (":" in seg and pos < seg.index(":")):
            # перечисление — фрагмент начинается с названия («Цвет чернил: синий») или
            # название в подписи до двоеточия («Вид материала обивки: Кожа натуральная»)
            if pos > 0:
                rest = seg.split(":", 1)[1]
            elif _SEPARATOR.match(fragment):
                rest = _SEPARATOR.sub("", fragment, count=1)
            else:  # «Формат A4 (210 x 297 мм)»
                rest = value_part(attr.name, fragment)
                rest = "" if norm(rest) == norm(fragment) else rest
            if not rest.strip() and i + 1 < len(segments):
                # ячейка таблицы «Материал жилы | Медь». Не значение: подпись следующей
                # ячейки («Цвет: | Производитель:») и числа другой характеристики
                # («Формат | Яркость ISO 94%»), если это не требуемое значение
                nxt = segments[i + 1]
                label = nxt.rstrip().endswith(":")
                if not label and (
                    not re.search(r"\d", nxt) or any(norm(v) in norm(nxt) for v in attr.values)
                ):
                    rest = nxt
            value = _enum_value(rest)
            if value is not None:
                value.evidence = _evidence(seg if rest in seg else f"{seg} {rest}")
        if value is not None:
            value.name = attr.name
            key = (value.number, value.min, value.max, norm(value.text))
            found[key] = value
    return None if ambiguous and not found else list(found.values())


def _join_bounds(found: list[ExtractedValue]) -> list[ExtractedValue]:
    """«Длина, min ≥ 26 мм; Длина, max ≤ 30 мм» (характеристики КТРУ в контрактах ЕИС) —
    одно значение-диапазон [26; 30], а не два разных значения."""
    lo = next((v for v in found if v.number is None and v.min is not None and v.max is None), None)
    hi = next((v for v in found if v.number is None and v.max is not None and v.min is None), None)
    if lo is None or hi is None or lo is hi or (lo.unit or "") != (hi.unit or ""):
        return found
    assert lo.min is not None and hi.max is not None
    if lo.min > hi.max:
        return found
    return [
        ExtractedValue(
            name=lo.name, min=lo.min, max=hi.max, unit=lo.unit, source="code",
            evidence=_evidence(f"{lo.evidence or ''}; {hi.evidence or ''}"),
        )
    ]  # fmt: skip


def _from_title(
    attr: SpecAttribute, title: str, units: UnitRegistry, synonyms: Synonyms
) -> ExtractedValue | None:
    if attr.type == AttrType.ENUM:
        tokens = _tokens(title)
        for expected in attr.values:
            for variant in {expected, *synonyms.expand(expected)}:
                if len(norm(variant)) >= 3 and _covers(_tokens(variant), tokens):
                    return ExtractedValue(
                        name=attr.name, text=expected, source="code", evidence=_evidence(title)
                    )
        return None
    name = attr.name.lower()
    if ("жил" in name or "сечен" in name) and (m := _CROSS.search(title)):
        cross = _numeric(attr, m.group(0) + (" мм²" if "сечен" in name else ""), units)
        return cross.model_copy(update={"evidence": _evidence(title)}) if cross else None
    if not attr.unit:
        return None
    values: set[Decimal] = set()
    for m in _NUM_UNIT.finditer(title):
        raw_unit = m.group(2).strip(" .")
        factor = _compatible(raw_unit, attr.unit, units)
        if factor is None:  # «146%(CIE)», «500л,» — единица захватила лишнее
            first = re.split(r"[\s(,]", raw_unit)[0]
            factor = _compatible(first, attr.unit, units) if first != raw_unit else None
        if factor is not None and (value := to_decimal(m.group(1))) is not None:
            values.add(clean_decimal(value * factor))
    if len(values) == 1:
        return ExtractedValue(
            name=attr.name, number=values.pop(), unit=attr.unit, source="code",
            evidence=_evidence(title),
        )  # fmt: skip
    return None


_SUPERSCRIPT = re.compile(r"(?<![а-яa-z])(мм|см|дм|м)\s*[\n|]\s*([23])\s*(?=[\n|])", re.I)


def prepare_text(text: str) -> str:
    """«Сечение жилы, мм | 2 | 2,5» (верхний индекс в разметке) → «мм² | 2,5»."""
    return _SUPERSCRIPT.sub(lambda m: m.group(1) + ("²" if m.group(2) == "2" else "³"), text)


def _compact(text: str) -> str:
    return re.sub(r"[^a-zа-я0-9]", "", norm(text))


def _designation_in(attr: SpecAttribute, text: str) -> ExtractedValue | None:
    """Обозначение из наименования позиции («SDS-plus») есть в тексте: «SDS Plus», «SDS+»."""
    compact = _compact(text.replace("+", "plus"))
    for expected in attr.values:
        if _compact(expected.replace("+", "plus")) in compact:
            return ExtractedValue(name=attr.name, text=expected, source="code")
    return None


def values_from_text(
    attributes: list[SpecAttribute],
    text: str,
    units: UnitRegistry = DEFAULT_REGISTRY,
    synonyms: Synonyms = DEFAULT_SYNONYMS,
) -> dict[str, ExtractedValue]:
    """Значения, однозначно найденные в тексте; остальные характеристики — для LLM."""
    text = prepare_text(text)
    title = text.split("\n", 1)[0]
    out: dict[str, ExtractedValue] = {}
    for attr in attributes:
        if attr.type not in (AttrType.NUMBER, AttrType.RANGE, AttrType.ENUM):
            continue
        if attr.name == DESIGNATION:
            if hit := _designation_in(attr, text):
                out[attr.name] = hit
            continue
        found = _from_segments(attr, text, units)
        if found is not None and len(found) == 2:
            found = _join_bounds(found)
        if found is None or len(found) > 1:
            continue  # неоднозначно или разные значения одной характеристики — решает LLM
        value = found[0] if found else _from_title(attr, title, units, synonyms)
        if value is not None:
            out[attr.name] = value
    return out


# ------------------------------------------------------------ проверка ответа LLM

_NUM_UNIT_LOOSE = re.compile(
    r"(\d+(?:[.,]\d+)?)\s*-?\s*(%|\"|''|″|[a-zа-яё²³/.]+(?:\s?[a-zа-яё²³/.]+)?)", re.I
)


def _number_supported(
    attr: SpecAttribute, n: Decimal, unit: str | None, text: str, units: UnitRegistry
) -> bool:
    """Число модели есть в тексте как значение этой характеристики: с её единицей,
    во фрагменте с её названием или в «3х2,5» у кабеля. «Ryzen 7» — не «7 ядер»."""
    if n not in numbers_in(text):
        return False
    if not attr.unit:
        return True
    wanted = [u for u in (unit, attr.unit) if u]
    for m in _NUM_UNIT_LOOSE.finditer(text):
        if to_decimal(m.group(1)) != n:
            continue
        raw = m.group(2).strip(" .")
        first = re.split(r"[\s(,]", raw)[0]
        if any(_compatible(u, w, units) is not None for u in {raw, first} for w in wanted):
            return True
    name = attr.name.lower()
    if ("жил" in name or "сечен" in name) and any(
        n in numbers_in(m.group(0)) for m in _CROSS.finditer(text)
    ):
        return True
    stems = _stems(attr.name)
    segments = _split(text)
    for i, seg in enumerate(segments):
        if (pos := _anchor(seg, stems)) is not None:
            near = seg[pos:] + (" " + segments[i + 1] if i + 1 < len(segments) else "")
            if n in numbers_in(near):
                return True
    return False


def _text_supported(value: str, text: str, synonyms: Synonyms) -> bool:
    """Все слова значения есть в тексте (с окончаниями и синонимами); «ож», «С» —
    обрывки обозначений, а не значения."""
    if not any(len(w) >= 3 for w in _WORD.findall(norm(value))):
        return False
    tokens = _tokens(text)
    for word in _tokens(value):
        if len(word) < 3 or any(c.isdigit() for c in word):
            continue  # модели и коды проверяет сравнение
        variants = {word, *synonyms.expand(word)}
        if not any(_covers(_tokens(v), tokens) for v in variants):
            return False
    return True


_LABEL_MAX_WORDS = 4


def _is_label(segment: str, stems: list[str]) -> bool:
    """Фрагмент — подпись этой характеристики: начинается с её названия («Материал корпуса»),
    называет её до двоеточия или это короткая подпись ячейки («Основной материал»).
    «Сертификаты и материалы» — название раздела, а не характеристики."""
    pos = _anchor(segment, stems)
    if pos is None:
        return False
    if pos == 0 or (":" in segment and pos < segment.index(":")):
        return True
    words = _WORD.findall(segment.lower())
    return len(words) <= _LABEL_MAX_WORDS and "и" not in words


def _where(
    attr: SpecAttribute, value: str, text: str, synonyms: Synonyms
) -> tuple[str, bool, bool]:
    """Где в тексте стоит значение модели: (фрагмент, привязан ли к характеристике,
    стоит ли только отдельными ячейками).
    Привязан — в названии товара, во фрагменте с подписью характеристики («Материал
    корпуса: пластик») или в следующей за подписью ячейке таблицы.
    Отдельная ячейка — фрагмент из одного этого значения («PDF», «Пластик»): её смысл
    задаёт подпись перед ней, и если подпись чужая, значение относится не к этой
    характеристике."""
    words = {w for w in _tokens(value) if len(w) >= 3 and not any(c.isdigit() for c in w)}
    if not words:
        return "", True, False  # коды и модели проверяет сравнение

    def has(fragment: str) -> bool:
        tokens = _tokens(fragment)
        return all(
            any(_covers(_tokens(v), tokens) for v in {w, *synonyms.expand(w)}) for w in words
        )

    segments = _split(text)
    title = text.split("\n", 1)[0]
    if has(title):
        return _evidence(title), True, False
    stems = _stems(attr.name)
    size = len(_tokens(value))
    anywhere, only_cells = "", True
    for i, seg in enumerate(segments):
        label = _is_label(seg, stems)
        if label and has(seg):
            return _evidence(seg), True, False
        if label and i + 1 < len(segments) and has(segments[i + 1]):
            return _evidence(f"{seg} {segments[i + 1]}"), True, False
        if has(seg):
            anywhere = anywhere or _evidence(seg)
            only_cells = only_cells and len(_tokens(seg)) <= size
    return anywhere, False, bool(anywhere) and only_cells


def _foreign_cell(
    attr: SpecAttribute, value: ExtractedValue, text: str, synonyms: Synonyms
) -> tuple[str, bool]:
    """(фрагмент, отбросить ли значение). Отбрасывается значение, которое противоречит
    требованию и стоит только отдельной ячейкой под чужой подписью: «PDF» в разделе
    «Сертификаты и материалы» — не материал скрепок. Значения в связном тексте
    («чернила красные»), совпадающие с требованием и модели с цифрами остаются."""
    assert value.text is not None
    where, tied, cell = _where(attr, value.text, text, synonyms)
    if tied or not cell or attr.type != AttrType.ENUM or any(c.isdigit() for c in value.text):
        return where, False
    matched = compare(attr, value, CompareContext(synonyms=synonyms)).status == "match"
    return where, not matched


def _belongs_to_other(
    attr: SpecAttribute, value: ExtractedValue, others: list[SpecAttribute], synonyms: Synonyms
) -> bool:
    """Модель отдала значение другой характеристики позиции: «SDS Plus» (обозначение)
    как значение «Сетевой». Своему требованию оно не соответствует, чужому — да."""
    ctx = CompareContext(synonyms=synonyms)
    if compare(attr, value, ctx).status == "match":
        return False
    compact = _compact((value.text or "").replace("+", "plus"))
    return any(
        o.name != attr.name
        and o.type in (AttrType.ENUM, AttrType.TEXT)
        and (
            compare(o, value.model_copy(update={"name": o.name}), ctx).status == "match"
            or any(_compact(v.replace("+", "plus")) == compact for v in o.values)
        )
        for o in others
    )


def ground_llm_value(
    attr: SpecAttribute,
    value: ExtractedValue,
    text: str,
    units: UnitRegistry = DEFAULT_REGISTRY,
    synonyms: Synonyms = DEFAULT_SYNONYMS,
    others: list[SpecAttribute] | None = None,
) -> ExtractedValue:
    """Отбрасывает значения модели, которых нет в тексте предложения: модель подставляет
    их из единицы («упак» → 1), из требований или додумывает («аккумуляторный»)."""
    text = prepare_text(text)
    if attr.name == DESIGNATION:  # решает код по тексту, а не модель (она путает с артикулом)
        return value.model_copy(update={"text": None})
    numbers = [n for n in (value.number, value.min, value.max) if n is not None]
    if numbers and not all(_number_supported(attr, n, value.unit, text, units) for n in numbers):
        value = value.model_copy(update={"number": None, "min": None, "max": None, "unit": None})
    evidence: str | None = None
    if value.text and attr.type in (AttrType.ENUM, AttrType.TEXT):
        if not _text_supported(value.text, text, synonyms) or _belongs_to_other(
            attr, value, others or [], synonyms
        ):
            value = value.model_copy(update={"text": None})
        else:
            evidence, drop = _foreign_cell(attr, value, text, synonyms)
            if drop:
                value = value.model_copy(update={"text": None})
    return value.model_copy(update={"source": "llm", "evidence": evidence or None})
