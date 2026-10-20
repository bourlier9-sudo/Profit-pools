"""Cached access to the adapter and engine results (one computation per process).

Every result is keyed by the active pool grouping's JSON ("" = standard). A grouping changes only the
hierarchy (roll-ups and `layer_id`), never values, so the baseline is computed once and re-layered.
"""

from __future__ import annotations

from dataclasses import replace

import pandas as pd
import streamlit as st

from profit_pools.core.data.adapters import DataAdapter, get_adapter
from profit_pools.core.grouping import Grouping, regroup
from profit_pools.core.model.cube import DriverCube, build_history
from profit_pools.core.model.engine import ModelResult, relayer, run_baseline
from profit_pools.core.model.scenario import Scenario
from profit_pools.core.model.scenario_engine import ScenarioRun, bridge, preview, run_scenario
from profit_pools.core.taxonomy import Metadata


@st.cache_resource(show_spinner="Loading fictional dataset…")
def adapter() -> DataAdapter:
    a = get_adapter()
    issues = [i for i in a.validate() if i.level == "error"]
    if issues:
        raise RuntimeError("Data contract violations: " + "; ".join(i.message for i in issues[:5]))
    return a


@st.cache_resource(show_spinner=False, max_entries=8)
def grouped(grouping_json: str = "") -> tuple[Metadata, list[str]]:
    """(metadata under the grouping, warnings). Raises TaxonomyError for an invalid grouping."""
    return regroup(adapter().metadata, Grouping.from_json(grouping_json) if grouping_json else None)


def metadata(grouping_json: str = "") -> Metadata:
    return grouped(grouping_json)[0]


@st.cache_resource(show_spinner="Running baseline model…")
def _baseline() -> ModelResult:
    return run_baseline(adapter())


@st.cache_resource(show_spinner=False, max_entries=8)
def baseline(grouping_json: str = "") -> ModelResult:
    return relayer(_baseline(), metadata(grouping_json))


@st.cache_resource(show_spinner=False)
def history_cube() -> DriverCube:
    return build_history(adapter())


@st.cache_resource(show_spinner="Running scenario…", max_entries=16)
def scenario_run(scenario_json: str, grouping_json: str = "") -> ScenarioRun:
    """Keyed by the scenario's JSON so edits always recompute and repeats are instant."""
    meta = metadata(grouping_json)
    run = run_scenario(history_cube(), meta, Scenario.from_json(scenario_json))
    return run if run.result is None else replace(run, result=relayer(run.result, meta))


@st.cache_resource(show_spinner="Attributing changes to assumptions…", max_entries=8)
def scenario_bridge(scenario_json: str, grouping_json: str = "") -> list[tuple[object, pd.DataFrame]]:
    meta = metadata(grouping_json)
    steps = bridge(history_cube(), meta, Scenario.from_json(scenario_json))
    return [(a, relayer(ModelResult(pools=p, flows=pd.DataFrame(), base_year=meta.periods.base_year), meta).pools)
            for a, p in steps]


@st.cache_resource(show_spinner="Previewing…", max_entries=32)
def assumption_preview(scenario_json: str, assumption_id: str, year: int, dataset: str,
                       grouping_json: str = "") -> dict:
    return preview(history_cube(), metadata(grouping_json), Scenario.from_json(scenario_json), assumption_id,
                   year, dataset)
