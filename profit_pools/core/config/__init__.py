"""Configuration loading. All metadata lives in YAML next to this module."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

CONFIG_DIR = Path(__file__).resolve().parent


@lru_cache(maxsize=None)
def _load_cached(name: str) -> Any:
    with (CONFIG_DIR / f"{name}.yaml").open(encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def load_config(name: str) -> Any:
    """Return a parsed config file by stem (e.g. ``"taxonomy"``).

    The cached object is shared; callers must not mutate it.
    """
    return _load_cached(name)
