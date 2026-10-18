"""Profit Pools Explorer — unified analytical workspace (sections A–B in Phase 3)."""

from __future__ import annotations

import numpy as np
import pandas as pd
import streamlit as st

from profit_pools.charts import marimekko, trends
from profit_pools.core.config import load_config
from profit_pools.core.model import views as V
from profit_pools.core.model.views import Geo
from profit_pools.core.taxonomy import Metadata
from profit_pools.design import components as ui
from profit_pools.design import format as fmt
from profit_pools.design.theme import PLOTLY_CONFIG
from profit_pools.design.tokens import FUNDING_COLOR, LAYER_COLOR
from profit_pools.shell import data, layout
from profit_pools.shell import state as S

K_TILE_REFS = "pp._tile_refs"
K_SEL_WIDGET = "pp.sel_widget"
K_SEG_LABELS = "pp._seg_labels"
NONE = "__none__"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _geo_text(meta: Metadata, geo: Geo) -> str:
    if geo.level == "national":
        return "National"
    if geo.level == "state":
        return meta.geography.states[geo.id].label
    return f"{meta.geography.county_label(geo.id)}, {meta.geography.states[meta.geography.state_of_county(geo.id)].label}"


def _label(meta: Metadata, node_id: str | None) -> str:
    return "All pools" if node_id is None else meta.taxonomy[node_id].label


def _has_children(meta: Metadata, dataset: str, node_id: str) -> bool:
    return any(meta.in_dataset(c.id, dataset) for c in meta.taxonomy.children(node_id))


def _provenance(meta: Metadata, dataset: str, node_id: str | None, year: int) -> list[tuple[str, str]]:
    out = [(meta.datasets[dataset].short_label, "")]
    leaves = meta.leaves_in_dataset(dataset, node_id)
    econ = {l.economics for l in leaves}
    if "payer_pnl" in econ:
        src = meta.datasets[dataset].payer_source_type
        out.append(("Payer: observed" if src == "observed" else "Payer: estimated", src))
    if "margin_on_revenue" in econ:
        out.append(("Revenue: allocated from flows", "allocated"))
    if "top_down" in econ:
        out.append(("Top-down estimate", "estimated"))
    if year > meta.periods.base_year:
        out.append(("Projected", "projected"))
    return out


def _delta(cur, prev, kind: str) -> str:
    if prev is None or cur is None or not np.isfinite(prev) or not np.isfinite(cur):
        return ui.delta_html("No prior-year comparison", None)
    if kind == "pp":
        d = cur - prev
        return ui.delta_html(f"{fmt.pp(d)} vs prior year", 1 if d > 0 else -1 if d < 0 else 0)
    if prev == 0:
        return ui.delta_html("—", None)
    d = cur / prev - 1
    if prev < 0:  # growth rates against a negative base are not meaningful
        return ui.delta_html(f"{fmt.money(cur - prev, signed=True)} vs prior year", 1 if cur > prev else -1)
    return ui.delta_html(f"{fmt.pct(d, 1, signed=True)} vs prior year", 1 if d > 0 else -1 if d < 0 else 0)


# ---------------------------------------------------------------------------
# selection plumbing (chart ⇄ synchronized selector)
# ---------------------------------------------------------------------------

def _ref_key(ref) -> str:
    return f"{ref[0]}|{ref[1] if ref[1] is not None else '*'}"


def _on_chart_select(chart_key: str) -> None:
    ev = st.session_state.get(chart_key)
    refs = st.session_state.get(K_TILE_REFS, [])
    pts = (ev or {}).get("selection", {}).get("points", []) if isinstance(ev, dict) else (
        ev.selection.points if ev is not None else [])
    if not pts:
        S.set_selected(None)
        return
    cd = pts[0].get("customdata")
    idx = int(cd[0] if isinstance(cd, (list, tuple)) else cd) if cd is not None else pts[0].get("point_index")
    if idx is not None and 0 <= idx < len(refs):
        r = refs[idx]
        if r.seg_node_id == r.col_id:  # a leaf column drawn as one tile = the whole column
            S.set_selected((r.col_id, None, r.col_id))
        else:
            S.set_selected((r.col_id, r.seg_key, r.seg_node_id))


def _on_selector_change(lookup: dict) -> None:
    v = st.session_state.get(K_SEL_WIDGET)
    S.set_selected(None if v in (None, NONE) else lookup[v])


def _merge_reasons(meta: Metadata, unavailable) -> list[str]:
    """'A: reason', 'B: reason' → 'A, B: reason' (one note per distinct reason)."""
    groups: dict[str, list[str]] = {}
    for u in unavailable:
        reason = u.reason.split(": ", 1)[1] if ": " in u.reason else u.reason
        groups.setdefault(reason, []).append(meta.taxonomy[u.node_id].label)
    return [f"{', '.join(names)}: {reason}" for reason, names in groups.items()]


def _drill(node_id: str | None, keep_selected: tuple | None = None) -> None:
    S.set_focus(node_id, keep_selected)


# ---------------------------------------------------------------------------
# Section A — overview + Marimekko
# ---------------------------------------------------------------------------

def _kpis(result, meta, f: S.Filters, geo: Geo) -> None:
    cur = V.node_metrics(result, meta, f.dataset, f.year, f.focus, geo, f.basis)
    prev = (V.node_metrics(result, meta, f.dataset, f.year - 1, f.focus, geo, f.basis)
            if f.year - 1 >= meta.periods.history_start else None)
    if cur is None:
        st.markdown(ui.note("No data for this scope at the selected geography and spend basis."), unsafe_allow_html=True)
        return

    def g(s, k):
        return None if s is None else s.get(k)

    rev_label = S.BASIS_REVENUE_LABEL[f.basis]
    if f.basis == "allowed":
        rev_label = "Gross revenue" if f.focus is None else "Revenue"
    margin_val = fmt.pct(cur["margin"])
    margin_delta = _delta(cur["margin"], g(prev, "margin"), "pp")
    if cur["margin_coverage"] < 0.9995:
        d = cur["margin"] - prev["margin"] if prev is not None else np.nan
        margin_delta = (ui.delta_html(fmt.pp(d), 1 if d > 0 else -1) if np.isfinite(d) else "") + ui.delta_html(
            f" · {fmt.pct(cur['margin_coverage'], 1)} covered", None)
    members = cur.get("members")
    if members is not None and np.isfinite(members) and members > 0:
        enr = (fmt.members(members), _delta(members, g(prev, "members"), "pct"))
    else:
        enr = (fmt.DASH, ui.delta_html("Payer pools only", None))
    s = V.node_series(result, meta, f.dataset, f.focus, geo, f.basis).set_index("year")
    b, e = meta.periods.base_year, meta.periods.forecast_end
    rev_cagr = V.cagr(s["revenue"].get(b), s["revenue"].get(e), e - b) if b in s.index and e in s.index else np.nan
    gain_cagr = V.cagr(s["gain"].get(b), s["gain"].get(e), e - b) if b in s.index and e in s.index else np.nan
    items = [
        (rev_label, fmt.money(cur["revenue"]), _delta(cur["revenue"], g(prev, "revenue"), "pct")),
        ("Operating gain", fmt.money(cur["gain"]), _delta(cur["gain"], g(prev, "gain"), "pct")),
        ("Operating margin", margin_val, margin_delta),
        ("Enrollment", *enr),
        (f"Revenue CAGR {b}–{str(e)[2:]}", fmt.cagr(rev_cagr),
         ui.delta_html(f"Gain CAGR {fmt.cagr(gain_cagr)}", None)),
    ]
    st.markdown(ui.kpi_strip(items), unsafe_allow_html=True)
    if f.focus is None and f.basis == "allowed":
        st.markdown(ui.note("Gross revenue sums every sector and includes inter-sector payments (payer claims are "
                            "provider revenue). Operating gain is additive across sectors."), unsafe_allow_html=True)


def _breadcrumb(meta: Metadata, focus: str | None) -> None:
    path = [None] + ([n.id for n in meta.taxonomy.path(focus)] if focus else [])
    with st.container(key="pp_crumbs", horizontal=True, gap="small", vertical_alignment="center", width="content"):
        for i, nid in enumerate(path):
            last = i == len(path) - 1
            label = "All pools" if nid is None else meta.taxonomy[nid].short_label
            child = path[i + 1] if not last else None
            st.button(label, key=f"pp.crumb.{nid}", disabled=last, on_click=_drill,
                      args=(nid, (child, None, child) if child else None))
            if not last:
                st.markdown('<span class="pp-crumb-sep">›</span>', unsafe_allow_html=True)


def section_overview(result, meta: Metadata, f: S.Filters, geo: Geo) -> None:
    ds = meta.datasets[f.dataset]
    yr = f"{f.year}{' (forecast)' if f.year > meta.periods.base_year else ''}"
    st.markdown(ui.page_header("Profit Pools Explorer",
                               f"{ds.label} · {yr} · {_geo_text(meta, geo)} · fictional data, for illustration only"),
                unsafe_allow_html=True)
    st.markdown(ui.section_nav([("overview", "Overview"), ("trends", "Trends & detail"), ("detail", "Summary table")]),
                unsafe_allow_html=True)
    st.markdown('<div id="overview"></div>', unsafe_allow_html=True)
    _kpis(result, meta, f, geo)

    # Toolbar
    with st.container(horizontal=True, wrap=True, vertical_alignment="bottom", gap="medium"):
        _breadcrumb(meta, f.focus)
        st.space("stretch")
        st.segmented_control("Segment columns by", list(S.SEGMENT_LABELS), key=S.K_SEGMENT, required=True,
                             format_func=S.SEGMENT_LABELS.get, on_change=S.set_selected, args=(None,),
                             help="Sub-pools: the next level of the hierarchy. Geography: states (or counties "
                                  "within a state). Funding source: who pays provider and pharmacy revenue.")
        st.segmented_control("Spend basis", list(S.BASIS_LABELS), key=S.K_BASIS, required=True,
                             format_func=S.BASIS_LABELS.get,
                             help="Allowed = everything providers receive. Payer-paid = paid by payers and "
                                  "public programs. Consumer = member cost sharing and self-pay (allowed − paid). "
                                  "Applies to provider and pharmacy pools; payer and top-down pools have no split.")

    tiles = V.marimekko_tiles(result, meta, f.dataset, f.year, f.focus, geo, f.basis, f.segment_by)
    rows = tiles.rows
    col_labels = {c: meta.taxonomy[c].short_label for c in rows["col_id"].unique()}
    estimate_cols = {c for c in col_labels if V.economics_label(meta, c, f.dataset) == "top_down"}
    sel = f.selected
    sel_ref = marimekko.TileRef(*sel) if sel else None
    if rows.empty:
        st.info("Nothing to show for this combination. " + " ".join(_merge_reasons(meta, tiles.unavailable)))
        st.session_state[K_TILE_REFS] = []
    else:
        fig, refs = marimekko.build(rows, col_labels, selected=sel_ref, estimate_cols=estimate_cols,
                                    basis_label=S.BASIS_REVENUE_LABEL[f.basis])
        st.session_state[K_TILE_REFS] = refs
        chart_key = f"pp.mekko.{f.dataset}.{f.focus}.{f.segment_by}.{f.basis}.{f.geo_id}.{f.year}"
        st.plotly_chart(fig, key=chart_key, on_select=lambda: _on_chart_select(chart_key),
                        selection_mode="points", config=PLOTLY_CONFIG)
    st.markdown(ui.margin_legend(bool(estimate_cols)), unsafe_allow_html=True)
    for n in tiles.notes:
        st.markdown(ui.note(n), unsafe_allow_html=True)
    st.markdown("".join(ui.note(r) for r in _merge_reasons(meta, tiles.unavailable)), unsafe_allow_html=True)
    if not rows.empty and f.segment_by == "hierarchy" and all(
            not _has_children(meta, f.dataset, c) for c in rows["col_id"].unique()):
        st.markdown(ui.note("Lowest level of the hierarchy. Segment columns by Geography or Funding source to "
                            "split them further."), unsafe_allow_html=True)

    # Synchronized selector + drill actions (equivalent to clicking a tile)
    if not rows.empty:
        st.session_state[K_SEG_LABELS] = {(r.col_id, r.seg_key): r.seg_label for r in rows.itertuples()}
        lookup, labels = {}, {}
        for col_id, g in rows.groupby("col_id", sort=False):
            lookup[f"{col_id}|*"] = (col_id, None, col_id)
            labels[f"{col_id}|*"] = col_labels[col_id]
            for r in g.itertuples():
                if r.seg_label != col_labels[col_id]:
                    k = f"{col_id}|{r.seg_key}"
                    lookup[k] = (col_id, r.seg_key, r.seg_node_id)
                    labels[k] = f"{col_labels[col_id]} › {r.seg_label}"
        current = _ref_key(sel) if sel and _ref_key(sel) in lookup else NONE
        st.session_state[K_SEL_WIDGET] = current
        a, b, c, _ = st.columns([3, 1.6, 1.6, 2], vertical_alignment="bottom")
        with a:
            st.selectbox("Selected pool", [NONE, *lookup], key=K_SEL_WIDGET,
                         format_func=lambda k: "Click a tile, or choose…" if k == NONE else labels[k],
                         on_change=_on_selector_change, args=(lookup,))
        if sel and current != NONE:
            col_id, seg_key, seg_node = sel
            with b:
                can = _has_children(meta, f.dataset, col_id)
                st.button(f"Drill into {col_labels.get(col_id, col_id)}", key="pp.drill.col", disabled=not can,
                          type="primary", on_click=_drill,
                          args=(col_id, (seg_node, None, seg_node) if seg_node and seg_node != col_id else None),
                          help=None if can else "Lowest level of the hierarchy.", width="stretch")
            if seg_node and seg_node != col_id and _has_children(meta, f.dataset, seg_node):
                with c:
                    st.button(f"Open {meta.taxonomy[seg_node].short_label}", key="pp.drill.seg",
                              on_click=_drill, args=(seg_node, None), width="stretch")


# ---------------------------------------------------------------------------
# Section B — trends, drivers, inspector
# ---------------------------------------------------------------------------

def _inspector(result, meta: Metadata, f: S.Filters, geo: Geo) -> None:
    sel = f.selected
    node_id = (sel[2] or sel[0]) if sel else f.focus
    seg_note = None
    if sel and sel[2] is None and sel[1] is not None:
        seg_note = st.session_state.get(K_SEG_LABELS, {}).get((sel[0], sel[1]), sel[1])
    m = V.node_metrics(result, meta, f.dataset, f.year, node_id, geo, f.basis)
    title = _label(meta, node_id)
    path = " › ".join(["All pools"] + [n.short_label for n in meta.taxonomy.path(node_id)][:-1]) if node_id else ""
    html = [f'<div class="pp-inspector"><div class="pp-path">{path or "Scope"}</div><h3 class="pp-h3">{title}</h3>',
            ui.badges(_provenance(meta, f.dataset, node_id, f.year))]
    if m is None:
        html.append(ui.note("Not available for this geography or spend basis."))
        st.markdown("".join(html) + "</div>", unsafe_allow_html=True)
        return
    prev = (V.node_metrics(result, meta, f.dataset, f.year - 1, node_id, geo, f.basis)
            if f.year - 1 >= meta.periods.history_start else None)
    kv: list = [
        (S.BASIS_REVENUE_LABEL[f.basis], fmt.money(m["revenue"])),
        ("Share of parent", fmt.pct(m.get("share_of_parent"))),
        ("Change vs prior year", fmt.pct(m["revenue"] / prev["revenue"] - 1, 1, signed=True)
         if prev is not None and prev["revenue"] > 0 else fmt.DASH),
        None,
        ("Operating gain", fmt.money(m["gain"])),
        ("Operating margin", fmt.pct(m["margin"])),
    ]
    if m["margin_coverage"] < 0.999:
        kv.append(("Margin data coverage", fmt.pct(m["margin_coverage"], 1)))
    if np.isfinite(m.get("members", np.nan)) and m["members"] > 0:
        pure_payer = node_id is not None and V.economics_label(meta, node_id, f.dataset) == "payer_pnl"
        kv += [None, ("Enrollment" if pure_payer else "Payer enrollment", fmt.members(m["members"])),
               ("Revenue PMPM", fmt.pmpm(m["revenue_pmpm"])),
               ("Allowed PMPM", fmt.pmpm(m["allowed_pmpm"])), ("Paid claims PMPM", fmt.pmpm(m["paid_pmpm"])),
               ("Medical loss ratio (paid)", fmt.pct(m["mlr"])),
               ("Residual operating expense", fmt.money(m["payer_opex"]))]
    html.append(ui.kv_grid(kv))
    if np.isfinite(m.get("members", np.nan)) and m["members"] > 0 and (
            node_id is None or V.economics_label(meta, node_id, f.dataset) != "payer_pnl"):
        html.append(ui.note("Enrollment and per-member measures cover payer pools only."))
    if seg_note:
        html.append(ui.note(f"Selected segment: {seg_note}. Figures above are for the whole column."))

    has_flows = node_id is None or any(l.receives_flows for l in meta.leaves_in_dataset(f.dataset, node_id))
    if has_flows and node_id is not None:
        comp = V.revenue_components(result, meta, f.dataset, f.year, node_id, geo)
        if not comp.empty:
            html.append('<div class="pp-h3" style="margin-top:14px">Revenue by funding source</div>')
            html.append(ui.table(["Funder", "Allowed", "Consumer", "Share"],
                                 [[r.funder, fmt.money(r.allowed), fmt.pct(r.consumer / r.allowed, 0),
                                   fmt.pct(r.share, 0)] for r in comp.itertuples()]))
            tot = comp[["allowed", "paid", "consumer"]].sum()
            html.append(ui.note(f"Consumer spend (member cost sharing and self-pay) is "
                                f"{fmt.pct(tot['consumer'] / tot['allowed'])} of allowed receipts."))
    st.markdown("".join(html) + "</div>", unsafe_allow_html=True)

    text = load_config("methodology")
    econ = V.economics_label(meta, node_id, f.dataset) if node_id else "mixed"
    with st.expander("How is provider revenue estimated?", expanded=False):
        st.markdown(text["provider_revenue"][f.dataset])
        st.markdown(text["provider_revenue"]["volume"])
    if econ in ("payer_pnl", "mixed"):
        with st.expander(text["payer_economics"]["title"]):
            st.markdown(text["payer_economics"]["body"])
    if econ in ("top_down", "mixed") and f.dataset == "MARKET":
        with st.expander(text["top_down"]["title"]):
            st.markdown(text["top_down"]["body"])


def section_trends(result, meta: Metadata, f: S.Filters, geo: Geo) -> None:
    st.markdown(ui.section_header("trends", "Trends & detail",
                                  f"{_label(meta, f.focus)} · {S.BASIS_REVENUE_LABEL[f.basis].lower()} by column of "
                                  f"the chart above · {_geo_text(meta, geo)}"), unsafe_allow_html=True)
    cols = V.rollup(result, meta, f.dataset, f.year, f.focus, geo, f.basis).rows
    ids = list(cols["node_id"])
    left, right = st.columns([8, 4], gap="large")
    with left:
        if not ids:
            st.info("No pools available for this view.")
        else:
            series = []
            for nid in ids:
                s = V.node_series(result, meta, f.dataset, nid, geo, f.basis)
                s["node_id"], s["label"] = nid, meta.taxonomy[nid].short_label
                series.append(s)
            df = pd.concat(series, ignore_index=True)
            fixed = LAYER_COLOR if f.focus is None else {}
            colors = trends.series_colors(ids, fixed)
            metric = st.segmented_control("Metric", ["revenue", "gain", "margin"], key=S.K_TREND_METRIC,
                                          required=True, label_visibility="collapsed",
                                          format_func={"revenue": "Revenue", "gain": "Operating gain",
                                                       "margin": "Operating margin"}.get)
            plot = df.copy()
            if metric == "margin":
                yfmt, hover, suffix = ".1%", fmt.pct, ""
            else:  # axis in $B; hover keeps full formatting
                plot[metric] = plot[metric] / 1e9
                yfmt, hover, suffix = "$,.0f", lambda v: fmt.money(v * 1e9), "B"
            fig = trends.lines(plot, metric, label_col="label", id_col="node_id", colors=colors,
                               base_year=meta.periods.base_year, yformat=yfmt, hover_fmt=hover,
                               zero_line=metric != "revenue")
            fig.update_yaxes(ticksuffix=suffix)
            st.plotly_chart(trends.mark_year(fig, f.year), config=PLOTLY_CONFIG, key="pp.trend")
            if len(ids) > trends.MAX_SERIES:
                st.markdown(ui.note("Series beyond the fifth share the neutral 'other' color; hover for names."),
                            unsafe_allow_html=True)

            tot = df.groupby("year")["revenue"].transform("sum")
            df["share"] = df["revenue"] / tot
            st.markdown('<div class="pp-h3" style="margin-top:8px">Composition of revenue</div>', unsafe_allow_html=True)
            st.plotly_chart(trends.composition(df, label_col="label", id_col="node_id", colors=colors,
                                               base_year=meta.periods.base_year),
                            config=PLOTLY_CONFIG, key="pp.composition")

            drivers = V.change_drivers(result, meta, f.dataset, f.year, f.focus, geo, f.basis)
            st.markdown(f'<div class="pp-h3" style="margin-top:8px">What drove the change, {f.year - 1}→{f.year}</div>',
                        unsafe_allow_html=True)
            if drivers.empty:
                st.markdown(ui.note("No prior year in range."), unsafe_allow_html=True)
            else:
                d = drivers.head(8)
                st.markdown(ui.table(
                    ["Pool", "Δ Revenue", "Share of Δ", "Δ Gain", "Δ Margin"],
                    [[r.label, fmt.money(r.d_revenue, signed=True), fmt.pct(r.share_of_change, 0),
                      fmt.money(r.d_gain, signed=True),
                      (f"{'+' if r.d_margin_pp > 0 else fmt.MINUS if r.d_margin_pp < 0 else ''}"
                       f"{abs(r.d_margin_pp):.1f} pp") if np.isfinite(r.d_margin_pp) else fmt.DASH]
                     for r in d.itertuples()]), unsafe_allow_html=True)
                st.markdown(ui.note("Baseline drivers only. Scenario attribution (by assumption) arrives with the "
                                    "scenario engine."), unsafe_allow_html=True)
    with right:
        _inspector(result, meta, f, geo)


def section_table(result, meta: Metadata, f: S.Filters, geo: Geo) -> None:
    st.markdown(ui.section_header("detail", "Summary table",
                                  f"{_label(meta, f.focus)} · {f.year} · {S.BASIS_REVENUE_LABEL[f.basis].lower()}"),
                unsafe_allow_html=True)
    r = V.rollup(result, meta, f.dataset, f.year, f.focus, geo, f.basis).rows
    if r.empty:
        st.info("No pools available for this view.")
        return
    out = pd.DataFrame({
        "Pool": r["label"],
        "Revenue ($B)": r["revenue"] / 1e9,
        "Share": r["share_of_parent"] * 100,
        "Operating gain ($B)": r["gain"] / 1e9,
        "Margin": r["margin"] * 100,
        "Margin coverage": r["margin_coverage"] * 100,
        "Enrollment": r["members"].map(fmt.members),
        "Revenue PMPM": r["revenue_pmpm"].map(fmt.pmpm),
        "Coverage": r["coverage"].map({"bottom_up": "Bottom-up", "allocated": "Allocated", "top_down": "Top-down",
                                       "mixed": "Mixed"}),
    })
    st.dataframe(out, hide_index=True, width="stretch", column_config={
        "Revenue ($B)": st.column_config.NumberColumn(format="$%.1f"),
        "Share": st.column_config.NumberColumn(format="%.1f%%"),
        "Operating gain ($B)": st.column_config.NumberColumn(format="$%.2f"),
        "Margin": st.column_config.NumberColumn(format="%.1f%%"),
        "Margin coverage": st.column_config.NumberColumn(format="%.1f%%"),
        "Enrollment": st.column_config.TextColumn(help="Payer pools only"),
        "Revenue PMPM": st.column_config.TextColumn(help="Payer pools only"),
    })


def render() -> None:
    a = data.adapter()
    meta = a.metadata
    result = data.baseline()
    S.init(meta)
    layout.global_bar(meta)
    f = S.current()
    if f.focus is not None and not meta.in_dataset(f.focus, f.dataset):
        S.set_focus(None)
        f = S.current()
    geo = Geo.parse(meta, f.geo_id)
    section_overview(result, meta, f, geo)
    f = S.current()
    section_trends(result, meta, f, geo)
    section_table(result, meta, f, geo)
