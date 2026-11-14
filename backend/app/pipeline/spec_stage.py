"""Этап 1: разбор описания закупки в спецификацию (FR-02)."""

from __future__ import annotations

import uuid

from sqlalchemy import delete

from app.core.logging import get_logger
from app.db import models as m
from app.domain.spec import PositionSpec, ProcurementSpec
from app.llm.provider import LLMError
from app.llm.tasks import extract_spec
from app.pipeline.deps import Deps, attrs_hash
from app.pipeline.progress import Progress

log = get_logger(__name__)


async def save_positions(deps: Deps, calc_id: uuid.UUID, positions: list[PositionSpec]) -> None:
    """Заменяет позиции расчёта (до сбора данных)."""
    async with deps.session() as session:
        await session.execute(delete(m.Position).where(m.Position.calculation_id == calc_id))
        for p in positions:
            attrs = [a.model_dump(mode="json") for a in p.attributes]
            session.add(
                m.Position(
                    calculation_id=calc_id,
                    ordinal=p.ordinal,
                    name=p.name,
                    quantity=p.quantity,
                    unit_raw=p.unit_raw,
                    okei_code=p.okei_code,
                    ktru_code=p.ktru_code,
                    ktru_confirmed=p.ktru_confirmed,
                    search_query=p.search_query,
                    attributes=attrs,
                    attrs_hash=attrs_hash(attrs),
                )
            )
        await session.commit()


async def run_extract(deps: Deps, calc_id: uuid.UUID) -> bool:
    """Возвращает True, если спецификация получена."""
    progress = Progress(deps.redis, str(calc_id))
    async with deps.session() as session:
        calc = await session.get(m.Calculation, calc_id)
        if calc is None:
            raise ValueError(f"расчёт {calc_id} не найден")
        calc.status, calc.stage, calc.error = "extracting", "spec", None
        text = calc.input_text
        await session.commit()
    await progress.emit("stage", stage="spec", status="started")

    try:
        extraction, result = await extract_spec(deps.llm, text)
    except LLMError as exc:
        await deps.flush_llm_calls(calc_id)
        async with deps.session() as session:
            calc = await session.get(m.Calculation, calc_id)
            assert calc is not None
            # FR-02.5: ошибка и пустая форма — спецификацию можно заполнить вручную
            calc.status = "spec_ready" if not calc.auto_run else "failed"
            calc.stage = "spec"
            calc.error = f"Не удалось разобрать описание закупки: {exc}"
            calc.spec_extracted = {"positions": [], "warnings": [], "raw": exc.raw}
            await session.commit()
        await progress.emit("stage", stage="spec", status="failed", message=str(exc))
        return False

    await deps.flush_llm_calls(calc_id)
    async with deps.session() as session:
        calc = await session.get(m.Calculation, calc_id)
        if calc is None or calc.status == "cancelled":
            return False  # отменён во время разбора
    spec_json = ProcurementSpec(positions=extraction.positions).model_dump(mode="json")
    await save_positions(deps, calc_id, extraction.positions)
    async with deps.session() as session:
        calc = await session.get(m.Calculation, calc_id)
        assert calc is not None
        calc.spec_extracted = {**spec_json, "warnings": extraction.warnings}
        calc.spec_final = spec_json
        calc.status, calc.stage = "spec_ready", "spec"
        await session.commit()
    await progress.emit(
        "stage",
        stage="spec",
        status="done",
        positions=len(extraction.positions),
        duration_ms=result.duration_ms,
    )
    log.info("spec.extracted", calculation_id=str(calc_id), positions=len(extraction.positions))
    return True
