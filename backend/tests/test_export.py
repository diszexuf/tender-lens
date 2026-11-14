"""Выгрузка отчёта: XLSX с формулами и HTML (FR-09.2)."""

from __future__ import annotations

import io
import uuid
from datetime import UTC, date, datetime
from decimal import Decimal

from openpyxl import load_workbook

from app.api.schemas import (
    CalculationDetail,
    OfferReport,
    PositionReport,
    PositionResultReport,
    Report,
    VersionReport,
)
from app.calc.stats import CalcRules, PriceItem, calculate_position
from app.domain.spec import AttrType, PositionSpec, SpecAttribute
from app.reports.export import build_xlsx, render_html

NOW = datetime(2026, 11, 14, 12, 0, tzinfo=UTC)
PRICES = [Decimal("101.1234"), Decimal("99.5"), Decimal("103.3333"), Decimal("98.0001")]


def _offer(i: int, price: Decimal | None, decision: str, reason: str | None = None) -> OfferReport:
    return OfferReport(
        id=uuid.uuid4(),
        source_name="Магазин",
        url=f"https://shop.example/p/{i}",
        title=f"Бумага А4 <b>{i}</b>",
        price=price,
        currency="RUB",
        vat_status="excluded" if i == 0 else "included",
        unit_raw="пач",
        pack_qty=None,
        supplier_name="ООО «Ромашка»",
        supplier_inn="7701234567",
        price_date=date(2026, 10, 1),
        fetched_at=NOW,
        extraction_method="jsonld",
        snapshot_ids=[uuid.uuid4()],
        decision=decision,
        reason_code=reason,
        reason_text="Выброс" if reason else "Включено",
        relevance=Decimal("0.9"),
        text_score=Decimal(80),
        attribute_matches=[{"status": "match", "requirement": "Формат: А4"}],
        normalization_steps=[{"reason": "без пересчёта", "before": "1", "after": "1"}],
        normalized_price=price,
        flags=[],
        is_manual=False,
        duplicate_of=None,
    )


def _report() -> Report:
    calc = calculate_position(
        [PriceItem(str(i), p) for i, p in enumerate(PRICES)], Decimal(10), CalcRules()
    )
    spec = PositionSpec(
        ordinal=1,
        name="Бумага офисная А4",
        quantity=Decimal(10),
        unit_raw="пач",
        attributes=[SpecAttribute(name="Формат", type=AttrType.ENUM, values=["А4"])],
    )
    offers = [_offer(i, p, "included") for i, p in enumerate(PRICES)]
    offers.append(_offer(9, Decimal(500), "excluded", "outlier_mad"))
    detail = CalculationDetail(
        id=uuid.uuid4(),
        created_at=NOW,
        updated_at=NOW,
        status="completed",
        stage="done",
        error=None,
        input_text="Бумага А4, 10 пачек",
        input_params={},
        auto_run=True,
        spec=[spec],
        spec_warnings=[],
        spec_edited=False,
        versions=[],
    )
    return Report(
        calculation=detail,
        version=VersionReport(
            id=uuid.uuid4(),
            version_no=2,
            created_at=NOW,
            trigger="manual",
            outcome="full",
            nmck_total=calc.total_price,
            settings_version_id=1,
            rules={"cv_threshold": "33"},
            llm_model="qwen3:4b",
            prompt_versions={"spec_extract": "v1"},
        ),
        positions=[
            PositionReport(
                id=uuid.uuid4(),
                spec=spec,
                result=PositionResultReport(
                    status=calc.status,
                    status_label="Цена определена",
                    unit_price=calc.unit_price,
                    total_price=calc.total_price,
                    stats=calc.stats.as_dict() if calc.stats else None,
                    filter_log=calc.log,
                    hints=[],
                    flags=[],
                    outlier_bounds=None,
                ),
                sources=[],
                offers=offers,
            )
        ],
        reasons={},
    )


def test_xlsx_formulas_cover_sample_rows() -> None:
    report = _report()
    wb = load_workbook(io.BytesIO(build_xlsx(report)))
    assert wb.sheetnames == ["Сводка", "Поз. 1", "Исключённые", "Параметры"]
    ws = wb["Поз. 1"]
    sample = [ws.cell(row=r, column=10).value for r in range(6, 6 + len(PRICES))]
    assert [Decimal(str(v)) for v in sample] == PRICES
    formulas = {
        ws.cell(row=r, column=2).value: ws.cell(row=r, column=3).value for r in range(1, 40)
    }
    assert formulas["n"] == "=COUNT(J6:J9)"
    assert formulas["Среднее"] == "=AVERAGE(J6:J9)"
    assert formulas["σ (выборочное)"] == "=STDEV(J6:J9)"
    assert str(formulas["CV, %"]).startswith("=IF(")
    assert str(formulas["Цена за единицу"]).startswith("=ROUND(")
    # Значение системы совпадает с тем, что даст формула по строкам выборки
    mean = sum(PRICES) / len(PRICES)
    assert mean.quantize(Decimal("0.01")) == report.positions[0].result.unit_price  # type: ignore[union-attr]
    summary = wb["Сводка"]
    assert summary["F6"].value == f"='Поз. 1'!{_cell(ws, 'Цена за единицу')}"
    assert summary["G7"].value == "=SUM(G6:G6)"
    excluded = wb["Исключённые"]
    assert excluded["E2"].value == "outlier_mad"


def _cell(ws, name: str) -> str:  # type: ignore[no-untyped-def]
    for r in range(1, 60):
        if ws.cell(row=r, column=2).value == name:
            return f"C{r}"
    raise AssertionError(name)


def test_html_report_escapes_and_lists_sections() -> None:
    html = render_html(_report())
    assert "Бумага А4 &lt;b&gt;0&lt;/b&gt;" in html
    assert "НМЦК:" in html
    assert "Исключённые предложения (1)" in html
    assert "Выборка (4)" in html
    assert "без НДС" in html
