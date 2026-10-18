"""Cached access to the adapter and engine results (one computation per process)."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from profit_pools.core.data.adapters import DataAdapter, get_adapter
from profit_pools.core.model.cube import DriverCube, build_history
from profit_pools.core.model.engine import ModelResult, run_baseline
from profit_pools.core.model.scenario import Scenario
from profit_pools.core.model.scenario_engine import ScenarioRun, bridge, preview, run_scenario


@st.cache_resource(show_spinner="Loading fictional dataset…")
def adapter() -> DataAdapter:
    a = get_adapter()
    issues = [i for i in a.validate() if i.level == "error"]
    if issues:
        raise RuntimeError("Data contract violations: " + "; ".join(i.message for i in issues[:5]))
    return a


@st.cache_resource(show_spinner="Running baseline model…")
def baseline() -> ModelResult:
    return run_baseline(adapter())


@st.cache_resource(show_spinner=False)
def history_cube() -> DriverCube:
    return build_history(adapter())


@st.cache_resource(show_spinner="Running scenario…", max_entries=16)
def scenario_run(scenario_json: str) -> ScenarioRun:
    """Keyed by the scenario's JSON so edits always recompute and repeats are instant."""
    return run_scenario(history_cube(), adapter().metadata, Scenario.from_json(scenario_json))


@st.cache_resource(show_spinner="Attributing changes to assumptions…", max_entries=8)
def scenario_bridge(scenario_json: str) -> list[tuple[object, pd.DataFrame]]:
    return bridge(history_cube(), adapter().metadata, Scenario.from_json(scenario_json))


@st.cache_resource(show_spinner="Previewing…", max_entries=32)
def assumption_preview(scenario_json: str, assumption_id: str, year: int, dataset: str) -> dict:
    return preview(history_cube(), adapter().metadata, Scenario.from_json(scenario_json), assumption_id, year, dataset)
