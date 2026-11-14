"""Сравнение характеристик оффера с требованиями спецификации кодом (FR-06.4).

LLM только извлекает значения из текста оффера (этап AttributeExtractor),
а решение «совпадает / не совпадает / неизвестно» принимает этот модуль.

Для перечислимых и текстовых требований используется и название товара:
- требуемое значение есть в названии — совпадение (название надёжнее извлечения);
- в названии другой код того же вида («КГВВнг» вместо «ВВГнг», нет «LS») — несовпадение.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal

from rapidfuzz import fuzz

from app.calc.units import DEFAULT_REGISTRY, UnitRegistry
from app.domain.offer import AttributeMatch, ExtractedValue, MatchStatus
from app.domain.spec import AttrType, Operator, SpecAttribute
from app.matching.text import DEFAULT_SYNONYMS, HYPERNYMS, Synonyms, norm

# Несовпадение фиксируем только для коротких значений (до N слов): длинное описание
# сравнивается ненадёжно — для него «неизвестно»
TEXT_MISMATCH_MAX_WORDS = 4
ENUM_TYPO_THRESHOLD = 92  # fuzz.ratio: опечатки в одном значении
CODE_SIMILAR_THRESHOLD = 70  # похожий, но другой код в названии («кгввнг» ~ «ввгнг»)
NUMBER_TOLERANCE = Decimal("0.005")  # 0,5 % для равенства чисел

_TOKEN = re.compile(r"[\w.+-]+", re.UNICODE)
_RAW_TOKEN = re.compile(r"[A-Za-zА-Яа-яЁё0-9]+")


@dataclass
class CompareContext:
    units: UnitRegistry = field(default_factory=lambda: DEFAULT_REGISTRY)
    synonyms: Synonyms = field(default_factory=lambda: DEFAULT_SYNONYMS)


def compare(
    attr: SpecAttribute,
    value: ExtractedValue | None,
    ctx: CompareContext,
    *,
    title: str | None = None,
) -> AttributeMatch:
    requirement = attr.describe()
    textual = attr.type in (AttrType.ENUM, AttrType.TEXT)
    expected = _expected_values(attr)

    # Название — главное описание товара: сначала совпадение, затем противоречие с ним
    if title and textual and (hit := _value_in_title(expected, title, ctx)):
        result = _result(attr, MatchStatus.MATCH, requirement, hit, f"«{hit}» указано в названии")
        return result.model_copy(update={"source": "title", "evidence": title[:160]})
    if title and textual and (conflict := _title_conflict(expected, title)):
        actual = value.text if value is not None and value.text else None
        result = _result(attr, MatchStatus.MISMATCH, requirement, actual, conflict)
        return result.model_copy(update={"source": "title", "evidence": title[:160]})

    if value is None or value.is_empty:
        result = _result(
            attr, MatchStatus.UNKNOWN, requirement, None, "значение не указано в предложении"
        )
    else:
        match attr.type:
            case AttrType.NUMBER:
                result = _compare_number(attr, value, ctx, requirement)
            case AttrType.RANGE:
                result = _compare_range(attr, value, ctx, requirement)
            case AttrType.BOOL:
                result = _compare_bool(attr, value, ctx, requirement)
            case _:
                result = _compare_values(attr, expected, value, ctx, requirement)

    if value is not None and not value.is_empty:
        result = result.model_copy(update={"source": value.source, "evidence": value.evidence})
    return result


# ---------------------------------------------------------------- токены


def _tokens(text: str | None) -> set[str]:
    return {t.strip(".-") for t in _TOKEN.findall(norm(text)) if t.strip(".-")}


def _common_prefix(a: str, b: str) -> int:
    n = 0
    for x, y in zip(a, b, strict=False):
        if x != y:
            break
        n += 1
    return n


def _same_word(a: str, b: str) -> bool:
    """Одно слово с учётом окончаний: «медный» ~ «медь», «черный» ~ «черного»."""
    if a == b:
        return True
    if not (a.isalpha() and b.isalpha()) or min(len(a), len(b)) < 3:
        return False
    short, long = sorted((a, b), key=len)
    if len(short) == 3:  # сокращение: «син» ~ «синяя», «мет» ~ «металлический»
        return long.startswith(short)
    prefix = _common_prefix(a, b)
    return prefix >= 3 and prefix >= 0.6 * min(len(a), len(b))


def _covers(expected: set[str], actual: set[str]) -> bool:
    """Все слова требования есть в значении оффера (с учётом окончаний).

    Для моделей решают токены с цифрами: «HP LaserJet Pro P1102» покрывается
    «HP LaserJet P1102» — номер модели совпал, «Pro» — уточнение серии.
    """
    if not expected:
        return False
    if all(any(_same_word(e, a) for a in actual) for e in expected):
        return True
    model_numbers = {
        e for e in expected if any(c.isdigit() for c in e) and any(c.isalpha() for c in e)
    }
    return bool(model_numbers) and model_numbers <= actual


def _model_numbers(tokens: set[str]) -> set[str]:
    """Номера моделей: токены из букв и цифр («p1102», «m1132», «4203cdz»)."""
    return {
        t for t in tokens
        if len(t) >= 4 and any(c.isdigit() for c in t) and any(c.isalpha() for c in t)
    }  # fmt: skip


def _is_code(raw_token: str) -> bool:
    """Код (марка, модель, артикул): латиница, цифры или несколько заглавных букв."""
    if len(raw_token) < 2:
        return False
    has_latin = bool(re.search(r"[A-Za-z]", raw_token))
    has_digit = any(c.isdigit() for c in raw_token)
    upper = sum(1 for c in raw_token if c.isupper())
    return has_latin or has_digit or upper >= 2


# ---------------------------------------------------------------- название товара


def _expected_values(attr: SpecAttribute) -> list[str]:
    if attr.type == AttrType.ENUM:
        return attr.values
    if attr.type == AttrType.TEXT and attr.value_text:
        return [attr.value_text]
    return []


_EDGE_PUNCT = re.compile(r"[.,]+(?=\s|$)")


def _spaced(text: str) -> str:
    """Нормализованный текст без знаков на границах слов: «28 мм, металлические, 100 шт.»
    → « 28 мм металлические 100 шт ». Знаки внутри («2,5», «ВВГнг-LS») сохраняются."""
    return f" {' '.join(_EDGE_PUNCT.sub(' ', norm(text)).split())} "


def _value_in_title(expected: list[str], title: str, ctx: CompareContext) -> str | None:
    title_norm = _spaced(title)
    for value in expected:
        for variant in ctx.synonyms.expand(value) | {norm(value)}:
            if len(variant) >= 3 and _spaced(variant) in title_norm:
                return value
    return None


def _word_conflict(word: str, title_tokens: set[str]) -> str | None:
    """Противоречие для одного составного кода требования («ВВГнг(А)-LS»)."""
    codes = [t.lower() for t in _RAW_TOKEN.findall(word) if _is_code(t)]
    if not codes:
        return None
    missing = [c for c in codes if c not in title_tokens]
    if not missing:
        return None
    present = [c for c in codes if c in title_tokens]
    similar = [
        (c, u)
        for c in missing
        for u in title_tokens
        if u != c and fuzz.ratio(c, u) >= CODE_SIMILAR_THRESHOLD
    ]
    if similar:
        c, u = similar[0]
        return f"в названии «{u.upper()}» вместо «{c.upper()}»"
    if present:
        return f"в названии нет «{', '.join(m.upper() for m in missing)}» из «{word}»"
    return None  # кода нет совсем — по названию не судим (например, сокращённый список моделей)


def _title_conflict(expected: list[str], title: str) -> str | None:
    """Название противоречит требованию: другой код того же вида или неполный составной код.

    Для нескольких допустимых значений противоречие — только если оно есть для каждого.
    """
    title_tokens = {t.lower() for t in _RAW_TOKEN.findall(title)}
    reasons: list[str] = []
    for value in expected:
        conflicts = [c for w in value.split() if (c := _word_conflict(w, title_tokens))]
        if not conflicts:
            return None
        reasons.append(f"{conflicts[0]} (требуется «{value}»)")
    return "; ".join(reasons) if reasons else None


# ---------------------------------------------------------------- сравнение значений


def _result(
    attr: SpecAttribute, status: MatchStatus, requirement: str, actual: str | None, why: str
) -> AttributeMatch:
    return AttributeMatch(
        name=attr.name,
        required=attr.required,
        status=status,
        requirement=requirement,
        actual=actual,
        explanation=why,
    )


def _compare_values(
    attr: SpecAttribute,
    expected_values: list[str],
    value: ExtractedValue,
    ctx: CompareContext,
    requirement: str,
) -> AttributeMatch:
    """Перечислимое или текстовое значение.

    Совпадение — все слова требуемого значения есть в значении оффера (оффер может
    перечислять варианты: «HP P1102, M1132»). Обратное — оффер лишь часть требования
    («ВВГнг(А)» при «ВВГнг(А)-LS») — совпадением не считается.
    """
    actual = value.text if value.text is not None else _fmt_actual(value)
    actual_norm = norm(actual)
    if not actual_norm:
        return _result(attr, MatchStatus.UNKNOWN, requirement, actual, "значение не указано")
    actual_tokens = _tokens(actual)

    for expected in expected_values:
        if ctx.synonyms.same(expected, actual):
            return _result(
                attr, MatchStatus.MATCH, requirement, actual, f"«{actual}» = «{expected}»"
            )
        short_ok = len(norm(expected)) >= 3 or any(c.isdigit() for c in expected)  # «А4»
        if short_ok and _covers(_tokens(expected), actual_tokens):
            return _result(
                attr, MatchStatus.MATCH, requirement, actual, f"«{actual}» содержит «{expected}»"
            )
        for syn in ctx.synonyms.expand(expected):
            if len(syn) >= 3 and _covers(_tokens(syn), actual_tokens):
                return _result(
                    attr, MatchStatus.MATCH, requirement, actual, f"«{actual}» ≈ «{expected}»"
                )
        if fuzz.ratio(norm(expected), actual_norm) >= ENUM_TYPO_THRESHOLD:
            return _result(
                attr, MatchStatus.MATCH, requirement, actual, f"«{actual}» ≈ «{expected}»"
            )
        if _is_kind_of(expected, actual_tokens):
            return _result(
                attr, MatchStatus.MATCH, requirement, actual,
                f"«{actual}» — разновидность «{expected}»",
            )  # fmt: skip

    # Совместимость с моделями: в оффере перечислены модели, но ни одной из требуемых
    wanted = set().union(*(_model_numbers(_tokens(e)) for e in expected_values))
    offered = _model_numbers(actual_tokens)
    if wanted and offered and not (wanted & offered):
        return _result(
            attr,
            MatchStatus.MISMATCH,
            requirement,
            actual,
            f"модели «{actual[:80]}» не включают требуемые "
            f"({', '.join(sorted(m.upper() for m in wanted))})",
        )

    if len(actual_tokens) <= TEXT_MISMATCH_MAX_WORDS or _is_known_other_value(
        expected_values, actual, ctx
    ):
        return _result(
            attr,
            MatchStatus.MISMATCH,
            requirement,
            actual,
            f"«{actual}» не входит в допустимые значения",
        )
    best = max(fuzz.token_set_ratio(norm(e), actual_norm) for e in expected_values)
    return _result(
        attr,
        MatchStatus.UNKNOWN,
        requirement,
        actual,
        f"«{actual[:80]}» — сходство неоднозначно ({best:.0f} %)",
    )


def _is_kind_of(expected: str, actual_tokens: set[str]) -> bool:
    """Общее требование выполнено частным значением: «металлические» ← «оцинкованные»."""
    for generic, kinds in HYPERNYMS.items():
        if any(t.startswith(generic) for t in _tokens(expected)):
            return any(t.startswith(k) for t in actual_tokens for k in kinds)
    return False


def _is_known_other_value(values: list[str], actual: str, ctx: CompareContext) -> bool:
    """Значение оффера — другой элемент известной группы синонимов («голубой» вместо «черный»)."""
    canon = ctx.synonyms.canonical(actual)
    return canon != norm(actual) and all(ctx.synonyms.canonical(v) != canon for v in values)


def _to_spec_unit(
    number: Decimal, unit: str | None, attr: SpecAttribute, ctx: CompareContext
) -> tuple[Decimal | None, str]:
    """Переводит число оффера в единицу требования. Возвращает (число, пояснение)."""
    if not attr.unit or not unit:
        return number, ""
    src, dst = ctx.units.resolve(unit), ctx.units.resolve(attr.unit)
    if src is None or dst is None:
        a, b = sorted((norm(unit).rstrip("."), norm(attr.unit).rstrip(".")), key=len)
        if a == b or (len(a) >= 3 and b.startswith(a)):  # «ядер» ~ «ядерный»
            return number, ""
        # «40 В» при требовании в ваттах: модель взяла другую величину
        return None, f"единицы «{unit}» и «{attr.unit}» не сопоставлены"
    if src.code == dst.code:
        return number, ""
    factor = ctx.units.factor(src, dst)
    if factor is None:
        # «100 л» при требовании в листах — сокращение «листов», а не литры
        if norm(attr.unit).startswith(norm(unit).rstrip(".")):
            return number, ""
        return None, f"единица «{unit}» несовместима с «{attr.unit}»"
    return number * factor, f" ({number} {src.short} = {number * factor} {dst.short})"


def _fmt_actual(value: ExtractedValue) -> str:
    unit = f" {value.unit}" if value.unit else ""
    if value.number is not None:
        return f"{value.number}{unit}"
    if value.min is not None or value.max is not None:
        lo = value.min if value.min is not None else "…"
        hi = value.max if value.max is not None else "…"
        return f"{lo}–{hi}{unit}"
    if value.boolean is not None:
        return "да" if value.boolean else "нет"
    return value.text or ""


def _compare_number(
    attr: SpecAttribute, value: ExtractedValue, ctx: CompareContext, requirement: str
) -> AttributeMatch:
    actual = _fmt_actual(value)
    raw = value.number
    if (
        raw is None
        and attr.operator == Operator.EQ
        and value.min is not None
        and value.max is not None
    ):
        # Диапазон из характеристик контракта («длина 26–30 мм»): товар сопоставим, если
        # требуемое значение в него входит
        lo, note = _to_spec_unit(value.min, value.unit, attr, ctx)
        hi, _ = _to_spec_unit(value.max, value.unit, attr, ctx)
        if lo is not None and hi is not None and attr.value_number is not None:
            ok = lo <= attr.value_number <= hi
            return _result(
                attr,
                MatchStatus.MATCH if ok else MatchStatus.MISMATCH,
                requirement,
                actual,
                f"диапазон {actual} {'включает' if ok else 'не включает'} "
                f"{attr.value_number.normalize():f}{note}",
            )
    if raw is None:
        # Оффер дал диапазон: для «≥» смотрим на минимум, для «≤» — на максимум
        raw = value.min if attr.operator == Operator.GTE else value.max
        raw = raw if raw is not None else (value.min or value.max)
    if raw is None:
        return _result(
            attr, MatchStatus.UNKNOWN, requirement, actual, "числовое значение не найдено"
        )

    number, note = _to_spec_unit(raw, value.unit, attr, ctx)
    if number is None:
        return _result(attr, MatchStatus.UNKNOWN, requirement, actual, note)
    target = attr.value_number
    assert target is not None

    match attr.operator:
        case Operator.GTE:
            ok = number >= target
        case Operator.LTE:
            ok = number <= target
        case _:
            ok = abs(number - target) <= abs(target) * NUMBER_TOLERANCE
    status = MatchStatus.MATCH if ok else MatchStatus.MISMATCH
    verdict = "удовлетворяет требованию" if ok else "не удовлетворяет требованию"
    return _result(attr, status, requirement, actual, f"{actual} {verdict}{note}")


def _compare_range(
    attr: SpecAttribute, value: ExtractedValue, ctx: CompareContext, requirement: str
) -> AttributeMatch:
    actual = _fmt_actual(value)
    candidates = [v for v in (value.number, value.min, value.max) if v is not None]
    if not candidates:
        return _result(
            attr, MatchStatus.UNKNOWN, requirement, actual, "числовое значение не найдено"
        )
    converted = []
    note = ""
    for raw in candidates:
        number, note = _to_spec_unit(raw, value.unit, attr, ctx)
        if number is None:
            return _result(attr, MatchStatus.UNKNOWN, requirement, actual, note)
        converted.append(number)
    lo_ok = attr.value_min is None or min(converted) >= attr.value_min
    hi_ok = attr.value_max is None or max(converted) <= attr.value_max
    ok = lo_ok and hi_ok
    verdict = "в пределах диапазона" if ok else "вне диапазона"
    return _result(
        attr,
        MatchStatus.MATCH if ok else MatchStatus.MISMATCH,
        requirement,
        actual,
        f"{actual} {verdict}{note}",
    )


def _compare_bool(
    attr: SpecAttribute, value: ExtractedValue, ctx: CompareContext, requirement: str
) -> AttributeMatch:
    actual_bool = value.boolean
    if actual_bool is None and value.text:
        canon = ctx.synonyms.canonical(value.text)
        actual_bool = True if canon == "да" else False if canon == "нет" else None
    actual = _fmt_actual(value)
    if actual_bool is None:
        return _result(
            attr, MatchStatus.UNKNOWN, requirement, actual, "значение не распознано как да/нет"
        )
    ok = actual_bool == attr.value_bool
    return _result(
        attr,
        MatchStatus.MATCH if ok else MatchStatus.MISMATCH,
        requirement,
        actual,
        "совпадает" if ok else "не совпадает",
    )
