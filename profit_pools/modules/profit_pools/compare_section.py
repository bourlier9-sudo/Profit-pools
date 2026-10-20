"""Compare scenarios — up to three library scenarios side by side with the baseline, at the current dataset,
year, geography and scope. Each scenario is the same cached engine run the workspace uses."""

from __future__ import annotations

import numpy as np
import streamlit as st

from profit_pools.charts import compare as cchart
from profit_pools.core.model import compare as C
from profit_pools.core.model.views import Geo
from profit_pools.core.taxonomy import Metadata
from profit_pools.design import components as ui
from profit_pools.design import format as fmt
from profit_pools.design.theme import PLOTLY_CONFIG
from profit_pools.shell import data
from profit_pools.shell import state as S

MAX = 3


def section_compare(base, meta: Metadata, f: S.Filters, geo: Geo) -> None:
    ss = st.session_state
    lib = S.library()
    names = [s.name for s in lib]
    scope = "All pools" if f.focus is None else meta.taxonomy[f.focus].label
    st.markdown(ui.section_header("scenarios", "Compare scenarios",
                                  f"{scope} · {meta.datasets[f.dataset].label} · {f.year} · up to {MAX} scenarios vs "
                                  f"baseline"), unsafe_allow_html=True)
    if not lib:
        st.info("The scenario library is empty. Apply a scenario, or save one from the scenario builder.")
        return
    if S.K_COMPARE not in ss:
        ss[S.K_COMPARE] = names[:2]
    if any(n not in names for n in ss[S.K_COMPARE]):
        ss[S.K_COMPARE] = [n for n in ss[S.K_COMPARE] if n in names]
    st.multiselect("Scenarios", names, key=S.K_COMPARE, max_selections=MAX, placeholder="Choose up to three",
                   help="Scenarios from the library (scenario builder › Scenario library). Applied scenarios are "
                        "saved there automatically.")
    chosen = ss[S.K_COMPARE]
    if not chosen:
        st.markdown(ui.note("Choose at least one scenario to compare with the baseline."), unsafe_allow_html=True)
        return
    gj = S.grouping_json()
    results, blocked = {C.BASELINE: base}, []
    for sc in (s for s in lib if s.name in chosen):
        run = data.scenario_run(sc.to_json(), gj)
        if run.ok:
            results[sc.name] = run.result
        else:
            blocked.append(sc.name)
    colors = cchart.scenario_colors(list(results), C.BASELINE)

    k = C.kpi_table(results, meta, f.dataset, f.year, f.focus, geo, f.basis)
    if k.empty:
        st.info("No data for this scope.")
        return
    ent = "consolidated_gain" in k
    head = ["Scenario", "Revenue", "Δ revenue", "Operating gain", "Δ gain", "Margin", "Δ margin",
            f"Revenue CAGR {meta.periods.base_year}–{str(meta.periods.forecast_end)[2:]}"]
    if ent:
        head.append("Consolidated gain")
    rows = []
    for r in k.itertuples():
        is_base = r.scenario == C.BASELINE
        row = [r.scenario, fmt.money(r.revenue), "" if is_base else fmt.money(r.d_revenue, signed=True),
               fmt.money(r.gain), "" if is_base else fmt.money(r.d_gain, signed=True), fmt.pct(r.margin),
               "" if is_base else fmt.pp(r.d_margin), fmt.cagr(r.revenue_cagr)]
        if ent:
            row.append(fmt.money(r.consolidated_gain) if np.isfinite(r.consolidated_gain) else fmt.DASH)
        rows.append(row)
    st.markdown(ui.table(head, rows), unsafe_allow_html=True)

    left, right = st.columns([6, 6], gap="large")
    with left:
        st.markdown(f'<div class="pp-h3">Change in operating gain by pool, {f.year}</div>', unsafe_allow_html=True)
        d = C.pool_deltas(results, meta, f.dataset, f.year, f.focus, geo, "gain", f.basis)
        if d.empty or (d["delta"].abs() < 0.5e6).all():
            st.markdown(ui.note(f"No scenario changes operating gain in {f.year}; try a forecast year."),
                        unsafe_allow_html=True)
        else:
            st.plotly_chart(cchart.pool_deltas(d, colors), config=PLOTLY_CONFIG, key="pp.compare.pools")
    with right:
        st.markdown('<div class="pp-h3">Operating gain over time</div>', unsafe_allow_html=True)
        st.plotly_chart(cchart.series(C.series(results, meta, f.dataset, f.focus, geo, "gain", f.basis), colors,
                                      meta.periods.base_year, C.BASELINE), config=PLOTLY_CONFIG,
                        key="pp.compare.series")
    notes = ["Each scenario runs on its own against the same baseline; they are not combined."]
    if blocked:
        notes.append(f"Not shown (blocking issues — open it in the builder to fix): {', '.join(blocked)}.")
    if ent:
        notes.append("Consolidated gain = plan gain + owned-asset gain (Value capture).")
    st.markdown("".join(ui.note(n) for n in notes), unsafe_allow_html=True)
