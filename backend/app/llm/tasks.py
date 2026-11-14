"""Задачи LLM: разбор закупки, извлечение характеристик офферов, извлечение со страницы.

Схемы ответа намеренно плоские и строгие (все поля обязательны, но допускают null):
мелкой модели так проще, а ошибки в числах исключаются отдельными числовыми полями.
"""

from __future__ import annotations

import copy
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, Field, ValidationError

from app.calc.units import DEFAULT_REGISTRY, UnitRegistry
from app.domain.offer import ExtractedValue
from app.domain.quote_parser import locate, numbers_in, parse_quantity, parse_quote
from app.domain.spec import AttrType, PositionSpec, SpecAttribute
from app.domain.spec_review import codes_from_name, review_attributes
from app.llm import prompts
from app.llm.provider import LLMProvider, LLMResult
from app.matching.text import DEFAULT_SYNONYMS, Synonyms
from app.matching.text_values import ground_llm_value, values_from_text

# ---------------------------------------------------------------- схемы ответов LLM


class LLMAttr(BaseModel):
    name: str
    quote: str  # дословный фрагмент описания закупки
    required: bool


class LLMPosition(BaseModel):
    name: str
    quantity_quote: str | None  # фрагмент с количеством и единицей: «500 м», «200 пачек»
    search_query: str
    attributes: list[LLMAttr]


class LLMSpec(BaseModel):
    positions: list[LLMPosition] = Field(min_length=1)


class LLMValue(BaseModel):
    name: str
    number: float | None
    min: float | None
    max: float | None
    text: str | None
    flag: bool | None
    unit: str | None


class LLMOfferValues(BaseModel):
    id: str
    sale_unit: str | None
    pack_qty: float | None
    values: list[LLMValue]


class LLMOfferBatch(BaseModel):
    offers: list[LLMOfferValues]


class LLMPageOffer(BaseModel):
    name: str | None
    price: float | None
    currency: str | None
    sale_unit: str | None
    pack_qty: float | None
    characteristics: str | None


def strict_schema(model: type[BaseModel]) -> dict[str, Any]:
    """JSON Schema, где все свойства обязательны (null допускается типом)."""
    schema = copy.deepcopy(model.model_json_schema())

    def fix(node: Any) -> None:
        if isinstance(node, dict):
            if node.get("type") == "object" and "properties" in node:
                node["required"] = list(node["properties"])
                node["additionalProperties"] = False
            for value in node.values():
                fix(value)
        elif isinstance(node, list):
            for value in node:
                fix(value)

    fix(schema)
    return schema


def _dec(value: float | None) -> Decimal | None:
    if value is None:
        return None
    if value != value:  # NaN
        return None
    # float → строка без артефактов двоичного представления; 50.0 → 50, без экспоненты
    d = Decimal(repr(value))
    return d.quantize(Decimal(1)) if d == d.to_integral_value() else d.normalize()


# ---------------------------------------------------------------- разбор закупки


class SpecExtraction(BaseModel):
    positions: list[PositionSpec]
    warnings: list[str]


def llm_spec_to_domain(
    spec: LLMSpec, text: str, units: UnitRegistry = DEFAULT_REGISTRY
) -> SpecExtraction:
    """Ответ модели → спецификация. Значения разбираются кодом из цитат (quote_parser);
    цитата, которой нет в тексте, отбрасывается с предупреждением."""
    positions: list[PositionSpec] = []
    warnings: list[str] = []
    for i, p in enumerate(spec.positions, start=1):
        attrs: list[SpecAttribute] = []
        for a in p.attributes:
            quote = locate(a.quote, text)
            if quote is None:
                warnings.append(f"Позиция {i}, «{a.name}»: цитаты «{a.quote}» нет в описании")
                continue
            try:
                attrs.append(parse_quote(a.name, quote, a.required, units))
            except (ValidationError, ValueError) as exc:
                warnings.append(f"Позиция {i}, «{a.name}»: значение не распознано ({exc})")
        attrs, review_warnings = review_attributes(i, attrs, units)
        attrs, code_warnings = codes_from_name(i, p.name, attrs)
        warnings.extend(review_warnings + code_warnings)

        quantity_quote = locate(p.quantity_quote or "", text)
        parsed = parse_quantity(quantity_quote, units)
        if parsed is None:
            warnings.append(f"Позиция {i}: количество не указано, принято 1 шт")
        quantity, unit_raw = parsed or (Decimal(1), "шт")
        unit = units.resolve(unit_raw)
        if unit is None:
            warnings.append(f"Позиция {i}: единица «{unit_raw}» не найдена в справочнике ОКЕИ")
        positions.append(
            PositionSpec(
                ordinal=i,
                name=p.name.strip()[:500] or f"Позиция {i}",
                quantity=quantity,
                unit_raw=unit_raw[:50],
                okei_code=unit.code if unit and not unit.code.startswith("x-") else None,
                search_query=p.search_query.strip()[:300],
                attributes=attrs,
            )
        )
    return SpecExtraction(positions=positions, warnings=warnings)


async def extract_spec(llm: LLMProvider, text: str) -> tuple[SpecExtraction, LLMResult[LLMSpec]]:
    result = await llm.complete_json(
        prompts.render("spec_extract", text=text), LLMSpec, json_schema=strict_schema(LLMSpec)
    )
    return llm_spec_to_domain(result.value, text), result


# ---------------------------------------------------------------- характеристики офферов


class OfferValues(BaseModel):
    offer_id: str
    sale_unit: str | None
    pack_qty: Decimal | None
    values: list[ExtractedValue]


def _batch_schema(offer_ids: list[str], attr_names: list[str]) -> dict[str, Any]:
    schema = strict_schema(LLMOfferBatch)
    defs = schema["$defs"]
    defs["LLMOfferValues"]["properties"]["id"] = {"type": "string", "enum": offer_ids}
    if attr_names:
        defs["LLMValue"]["properties"]["name"] = {"type": "string", "enum": attr_names}
    else:  # все значения найдены кодом — от модели нужны только единица и фасовка
        defs["LLMOfferValues"]["properties"]["values"]["maxItems"] = 0
    return schema


# Версия обработки ответа модели кодом: меняется — кэш извлечений пересчитывается
# 2: числа, которых нет в тексте оффера, отбрасываются; 3: значения сначала ищет код;
# 4: числа — только с единицей или названием характеристики, слова — только из текста;
# 5: «min ≥ 26 / max ≤ 30» — диапазон; 6: слово, противоречащее требованию, должно
# быть привязано к характеристике, значение другой характеристики отбрасывается;
# модель получает только ненайденные кодом
OFFER_VALUES_RULES = 6


def values_by_code(
    position: PositionSpec,
    offer_id: str,
    text: str,
    *,
    unit_known: bool,
    units: UnitRegistry = DEFAULT_REGISTRY,
    synonyms: Synonyms = DEFAULT_SYNONYMS,
) -> tuple[dict[str, ExtractedValue], OfferValues | None]:
    """Значения, найденные кодом, и готовый результат, если LLM не нужна: найдены все
    характеристики, а единица цены известна из источника (её LLM нужна для нормализации)."""
    found = values_from_text(position.attributes, text, units, synonyms)
    complete = unit_known and len(found) == len(position.attributes)
    ready = OfferValues(
        offer_id=offer_id, sale_unit=None, pack_qty=None, values=list(found.values())
    )
    return found, (ready if complete else None)


def missing_attributes(
    position: PositionSpec, found: list[dict[str, ExtractedValue]]
) -> list[SpecAttribute]:
    """Характеристики, которых код не нашёл хотя бы в одном предложении пачки."""
    return [a for a in position.attributes if any(a.name not in f for f in found)]


def merge_code_values(llm_values: OfferValues, found: dict[str, ExtractedValue]) -> OfferValues:
    """Найденное в тексте кодом важнее ответа модели."""
    values = [found.get(v.name, v) for v in llm_values.values]
    values += [v for name, v in found.items() if all(x.name != name for x in values)]
    return llm_values.model_copy(update={"values": values})


async def extract_offer_values(
    llm: LLMProvider,
    position: PositionSpec,
    offers: dict[str, str],
    *,
    max_chars_per_offer: int = 1200,
    attributes: list[SpecAttribute] | None = None,
) -> tuple[list[OfferValues], LLMResult[LLMOfferBatch]]:
    """Извлекает значения характеристик позиции для пачки офферов (FR-06.3).
    attributes — какие характеристики спрашивать (по умолчанию все): найденные кодом
    модели не нужны, её ответ по ним всё равно не используется."""
    wanted = position.attributes if attributes is None else attributes
    local_ids = {str(i): oid for i, oid in enumerate(offers, start=1)}
    attr_info = [{"name": a.name, "kind": a.type.value, "unit": a.unit} for a in wanted]
    prompt = prompts.render(
        "offer_attributes",
        position_name=position.name,
        attributes=attr_info,
        offers=[
            {"id": lid, "text": offers[oid][:max_chars_per_offer]} for lid, oid in local_ids.items()
        ],
    )
    result = await llm.complete_json(
        prompt,
        LLMOfferBatch,
        json_schema=_batch_schema(list(local_ids), [a.name for a in wanted]),
    )
    attrs = {a.name: a for a in wanted}
    out: list[OfferValues] = []
    for item in result.value.offers:
        oid = local_ids.get(item.id)
        if oid is None:
            continue
        text = offers[oid]
        in_text = numbers_in(text)
        values = [
            ground_llm_value(
                attrs[v.name],
                _offer_value(v, attrs[v.name].type),
                text,
                others=position.attributes,
            )
            for v in item.values
            if v.name in attrs
        ]
        pack = _dec(item.pack_qty)
        if pack is not None and pack != 1 and pack not in in_text:
            pack = None  # фасовки нет в тексте — модель её придумала
        out.append(
            OfferValues(
                offer_id=oid,
                sale_unit=item.sale_unit or None,
                pack_qty=pack if pack and pack > 0 else None,
                values=values,
            )
        )
    return out, result


def _offer_value(v: LLMValue, kind: AttrType) -> ExtractedValue:
    """Значение характеристики оффера. Число, записанное моделью текстом («2,5 мм²»),
    разбирается кодом — так же, как цитаты требований."""
    value = ExtractedValue(
        name=v.name,
        number=_dec(v.number),
        min=_dec(v.min),
        max=_dec(v.max),
        text=(v.text or None),
        boolean=v.flag,
        unit=(v.unit or None),
    )
    numeric = kind in (AttrType.NUMBER, AttrType.RANGE)
    if numeric and value.number is None and value.min is None and value.max is None and v.text:
        try:
            parsed = parse_quote(v.name, v.text, True)
        except ValidationError, ValueError:
            return value
        value.number, value.min, value.max = parsed.value_number, parsed.value_min, parsed.value_max
        value.unit = value.unit or parsed.unit
    return value


async def extract_page_offer(
    llm: LLMProvider, url: str, text: str
) -> tuple[LLMPageOffer, LLMResult[LLMPageOffer]]:
    result = await llm.complete_json(
        prompts.render("page_offer", url=url, text=text),
        LLMPageOffer,
        json_schema=strict_schema(LLMPageOffer),
    )
    return result.value, result
