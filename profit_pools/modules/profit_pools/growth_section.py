"""Section D — growth and forecasting: CAGRs, indexed growth, revenue-growth drivers, YoY heatmap."""

from __future__ import annotations

import numpy as np
import streamlit as st

from profit_pools.charts import growth as gchart
from profit_pools.charts import trends
from profit_pools.core.model import growth as G
from profit_pools.core.model.views import Geo
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


def section_growth(result, meta: Metadata, f: S.Filters, geo: Geo, cmp=None) -> None:
    p = meta.periods
    ss = st.session_state
    ss.setdefault(K + "from", p.base_year)
    ss.setdefault(K + "to", p.forecast_end)
    ss.setdefault(K + "metric", "revenue")
    node = (f.selected[2] or f.selected[0]) if f.selected else f.focus
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

    label = "All pools" if node is None else meta.taxonomy[node].label
    st.markdown(f'<div class="pp-h3" style="margin-top:12px">What drives revenue growth, {y0}→{y1} · {label}</div>',
                unsafe_allow_html=True)
    br = G.revenue_bridge(result, meta, f.dataset, node, geo, y0, y1)
    if br["value"].iloc[0] <= 0 and br["value"].iloc[-1] <= 0:
        st.info("No revenue for this selection.")
    else:
        st.plotly_chart(gchart.bridge(br), config=PLOTLY_CONFIG, key=K + "bridge")
        notes = ["Plans: enrollment × premium per member. Providers and pharmacy: members of the paying plans × "
                 "services per member × price per service (spend per member where service counts are not "
                 "available, e.g. pharmacy). Uninsured, government FFS, supplemental and top-down revenue are "
                 "shown as their own lines. Components are sequential and sum exactly to the change."]
        if f.basis != "allowed":
            notes.append("The growth bridge uses allowed revenue (everything received) regardless of spend basis.")
        if f.selected:
            notes.append("Showing the selected pool. Clear the selection to see the whole view.")
        st.markdown("".join(ui.note(n) for n in notes), unsafe_allow_html=True)

    m = G.yoy(result, meta, f.dataset, f.focus, geo, f.basis, metric)
    st.markdown(f'<div class="pp-h3" style="margin-top:12px">Year-over-year growth · '
                f'{"revenue" if metric == "revenue" else "operating gain"}</div>', unsafe_allow_html=True)
    if m.empty:
        st.info("No pools available for this view.")
    else:
        st.plotly_chart(gchart.yoy_heatmap(m, p.base_year), config=PLOTLY_CONFIG, key=K + "yoy")
        st.markdown(ui.note("Same red–gray–teal scale as margins, neutral at 0%. f = forecast year. "
                            "— = prior year ≤ 0 (growth not meaningful)."), unsafe_allow_html=True)
