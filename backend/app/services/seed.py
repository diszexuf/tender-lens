"""Начальные данные (NFR-06.3): справочники, настройки, источники по умолчанию. Идемпотентно."""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.calc.adjust import SEED_NOTE, default_price_indexes
from app.calc.units import DEFAULT_UNITS
from app.core.logging import get_logger
from app.db import models as m
from app.domain.rules import Rules
from app.matching.text import DEFAULT_SYNONYM_GROUPS

log = get_logger(__name__)

DEFAULT_SOURCES: list[dict] = [
    {
        "name": "ЕИС — реестр контрактов",
        "type": "eis",
        "role": "price",
        "priority": 10,
        "config": {},
    },
    {
        "name": "ЭТМ (электротехника)",
        "type": "html",
        "role": "price",
        "priority": 50,
        "config": {
            "search_url": "https://www.etm.ru/catalog?searchValue={query}",
            "links_from": "itemlist",
            "max_cards": 8,
            "supplier_name": "ООО «ЭТМ»",
            "vat_status": "included",
        },
    },
    {
        "name": "Веб-поиск (SearXNG)",
        "type": "searxng",
        "role": "price",
        "priority": 90,
        "config": {},
    },
]


async def _empty(session: AsyncSession, model: type) -> bool:
    return (await session.execute(select(func.count()).select_from(model))).scalar_one() == 0


async def seed(session: AsyncSession) -> dict[str, int]:
    added: dict[str, int] = {}
    if await _empty(session, m.Unit):
        for u in DEFAULT_UNITS:
            session.add(
                m.Unit(
                    code=u.code,
                    name=u.name,
                    short=u.short,
                    dimension=u.dimension,
                    factor=u.factor,
                    aliases=list(u.aliases),
                )
            )
        added["units"] = len(DEFAULT_UNITS)
    if await _empty(session, m.PriceIndex):
        indexes = default_price_indexes()
        for period, value in indexes.items():
            session.add(m.PriceIndex(period=period, value=value, note=SEED_NOTE))
        added["price_indexes"] = len(indexes)
    # Синонимы: новые группы добавляются, существующие пополняются словами из поставки
    # (слова, добавленные администратором, сохраняются)
    groups = list((await session.execute(select(m.SynonymGroup))).scalars())
    changed = 0
    for group in DEFAULT_SYNONYM_GROUPS:
        words = set(group)
        target = next((g for g in groups if words & set(g.words)), None)
        if target is None:
            session.add(m.SynonymGroup(words=group))
            changed += 1
        elif missing := [w for w in group if w not in target.words]:
            target.words = [*target.words, *missing]
            changed += 1
    if changed:
        added["synonym_groups"] = changed
    if await _empty(session, m.SettingsVersion):
        session.add(
            m.SettingsVersion(rules=Rules().model_dump(mode="json"), comment="по умолчанию")
        )
        added["settings"] = 1
        # Создаём исходный каталог один раз. Имена и состав дальше задаёт администратор.
        existing = set((await session.execute(select(m.Source.name))).scalars())
        new_sources = [src for src in DEFAULT_SOURCES if src["name"] not in existing]
        for src in new_sources:
            session.add(m.Source(**src))
        added["sources"] = len(new_sources)
    await session.commit()
    log.info("seed.done", **added)
    return added
