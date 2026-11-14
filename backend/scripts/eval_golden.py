"""Оценка качества на эталонном наборе (NFR-05.6): разбор закупки и решения о сопоставимости.

    python scripts/eval_golden.py [--model qwen3:4b] [--cases cable,laptop] [--out report.json]

Метрики:
- позиции: доля случаев с верным числом позиций, количеством и кодом ОКЕИ;
- характеристики: доля ожидаемых характеристик, найденных с верным типом, оператором и значением;
- сопоставимость: точность решения «сопоставим / нет» по размеченным предложениям
  (извлечение LLM + сравнение кодом, как в пайплайне);
- время LLM на разбор и на извлечение характеристик.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from dataclasses import asdict, dataclass, field
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import get_settings
from app.core.logging import configure_logging
from app.domain.spec import PositionSpec, SpecAttribute
from app.llm.provider import LLMError, OllamaProvider
from app.llm.tasks import extract_offer_values, extract_spec
from app.matching.comparators import CompareContext, compare
from app.matching.scoring import ScoringRules, score
from app.matching.text import norm

CASES = Path(__file__).resolve().parents[1] / "tests" / "golden" / "cases.json"


@dataclass
class CaseResult:
    id: str
    positions_ok: bool = False
    quantity_ok: int = 0
    okei_ok: int = 0
    positions_expected: int = 0
    attrs_expected: int = 0
    attrs_found: int = 0
    attrs_exact: int = 0
    offers_total: int = 0
    offers_correct: int = 0
    spec_ms: int = 0
    attrs_ms: int = 0
    errors: list[str] = field(default_factory=list)
    details: list[str] = field(default_factory=list)


def _find_attr(attrs: list[SpecAttribute], names: list[str]) -> SpecAttribute | None:
    wanted = [norm(n) for n in names]

    def stem_match(w: str, name: str) -> bool:
        # «цоколь» ~ «тип цоколя»: совпадение основ слов (без окончаний)
        tokens = [t for t in w.split() if len(t) > 3]
        return bool(tokens) and all(any(t[:5] == x[:5] for x in name.split()) for t in tokens)

    # Сначала точное совпадение, затем вхождение и основы — по порядку ожидаемых названий
    checks = (
        lambda w, n: w == n,
        lambda w, n: w in n or n in w,
        stem_match,
    )
    for check in checks:
        for w in wanted:
            for a in attrs:
                if check(w, norm(a.name)):
                    return a
    return None


def _attr_exact(found: SpecAttribute, expected: dict) -> bool:
    if found.type.value != expected["type"]:
        return False
    if "operator" in expected and found.operator.value != expected["operator"]:
        return False
    if "value" in expected and found.value_number != Decimal(str(expected["value"])):
        return False
    if "min" in expected and found.value_min != Decimal(str(expected["min"])):
        return False
    if "max" in expected and found.value_max != Decimal(str(expected["max"])):
        return False
    if "values" in expected:
        got = {norm(v).replace("ё", "е") for v in found.values}
        return all(norm(v) in got for v in expected["values"])
    return True


async def run_case(llm: OllamaProvider, case: dict) -> CaseResult:
    r = CaseResult(id=case["id"])
    started = time.perf_counter()
    try:
        extraction, _ = await extract_spec(llm, case["text"])
    except LLMError as exc:
        r.errors.append(f"разбор: {exc}")
        return r
    r.spec_ms = int((time.perf_counter() - started) * 1000)
    positions = extraction.positions
    expected_positions = case["positions"]
    r.positions_expected = len(expected_positions)
    r.positions_ok = len(positions) == len(expected_positions)

    for exp, got in zip(expected_positions, positions, strict=False):
        r.quantity_ok += int(float(got.quantity) == float(exp["quantity"]))
        r.okei_ok += int(got.okei_code == exp["okei"])
        if got.okei_code != exp["okei"]:
            r.details.append(f"ОКЕИ {got.okei_code} ({got.unit_raw}) ≠ {exp['okei']}")
        for ea in exp["attributes"]:
            r.attrs_expected += 1
            found = _find_attr(got.attributes, ea["names"])
            if found is None:
                r.details.append(f"нет характеристики {ea['names'][0]}")
                continue
            r.attrs_found += 1
            if _attr_exact(found, ea):
                r.attrs_exact += 1
            else:
                r.details.append(f"{ea['names'][0]}: получено «{found.describe()}» ({found.type})")

    if case["offers"] and positions:
        position: PositionSpec = positions[0]
        offers = {f"o{i}": o["text"] for i, o in enumerate(case["offers"])}
        started = time.perf_counter()
        try:
            values, _ = await extract_offer_values(llm, position, offers)
        except LLMError as exc:
            r.errors.append(f"характеристики: {exc}")
            return r
        r.attrs_ms = int((time.perf_counter() - started) * 1000)
        by_id = {v.offer_id: v for v in values}
        ctx = CompareContext()
        for i, offer in enumerate(case["offers"]):
            ov = by_id.get(f"o{i}")
            extracted = {v.name: v for v in (ov.values if ov else [])}
            matches = [
                compare(a, extracted.get(a.name), ctx, title=offer["text"])
                for a in position.attributes
            ]
            verdict = score(matches, ScoringRules())
            r.offers_total += 1
            if verdict.included == offer["comparable"]:
                r.offers_correct += 1
            else:
                r.details.append(
                    f"оффер «{offer['text'][:50]}»: ожидалось "
                    f"{'сопоставим' if offer['comparable'] else 'нет'}, получено "
                    f"{'сопоставим' if verdict.included else 'нет'} ({verdict.explanation[:90]})"
                )
    return r


def pct(a: int, b: int) -> str:
    return f"{100 * a / b:5.1f} %" if b else "   —  "


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", help="модель Ollama (по умолчанию из настроек)")
    parser.add_argument("--cases", help="id случаев через запятую")
    parser.add_argument("--out", help="сохранить результаты в JSON")
    args = parser.parse_args()

    configure_logging("WARNING", json=False)
    settings = get_settings()
    if args.model:
        settings = settings.model_copy(update={"ollama_model": args.model})
    llm = OllamaProvider(settings)
    cases = json.loads(CASES.read_text(encoding="utf-8"))
    if args.cases:
        wanted = set(args.cases.split(","))
        cases = [c for c in cases if c["id"] in wanted]

    print(f"Модель: {settings.ollama_model}, случаев: {len(cases)}\n")
    results = []
    for case in cases:
        r = await run_case(llm, case)
        results.append(r)
        print(
            f"{r.id:18} позиции {'✓' if r.positions_ok else '✗'}  "
            f"кол-во {r.quantity_ok}/{r.positions_expected}  ОКЕИ {r.okei_ok}/{r.positions_expected}  "
            f"характеристики {r.attrs_exact}/{r.attrs_expected} (найдено {r.attrs_found})  "
            f"сопоставимость {r.offers_correct}/{r.offers_total}  "
            f"{r.spec_ms / 1000:.1f}+{r.attrs_ms / 1000:.1f} с"
        )
        for d in r.details + r.errors:
            print(f"    · {d}")

    def total(attr: str) -> int:
        return sum(getattr(r, attr) for r in results)

    print("\nИтого:")
    print(
        f"  позиции выделены верно      {pct(sum(r.positions_ok for r in results), len(results))}"
    )
    print(f"  количество                  {pct(total('quantity_ok'), total('positions_expected'))}")
    print(f"  код ОКЕИ                    {pct(total('okei_ok'), total('positions_expected'))}")
    print(f"  характеристики найдены      {pct(total('attrs_found'), total('attrs_expected'))}")
    print(f"  характеристики точно        {pct(total('attrs_exact'), total('attrs_expected'))}")
    print(f"  решения о сопоставимости    {pct(total('offers_correct'), total('offers_total'))}")
    print(f"  среднее время разбора       {total('spec_ms') / max(len(results), 1) / 1000:.1f} с")
    if args.out:
        Path(args.out).write_text(
            json.dumps(
                {"model": settings.ollama_model, "results": [asdict(r) for r in results]},
                ensure_ascii=False,
                indent=1,
            ),
            encoding="utf-8",
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
