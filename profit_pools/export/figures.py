"""Figures and summary numbers for the HTML / PPTX exports, built with the workspace's own chart
builders and view functions (one engine, one layout code path)."""

from __future__ import annotations

import numpy as np
import pandas as pd
import plotly.graph_objects as go

from profit_pools.charts import growth as gchart
from profit_pools.charts import marimekko, sankey, trends
from profit_pools.core.model import flows_view as FV
from profit_pools.core.model import growth as G
from profit_pools.core.model import views as V
from profit_pools.design import format as fmt
from profit_pools.design.theme import register_plotly_template
from profit_pools.design.tokens import LAYER_COLOR
from profit_pools.export import ExportContext


def tiles(ctx: ExportContext) -> tuple[pd.DataFrame, dict[str, str], set[str]]:
    """Marimekko tiles (with Δ columns when comparing), column labels, top-down columns."""
    if ctx.compare is not None:
        rows = V.compare_tiles(ctx.result, ctx.compare, ctx.meta, ctx.dataset, ctx.year, ctx.focus, ctx.geo,
                               ctx.basis, ctx.segment_by).rows
    else:
        rows = V.marimekko_tiles(ctx.result, ctx.meta, ctx.dataset, ctx.year, ctx.focus, ctx.geo, ctx.basis,
                                 ctx.segment_by).rows
    labels = {c: ctx.meta.taxonomy[c].short_label for c in rows["col_id"].unique()} if not rows.empty else {}
    est = {c for c in labels if V.economics_label(ctx.meta, c, ctx.dataset) == "top_down"}
    return rows, labels, est


def kpis(ctx: ExportContext) -> list[tuple[str, str, str]]:
    """(label, value, comparison) — the workspace KPI strip in plain text."""
    m = ctx.meta
    cur = V.node_metrics(ctx.result, m, ctx.dataset, ctx.year, ctx.focus, ctx.geo, ctx.basis)
    if cur is None:
        return []
    ref = ctx.compare
    vs = "vs baseline"
    if ref is None:
        ref, vs = (ctx.result, "vs prior year") if ctx.year - 1 >= m.periods.history_start else (None, "")
        prev = V.node_metrics(ref, m, ctx.dataset, ctx.year - 1, ctx.focus, ctx.geo, ctx.basis) if ref else None
    else:
        prev = V.node_metrics(ref, m, ctx.dataset, ctx.year, ctx.focus, ctx.geo, ctx.basis)

    def chg(k, kind="money"):
        if prev is None or not np.isfinite(prev.get(k, np.nan)) or not np.isfinite(cur.get(k, np.nan)):
            return ""
        d = cur[k] - prev[k]
        if kind == "pp":
            return f"{fmt.pp(d)} {vs}"
        if kind == "count":
            return f"{fmt.members(d, signed=True)} {vs}"
        return f"{fmt.money(d, signed=True)} {vs}"

    s = V.node_series(ctx.result, m, ctx.dataset, ctx.focus, ctx.geo, ctx.basis).set_index("year")
    b, e = m.periods.base_year, m.periods.forecast_end
    cg = V.cagr(s["revenue"].get(b), s["revenue"].get(e), e - b) if b in s.index and e in s.index else np.nan
    members = cur.get("members")
    out = [("Revenue", fmt.money(cur["revenue"]), chg("revenue")),
           ("Operating gain", fmt.money(cur["gain"]), chg("gain")),
           ("Operating margin", fmt.pct(cur["margin"]), chg("margin", "pp")),
           ("Enrollment", fmt.members(members) if members is not None and np.isfinite(members) and members > 0
            else fmt.DASH, chg("members", "count") if members and np.isfinite(members) else "Payer pools only"),
           (f"Revenue CAGR {b}–{str(e)[2:]}", fmt.cagr(cg), "")]
    return out


def marimekko_fig(ctx: ExportContext) -> go.Figure | None:
    rows, labels, est = tiles(ctx)
    if rows.empty:
        return None
    color_by = "impact" if ctx.view == "impact" else "margin"
    fig, _ = marimekko.build(rows, labels, estimate_cols=est, color_by=color_by, impact_min=V.IMPACT_MIN)
    return fig


def trend_frame(ctx: ExportContext, res=None) -> pd.DataFrame:
    res = res or ctx.result
    ids = list(V.rollup(ctx.result, ctx.meta, ctx.dataset, ctx.year, ctx.focus, ctx.geo, ctx.basis).rows["node_id"])
    out = []
    for nid in ids:
        s = V.node_series(res, ctx.meta, ctx.dataset, nid, ctx.geo, ctx.basis)
        s["node_id"], s["label"] = nid, ctx.meta.taxonomy[nid].short_label
        out.append(s)
    return pd.concat(out, ignore_index=True) if out else pd.DataFrame()


def trend_fig(ctx: ExportContext) -> go.Figure | None:
    df = trend_frame(ctx)
    if df.empty:
        return None
    ids = list(dict.fromkeys(df["node_id"]))
    colors = trends.series_colors(ids, LAYER_COLOR if ctx.focus is None else {})
    plot = df.assign(revenue=df["revenue"] / 1e9)
    base = None
    if ctx.compare is not None:
        b = trend_frame(ctx, ctx.compare)
        base = b.assign(revenue=b["revenue"] / 1e9)
    fig = trends.lines(plot, "revenue", label_col="label", id_col="node_id", colors=colors,
                       base_year=ctx.meta.periods.base_year, yformat="$,.0f", hover_fmt=lambda v: fmt.money(v * 1e9),
                       baseline=base)
    fig.update_yaxes(ticksuffix="B")
    return trends.mark_year(fig, ctx.year)


def sankey_fig(ctx: ExportContext) -> go.Figure | None:
    cfg = FV.SankeyConfig(min_share=0.005)
    view = FV.build(ctx.result, ctx.meta, ctx.dataset, ctx.year, ctx.geo, cfg)
    if view.nodes.empty or not len(view.links):
        return None
    delta = None
    if ctx.compare is not None:
        delta = FV.compare_links(view, FV.build(ctx.compare, ctx.meta, ctx.dataset, ctx.year, ctx.geo, cfg))
    return sankey.build(view, ctx.meta, delta=delta, impact=ctx.view == "impact", impact_min=V.IMPACT_MIN)


def growth_bridge(ctx: ExportContext) -> pd.DataFrame:
    """Operating-gain bridge for the current scope (additive across sectors), base year → forecast end."""
    p = ctx.meta.periods
    kind = G.bridge_kind(ctx.meta, ctx.dataset, ctx.focus)
    return G.bridge(ctx.result, ctx.meta, ctx.dataset, ctx.focus, ctx.geo, p.base_year, p.forecast_end, "gain",
                    by_child=kind == "top_down")


def growth_fig(ctx: ExportContext) -> go.Figure:
    return gchart.bridge(growth_bridge(ctx))


def yoy_fig(ctx: ExportContext) -> go.Figure | None:
    m = G.yoy(ctx.result, ctx.meta, ctx.dataset, ctx.focus, ctx.geo, ctx.basis)
    return None if m.empty else gchart.yoy_heatmap(m, ctx.meta.periods.base_year)


def ensure_template() -> None:
    register_plotly_template()
