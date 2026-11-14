"""Служебные команды.

python -m app.cli migrate         # применить миграции Alembic
python -m app.cli init-storage    # инициализировать Garage (layout, ключ, бакеты)
python -m app.cli bootstrap       # всё вместе — используется init-контейнером
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from alembic import command
from alembic.config import Config

from app.core.config import get_settings
from app.core.logging import configure_logging, get_logger
from app.storage.garage_admin import GarageAdmin

BACKEND_DIR = Path(__file__).resolve().parents[1]
log = get_logger("cli")


def migrate() -> None:
    cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_DIR / "migrations"))
    cfg.attributes["configure_logger"] = False  # не перетирать настройки structlog
    command.upgrade(cfg, "head")
    log.info("migrations.applied")


async def seed_data() -> None:
    from app.db.session import get_engine, get_sessionmaker
    from app.services.seed import seed

    async with get_sessionmaker()() as session:
        await seed(session)
    await get_engine().dispose()


async def init_storage() -> None:
    admin = GarageAdmin(get_settings())
    try:
        await admin.bootstrap()
    finally:
        await admin.aclose()


def main(argv: list[str] | None = None) -> int:
    settings = get_settings()
    configure_logging(settings.log_level, settings.log_json)

    parser = argparse.ArgumentParser(prog="app.cli")
    parser.add_argument("command", choices=["migrate", "seed", "init-storage", "bootstrap"])
    args = parser.parse_args(argv)

    if args.command in ("migrate", "bootstrap"):
        migrate()
    if args.command in ("seed", "bootstrap"):
        asyncio.run(seed_data())
    if args.command in ("init-storage", "bootstrap"):
        asyncio.run(init_storage())
    return 0


if __name__ == "__main__":
    sys.exit(main())
