"""Session-state keys and the global filter object. Keys are namespaced `pp.*`."""

from __future__ import annotations

from dataclasses import dataclass

import streamlit as st

from profit_pools.core.taxonomy import Metadata

K_DATASET = "pp.dataset"
K_YEAR = "pp.year"
K_GEO = "pp.geo"
K_BASIS = "pp.basis"
K_SEGMENT = "pp.segment"
K_FOCUS = "pp.focus"          # Marimekko parent node (None = all pools)
K_SELECTED = "pp.selected"    # (col_id, seg_key, seg_node_id) or None
K_TREND_METRIC = "pp.trend_metric"

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
    }
    for k, v in defaults.items():
        st.session_state.setdefault(k, v)


def current() -> Filters:
    s = st.session_state
    return Filters(s[K_DATASET], int(s[K_YEAR]), s[K_GEO], s[K_BASIS], s[K_SEGMENT], s[K_FOCUS], s[K_SELECTED])


def set_focus(node_id: str | None, selected: tuple | None = None) -> None:
    st.session_state[K_FOCUS] = node_id
    st.session_state[K_SELECTED] = selected


def set_selected(selected: tuple | None) -> None:
    st.session_state[K_SELECTED] = selected
