"""Exports (Phase 7): CSV, scenario outputs (zip), HTML snapshot and PPTX summary.

Every export reads the same `ModelResult` and view functions as the workspace, through one
`ExportContext` that mirrors the current filters, so a file always matches what is on screen.
Exports never include credentials; the scenario JSON is configuration only.
"""

from __future__ import annotations

from dataclasses import dataclass

from profit_pools.core.model.engine import ModelResult
from profit_pools.core.model.scenario import Scenario
from profit_pools.core.model.views import Geo
from profit_pools.core.taxonomy import Metadata

FICTIONAL = "Fictional data generated for illustration only. Not real market or company data."


@dataclass(frozen=True)
class ExportContext:
    meta: Metadata
    result: ModelResult              # what the workspace shows (baseline or scenario)
    base: ModelResult                # baseline, always
    view: str                        # baseline | scenario | compare | impact
    dataset: str
    year: int
    geo: Geo
    basis: str = "allowed"
    focus: str | None = None
    segment_by: str = "hierarchy"
    scenario: Scenario | None = None

    @property
    def compare(self) -> ModelResult | None:
        return self.base if self.view in ("compare", "impact") else None

    @property
    def has_scenario(self) -> bool:
        return self.scenario is not None and self.view != "baseline"

    def geo_label(self) -> str:
        g = self.meta.geography
        if self.geo.level == "national":
            return "National"
        if self.geo.level == "state":
            return g.states[self.geo.id].label
        return f"{g.county_label(self.geo.id)}, {g.states[g.state_of_county(self.geo.id)].label}"

    def scope_label(self) -> str:
        return "All pools" if self.focus is None else self.meta.taxonomy[self.focus].label

    def title(self) -> str:
        ds = self.meta.datasets[self.dataset].label
        view = {"baseline": "Baseline", "scenario": "Scenario", "compare": "Scenario vs baseline",
                "impact": "Scenario impact"}[self.view]
        name = f" · {self.scenario.name}" if self.has_scenario else ""
        return f"{ds} · {self.year} · {self.geo_label()} · {self.scope_label()} · {view}{name}"

    def slug(self) -> str:
        parts = [self.dataset.lower(), str(self.year), (self.geo.id or "us").lower(), self.view]
        if self.focus:
            parts.insert(2, self.focus.lower())
        return "_".join(parts)
