"""Application chrome: sidebar navigation and the sticky global control bar."""

from __future__ import annotations

import streamlit as st

from profit_pools.core.data.adapters import DataAdapter
from profit_pools.core.taxonomy import Metadata
from profit_pools.shell import scenario_builder
from profit_pools.shell import state as S


def sidebar(pages: list, adapter: DataAdapter) -> None:
    with st.sidebar:
        st.markdown('<div class="pp-brand"><b>Healthcare</b> Analytics</div>', unsafe_allow_html=True)
        st.markdown('<div class="pp-nav-group">Modules</div>', unsafe_allow_html=True)
        for page in pages:
            st.page_link(page)
        st.markdown(
            f'<div class="pp-sidebar-foot"><b>Data:</b> {adapter.info.name}<br>{adapter.info.description}</div>',
            unsafe_allow_html=True)


def _geo_options(meta: Metadata) -> list[str]:
    out = [meta.geography.national_id]
    for s in meta.geography.states.values():
        out.append(s.id)
        out += [c for c, _, _ in s.counties]
    return out


def _geo_label(meta: Metadata, geo_id: str) -> str:
    g = meta.geography
    if geo_id == g.national_id:
        return "National"
    if geo_id in g.states:
        return g.states[geo_id].label
    return f"   ↳ {g.county_label(geo_id)}"


def _on_dataset_change(meta: Metadata) -> None:
    ds = st.session_state[S.K_DATASET]
    focus = st.session_state[S.K_FOCUS]
    if focus is not None and not meta.in_dataset(focus, ds):
        S.set_focus(None)
    sel = st.session_state[S.K_SELECTED]
    if sel is not None and not meta.in_dataset(sel[0], ds):
        S.set_selected(None)


def global_bar(meta: Metadata) -> None:
    """Sticky, wrapping control row (wraps instead of clipping on narrower screens)."""
    with st.container(key="pp_globalbar", horizontal=True, wrap=True, vertical_alignment="bottom", gap="medium"):
        st.segmented_control(
            "Dataset", list(meta.datasets), key=S.K_DATASET, required=True,
            format_func=lambda d: meta.datasets[d].label, on_change=_on_dataset_change, args=(meta,),
            help="Total market, or the enterprise's own book. The enterprise view excludes "
                 "Manufacturer, Health Services and PBM Services.")
        st.selectbox("Year", meta.periods.all_years, key=S.K_YEAR, width=150,
                     format_func=lambda y: f"{y} · forecast" if y > meta.periods.base_year else str(y))
        st.selectbox("Geography", _geo_options(meta), key=S.K_GEO, width=220,
                     format_func=lambda g: _geo_label(meta, g),
                     help="Top-down pools exist nationally only. Provider margins are state-level; "
                          "county views apply the state margin.")
        focus = st.session_state[S.K_FOCUS]
        path = ["All pools"] + ([n.short_label for n in meta.taxonomy.path(focus)] if focus else [])
        st.markdown('<div class="pp-caption" style="padding-bottom:8px"><span class="pp-muted">SCOPE&nbsp;&nbsp;</span>'
                    + " › ".join(path) + "</div>", unsafe_allow_html=True, width="content")
        st.space("stretch")
        if S.has_scenario():
            st.segmented_control("View", list(S.VIEW_LABELS), key=S.K_VIEW, required=True,
                                 format_func=S.VIEW_LABELS.get,
                                 help="Baseline, the applied scenario, or the scenario compared with baseline.")
        active = S.has_scenario()
        if st.button(scenario_builder.chip_label(), key="pp.chip", icon=":material/tune:",
                     type="primary" if active else "secondary",
                     help="Open the scenario builder (assumptions apply to every chart)."):
            scenario_builder.open_builder(meta)
