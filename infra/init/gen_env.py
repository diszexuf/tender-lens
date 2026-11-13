"""Создаёт .env из .env.example, подставляя случайные секреты вместо CHANGE_ME.

Запуск из корня репозитория:
    python infra/init/gen_env.py           # не перезаписывает существующий .env
    python infra/init/gen_env.py --force   # перезаписать
"""

from __future__ import annotations

import secrets
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
EXAMPLE = ROOT / ".env.example"
TARGET = ROOT / ".env"

GENERATORS = {
    "POSTGRES_PASSWORD": lambda: secrets.token_urlsafe(24),
    "GARAGE_RPC_SECRET": lambda: secrets.token_hex(32),
    "GARAGE_ADMIN_TOKEN": lambda: secrets.token_urlsafe(32),
    "GARAGE_METRICS_TOKEN": lambda: secrets.token_urlsafe(32),
    "S3_ACCESS_KEY_ID": lambda: "GK" + secrets.token_hex(12),
    "S3_SECRET_ACCESS_KEY": lambda: secrets.token_hex(32),
    "SEARXNG_SECRET": lambda: secrets.token_hex(32),
}


def main() -> int:
    if TARGET.exists() and "--force" not in sys.argv:
        print(f"{TARGET} уже существует. Используйте --force для перезаписи.")
        return 1

    lines_out: list[str] = []
    for line in EXAMPLE.read_text(encoding="utf-8").splitlines():
        key, sep, rest = line.partition("=")
        key = key.strip()
        if sep and "CHANGE_ME" in rest:
            if key not in GENERATORS:
                print(f"Нет генератора для {key}", file=sys.stderr)
                return 2
            line = f"{key}={rest.replace('CHANGE_ME', GENERATORS[key](), 1)}"
        lines_out.append(line)

    TARGET.write_text("\n".join(lines_out) + "\n", encoding="utf-8")
    print(f"Создан {TARGET}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
