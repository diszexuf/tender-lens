"""Выгрузка отчёта: XLSX с формулами, HTML и PDF (FR-09.2, FR-09.3).

В XLSX показатели выборки (n, среднее, σ, CV) — формулы Excel по строкам нормализованных цен,
чтобы расчёт можно было проверить; рядом — значения, посчитанные системой.
"""

from __future__ import annotations

import io
import uuid
from decimal import Decimal
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, select_autoescape
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.worksheet import Worksheet

from app.api.schemas import OfferReport, PositionReport, Report

PRICED = {"ok", "ok_adjusted"}
OUTCOME = {
    "full": "НМЦК определена",
    "partial": "Неполный расчёт: цена определена не по всем позициям",
    "none": "Цена не определена",
}
VAT = {"included": "с НДС", "excluded": "без НДС", "not_applicable": "без НДС (не облагается)"}
BOLD = Font(bold=True)
HEAD_FILL = PatternFill("solid", fgColor="DDE7F3")
MONEY = "#,##0.00"
PRICE4 = "#,##0.0000"

_env = Environment(
    loader=FileSystemLoader(Path(__file__).parent / "templates"),
    autoescape=select_autoescape(["html"]),
    trim_blocks=True,
    lstrip_blocks=True,
)


def _money(value: Any) -> str:
    if value is None or value == "":
        return "—"
    return f"{Decimal(str(value)):,.2f}".replace(",", " ").replace(".", ",") + " ₽"


_env.filters["money"] = _money


def file_name(report: Report, ext: str) -> str:
    version = report.version.version_no if report.version else 0
    return f"nmck-{str(report.calculation.id)[:8]}-v{version}.{ext}"


# ---------------------------------------------------------------- HTML / PDF


def render_html(report: Report) -> str:
    return _env.get_template("report.html").render(
        r=report,
        outcome=OUTCOME,
        vat=VAT,
        included=lambda p: [o for o in p.offers if o.decision == "included"],
        excluded=lambda p: [o for o in p.offers if o.decision != "included"],
    )


async def render_pdf_to_s3(
    settings: Any, browser: Any, calc_id: uuid.UUID, version: int | None
) -> str:
    """Отчёт → PDF в бакете отчётов (вызывается в воркере, где есть Chromium)."""
    from app.db import models as m
    from app.db.session import get_sessionmaker
    from app.services.calculations import build_report
    from app.storage.s3 import s3_client

    async with get_sessionmaker()() as session:
        calc = await session.get(m.Calculation, calc_id)
        if calc is None:
            raise ValueError("расчёт не найден")
        report = await build_report(session, calc, version)
    pdf = await browser.pdf(render_html(report))
    key = f"{calc_id}/{file_name(report, 'pdf')}"
    async with s3_client(settings) as s3:
        await s3.put_object(
            Bucket=settings.s3_bucket_reports, Key=key, Body=pdf, ContentType="application/pdf"
        )
    return key


# ---------------------------------------------------------------- XLSX


def _header(ws: Worksheet, row: int, titles: list[str]) -> None:
    for col, title in enumerate(titles, start=1):
        cell = ws.cell(row=row, column=col, value=title)
        cell.font = BOLD
        cell.fill = HEAD_FILL
        cell.alignment = Alignment(wrap_text=True, vertical="top")


def _widths(ws: Worksheet, widths: list[int]) -> None:
    for col, width in enumerate(widths, start=1):
        ws.column_dimensions[get_column_letter(col)].width = width


def _num(value: Decimal | str | None) -> Decimal | None:
    return Decimal(str(value)) if value is not None and value != "" else None


def _steps(o: OfferReport) -> str:
    return "; ".join(str(s.get("reason", "")) for s in o.normalization_steps)


def build_xlsx(report: Report) -> bytes:
    wb = Workbook()
    summary = wb.active
    assert summary is not None
    summary.title = "Сводка"
    sheets = [_position_sheet(wb, p, n) for n, p in enumerate(report.positions, start=1)]
    _summary_sheet(summary, report, sheets)
    _excluded_sheet(wb, report)
    _params_sheet(wb, report)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _summary_sheet(ws: Worksheet, report: Report, sheets: list[tuple[str, str | None]]) -> None:
    v = report.version
    ws["A1"] = "Обоснование НМЦК методом сопоставимых рыночных цен"
    ws["A1"].font = Font(bold=True, size=14)
    ws["A2"] = f"Расчёт {report.calculation.id}, версия {v.version_no if v else '—'}"
    ws["A3"] = OUTCOME.get(v.outcome, v.outcome) if v else "Расчёт не выполнен"
    _header(
        ws,
        5,
        ["№", "Наименование", "Количество", "Ед.", "Статус", "Цена за ед., ₽", "Цена позиции, ₽"],
    )
    row = 6
    for (sheet, unit_cell), p in zip(sheets, report.positions, strict=True):
        ws.cell(row=row, column=1, value=p.spec.ordinal)
        ws.cell(row=row, column=2, value=p.spec.name)
        ws.cell(row=row, column=3, value=p.spec.quantity)
        ws.cell(row=row, column=4, value=p.spec.unit_raw)
        ws.cell(row=row, column=5, value=p.result.status_label if p.result else "—")
        if unit_cell:
            ws.cell(row=row, column=6, value=f"='{sheet}'!{unit_cell}").number_format = MONEY
            ws.cell(row=row, column=7, value=f"=ROUND(F{row}*C{row},2)").number_format = MONEY
        row += 1
    ws.cell(row=row, column=5, value="НМЦК").font = BOLD
    total = ws.cell(row=row, column=7, value=f"=SUM(G6:G{row - 1})" if row > 6 else 0)
    total.font = BOLD
    total.number_format = MONEY
    if v and v.outcome != "full":
        ws.cell(row=row + 1, column=2, value="Сумма только по позициям с определённой ценой")
    _widths(ws, [5, 50, 12, 8, 30, 16, 18])


def _position_sheet(wb: Workbook, p: PositionReport, n: int) -> tuple[str, str | None]:
    """Лист позиции; возвращает имя листа и ячейку цены за единицу (если цена определена)."""
    title = f"Поз. {n}"
    ws = wb.create_sheet(title)
    ws["A1"] = f"{p.spec.ordinal}. {p.spec.name}"
    ws["A1"].font = Font(bold=True, size=12)
    ws["A2"] = f"Количество: {p.spec.quantity.normalize():f} {p.spec.unit_raw}"
    ws["A3"] = f"Статус: {p.result.status_label if p.result else '—'}"
    _header(
        ws,
        5,
        [
            "№", "Источник", "Наименование", "Поставщик", "ИНН", "Дата цены", "Исходная цена",
            "Ед. / НДС", "Приведение цены", "Цена за ед. позиции", "Релевантность", "Ссылка",
        ],
    )  # fmt: skip
    offers = [o for o in p.offers if o.decision == "included"]
    first = row = 6
    for i, o in enumerate(offers, start=1):
        unit = " · ".join(x for x in (o.unit_raw, VAT.get(o.vat_status, "НДС не указан")) if x)
        values = [
            i, o.source_name, o.title, o.supplier_name, o.supplier_inn,
            o.price_date, o.price, unit, _steps(o), o.normalized_price, o.relevance, o.url,
        ]  # fmt: skip
        for col, value in enumerate(values, start=1):
            ws.cell(row=row, column=col, value=value)
        ws.cell(row=row, column=7).number_format = MONEY
        ws.cell(row=row, column=10).number_format = PRICE4
        if o.url.startswith("http"):
            ws.cell(row=row, column=12).hyperlink = o.url
        row += 1
    last = row - 1
    _widths(ws, [5, 22, 50, 28, 13, 12, 14, 14, 50, 16, 12, 40])

    stats = (p.result.stats if p.result else None) or {}
    row += 1
    _header(ws, row, ["", "Показатель", "Формула Excel", "Значение в системе"])
    row += 1
    rng = f"J{first}:J{last}"
    lines: list[tuple[str, str | None, Any, str]] = [
        ("n", f"=COUNT({rng})", stats.get("n"), "0"),
        ("Минимум", f"=MIN({rng})", _num(stats.get("min")), MONEY),
        ("Максимум", f"=MAX({rng})", _num(stats.get("max")), MONEY),
        ("Среднее", f"=AVERAGE({rng})", _num(stats.get("mean")), MONEY),
        ("Медиана", f"=MEDIAN({rng})", _num(stats.get("median")), MONEY),
        (
            "σ (выборочное)",
            f"=STDEV({rng})" if len(offers) > 1 else None,
            _num(stats.get("stdev")),
            MONEY,
        ),
    ]
    refs: dict[str, str] = {}
    for name, formula, value, fmt in lines:
        ws.cell(row=row, column=2, value=name)
        if offers and formula:
            ws.cell(row=row, column=3, value=formula).number_format = fmt
        ws.cell(row=row, column=4, value=value).number_format = fmt
        refs[name] = f"C{row}"
        row += 1
    ws.cell(row=row, column=2, value="CV, %")
    if len(offers) > 1:
        mean, sd = refs["Среднее"], refs["σ (выборочное)"]
        ws.cell(row=row, column=3, value=f'=IF({mean}=0,"",{sd}/{mean}*100)').number_format = "0.00"
    ws.cell(row=row, column=4, value=_num(stats.get("cv"))).number_format = "0.00"
    row += 1
    unit_cell = None
    ws.cell(row=row, column=2, value="Цена за единицу").font = BOLD
    if p.result and p.result.status in PRICED and offers:
        ws.cell(row=row, column=3, value=f"=ROUND({refs['Среднее']},2)").number_format = MONEY
        unit_cell = f"C{row}"
    else:
        ws.cell(row=row, column=3, value=p.result.status_label if p.result else "—")
    ws.cell(
        row=row, column=4, value=p.result.unit_price if p.result else None
    ).number_format = MONEY
    row += 1
    ws.cell(row=row, column=2, value="Цена позиции").font = BOLD
    if unit_cell:
        ws.cell(
            row=row, column=3, value=f"=ROUND({unit_cell}*{p.spec.quantity.normalize():f},2)"
        ).number_format = MONEY
    ws.cell(
        row=row, column=4, value=p.result.total_price if p.result else None
    ).number_format = MONEY

    if p.result:
        row += 2
        ws.cell(row=row, column=2, value="Ход расчёта").font = BOLD
        for line in [*p.result.filter_log, *p.result.hints]:
            row += 1
            ws.cell(row=row, column=2, value=line)
    return title, unit_cell


def _excluded_sheet(wb: Workbook, report: Report) -> None:
    ws = wb.create_sheet("Исключённые")
    _header(
        ws, 1, ["Позиция", "Источник", "Наименование", "Цена", "Код причины", "Причина", "Ссылка"]
    )
    row = 2
    for p in report.positions:
        for o in p.offers:
            if o.decision == "included":
                continue
            values = [
                p.spec.ordinal, o.source_name, o.title, o.price, o.reason_code, o.reason_text, o.url,
            ]  # fmt: skip
            for col, value in enumerate(values, start=1):
                ws.cell(row=row, column=col, value=value)
            ws.cell(row=row, column=4).number_format = MONEY
            row += 1
    _widths(ws, [9, 22, 50, 14, 22, 70, 40])


def _params_sheet(wb: Workbook, report: Report) -> None:
    ws = wb.create_sheet("Параметры")
    v = report.version
    rows: list[tuple[str, Any]] = [
        ("Описание закупки", report.calculation.input_text),
        ("Спецификация изменена пользователем", "да" if report.calculation.spec_edited else "нет"),
    ]
    if v:
        rows += [
            ("Версия расчёта", v.version_no),
            ("Версия настроек", v.settings_version_id),
            ("Модель LLM", v.llm_model),
            ("Версии промптов", ", ".join(f"{k}={x}" for k, x in v.prompt_versions.items())),
            *((f"Правило: {k}", str(x)) for k, x in v.rules.items()),
        ]
    for i, (name, value) in enumerate(rows, start=1):
        ws.cell(row=i, column=1, value=name).font = BOLD
        ws.cell(row=i, column=2, value=value).alignment = Alignment(wrap_text=True)
    _widths(ws, [40, 100])
