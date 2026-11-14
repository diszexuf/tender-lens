"""Этап 3: оценка собранных предложений и расчёт (FR-05 – FR-08).

Перезапускаемый: работает только с данными из БД и создаёт новую версию расчёта (UC-08).
Порядок: базовые фильтры → дедупликация → предфильтр top-K → извлечение характеристик (LLM,
кэш) → сравнение кодом → нормализация → ручные решения → выбросы, CV, цена.
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select

from app import __version__
from app.calc.adjust import index_step, vat_step
from app.calc.normalize import normalize_price, resolve_pack_ambiguity
from app.calc.stats import PositionStatus, PriceItem, calculate_position
from app.calc.units import UnitRegistry
from app.core.logging import get_logger
from app.db import models as m
from app.domain.offer import AttributeMatch, ExtractedValue
from app.domain.reasons import REASON_TITLES, Flag, Reason
from app.domain.rules import Rules
from app.domain.spec import PositionSpec
from app.llm import prompts
from app.llm.provider import LLMError
from app.llm.tasks import (
    OFFER_VALUES_RULES,
    OfferValues,
    extract_offer_values,
    merge_code_values,
    missing_attributes,
    values_by_code,
)
from app.matching.comparators import CompareContext, compare
from app.matching.dedup import DedupItem, deduplicate
from app.matching.prefilter import excluded_word, other_product, rank
from app.matching.region import same_region
from app.matching.scoring import score
from app.matching.text import Synonyms
from app.pipeline.collect_stage import position_spec, unescape_text
from app.pipeline.deps import Deps, latest_settings, load_dictionaries
from app.pipeline.progress import Progress

log = get_logger(__name__)

PRICE_QUANT = Decimal("0.0001")  # точность хранения нормализованной цены (Numeric(18, 4))


@dataclass(frozen=True)
class Adjust:
    """Поправки после нормализации единиц: НДС, индексация, регион поставки."""

    vat_rate: Decimal = Decimal(0)
    indexes: dict[date, Decimal] = field(default_factory=dict)
    region: str | None = None
    region_required: bool = False


@dataclass
class Draft:
    """Решение по офферу в процессе оценки."""

    offer: m.Offer
    source_type: str
    included: bool = True
    reason: Reason | None = None
    text: str = ""
    text_score: float | None = None
    matches: list[AttributeMatch] = field(default_factory=list)
    relevance: Decimal | None = None
    steps: list[dict[str, str]] = field(default_factory=list)
    normalized: Decimal | None = None
    flags: list[str] = field(default_factory=list)
    duplicate_of: uuid.UUID | None = None
    is_manual: bool = False
    pack_candidate: Decimal | None = None

    def exclude(self, reason: Reason, detail: str) -> None:
        self.included = False
        self.reason = reason
        self.text = f"{REASON_TITLES[reason]}: {detail}" if detail else REASON_TITLES[reason]


async def run_evaluate(deps: Deps, calc_id: uuid.UUID, *, trigger: str = "initial") -> uuid.UUID:
    progress = Progress(deps.redis, str(calc_id))
    await progress.emit("stage", stage="evaluate", status="started")

    async with deps.session() as session:
        settings_row, rules = await latest_settings(session)
        units, synonyms = await load_dictionaries(session)
        positions = (
            (
                await session.execute(
                    select(m.Position)
                    .where(m.Position.calculation_id == calc_id)
                    .order_by(m.Position.ordinal)
                )
            )
            .scalars()
            .all()
        )
        calc_row = await session.get(m.Calculation, calc_id)
        indexes = (
            {r.period: r.value for r in (await session.execute(select(m.PriceIndex))).scalars()}
            if rules.indexation_enabled
            else {}
        )
        adjust = Adjust(
            vat_rate=rules.vat_rate,
            indexes=indexes,
            region=calc_region(calc_row),
            region_required=rules.region_required,
        )
        manual = {
            d.offer_id: d
            for d in (
                await session.execute(
                    select(m.ManualDecision).where(m.ManualDecision.calculation_id == calc_id)
                )
            ).scalars()
        }
        version_no = (
            await session.execute(
                select(func.coalesce(func.max(m.CalculationVersion.version_no), 0)).where(
                    m.CalculationVersion.calculation_id == calc_id
                )
            )
        ).scalar_one() + 1
        await session.commit()
        settings_id = settings_row.id

    results: list[tuple[m.Position, list[Draft], Any]] = []
    for position in positions:
        drafts, calc_result = await _evaluate_position(
            deps, calc_id, position, rules, units, synonyms, manual, adjust
        )
        results.append((position, drafts, calc_result))
        await progress.emit(
            "position",
            position=position.ordinal,
            status=calc_result.status,
            included=len(calc_result.included),
            excluded=sum(1 for d in drafts if not d.included),
            unit_price=calc_result.unit_price,
        )

    await deps.flush_llm_calls(calc_id)

    priced = [r for _, _, r in results if r.total_price is not None]
    outcome = "full" if len(priced) == len(results) and results else "partial" if priced else "none"
    total = sum((r.total_price for r in priced), Decimal(0)) if priced else None

    async with deps.session() as session:
        version = m.CalculationVersion(
            calculation_id=calc_id,
            settings_version_id=settings_id,
            version_no=version_no,
            trigger=trigger,
            outcome=outcome,
            nmck_total=total,
            llm_model=deps.llm.model,
            prompt_versions={
                n: prompts.prompt_version(n)
                for n in ("spec_extract", "offer_attributes", "page_offer")
            },
        )
        session.add(version)
        await session.flush()
        for position, drafts, calc_result in results:
            for d in drafts:
                session.add(
                    m.OfferEvaluation(
                        version_id=version.id,
                        offer_id=d.offer.id,
                        text_score=Decimal(str(d.text_score)) if d.text_score is not None else None,
                        attribute_matches=[x.model_dump(mode="json") for x in d.matches],
                        relevance=d.relevance,
                        normalization_steps=d.steps,
                        normalized_price=d.normalized,
                        decision="included" if d.included else "excluded",
                        reason_code=d.reason.value if d.reason else None,
                        reason_text=d.text,
                        duplicate_of=d.duplicate_of,
                        is_manual=d.is_manual,
                        flags=sorted(set(d.flags)),
                    )
                )
            session.add(
                m.PositionResult(
                    version_id=version.id,
                    position_id=position.id,
                    status=calc_result.status,
                    stats=calc_result.stats.as_dict() if calc_result.stats else None,
                    filter_log=calc_result.log,
                    hints=calc_result.hints,
                    flags=[str(f) for f in calc_result.flags],
                    outlier_bounds=[str(b) for b in calc_result.outlier_bounds]
                    if calc_result.outlier_bounds
                    else None,
                    unit_price=calc_result.unit_price,
                    total_price=calc_result.total_price,
                )
            )
        calc = await session.get(m.Calculation, calc_id)
        assert calc is not None
        calc.status, calc.stage, calc.app_version = "completed", "done", __version__
        await session.commit()
        version_id = version.id

    await progress.emit("stage", stage="evaluate", status="done", outcome=outcome, nmck=total)
    return version_id


async def _evaluate_position(
    deps: Deps,
    calc_id: uuid.UUID,
    position: m.Position,
    rules: Rules,
    units: UnitRegistry,
    synonyms: Synonyms,
    manual: dict[uuid.UUID, m.ManualDecision],
    adjust: Adjust | None = None,
) -> tuple[list[Draft], Any]:
    adjust = adjust or Adjust()
    spec = position_spec(position)
    async with deps.session() as session:
        rows = (
            await session.execute(
                select(m.Offer, m.SourceRun.source_type, m.Source.priority)
                .join(m.SourceRun, m.Offer.source_run_id == m.SourceRun.id)
                .outerjoin(m.Source, m.SourceRun.source_id == m.Source.id)
                .where(m.Offer.position_id == position.id)
                .order_by(func.coalesce(m.Source.priority, 1000), m.Offer.id)
            )
        ).all()
        cache_key = extraction_cache_key(position.attrs_hash, deps.llm.model)
        cached_attrs = {
            a.offer_id: a
            for a in (
                await session.execute(
                    select(m.OfferAttributes).where(
                        m.OfferAttributes.offer_id.in_([r[0].id for r in rows]),
                        m.OfferAttributes.attrs_hash == cache_key,
                    )
                )
            ).scalars()
        }
    drafts = [Draft(offer=o, source_type=st, flags=list(o.flags)) for o, st, _ in rows]

    _basic_filters(drafts, rules)
    _dedup(drafts)
    stop_words = [*rules.excluded_words, *spec.exclude_words]
    for d in drafts:
        if not d.included:
            continue
        if why := other_product(spec, d.offer.title, _offer_text(d.offer)):
            d.exclude(Reason.OTHER_PRODUCT, why)
        elif why := excluded_word(spec, d.offer.title, stop_words):
            d.exclude(Reason.EXCLUDED_WORD, why)
    active = [d for d in drafts if d.included]

    # Предфильтр top-K по текстовому сходству (FR-06.2): отдельно для контрактов и для
    # остальных источников — иначе контракты вытесняются похожими названиями магазинов
    by_id = {str(d.offer.id): d for d in active}
    candidates: list[Draft] = []
    for is_contract in (True, False):
        group = [d for d in active if (d.source_type in rules.contract_source_types) == is_contract]
        texts = {
            str(d.offer.id): f"{d.offer.title} {d.offer.attributes_raw}".strip() for d in group
        }
        top, rest = rank(spec, texts, rules.top_k, synonyms)
        for r in top:
            by_id[r.offer_id].text_score = r.score
            candidates.append(by_id[r.offer_id])
        kind = "контрактов" if is_contract else "предложений"
        for r in rest:
            d = by_id[r.offer_id]
            d.text_score = r.score
            d.exclude(
                Reason.LOW_TEXT_SIMILARITY,
                f"сходство {r.score:.0f} % — на проверку характеристик идут {rules.top_k} самых "
                f"похожих {kind} (у последнего {top[-1].score:.0f} %)",
            )

    values = await _offer_values(
        deps, calc_id, spec, position, candidates, cached_attrs, rules, units, synonyms
    )

    ctx = CompareContext(units=units, synonyms=synonyms)
    scoring = rules.scoring_rules()
    for d in candidates:
        ov = values.get(d.offer.id)
        extracted = {v.name: v for v in (ov.values if ov else [])}
        d.matches = [
            compare(a, extracted.get(a.name), ctx, title=d.offer.title) for a in spec.attributes
        ]
        verdict = score(d.matches, scoring)
        d.relevance = verdict.relevance
        if not verdict.included:
            assert verdict.reason is not None
            d.exclude(verdict.reason, verdict.explanation)
            continue
        if not _region_ok(d, adjust):
            continue
        d.text = f"Включено: {verdict.explanation}; релевантность {verdict.relevance}"
        _normalize(d, ov, spec, units, adjust)

    _resolve_packs([d for d in candidates if d.included and d.normalized is not None])
    if rules.contracts_first:
        _prefer_contracts(candidates, rules, spec.quantity)

    _apply_manual(drafts, manual, spec, units, values, adjust)

    included = [d for d in drafts if d.included and d.normalized is not None]
    for d in included:  # расчёт по тем же числам, что хранятся в БД и выгружаются в XLSX
        assert d.normalized is not None
        d.normalized = d.normalized.quantize(PRICE_QUANT)
    calc_result = calculate_position(
        [PriceItem(str(d.offer.id), d.normalized) for d in included if d.normalized is not None],
        spec.quantity,
        rules.calc_rules(),
    )
    excluded_by_calc = {e.offer_id: e for e in calc_result.excluded}
    for d in included:
        if (e := excluded_by_calc.get(str(d.offer.id))) is not None:
            d.exclude(e.reason, e.detail)
    return drafts, calc_result


def _basic_filters(drafts: list[Draft], rules: Rules) -> None:
    today = datetime.now(UTC).date()
    for d in drafts:
        o = d.offer
        if o.price is None:
            d.exclude(Reason.MISSING_PRICE, "")
        elif o.currency.upper() != "RUB":
            d.exclude(Reason.CURRENCY_UNSUPPORTED, o.currency)
        elif "contract_penalties" in o.flags:
            d.exclude(Reason.CONTRACT_PENALTIES, "")
        elif o.price_date and o.price_date < today - timedelta(days=rules.max_age(d.source_type)):
            d.exclude(
                Reason.STALE_PRICE,
                f"цена от {o.price_date:%d.%m.%Y}, допустимо не старше {rules.max_age(d.source_type)} дн.",
            )


def _dedup(drafts: list[Draft]) -> None:
    active = [d for d in drafts if d.included]
    items = [
        DedupItem(
            offer_id=str(d.offer.id),
            url=d.offer.url,
            title=d.offer.title,
            price=d.offer.price,
            supplier_name=d.offer.supplier_name,
            supplier_inn=d.offer.supplier_inn,
            external_id=d.offer.external_id,
            price_date=d.offer.price_date,
            fetched_at=d.offer.fetched_at,
        )
        for d in active
    ]
    by_id = {str(d.offer.id): d for d in active}
    for decision in deduplicate(items):
        d = by_id[decision.offer_id]
        d.duplicate_of = uuid.UUID(decision.duplicate_of)
        d.exclude(decision.reason, decision.detail)


async def _offer_values(
    deps: Deps,
    calc_id: uuid.UUID,
    spec: PositionSpec,
    position: m.Position,
    candidates: list[Draft],
    cached: dict[uuid.UUID, m.OfferAttributes],
    rules: Rules,
    units: UnitRegistry,
    synonyms: Synonyms,
) -> dict[uuid.UUID, OfferValues]:
    """Значения характеристик: из кэша, кодом из текста или пачками через LLM (FR-06.3)."""
    out: dict[uuid.UUID, OfferValues] = {}
    for d in candidates:
        if (row := cached.get(d.offer.id)) is not None:
            out[d.offer.id] = OfferValues.model_validate(row.values[0])
    if not spec.attributes:
        return out
    # Сначала код: значения, явно указанные в тексте; LLM — только для остального
    by_code: dict[uuid.UUID, dict[str, ExtractedValue]] = {}
    ready: list[tuple[uuid.UUID, OfferValues]] = []
    for d in candidates:
        if d.offer.id in out:
            continue
        found, done = values_by_code(
            spec, str(d.offer.id), _offer_text(d.offer), unit_known=bool(d.offer.unit_raw),
            units=units, synonyms=synonyms,
        )  # fmt: skip
        by_code[d.offer.id] = found
        if done is not None:
            ready.append((d.offer.id, done))
    if ready:
        await _store_values(deps, position, ready)
        out.update(ready)
    missing = [d for d in candidates if d.offer.id not in out]
    if not missing:
        return out

    progress = Progress(deps.redis, str(calc_id))
    size = rules.llm_batch_size
    batches = [missing[i : i + size] for i in range(0, len(missing), size)]
    for n, batch in enumerate(batches, start=1):
        await progress.emit(
            "llm", position=spec.ordinal, batch=n, batches=len(batches), offers=len(batch)
        )
        texts = {str(d.offer.id): _offer_text(d.offer) for d in batch}
        try:
            wanted = missing_attributes(spec, [by_code.get(d.offer.id, {}) for d in batch])
            values, _ = await extract_offer_values(deps.llm, spec, texts, attributes=wanted)
        except LLMError as exc:
            log.warning("attributes.llm_failed", error=str(exc)[:300])
            for d in batch:
                d.flags.append("llm_failed")
            continue
        merged = []
        for v in values:
            oid = uuid.UUID(v.offer_id)
            merged.append((oid, merge_code_values(v, by_code.get(oid, {}))))
        await _store_values(deps, position, merged)
        out.update(merged)
    return out


async def _store_values(
    deps: Deps, position: m.Position, values: list[tuple[uuid.UUID, OfferValues]]
) -> None:
    async with deps.session() as session:
        for oid, v in values:
            session.add(
                m.OfferAttributes(
                    offer_id=oid,
                    attrs_hash=extraction_cache_key(position.attrs_hash, deps.llm.model),
                    values=[v.model_dump(mode="json")],
                )
            )
        await session.commit()


def extraction_cache_key(attrs_hash: str, model: str) -> str:
    """Кэш извлечённых значений: набор характеристик + модель + версия промпта
    + версия обработки ответа кодом (OFFER_VALUES_RULES)."""
    version = prompts.prompt_version("offer_attributes")
    key = f"{attrs_hash}|{model}|{version}|{OFFER_VALUES_RULES}"
    return hashlib.sha256(key.encode()).hexdigest()


def _offer_text(o: m.Offer) -> str:
    attrs = "; ".join(f"{k}: {v}" for k, v in (o.attributes or {}).items())
    parts = [o.title, attrs, o.attributes_raw]
    if o.price is not None:
        parts.append(f"Цена: {o.price} руб.{' за ' + o.unit_raw if o.unit_raw else ''}")
    return unescape_text("\n".join(p for p in parts if p)) or ""


def calc_region(calc: m.Calculation | None) -> str | None:
    """Регион поставки: из параметров расчёта, иначе — из разобранного описания."""
    if calc is None:
        return None
    region = (calc.input_params or {}).get("region") or (calc.spec_final or {}).get("region")
    if not region:
        region = (calc.spec_extracted or {}).get("region")
    return str(region).strip() or None if region else None


def _region_ok(d: Draft, adjust: Adjust) -> bool:
    """Регион предложения против региона поставки (FR-06.7)."""
    if not adjust.region or not d.offer.region:
        return True
    if same_region(adjust.region, d.offer.region) is not False:
        return True
    if adjust.region_required:
        d.exclude(
            Reason.REGION_MISMATCH,
            f"регион предложения «{d.offer.region}», регион поставки «{adjust.region}»",
        )
        return False
    d.flags.append(Flag.REGION_DIFFERS)
    return True


def _normalize(
    d: Draft,
    ov: OfferValues | None,
    spec: PositionSpec,
    units: UnitRegistry,
    adjust: Adjust | None = None,
) -> None:
    o = d.offer
    assert o.price is not None
    res = normalize_price(
        price=o.price,
        offer_unit_raw=o.unit_raw,
        pack_qty=o.pack_qty,
        hint_unit=ov.sale_unit if ov else None,
        hint_pack_qty=ov.pack_qty if ov else None,
        position_okei=spec.okei_code,
        position_unit_raw=spec.unit_raw,
        registry=units,
        title=o.title,
    )
    d.flags.extend(str(f) for f in res.flags)
    d.steps = [s.as_dict() for s in res.steps]
    if not res.ok:
        assert res.reason is not None
        d.exclude(res.reason, res.detail or "")
        return
    price = res.price
    assert price is not None
    adjust = adjust or Adjust()
    if (step := vat_step(price, o.vat_status, adjust.vat_rate)) is not None:
        d.steps.append(step.as_dict())
        d.flags.append(Flag.VAT_ADDED)
        price = step.after
    if (step := index_step(price, o.price_date, adjust.indexes)) is not None:
        d.steps.append(step.as_dict())
        d.flags.append(Flag.PRICE_INDEXED)
        price = step.after
    d.normalized = price
    d.pack_candidate = res.pack_candidate
    if o.vat_status == "unknown":
        d.flags.append(Flag.VAT_UNKNOWN)


def _prefer_contracts(drafts: list[Draft], rules: Rules, quantity: Decimal) -> None:
    """Цены исполненных контрактов — основной источник: если по ним одним цена
    определяется (их не меньше min_sample и выборка однородна), цены магазинов в выборку
    не идут и остаются в отчёте с причиной. Иначе в расчёт идут все сопоставимые цены."""
    usable = [d for d in drafts if d.included and d.normalized is not None]
    contracts = [d for d in usable if d.source_type in rules.contract_source_types]
    if len(contracts) < rules.min_sample or len(contracts) == len(usable):
        return
    alone = calculate_position(
        [
            PriceItem(str(d.offer.id), d.normalized.quantize(PRICE_QUANT))
            for d in contracts
            if d.normalized is not None
        ],
        quantity,
        rules.calc_rules(),
    )
    if alone.unit_price is None:
        return  # по одним контрактам цена не определяется — добавляем цены магазинов
    for d in usable:
        if d.source_type not in rules.contract_source_types:
            d.exclude(
                Reason.CONTRACTS_SUFFICIENT,
                f"цена рассчитана по {len(alone.included)} исполненным контрактам "
                f"(минимум {rules.min_sample}); цены магазинов в этом случае не нужны",
            )


def _resolve_packs(drafts: list[Draft]) -> None:
    """Неоднозначная фасовка: выбор по ценам других предложений позиции."""
    prices = {str(d.offer.id): d.normalized for d in drafts if d.normalized is not None}
    candidates = {str(d.offer.id): d.pack_candidate for d in drafts if d.pack_candidate}
    chosen = resolve_pack_ambiguity(prices, candidates)
    for d in drafts:
        if (choice := chosen.get(str(d.offer.id))) is None:
            continue
        new_price, why = choice
        d.steps.append(
            {
                "op": "pack",
                "factor": str(d.pack_candidate),
                "before": str(d.normalized),
                "after": str(new_price),
                "reason": why,
            }
        )
        d.normalized = new_price
        d.flags = [f for f in d.flags if f != Flag.PACK_AMBIGUOUS] + [Flag.PACK_BY_PEERS]


def _apply_manual(
    drafts: list[Draft],
    manual: dict[uuid.UUID, m.ManualDecision],
    spec: PositionSpec,
    units: UnitRegistry,
    values: dict[uuid.UUID, OfferValues],
    adjust: Adjust | None = None,
) -> None:
    """Ручные решения поверх автоматических (FR-06.9)."""
    for d in drafts:
        decision = manual.get(d.offer.id)
        if decision is None:
            continue
        d.is_manual = True
        if decision.decision == "excluded":
            d.exclude(Reason.MANUAL, decision.comment)
        elif decision.decision == "included":
            if d.normalized is None and d.offer.price is not None:
                d.included = True  # _normalize исключает только при ошибке пересчёта
                _normalize(d, values.get(d.offer.id), spec, units, adjust)
            if d.normalized is None:
                d.text = f"Ручное включение невозможно: {d.text or 'нет цены'}"
                continue
            d.included, d.reason = True, None
            d.text = f"Включено пользователем: {decision.comment}"


def status_label(status: str) -> str:
    labels: dict[str, str] = {
        PositionStatus.OK: "Цена определена",
        PositionStatus.OK_ADJUSTED: "Цена определена (скорректированная выборка)",
        PositionStatus.HETEROGENEOUS: "Неоднородная выборка",
        PositionStatus.INSUFFICIENT_DATA: "Недостаточно надёжных данных",
    }
    return labels.get(status, status)


__all__ = ["ExtractedValue", "run_evaluate", "status_label"]
