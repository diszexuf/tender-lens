"""Версионируемые промпты (NFR-05.2).

Каждый промпт — пара файлов `<name>.system.j2` и `<name>.user.j2`.
Первая строка system-шаблона: `{# version: N #}` — версия попадает в журнал вызовов.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, StrictUndefined

PROMPTS_DIR = Path(__file__).parent
_VERSION = re.compile(r"\{#\s*version:\s*(\S+)\s*#\}")


@dataclass(frozen=True)
class RenderedPrompt:
    name: str
    version: str
    system: str
    user: str


@cache
def _env() -> Environment:
    return Environment(
        loader=FileSystemLoader(PROMPTS_DIR),
        undefined=StrictUndefined,
        trim_blocks=True,
        lstrip_blocks=True,
        keep_trailing_newline=False,
        autoescape=False,
    )


@cache
def prompt_version(name: str) -> str:
    head = (PROMPTS_DIR / f"{name}.system.j2").read_text(encoding="utf-8").splitlines()[0]
    match = _VERSION.search(head)
    if not match:
        raise ValueError(f"в промпте {name} нет строки версии")
    return f"{name}@{match.group(1)}"


def render(name: str, **context: Any) -> RenderedPrompt:
    env = _env()
    return RenderedPrompt(
        name=name,
        version=prompt_version(name),
        system=env.get_template(f"{name}.system.j2").render(**context).strip(),
        user=env.get_template(f"{name}.user.j2").render(**context).strip(),
    )
