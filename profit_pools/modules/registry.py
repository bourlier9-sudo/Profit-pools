"""Analytics module registry.

A module is a page with its own render function. Only modules that work are
registered; future modules (e.g. Enrollment Intelligence, Quality
Intelligence) are added here when they exist. No placeholder pages.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable


@dataclass(frozen=True)
class ModuleSpec:
    id: str
    title: str
    icon: str
    description: str
    render: Callable[[], None]
    url_path: str


def modules() -> list[ModuleSpec]:
    from profit_pools.modules.profit_pools.page import render as render_profit_pools

    return [
        ModuleSpec(
            id="profit_pools",
            title="Profit Pools Explorer",
            icon=":material/stacked_bar_chart:",
            description="Revenue, operating gain and spending flows across a fictional healthcare economy.",
            render=render_profit_pools,
            url_path="profit-pools",
        ),
    ]
