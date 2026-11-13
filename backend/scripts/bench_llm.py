"""Быстрый замер моделей Ollama на разборе позиции закупки (этап 0 плана).

python scripts/bench_llm.py qwen2.5:7b qwen2.5:3b qwen3:4b
"""

from __future__ import annotations

import json
import sys

import httpx

from app.core.config import get_settings

SCHEMA = {
    "type": "object",
    "properties": {
        "name": {"type": "string"},
        "quantity": {"type": "number"},
        "unit": {"type": "string"},
        "attributes": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "type": {"type": "string", "enum": ["number", "range", "enum", "bool", "text"]},
                    "operator": {"type": "string", "enum": ["=", ">=", "<=", "in", "between"]},
                    "value": {"type": "string"},
                    "unit": {"type": "string"},
                    "required": {"type": "boolean"},
                },
                "required": ["name", "type", "operator", "value", "required"],
            },
        },
    },
    "required": ["name", "quantity", "unit", "attributes"],
}

SYSTEM = (
    "Ты извлекаешь позицию закупки в JSON. Характеристики типизируй: number — число "
    "с единицей в поле unit, enum — значение из перечня. Требование «не менее» — оператор >=."
)
TEXT = (
    "Картридж лазерный для принтеров HP LaserJet Pro P1102/M1132, ресурс не менее 1600 страниц, "
    "цвет чёрный, оригинальный или совместимый. Количество — 50 шт."
)


def run(model: str) -> None:
    url = get_settings().ollama_url
    body = {
        "model": model,
        "stream": False,
        "format": SCHEMA,
        "think": False,
        "options": {"temperature": 0, "seed": 42, "num_ctx": get_settings().ollama_num_ctx},
        "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": TEXT}],
    }
    httpx.post(f"{url}/api/chat", json=body, timeout=600)  # прогрев: загрузка модели
    r = httpx.post(f"{url}/api/chat", json=body, timeout=600).json()
    gen_tps = r["eval_count"] / (r["eval_duration"] / 1e9)
    prompt_tps = r["prompt_eval_count"] / (r["prompt_eval_duration"] / 1e9)
    ps = httpx.get(f"{url}/api/ps").json()["models"]
    loaded = next((m for m in ps if m["name"] == model), {})
    vram = loaded.get("size_vram", 0) / 1024**3
    size = loaded.get("size", 0) / 1024**3
    print(
        f"\n=== {model}: {r['total_duration'] / 1e9:.1f} с, промпт {prompt_tps:.0f} ток/с, "
        f"генерация {gen_tps:.1f} ток/с, в VRAM {vram:.1f} из {size:.1f} ГБ"
    )
    print(json.dumps(json.loads(r["message"]["content"]), ensure_ascii=False, indent=1))


if __name__ == "__main__":
    for name in sys.argv[1:] or [get_settings().ollama_model]:
        run(name)
