"""Session-state keys and the global filter object. Keys are namespaced `pp.*`."""

from __future__ import annotations

from dataclasses import dataclass

import streamlit as st

from profit_pools.core.config import CONFIG_DIR
from profit_pools.core.grouping import Grouping
from profit_pools.core.model.scenario import Scenario
from profit_pools.core.taxonomy import Metadata

K_DATASET = "pp.dataset"
K_YEAR = "pp.year"
K_GEO = "pp.geo"
K_BASIS = "pp.basis"
K_SEGMENT = "pp.segment"
K_FOCUS = "pp.focus"          # Marimekko parent node (None = all pools)
K_SELECTED = "pp.selected"    # (col_id, seg_key, seg_node_id) or None
K_TREND_METRIC = "pp.trend_metric"
K_DRAFT = "pp.scenario.draft"        # Scenario being edited in the builder
K_APPLIED = "pp.scenario.applied"    # Scenario driving the workspace (None = baseline only)
K_VIEW = "pp.view"                   # baseline | scenario | compare | impact
K_COLOR_BY = "pp.color_by"           # margin | delta_margin (compare view)
K_EDIT = "pp.scenario.editing"       # assumption id being edited, "__new__", or None
K_IMPACT_OF = "pp.impact.of"         # "__all__" or one assumption id (run standalone against baseline)
K_IMPACT_METRIC = "pp.impact.metric" # d_gain | d_revenue
K_IMPACT_SIZE = "pp.impact.size"     # pool | change
IMPACT_ALL = "__all__"
K_LIBRARY = "pp.scenario.library"   # saved scenarios (session only), seeded with the fictional examples
K_COMPARE = "pp.compare.names"       # library scenarios chosen for side-by-side comparison
K_GROUPING = "pp.grouping.applied"   # Grouping driving the workspace (standard by default)
K_GROUPING_DRAFT = "pp.grouping.draft"

VIEW_LABELS = {"baseline": "Baseline", "scenario": "Scenario", "compare": "Compare", "impact": "Impact only"}
IMPACT_METRIC_LABELS = {"d_gain": "Operating gain", "d_revenue": "Revenue"}
IMPACT_SIZE_LABELS = {"pool": "Pool size", "change": "Size of change"}

BASIS_LABELS = {"allowed": "Allowed", "paid": "Payer-paid", "consumer": "Consumer"}
BASIS_REVENUE_LABEL = {"allowed": "Revenue", "paid": "Payer-paid revenue", "consumer": "Consumer-paid revenue"}
SEGMENT_LABELS = {"hierarchy": "Sub-pools", "geography": "Geography", "funding": "Funding source"}


@dataclass(frozen=True)
class Filters:
    dataset: str
    year: int
    geo_id: str | None
    basis: str
    segment_by: str
    focus: str | None
    selected: tuple | None
    view: str = "baseline"


def init(meta: Metadata) -> None:
    defaults = {
        K_DATASET: meta.default_dataset,
        K_YEAR: meta.periods.base_year,
        K_GEO: meta.geography.national_id,
        K_BASIS: "allowed",
        K_SEGMENT: "hierarchy",
        K_FOCUS: None,
        K_SELECTED: None,
        K_TREND_METRIC: "revenue",
        K_VIEW: "baseline",
        K_COLOR_BY: "margin",
        K_EDIT: None,
        K_APPLIED: None,
        K_IMPACT_OF: IMPACT_ALL,
        K_IMPACT_METRIC: "d_gain",
        K_IMPACT_SIZE: "pool",
    }
    for k, v in defaults.items():
        st.session_state.setdefault(k, v)
    if K_LIBRARY not in st.session_state:
        st.session_state[K_LIBRARY] = example_scenarios()
    st.session_state.setdefault(K_GROUPING, Grouping())
    st.session_state.setdefault(K_GROUPING_DRAFT, st.session_state[K_GROUPING])
    if K_DRAFT not in st.session_state:
        st.session_state[K_DRAFT] = Scenario("Scenario A", base_year=meta.periods.base_year)


def example_scenarios() -> list[Scenario]:
    return [Scenario.from_json(f.read_text()) for f in sorted((CONFIG_DIR / "scenarios").glob("*.json"))]


def library() -> list[Scenario]:
    return st.session_state.get(K_LIBRARY, [])


def save_to_library(sc: Scenario) -> None:
    """Add or replace (same name) a scenario in the session library."""
    lib = [s for s in library() if s.name != sc.name]
    st.session_state[K_LIBRARY] = [*lib, sc]


def grouping_json() -> str:
    """Cache key for the active pool grouping ("" = standard taxonomy)."""
    g = st.session_state.get(K_GROUPING)
    return "" if g is None or g.is_standard else g.to_json()


def current() -> Filters:
    s = st.session_state
    view = s[K_VIEW] if has_scenario() else "baseline"
    return Filters(s[K_DATASET], int(s[K_YEAR]), s[K_GEO], s[K_BASIS], s[K_SEGMENT], s[K_FOCUS], s[K_SELECTED], view)


def applied():
    return st.session_state.get(K_APPLIED)


def has_scenario() -> bool:
    sc = applied()
    return sc is not None and len(sc.enabled) > 0


def draft_dirty() -> bool:
    d, a = st.session_state.get(K_DRAFT), applied()
    if a is None:
        return d is not None and len(d.assumptions) > 0
    return d.to_json() != a.to_json()


def set_focus(node_id: str | None, selected: tuple | None = None) -> None:
    st.session_state[K_FOCUS] = node_id
    st.session_state[K_SELECTED] = selected


def set_selected(selected: tuple | None) -> None:
    st.session_state[K_SELECTED] = selected
