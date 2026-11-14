"""Ручная проверка LLM-задач на живой модели.

python scripts/try_llm.py spec "Картридж ... 50 шт"
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import get_settings
from app.llm.provider import OllamaProvider
from app.llm.tasks import extract_offer_values, extract_spec


async def main() -> None:
    llm = OllamaProvider(get_settings())
    text = (
        sys.argv[2]
        if len(sys.argv) > 2
        else (
            "Картридж лазерный для принтеров HP LaserJet Pro P1102/M1132, ресурс не менее 1600 страниц, "
            "цвет чёрный, оригинальный или совместимый. Количество — 50 шт."
        )
    )
    spec, res = await extract_spec(llm, text)
    print(
        f"spec: {res.duration_ms} мс, токены {res.tokens_in}/{res.tokens_out}, попыток {res.attempts}"
    )
    print(
        json.dumps(
            [p.model_dump(mode="json") for p in spec.positions], ensure_ascii=False, indent=1
        )
    )
    print("warnings:", spec.warnings)
    if sys.argv[1:2] == ["attrs"]:
        offers = {
            "a": "Картридж лазерный HP 85A CE285A черный оригинальный. Ресурс 1600 стр. Совместим: HP LaserJet P1102, P1102w, M1132, M1212nf. Цена 10494 руб./шт",
            "b": "Картридж совместимый Retech 85A для HP, черный, ресурс 2000 страниц, упаковка 2 шт",
            "c": "Картридж Canon 725, черный, ресурс 1600 стр., для i-SENSYS LBP6000",
        }
        values, r2 = await extract_offer_values(llm, spec.positions[0], offers)
        print(f"attrs: {r2.duration_ms} мс, попыток {r2.attempts}")
        print(json.dumps([v.model_dump(mode="json") for v in values], ensure_ascii=False, indent=1))


asyncio.run(main())
