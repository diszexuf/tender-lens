"""Оценка решений «сопоставимо / нет» на размеченных предложениях из реальных расчётов.

    python scripts/eval_decisions.py [--positions Кабель,Бумага] [--out report.json]

Набор tests/golden/decisions.json: позиция (спецификация как в расчёте) и предложения
(текст как его видит пайплайн) с разметкой эксперта:
  y — сопоставимо (нет противоречий требованиям), n — несопоставимо, e — спорно (не считается).
Пайплайн: извлечение значений (код + LLM, как в расчёте) → сравнение кодом → решение.
Ошибки: ложное исключение (y → исключено) и ложное включение (n → включено).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import get_settings
from app.core.logging import configure_logging
from app.domain.spec import PositionSpec
from app.llm.provider import OllamaProvider
from app.llm.tasks import extract_offer_values, merge_code_values, values_by_code
from app.matching.comparators import CompareContext, compare
from app.matching.prefilter import other_product
from app.matching.scoring import ScoringRules, score
from app.pipeline.collect_stage import unescape_text

DATASET = Path(__file__).resolve().parents[1] / "tests" / "golden" / "decisions.json"
BATCH = 4


async def eval_position(llm: OllamaProvider, item: dict, mode: str) -> dict:
    position = PositionSpec.model_validate(item["position"])
    offers = [
        o | {"text": unescape_text(o["text"]) or "", "title": unescape_text(o["title"]) or ""}
        for o in item["offers"]
    ]
    texts = {
        str(i): o["text"]
        for i, o in enumerate(offers)
        if not other_product(position, o["title"], o["text"])
    }
    started = time.perf_counter()
    values = {}
    by_code = {}
    if mode == "hybrid":  # как в пайплайне: сначала код, LLM — для остального
        for k in texts:
            o = offers[int(k)]
            found, ready = values_by_code(position, k, o["text"], unit_known=bool(o["unit_raw"]))
            by_code[k] = found
            if ready is not None:
                values[k] = ready
    ids = [k for k in texts if k not in values]
    calls = 0
    for i in range(0, len(ids), BATCH):
        batch = {k: texts[k] for k in ids[i : i + BATCH]}
        got, _ = await extract_offer_values(llm, position, batch)
        calls += 1
        values.update({v.offer_id: merge_code_values(v, by_code.get(v.offer_id, {})) for v in got})
    elapsed = time.perf_counter() - started

    ctx, rules = CompareContext(), ScoringRules()
    rows = []
    for i, o in enumerate(offers):
        if why := other_product(position, o["title"], o["text"]):
            rows.append(
                {
                    "i": i,
                    "label": o["label"],
                    "included": False,
                    "title": o["title"][:90],
                    "why": f"другой вид товара: {why}",
                }
            )
            continue
        ov = values.get(str(i))
        extracted = {v.name: v for v in (ov.values if ov else [])}
        matches = [
            compare(a, extracted.get(a.name), ctx, title=o["title"]) for a in position.attributes
        ]
        verdict = score(matches, rules)
        rows.append(
            {
                "i": i,
                "label": o["label"],
                "included": verdict.included,
                "title": o["title"][:90],
                "why": verdict.explanation[:160],
            }
        )
    return {
        "position": position.name,
        "seconds": round(elapsed, 1),
        "llm_calls": calls,
        "rows": rows,
    }


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--positions", help="подстроки названий позиций через запятую")
    parser.add_argument("--out", help="сохранить результаты в JSON")
    parser.add_argument(
        "--mode",
        choices=["hybrid", "llm"],
        default="hybrid",
        help="hybrid — код + LLM, как в пайплайне; llm — только LLM",
    )
    args = parser.parse_args()
    configure_logging("WARNING", json=False)
    llm = OllamaProvider(get_settings())
    data = json.loads(DATASET.read_text(encoding="utf-8"))
    if args.positions:
        wanted = args.positions.split(",")
        data = [d for d in data if any(w in d["position"]["name"] for w in wanted)]

    results = []
    tot = {"y": 0, "n": 0, "fe": 0, "fi": 0, "s": 0.0, "calls": 0}
    for item in data:
        r = await eval_position(llm, item, args.mode)
        results.append(r)
        fe = [x for x in r["rows"] if x["label"] == "y" and not x["included"]]
        fi = [x for x in r["rows"] if x["label"] == "n" and x["included"]]
        ny = sum(x["label"] == "y" for x in r["rows"])
        nn = sum(x["label"] == "n" for x in r["rows"])
        tot["y"] += ny
        tot["n"] += nn
        tot["fe"] += len(fe)
        tot["fi"] += len(fi)
        tot["s"] += r["seconds"]
        tot["calls"] += r["llm_calls"]
        print(
            f"{r['position'][:28]:28} ошибок {len(fe) + len(fi)}/{ny + nn}  "
            f"(ложно искл. {len(fe)}, ложно вкл. {len(fi)})  {r['seconds']} с, {r['llm_calls']} вызовов"
        )
        for x in fe:
            print(f"    ✗ исключено [{x['i']}] {x['title']}\n        {x['why']}")
        for x in fi:
            print(f"    ✗ включено  [{x['i']}] {x['title']}\n        {x['why']}")

    judged = tot["y"] + tot["n"]
    errors = tot["fe"] + tot["fi"]
    print(
        f"\nИтого: ошибок {errors}/{judged} = {100 * errors / max(judged, 1):.1f} %  "
        f"(ложные исключения {tot['fe']}/{tot['y']}, ложные включения {tot['fi']}/{tot['n']}); "
        f"LLM {tot['s']:.0f} с, {tot['calls']} вызовов"
    )
    if args.out:
        Path(args.out).write_text(
            json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
