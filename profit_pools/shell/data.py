"""Cached access to the adapter and engine results (one computation per process)."""

from __future__ import annotations

import streamlit as st

from profit_pools.core.data.adapters import DataAdapter, get_adapter
from profit_pools.core.model.engine import ModelResult, run_baseline


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
