"""Application chrome: sidebar navigation and the sticky global control bar."""

from __future__ import annotations

import streamlit as st

from profit_pools.core.data.adapters import DataAdapter
from profit_pools.core.taxonomy import Metadata
from profit_pools.export import csv_export, html_snapshot, pptx_summary
from profit_pools.shell import grouping_editor, scenario_builder, tour
from profit_pools.shell import state as S


def sidebar(pages: list, adapter: DataAdapter) -> None:
    with st.sidebar:
        st.markdown('<div class="pp-brand"><b>Healthcare</b> Analytics</div>', unsafe_allow_html=True)
        st.markdown('<div class="pp-nav-group">Modules</div>', unsafe_allow_html=True)
        for page in pages:
            st.page_link(page)
        st.space("small")
        tour.sidebar_button()
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


def global_bar(meta: Metadata):
    """Sticky, wrapping control row (wraps instead of clipping on narrower screens). Returns the slot
    the page fills with the Export menu once the results it exports are known."""
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
                                 help="Baseline, the applied scenario, the scenario compared with baseline, or only the "
                                      "change (everything the scenario does not touch is greyed out).")
        if st.button(grouping_editor.button_label(), key="pp.groupings", icon=":material/account_tree:",
                     help="Rename pools, move them between groups, or add groups. Changes the hierarchy in every "
                          "chart, never the numbers."):
            grouping_editor.open_editor(meta)
        active = S.has_scenario()
        if st.button(scenario_builder.chip_label(), key="pp.chip", icon=":material/tune:",
                     type="primary" if active else "secondary",
                     help="Open the scenario builder (assumptions apply to every chart)."):
            scenario_builder.open_builder(meta)
        return st.container(width="content", key="pp_export")


def export_menu(ctx) -> None:
    """Downloads for the current view. Files are generated only when clicked (callable data)."""
    with st.popover("Export", icon=":material/download:"):
        st.caption(ctx.title())
        st.download_button("CSV · rows behind this view", data=lambda: csv_export.analysis_csv(ctx),
                           file_name=f"profit_pools_{ctx.slug()}.csv", mime="text/csv", key="pp.x.csv",
                           on_click="ignore", width="stretch", icon=":material/table:",
                           help="Pools in the current scope with revenue, gain, margin, enrollment"
                                + (" and Δ vs baseline." if ctx.compare is not None else "."))
        st.download_button("Scenario outputs · ZIP of CSVs", data=lambda: csv_export.scenario_zip(ctx),
                           file_name=f"profit_pools_outputs_{ctx.slug()}.zip", mime="application/zip",
                           key="pp.x.zip", on_click="ignore", width="stretch", icon=":material/folder_zip:",
                           help="Pool, flow and ownership results for every year at state × pool grain"
                                + (", baseline vs scenario." if ctx.has_scenario else " (baseline; no scenario applied)."))
        st.download_button("Scenario JSON", data=(ctx.scenario.to_json() if ctx.has_scenario else ""),
                           file_name="scenario.json", mime="application/json", key="pp.x.json", on_click="ignore",
                           width="stretch", icon=":material/data_object:", disabled=not ctx.has_scenario,
                           help="Assumptions only, no data." if ctx.has_scenario else
                           "Apply a scenario (and show it) to export its assumptions.")
        st.download_button("HTML snapshot", data=lambda: html_snapshot.build_bytes(ctx),
                           file_name=f"profit_pools_{ctx.slug()}.html", mime="text/html", key="pp.x.html",
                           on_click="ignore", width="stretch", icon=":material/web:",
                           help="Self-contained page with the KPIs, charts and tables of this view. Opens offline; "
                                "it is a snapshot, not a simulator.")
        if pptx_summary.available():
            up = st.session_state.get("pp.x.template")
            template, bad = (up.getvalue() if up is not None else None), None
            if template is not None:
                bad = pptx_summary.check_template(template)
            st.download_button("PowerPoint summary" + (" · your template" if template and not bad else ""),
                               data=lambda: pptx_summary.build(ctx, None if bad else template),
                               file_name=f"profit_pools_{ctx.slug()}.pptx",
                               mime="application/vnd.openxmlformats-officedocument.presentationml.presentation",
                               key="pp.x.pptx", on_click="ignore", width="stretch", icon=":material/slideshow:",
                               help="Editable slides with headline titles: executive summary, Marimekko (native "
                                    "shapes), where profit moves, where to play, trend, growth drivers, flows, "
                                    "scenario and value capture.")
            st.file_uploader("Slide template (.pptx, optional)", type=["pptx"], key="pp.x.template",
                             help="Your master slides set fonts, colors and title positions. Read in memory for "
                                  "this export only; never stored. Save a .potx as .pptx first.")
            if bad:
                st.error(f"Template not used: {bad}")
        st.caption("All exports use fictional data and contain no credentials.")
