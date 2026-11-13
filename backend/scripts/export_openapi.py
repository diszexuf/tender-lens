"""Выгружает OpenAPI-схему без запуска сервера.

python scripts/export_openapi.py [путь]   # по умолчанию ../contracts/openapi.json
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.main import app

target = Path(
    sys.argv[1] if len(sys.argv) > 1 else Path(__file__).parents[2] / "contracts" / "openapi.json"
)
target.write_text(json.dumps(app.openapi(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print(f"OpenAPI -> {target}")
