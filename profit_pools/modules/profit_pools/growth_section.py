"""Section D — growth and forecasting: CAGRs, indexed growth, revenue-growth drivers, YoY heatmap."""

from __future__ import annotations

import numpy as np
import streamlit as st

from profit_pools.charts import growth as gchart
from profit_pools.charts import trends
from profit_pools.core.model import growth as G
from profit_pools.core.model.views import Geo, children_in_dataset
from profit_pools.core.taxonomy import Metadata
from profit_pools.design import components as ui
from profit_pools.design import format as fmt
from profit_pools.design.theme import PLOTLY_CONFIG
from profit_pools.design.tokens import COLOR, LAYER_COLOR
from profit_pools.shell import state as S

K = "pp.growth."


def _cagr_cell(v: float) -> str:
    return fmt.cagr(v) if np.isfinite(v) else "n/m"


def _keep_order(changed: str) -> None:
    """Keep From < To by moving the other end (runs as a callback, before the widgets are drawn)."""
    ss = st.session_state
    if ss[K + "from"] < ss[K + "to"]:
        return
    if changed == "from":
        ss[K + "to"] = ss[K + "from"] + 1
    else:
        ss[K + "from"] = ss[K + "to"] - 1


BRIDGE_NOTES = {
    "overall": "Operating gain is additive across sectors, so the overall bridge never double counts. Plans: "
               "enrollment, premium rates, medical cost and admin cost per member. Providers and pharmacy: people "
               "served × services per person × price per service (spend per person where there are no service "
               "counts), at the pool margin. Top-down pools: market size × margin.",
    "plan": "Plan gain = members × (premium − medical cost − admin cost) per member, per plan segment and county.",
    "provider": "Revenue = people served × services per person × price per service (spend per person where there "
                "are no service counts, e.g. pharmacy), for every payer and public program; gain applies the "
                "pool margin. Supplemental program payments are hospital lump sums.",
    "top_down": "Top-down estimates have no volume or price detail: growth is shown by sub-pool, at the starting "
                "margin, with margin change separately.",
    "mixed": "This pool mixes economics (e.g. dispensing plus top-down PBM services): provider drivers and "
             "top-down market growth are shown side by side.",
}


def _attractiveness(result, meta: Metadata, f: S.Filters, geo: Geo, y0: int, y1: int, cmp=None) -> None:
    ss = st.session_state
    ss.setdefault(K + "att_levels", 1)
    st.markdown(f'<div class="pp-h3" style="margin-top:12px">Where to play: growth vs margin, {y0}→{y1}</div>',
                unsafe_allow_html=True)
    deeper = any(children_in_dataset(meta, f.dataset, n.id) for n in children_in_dataset(meta, f.dataset, f.focus))
    if not deeper:
        ss[K + "att_levels"] = 1
    st.segmented_control("Pools", [1, 2], key=K + "att_levels", required=True, disabled=not deeper,
                         format_func={1: "This level", 2: "One level deeper"}.get,
                         help=None if deeper else "These pools have no sub-pools.")
    levels = ss[K + "att_levels"]
    df = G.attractiveness(result, meta, f.dataset, f.focus, geo, f.basis, y0, y1, levels)
    if df.empty or df["margin"].notna().sum() == 0:
        st.info("No pools with margin data for this view.")
        return
    fixed = LAYER_COLOR if (f.focus is None or levels == 2) else {}
    key = "layer_id" if fixed else "node_id"
    ids = list(dict.fromkeys(df[key]))
    pal = trends.series_colors(ids, fixed)
    colors = {r.node_id: pal.get(getattr(r, key)) for r in df.itertuples()}
    base = G.attractiveness(cmp, meta, f.dataset, f.focus, geo, f.basis, y0, y1, levels) if cmp is not None else None
    st.plotly_chart(gchart.attractiveness(df, colors, base), config=PLOTLY_CONFIG, key=K + "attract")
    notes = [f"Bubble area = operating gain in {y1}; hollow red rings are losses. Dashed lines are the revenue-weighted "
             f"averages ({fmt.pct(df.attrs['avg_growth'], 1)} growth, {fmt.pct(df.attrs['avg_margin'])} margin): "
             f"top-right pools grow faster and earn more than the average."]
    if base is not None:
        notes.append("Dotted lines run from each pool's baseline position to its scenario position.")
    if fixed and levels == 2:
        notes.append("Colors follow the sector.")
    st.markdown("".join(ui.note(n) for n in notes), unsafe_allow_html=True)


def _bridge_targets(meta: Metadata, f: S.Filters) -> list[str | None]:
    """Overall (current scope) first, then each pool one level down, so each can show its own detail."""
    return [f.focus, *[n.id for n in children_in_dataset(meta, f.dataset, f.focus)]]


def _bridge(result, meta: Metadata, f: S.Filters, geo: Geo, y0: int, y1: int) -> None:
    ss = st.session_state
    targets = _bridge_targets(meta, f)
    key = K + "bridge_node"
    sel = f.selected[2] or f.selected[0] if f.selected else None
    if ss.get(key, "__scope__") not in [t or "__scope__" for t in targets]:
        ss[key] = "__scope__"
    scope_label = "All pools" if f.focus is None else meta.taxonomy[f.focus].label
    with st.container(horizontal=True, wrap=True, vertical_alignment="bottom", gap="medium"):
        st.selectbox("Bridge for", [t or "__scope__" for t in targets], key=key, width=300,
                     format_func=lambda t: f"Overall · {scope_label}" if t == "__scope__" else meta.taxonomy[t].label,
                     help="Overall uses the drivers every pool shares; a single pool shows the detail that fits "
                          "its economics (plans, providers, top-down estimates).")
        node = None if ss[key] == "__scope__" else ss[key]
        node = f.focus if node is None else node
        overall = node is None
        if overall:
            ss[K + "bridge_metric"] = "gain"
        ss.setdefault(K + "bridge_metric", "gain")
        st.segmented_control("Measure", ["gain", "revenue"], key=K + "bridge_metric", required=True,
                             format_func={"gain": "Operating gain", "revenue": "Revenue"}.get,
                             disabled=overall,
                             help="Revenue is not additive across sectors (plan claims are provider revenue), "
                                  "so the all-pools bridge is operating gain only." if overall else None)
    metric = ss[K + "bridge_metric"]
    kind = G.bridge_kind(meta, f.dataset, node)
    label = "All pools" if node is None else meta.taxonomy[node].label
    st.markdown(f'<div class="pp-h3" style="margin-top:8px">What drives {"operating gain" if metric == "gain" else "revenue"}, '
                f'{y0}→{y1} · {label}</div>', unsafe_allow_html=True)
    br = G.bridge(result, meta, f.dataset, node, geo, y0, y1, metric, by_child=kind == "top_down")
    if br["value"].iloc[0] == 0 and br["value"].iloc[-1] == 0:
        st.info("Nothing to bridge for this selection.")
        return
    st.plotly_chart(gchart.bridge(br), config=PLOTLY_CONFIG, key=K + "bridge")
    notes = [BRIDGE_NOTES[kind], "Each step is sequential at the finest grain and the steps sum exactly to the change."]
    if f.basis != "allowed":
        notes.append("The bridge uses allowed revenue (everything received) regardless of spend basis.")
    if sel and sel != node:
        notes.append("Tip: choose the selected pool in “Bridge for” to see its own detail.")
    st.markdown("".join(ui.note(n) for n in notes), unsafe_allow_html=True)


def section_growth(result, meta: Metadata, f: S.Filters, geo: Geo, cmp=None) -> None:
    p = meta.periods
    ss = st.session_state
    ss.setdefault(K + "from", p.base_year)
    ss.setdefault(K + "to", p.forecast_end)
    ss.setdefault(K + "metric", "revenue")
    title = "All pools" if f.focus is None else meta.taxonomy[f.focus].label
    st.markdown(ui.section_header("growth", "Growth & forecasting",
                                  f"{title} · history {p.history_start}–{p.base_year}, forecast to {p.forecast_end} · "
                                  f"{'scenario' if f.view != 'baseline' else 'baseline'}"), unsafe_allow_html=True)

    if f.view == "impact":
        st.markdown(ui.note("Growth is shown for the scenario as a whole; the Baseline column and faint lines "
                            "show what changed."), unsafe_allow_html=True)
    years = p.all_years
    with st.container(horizontal=True, wrap=True, vertical_alignment="bottom", gap="medium"):
        st.selectbox("From", years[:-1], key=K + "from", width=110, on_change=_keep_order, args=("from",))
        st.selectbox("To", years[1:], key=K + "to", width=110, on_change=_keep_order, args=("to",))
        st.segmented_control("Index", ["revenue", "gain"], key=K + "metric", required=True,
                             format_func={"revenue": "Revenue", "gain": "Operating gain"}.get)
    y0, y1 = int(ss[K + "from"]), int(ss[K + "to"])
    metric = ss[K + "metric"]
    if y0 >= y1:
        st.info("Choose a start year before the end year.")
        return

    left, right = st.columns([7, 5], gap="large")
    with left:
        st.markdown(f'<div class="pp-h3">Indexed {metric if metric == "revenue" else "operating gain"} '
                    f'({y0} = 100)</div>', unsafe_allow_html=True)
        idx = G.indexed(result, meta, f.dataset, f.focus, geo, f.basis, y0, metric)
        if idx.empty or idx["index"].isna().all():
            st.info("No series to index for this view.")
        else:
            ids = list(dict.fromkeys(idx["node_id"]))
            colors = trends.series_colors(ids, LAYER_COLOR if f.focus is None else {})
            base_idx = G.indexed(cmp, meta, f.dataset, f.focus, geo, f.basis, y0, metric) if cmp is not None else None
            fig = trends.lines(idx, "index", label_col="label", id_col="node_id", colors=colors,
                               base_year=p.base_year, yformat=",.0f", hover_fmt=lambda v: f"{v:,.1f}",
                               baseline=base_idx)
            fig.add_hline(y=100, line_width=1, line_color=COLOR["line_strong"])
            st.plotly_chart(fig, config=PLOTLY_CONFIG, key=K + "indexed")
            notes = ["Each line is rebased to 100 in the start year, so growth is comparable across pools of "
                     "different size."]
            if idx.groupby("node_id")["index"].apply(lambda s: s.isna().all()).any():
                notes.append("Pools with a non-positive start-year value cannot be indexed and are omitted.")
            if base_idx is not None:
                notes.append("Faint lines show the baseline.")
            st.markdown("".join(ui.note(n) for n in notes), unsafe_allow_html=True)
    with right:
        hist = (p.history_start, p.base_year)
        periods = [hist, (y0, y1)] if (y0, y1) != hist else [hist]
        tbl = G.growth_table(result, meta, f.dataset, f.focus, geo, f.basis, periods, base=cmp)
        st.markdown('<div class="pp-h3">Compound annual growth</div>', unsafe_allow_html=True)
        if tbl.empty:
            st.info("No pools available for this view.")
        else:
            head = ["Pool", f"Revenue {hist[0]}–{str(hist[1])[2:]}"]
            if len(periods) > 1:
                head.append(f"Revenue {y0}–{str(y1)[2:]}")
            if cmp is not None:
                head.append("Baseline")
            head += [f"Gain {y0}–{str(y1)[2:]}", "Δ margin"]
            body = []
            for r in tbl.itertuples():
                row = [r.label, _cagr_cell(getattr(r, f"rev_{hist[0]}_{hist[1]}"))]
                if len(periods) > 1:
                    row.append(_cagr_cell(getattr(r, f"rev_{y0}_{y1}")))
                if cmp is not None:
                    row.append(_cagr_cell(r.rev_base))
                row += [_cagr_cell(r.gain_cagr), fmt.pp(r.d_margin)]
                body.append(row)
            st.markdown(ui.table(head, body), unsafe_allow_html=True)
            st.markdown(ui.note("Realized from model results. n/m = not meaningful (an endpoint ≤ 0). "
                                f"Δ margin is {y1} minus {y0}."), unsafe_allow_html=True)

    _attractiveness(result, meta, f, geo, y0, y1, cmp)
    _bridge(result, meta, f, geo, y0, y1)

    m = G.yoy(result, meta, f.dataset, f.focus, geo, f.basis, metric)
    st.markdown(f'<div class="pp-h3" style="margin-top:12px">Year-over-year growth · '
                f'{"revenue" if metric == "revenue" else "operating gain"}</div>', unsafe_allow_html=True)
    if m.empty:
        st.info("No pools available for this view.")
    else:
        st.plotly_chart(gchart.yoy_heatmap(m, p.base_year), config=PLOTLY_CONFIG, key=K + "yoy")
        st.markdown(ui.note("Same red–gray–teal scale as margins, neutral at 0%. f = forecast year. "
                            "— = prior year ≤ 0 (growth not meaningful)."), unsafe_allow_html=True)
