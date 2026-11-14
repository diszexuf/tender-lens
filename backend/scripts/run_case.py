"""Сквозной прогон расчёта через API и краткий отчёт (для ручной проверки и демо).

python scripts/run_case.py "Описание закупки..." [--base http://localhost:8080]
"""

from __future__ import annotations

import argparse
import sys
import time

import httpx


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("text")
    parser.add_argument("--base", default="http://localhost:8080")
    parser.add_argument("--timeout", type=int, default=1500)
    parser.add_argument("--offers", action="store_true", help="печатать все предложения")
    args = parser.parse_args()
    api = f"{args.base}/api"

    started = time.time()
    calc = httpx.post(f"{api}/calculations", json={"text": args.text}, timeout=30).json()
    cid = calc["id"]
    print(f"Расчёт {cid}")
    last = None
    while time.time() - started < args.timeout:
        d = httpx.get(f"{api}/calculations/{cid}", timeout=10).json()
        if (d["status"], d["stage"]) != last:
            print(f"  {time.time() - started:5.0f} с  {d['status']} / {d['stage']}")
            last = (d["status"], d["stage"])
        if d["status"] in ("completed", "failed", "cancelled", "interrupted"):
            break
        time.sleep(3)
    if d["status"] != "completed":
        print("Ошибка:", d["error"])
        return 1

    r = httpx.get(f"{api}/calculations/{cid}/report", timeout=30).json()
    v = r["version"]
    print(
        f"\nНМЦК: {v['nmck_total']} ({v['outcome']}), модель {v['llm_model']}, {time.time() - started:.0f} с"
    )
    for p in r["positions"]:
        spec, res = p["spec"], p["result"]
        attrs = "; ".join(
            f"{a['name']} {a['operator']} {a.get('value_number') or a.get('values') or a.get('value_text')}"
            for a in spec["attributes"]
        )
        print(
            f"\n[{spec['ordinal']}] {spec['name']} — {spec['quantity']} {spec['unit_raw']} | {attrs}"
        )
        print(
            "    источники: "
            + ", ".join(
                f"{s['source_name']}={s['status']}({s['offers_found']})" for s in p["sources"]
            )
        )
        stats = res["stats"] or {}
        print(
            f"    {res['status_label']}: цена ед. {res['unit_price']}, сумма {res['total_price']}, n={stats.get('n')}, CV={stats.get('cv')} %"
        )
        for h in res["hints"]:
            print(f"    подсказка: {h[:160]}")
        counts: dict[str, int] = {}
        for o in p["offers"]:
            key = o["reason_code"] or "included"
            counts[key] = counts.get(key, 0) + 1
        print(f"    решения: {counts}")
        for o in p["offers"]:
            if args.offers or o["decision"] == "included":
                mark = "+" if o["decision"] == "included" else "-"
                print(
                    f"    {mark} {o['normalized_price'] or o['price']} | {o['source_name'][:18]} | {o['title'][:60]}"
                )
                if o["decision"] != "included":
                    print(f"        {o['reason_text'][:140]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
