"""Data dictionary — glossary, pools, metrics, scenario levers, funders, data tables and data coverage, all read
from configuration and contracts (core/dictionary.py) so the definitions never drift from the engine."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from profit_pools.core import dictionary as D
from profit_pools.design import components as ui
from profit_pools.design.tokens import COLOR
from profit_pools.shell import data
from profit_pools.shell import state as S

K = "pp.dict."
COVERAGE_FILL = {"margin + services": COLOR["accent_weak"], "margin": COLOR["surface"],
                 "no margin": COLOR["missing"], "": COLOR["bg"]}


def _filter(df: pd.DataFrame, q: str) -> pd.DataFrame:
    if not q:
        return df
    mask = df.astype(str).apply(lambda c: c.str.contains(q, case=False, regex=False)).any(axis=1)
    return df[mask]


def _show(df: pd.DataFrame, q: str, height: int | None = None, **cfg) -> None:
    out = _filter(df, q)
    if out.empty:
        st.caption(f"Nothing matches “{q}”.")
        return
    st.dataframe(out, hide_index=True, width="stretch", height=height or "auto", column_config=cfg or None)


def render() -> None:
    S.init(data.adapter().metadata)
    gj = S.grouping_json()
    meta = data.metadata(gj)
    st.markdown(ui.page_header("Data dictionary", "Definitions, pools, levers and data coverage · fictional data"),
                unsafe_allow_html=True)
    c1, c2 = st.columns([3, 1], vertical_alignment="bottom")
    with c1:
        q = st.text_input("Search every table", key=K + "q", placeholder="e.g. PMPM, Hospitals, routing, margin")
    with c2:
        year = meta.periods.base_year
        st.download_button("Download dictionary (ZIP of CSVs)", width="stretch", icon=":material/download:",
                           data=lambda: D.zip_all(meta, data.baseline(gj), year),
                           file_name="profit_pools_data_dictionary.zip", mime="application/zip", on_click="ignore",
                           key=K + "dl")
    tabs = st.tabs(["Glossary", "Pools", "Metrics", "Scenario levers", "Plans & funders", "Data tables",
                    "Data coverage"])
    with tabs[0]:
        _show(D.glossary(), q, Definition=st.column_config.TextColumn(width="large"))
    with tabs[1]:
        st.caption("The standard taxonomy" + ("" if meta.grouping_name == "Standard" else
                                               f", shown with your grouping “{meta.grouping_name}”") +
                   ". Pools that hold data are where values live; groups roll them up.")
        _show(D.pools(meta), q, height=560, Description=st.column_config.TextColumn(width="large"))
    with tabs[2]:
        st.caption("Non-additive metrics (margins, PMPMs, ratios) are always recomputed from additive parts, never "
                   "averaged. Provenance says how each value is obtained for each kind of pool.")
        _show(D.metrics(), q)
    with tabs[3]:
        st.caption("Every assumption changes one driver, consumed by exactly one pipeline stage. Wording follows who "
                   "pays whom, so knock-on effects are implied.")
        _show(D.levers(), q, height=520, **{"What it does": st.column_config.TextColumn(width="large")})
    with tabs[4]:
        _show(D.funders(meta), q)
    with tabs[5]:
        st.caption("Fictional warehouse-shaped contracts (DATA_CONTRACTS.md). Column names are UPPER_SNAKE.")
        _show(D.tables(), q, height=560, Description=st.column_config.TextColumn(width="large"))
    with tabs[6]:
        a, b = st.columns([1, 1])
        with a:
            st.segmented_control("Dataset", list(meta.datasets), key=K + "ds", default=meta.default_dataset,
                                 required=True, format_func=lambda d: meta.datasets[d].label)
        with b:
            st.selectbox("Year", meta.periods.all_years, key=K + "year",
                         index=meta.periods.all_years.index(meta.periods.base_year))
        cov = D.coverage(data.baseline(gj), meta, st.session_state[K + "ds"], int(st.session_state[K + "year"]))
        cov = _filter(cov.reset_index(names="Pool"), q)
        st.dataframe(cov.style.map(lambda v: f"background-color: {COVERAGE_FILL.get(v, COLOR['surface'])}"),
                     hide_index=True, width="stretch", height=560)
        st.markdown(ui.note("margin + services: revenue, operating margin and service counts · margin: no service "
                            "counts (pharmacy never has them) · no margin: revenue only, gain shown as unavailable "
                            "(hatched) · blank: no data in that state. Top-down pools are national."),
                    unsafe_allow_html=True)
