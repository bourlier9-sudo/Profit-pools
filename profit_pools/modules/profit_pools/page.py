"""Profit Pools Explorer — unified analytical workspace (sections A–B in Phase 3)."""

from __future__ import annotations

import numpy as np
import pandas as pd
import streamlit as st

from profit_pools.charts import marimekko, trends
from profit_pools.core.config import load_config
from profit_pools.core.model import views as V
from profit_pools.core.model.engine import ModelResult
from profit_pools.core.model.scenario import Scenario
from profit_pools.core.model.scenario_engine import summarize
from profit_pools.core.model.views import Geo
from profit_pools.core.taxonomy import Metadata
from profit_pools.design import components as ui
from profit_pools.design import format as fmt
from profit_pools.design.theme import PLOTLY_CONFIG
from profit_pools.design.tokens import LAYER_COLOR
from profit_pools.modules.profit_pools.flows_section import section_flows
from profit_pools.modules.profit_pools.growth_section import section_growth
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


def _delta(cur, prev, kind: str, vs: str = "vs prior year", money: bool = False) -> str:
    if prev is None or cur is None or not np.isfinite(prev) or not np.isfinite(cur):
        return ui.delta_html("No prior-year comparison" if vs == "vs prior year" else "—", None)
    if kind == "pp":
        d = cur - prev
        return ui.delta_html(f"{fmt.pp(d)} {vs}", 1 if d > 0 else -1 if d < 0 else 0)
    if prev == 0:
        return ui.delta_html("—", None)
    d = cur / prev - 1
    if prev < 0 or money:  # growth rates against a negative base are not meaningful
        extra = f" ({fmt.pct(d, 1, signed=True)})" if money and prev > 0 else ""
        return ui.delta_html(f"{fmt.money(cur - prev, signed=True)}{extra} {vs}", 1 if cur > prev else -1 if cur < prev else 0)
    return ui.delta_html(f"{fmt.pct(d, 1, signed=True)} {vs}", 1 if d > 0 else -1 if d < 0 else 0)


def _as_result(pools, base_year: int) -> ModelResult:
    """Wrap a bridge step's pools so the same views (which read only `pools`) can slice it."""
    return ModelResult(pools=pools, flows=pd.DataFrame(), base_year=base_year)


def _active_scenario(view: str) -> Scenario:
    """The scenario behind the workspace: the applied one, or in Impact view optionally a single
    assumption run on its own against the baseline."""
    sc = S.applied()
    if view != "impact":
        return sc
    pick = next((a for a in sc.enabled if a.id == st.session_state.get(S.K_IMPACT_OF)), None)
    return sc if pick is None else sc.with_assumptions([pick])


def _bridge_for(meta: Metadata, f: S.Filters, geo: Geo, node_id: str | None) -> list[tuple]:
    """[(assumption, Δ revenue, Δ gain)] for one node — consecutive differences of the cached bridge."""
    steps = data.scenario_bridge(_active_scenario(f.view).to_json(), S.grouping_json())
    vals = []
    for a, pools in steps:
        mtr = V.node_metrics(_as_result(pools, meta.periods.base_year), meta, f.dataset, f.year, node_id, geo, f.basis)
        vals.append((a, 0.0 if mtr is None else float(mtr["revenue"]),
                     0.0 if mtr is None or not np.isfinite(mtr["gain"]) else float(mtr["gain"])))
    return [(vals[k][0], vals[k][1] - vals[k - 1][1], vals[k][2] - vals[k - 1][2]) for k in range(1, len(vals))]


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

def _kpis(result, meta, f: S.Filters, geo: Geo, cmp=None) -> None:
    cur = V.node_metrics(result, meta, f.dataset, f.year, f.focus, geo, f.basis)
    if cmp is not None:
        prev, vs = V.node_metrics(cmp, meta, f.dataset, f.year, f.focus, geo, f.basis), "vs baseline"
    else:
        prev = (V.node_metrics(result, meta, f.dataset, f.year - 1, f.focus, geo, f.basis)
                if f.year - 1 >= meta.periods.history_start else None)
        vs = "vs prior year"
    if cur is None:
        st.markdown(ui.note("No data for this scope at the selected geography and spend basis."), unsafe_allow_html=True)
        return

    def g(s, k):
        return None if s is None else s.get(k)

    rev_label = S.BASIS_REVENUE_LABEL[f.basis]
    if f.basis == "allowed":
        rev_label = "Gross revenue" if f.focus is None else "Revenue"
    margin_val = fmt.pct(cur["margin"])
    margin_delta = _delta(cur["margin"], g(prev, "margin"), "pp", vs)
    if cur["margin_coverage"] < 0.9995:
        d = cur["margin"] - prev["margin"] if prev is not None else np.nan
        margin_delta = (ui.delta_html(fmt.pp(d), 1 if d > 0 else -1) if np.isfinite(d) else "") + ui.delta_html(
            f" · {fmt.pct(cur['margin_coverage'], 1)} covered", None)
    members = cur.get("members")
    if members is not None and np.isfinite(members) and members > 0:
        enr = (fmt.members(members), _delta(members, g(prev, "members"), "pct", vs))
    else:
        enr = (fmt.DASH, ui.delta_html("Payer pools only", None))
    s = V.node_series(result, meta, f.dataset, f.focus, geo, f.basis).set_index("year")
    b, e = meta.periods.base_year, meta.periods.forecast_end
    rev_cagr = V.cagr(s["revenue"].get(b), s["revenue"].get(e), e - b) if b in s.index and e in s.index else np.nan
    gain_cagr = V.cagr(s["gain"].get(b), s["gain"].get(e), e - b) if b in s.index and e in s.index else np.nan
    cagr_note = f"Gain CAGR {fmt.cagr(gain_cagr)}"
    if cmp is not None:
        sb = V.node_series(cmp, meta, f.dataset, f.focus, geo, f.basis).set_index("year")
        base_cagr = V.cagr(sb["revenue"].get(b), sb["revenue"].get(e), e - b) if b in sb.index and e in sb.index else np.nan
        cagr_note = f"baseline {fmt.cagr(base_cagr)} · gain {fmt.cagr(gain_cagr)}"
    items = [
        (rev_label, fmt.money(cur["revenue"]), _delta(cur["revenue"], g(prev, "revenue"), "pct", vs, cmp is not None)),
        ("Operating gain", fmt.money(cur["gain"]), _delta(cur["gain"], g(prev, "gain"), "pct", vs, cmp is not None)),
        ("Operating margin", margin_val, margin_delta),
        ("Enrollment", *enr),
        (f"Revenue CAGR {b}–{str(e)[2:]}", fmt.cagr(rev_cagr),
         ui.delta_html(cagr_note, None)),
    ]
    st.markdown(ui.kpi_strip(items), unsafe_allow_html=True)
    if f.focus is None and f.basis == "allowed":
        st.markdown(ui.note("Gross revenue sums every sector and includes inter-sector payments (payer claims are "
                            "provider revenue). Operating gain is additive across sectors."), unsafe_allow_html=True)


def _impact_kpis(result, meta, f: S.Filters, geo: Geo, cmp) -> None:
    cur = V.node_metrics(result, meta, f.dataset, f.year, f.focus, geo, f.basis)
    base = V.node_metrics(cmp, meta, f.dataset, f.year, f.focus, geo, f.basis)
    if cur is None or base is None:
        st.markdown(ui.note("No data for this scope at the selected geography and spend basis."), unsafe_allow_html=True)
        return

    def dm(k):
        a, b = cur.get(k), base.get(k)
        a = a if a is not None and np.isfinite(a) else 0.0
        b = b if b is not None and np.isfinite(b) else 0.0
        return a - b, b

    def pct_of(d, b):
        return ui.delta_html(f"{fmt.pct(d / b, 1, signed=True)} of baseline" if b > 0 else "—", None)

    d_rev, b_rev = dm("revenue")
    d_gain, b_gain = dm("gain")
    d_mem, b_mem = dm("members")
    s = V.node_series(result, meta, f.dataset, f.focus, geo, f.basis).set_index("year")
    sb = V.node_series(cmp, meta, f.dataset, f.focus, geo, f.basis).set_index("year")
    b, e = meta.periods.base_year, meta.periods.forecast_end
    cg = (V.cagr(s["revenue"].get(b), s["revenue"].get(e), e - b) - V.cagr(sb["revenue"].get(b), sb["revenue"].get(e), e - b)
          if b in s.index and e in s.index and b in sb.index and e in sb.index else np.nan)
    items = [
        ("Δ " + S.BASIS_REVENUE_LABEL[f.basis].lower(), fmt.money(d_rev, signed=True), pct_of(d_rev, b_rev)),
        ("Δ operating gain", fmt.money(d_gain, signed=True), pct_of(d_gain, b_gain)),
        ("Δ operating margin", fmt.pp(cur["margin"] - base["margin"]), ui.delta_html("vs baseline", None)),
        ("Δ enrollment", fmt.members(d_mem, signed=True) if b_mem > 0 else fmt.DASH,
         pct_of(d_mem, b_mem) if b_mem > 0 else ui.delta_html("Payer pools only", None)),
        (f"Δ revenue CAGR {b}–{str(e)[2:]}", fmt.pp(cg) if np.isfinite(cg) else fmt.DASH, ui.delta_html("vs baseline", None)),
    ]
    st.markdown(ui.kpi_strip(items), unsafe_allow_html=True)


def _impact_controls(meta: Metadata) -> None:
    ap = S.applied()
    opts = [S.IMPACT_ALL, *[a.id for a in ap.enabled]]
    labels = {S.IMPACT_ALL: f"All {len(ap.enabled)} assumptions together"} | {
        a.id: f"{i}. {a.label or summarize(meta, a)} (on its own)" for i, a in enumerate(ap.enabled, start=1)}
    with st.container(horizontal=True, wrap=True, vertical_alignment="bottom", gap="medium"):
        st.selectbox("Impact of", opts, key=S.K_IMPACT_OF, format_func=labels.get, width=460,
                     help="One assumption is run on its own against the baseline, so you see only its effect.")
        st.segmented_control("Measure", list(S.IMPACT_METRIC_LABELS), key=S.K_IMPACT_METRIC, required=True,
                             format_func=S.IMPACT_METRIC_LABELS.get)
        st.segmented_control("Tile size", list(S.IMPACT_SIZE_LABELS), key=S.K_IMPACT_SIZE, required=True,
                             format_func=S.IMPACT_SIZE_LABELS.get, on_change=S.set_selected, args=(None,),
                             help="Pool size keeps the usual layout and greys pools the scenario does not touch. "
                                  "Size of change sizes each tile by its absolute change.")


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


def section_overview(result, meta: Metadata, f: S.Filters, geo: Geo, cmp=None) -> None:
    ds = meta.datasets[f.dataset]
    yr = f"{f.year}{' (forecast)' if f.year > meta.periods.base_year else ''}"
    st.markdown(ui.page_header("Profit Pools Explorer",
                               f"{ds.label} · {yr} · {_geo_text(meta, geo)} · fictional data, for illustration only"),
                unsafe_allow_html=True)
    st.markdown(ui.section_nav([("overview", "Overview"), ("trends", "Trends & detail"), ("flows", "Value flows"),
                                ("growth", "Growth"), ("detail", "Summary table")]),
                unsafe_allow_html=True)
    st.markdown('<div id="overview"></div>', unsafe_allow_html=True)
    if f.view == "impact":
        ap = S.applied()
        st.markdown(ui.banner(f"Impact only: the change from scenario “{ap.name}” versus baseline. Pools the "
                              f"scenario does not touch are greyed out. Every chart below shows the change."),
                    unsafe_allow_html=True)
        _impact_controls(meta)
    elif f.view != "baseline":
        ap = S.applied()
        n = len(ap.enabled)
        st.markdown(ui.banner(f"{'Comparing' if f.view == 'compare' else 'Showing'} scenario “{ap.name}” "
                              f"({n} assumption{'s' if n != 1 else ''}){' with baseline' if f.view == 'compare' else ''}. "
                              f"Every chart, KPI and table below uses the same scenario run."), unsafe_allow_html=True)
    if f.view == "impact":
        _impact_kpis(result, meta, f, geo, cmp)
    else:
        _kpis(result, meta, f, geo, cmp)

    # Toolbar
    with st.container(horizontal=True, wrap=True, vertical_alignment="bottom", gap="medium"):
        _breadcrumb(meta, f.focus)
        st.space("stretch")
        st.segmented_control("Segment columns by", list(S.SEGMENT_LABELS), key=S.K_SEGMENT, required=True,
                             format_func=S.SEGMENT_LABELS.get, on_change=S.set_selected, args=(None,),
                             help="Sub-pools: the next level of the hierarchy. Geography: states (or counties "
                                  "within a state). Funding source: who pays provider and pharmacy revenue.")
        if cmp is not None and f.view != "impact":
            st.segmented_control("Color by", ["margin", "delta_margin"], key=S.K_COLOR_BY, required=True,
                                 format_func={"margin": "Margin", "delta_margin": "Δ margin"}.get,
                                 help="Margin, or the change in margin versus baseline (pp).")
        st.segmented_control("Spend basis", list(S.BASIS_LABELS), key=S.K_BASIS, required=True,
                             format_func=S.BASIS_LABELS.get,
                             help="Allowed = everything providers receive. Payer-paid = paid by payers and "
                                  "public programs. Consumer = member cost sharing and self-pay (allowed − paid). "
                                  "Applies to provider and pharmacy pools; payer and top-down pools have no split.")

    if cmp is not None:
        tiles = V.compare_tiles(result, cmp, meta, f.dataset, f.year, f.focus, geo, f.basis, f.segment_by)
    else:
        tiles = V.marimekko_tiles(result, meta, f.dataset, f.year, f.focus, geo, f.basis, f.segment_by)
    rows = tiles.rows
    color_by = st.session_state[S.K_COLOR_BY] if cmp is not None else "margin"
    impact_metric = st.session_state[S.K_IMPACT_METRIC]
    by_change = False
    if f.view == "impact":
        color_by = "impact"
        by_change = st.session_state[S.K_IMPACT_SIZE] == "change"
        if by_change and not rows.empty:
            rows = V.impact_layout(rows, impact_metric)
    col_labels = {c: meta.taxonomy[c].short_label for c in rows["col_id"].unique()}
    estimate_cols = {c for c in col_labels if V.economics_label(meta, c, f.dataset) == "top_down"}
    sel = f.selected
    sel_ref = marimekko.TileRef(*sel) if sel else None
    if rows.empty:
        if by_change:
            st.info(f"No pool in this view changes {S.IMPACT_METRIC_LABELS[impact_metric].lower()} in {f.year}. "
                    f"Try another year or measure.")
        else:
            st.info("Nothing to show for this combination. " + " ".join(_merge_reasons(meta, tiles.unavailable)))
        st.session_state[K_TILE_REFS] = []
    else:
        fig, refs = marimekko.build(rows, col_labels, selected=sel_ref, estimate_cols=estimate_cols,
                                    basis_label=S.BASIS_REVENUE_LABEL[f.basis], color_by=color_by,
                                    impact_metric=impact_metric, impact_min=V.IMPACT_MIN,
                                    x_title="Share of total absolute change (column width)" if by_change
                                    else "Share of revenue (column width)")
        st.session_state[K_TILE_REFS] = refs
        chart_key = (f"pp.mekko.{f.dataset}.{f.focus}.{f.segment_by}.{f.basis}.{f.geo_id}.{f.year}.{f.view}"
                     f"{'.' + impact_metric + str(by_change) if f.view == 'impact' else ''}")
        st.plotly_chart(fig, key=chart_key, on_select=lambda: _on_chart_select(chart_key),
                        selection_mode="points", config=PLOTLY_CONFIG)
    if color_by == "impact":
        mx = float(rows[impact_metric].abs().max()) if not rows.empty else 0.0
        st.markdown(ui.impact_legend(S.IMPACT_METRIC_LABELS[impact_metric], fmt.money(mx), by_change),
                    unsafe_allow_html=True)
    else:
        st.markdown(ui.margin_legend(bool(estimate_cols), delta=color_by == "delta_margin"), unsafe_allow_html=True)
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

def _inspector(result, meta: Metadata, f: S.Filters, geo: Geo, cmp=None) -> None:
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
    if cmp is not None:
        b = V.node_metrics(cmp, meta, f.dataset, f.year, node_id, geo, f.basis)
        if b is not None:
            kv += [None, ("Δ revenue vs baseline", fmt.money(m["revenue"] - b["revenue"], signed=True)),
                   ("Δ gain vs baseline", fmt.money((m["gain"] if np.isfinite(m["gain"]) else 0)
                                                    - (b["gain"] if np.isfinite(b["gain"]) else 0), signed=True)),
                   ("Δ margin vs baseline", fmt.pp(m["margin"] - b["margin"]))]
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
    if cmp is not None:
        why = [(a, dr, dg) for a, dr, dg in _bridge_for(meta, f, geo, node_id)
               if abs(dr) >= 0.5e6 or abs(dg) >= 0.5e6]
        html.append('<div class="pp-h3" style="margin-top:14px">Why it changed</div>')
        if why:
            html.append(ui.table(["Assumption (in order)", "Δ Revenue", "Δ Gain"],
                                 [[a.label or a.driver, fmt.money(dr, signed=True), fmt.money(dg, signed=True)]
                                  for a, dr, dg in why]))
            html.append(ui.note("Sequential attribution in list order; contributions sum to the total change."))
        else:
            html.append(ui.note(f"No assumption changes this pool in {f.year}."))
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


def section_trends(result, meta: Metadata, f: S.Filters, geo: Geo, cmp=None) -> None:
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
            def all_series(res):
                out = []
                for nid in ids:
                    s = V.node_series(res, meta, f.dataset, nid, geo, f.basis)
                    s["node_id"], s["label"] = nid, meta.taxonomy[nid].short_label
                    out.append(s)
                return pd.concat(out, ignore_index=True)

            df = all_series(result)
            bdf = all_series(cmp) if cmp is not None else None
            fixed = LAYER_COLOR if f.focus is None else {}
            colors = trends.series_colors(ids, fixed)
            metric = st.segmented_control("Metric", ["revenue", "gain", "margin"], key=S.K_TREND_METRIC,
                                          required=True, label_visibility="collapsed",
                                          format_func={"revenue": "Revenue", "gain": "Operating gain",
                                                       "margin": "Operating margin"}.get)
            plot = df.copy()
            bplot = bdf.copy() if bdf is not None else None
            if f.view == "impact":  # the change only: scenario − baseline per pool and year
                b = bdf.set_index(["node_id", "year"])[metric]
                plot[metric] = plot[metric].to_numpy() - b.reindex(
                    pd.MultiIndex.from_frame(plot[["node_id", "year"]])).fillna(0).to_numpy()
                bplot = None
            if metric == "margin":
                yfmt, hover, suffix = ".1%", fmt.pct, ""
            else:  # axis in $B; hover keeps full formatting
                plot[metric] = plot[metric] / 1e9
                if bplot is not None:
                    bplot[metric] = bplot[metric] / 1e9
                yfmt = "$,.2f" if f.view == "impact" else "$,.0f"
                hover, suffix = (lambda v: fmt.money(v * 1e9, signed=f.view == "impact")), "B"
            fig = trends.lines(plot, metric, label_col="label", id_col="node_id", colors=colors,
                               base_year=meta.periods.base_year, yformat=yfmt, hover_fmt=hover,
                               zero_line=metric != "revenue" or f.view == "impact", baseline=bplot)
            fig.update_yaxes(ticksuffix=suffix)
            st.plotly_chart(trends.mark_year(fig, f.year), config=PLOTLY_CONFIG, key="pp.trend")
            if f.view == "impact":
                st.markdown(ui.note("Change versus baseline by year" + (" (percentage points)." if metric == "margin"
                                                                         else ".")), unsafe_allow_html=True)
            elif cmp is not None:
                st.markdown(ui.note("Faint lines show the baseline; solid lines the scenario."), unsafe_allow_html=True)
            if len(ids) > trends.MAX_SERIES:
                st.markdown(ui.note("Series beyond the fifth share the neutral 'other' color; hover for names."),
                            unsafe_allow_html=True)

            if f.view != "impact":
                tot = df.groupby("year")["revenue"].transform("sum")
                df["share"] = df["revenue"] / tot
                st.markdown('<div class="pp-h3" style="margin-top:8px">Composition of revenue</div>',
                            unsafe_allow_html=True)
                st.plotly_chart(trends.composition(df, label_col="label", id_col="node_id", colors=colors,
                                                   base_year=meta.periods.base_year),
                                config=PLOTLY_CONFIG, key="pp.composition")

            if cmp is not None:
                _scenario_bridge_table(meta, f, geo)
            if f.view != "impact":
                drivers = V.change_drivers(result, meta, f.dataset, f.year, f.focus, geo, f.basis)
                st.markdown(f'<div class="pp-h3" style="margin-top:8px">What drove the change, {f.year - 1}→{f.year}'
                            f'{" (scenario)" if f.view != "baseline" else ""}</div>', unsafe_allow_html=True)
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
                    st.markdown(ui.note("Year-over-year change by pool. In Compare view the table above attributes "
                                        "the scenario's change to each assumption."), unsafe_allow_html=True)
    with right:
        _inspector(result, meta, f, geo, cmp)


def _scenario_bridge_table(meta: Metadata, f: S.Filters, geo: Geo) -> None:
    rows = _bridge_for(meta, f, geo, f.focus)
    st.markdown(f'<div class="pp-h3" style="margin-top:8px">Why the scenario differs from baseline, {f.year} · '
                f'{_label(meta, f.focus)}</div>', unsafe_allow_html=True)
    total_r, total_g = sum(r for _, r, _ in rows), sum(g for _, _, g in rows)
    body = [[f"{i}. {a.label or a.driver}", fmt.money(dr, signed=True), fmt.money(dg, signed=True)]
            for i, (a, dr, dg) in enumerate(rows, start=1)]
    body.append(["Total change", fmt.money(total_r, signed=True), fmt.money(total_g, signed=True)])
    st.markdown(ui.table(["Assumption (applied in order)", "Δ Revenue", "Δ Gain"], body), unsafe_allow_html=True)
    st.markdown(ui.note("Sequential attribution: each row is the change from adding that assumption to the ones "
                        "above it. Rows sum exactly to the total; reordering assumptions can shift amounts "
                        "between rows."), unsafe_allow_html=True)


def section_table(result, meta: Metadata, f: S.Filters, geo: Geo, cmp=None) -> None:
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
    if cmp is not None:
        b = V.rollup(cmp, meta, f.dataset, f.year, f.focus, geo, f.basis).rows.set_index("node_id")
        out.insert(2, "Δ Revenue ($B)", (r["revenue"].to_numpy() - b["revenue"].reindex(r["node_id"]).fillna(0).to_numpy()) / 1e9)
        out.insert(5, "Δ Gain ($B)", (r["gain"].fillna(0).to_numpy() - b["gain"].reindex(r["node_id"]).fillna(0).to_numpy()) / 1e9)
    st.dataframe(out, hide_index=True, width="stretch", column_config={
        "Revenue ($B)": st.column_config.NumberColumn(format="$%.1f"),
        "Δ Revenue ($B)": st.column_config.NumberColumn(format="%+.2f"),
        "Δ Gain ($B)": st.column_config.NumberColumn(format="%+.2f"),
        "Share": st.column_config.NumberColumn(format="%.1f%%"),
        "Operating gain ($B)": st.column_config.NumberColumn(format="$%.2f"),
        "Margin": st.column_config.NumberColumn(format="%.1f%%"),
        "Margin coverage": st.column_config.NumberColumn(format="%.1f%%"),
        "Enrollment": st.column_config.TextColumn(help="Payer pools only"),
        "Revenue PMPM": st.column_config.TextColumn(help="Payer pools only"),
    })


def render() -> None:
    S.init(data.adapter().metadata)
    gj = S.grouping_json()
    meta = data.metadata(gj)
    base = data.baseline(gj)
    f = S.current()
    if f.focus is not None and f.focus not in meta.taxonomy:      # a group from a grouping no longer active
        S.set_focus(None)
    if f.selected and any(x is not None and x not in meta.taxonomy for x in (f.selected[0], f.selected[2])):
        S.set_selected(None)
    layout.global_bar(meta)
    f = S.current()
    if f.focus is not None and not meta.in_dataset(f.focus, f.dataset):
        S.set_focus(None)
        f = S.current()
    geo = Geo.parse(meta, f.geo_id)

    # ONE engine: the scenario view and every chart read the same cached scenario run.
    result, cmp = base, None
    if f.view == "impact" and st.session_state[S.K_IMPACT_OF] not in {a.id for a in S.applied().enabled}:
        st.session_state[S.K_IMPACT_OF] = S.IMPACT_ALL   # before the picker is drawn
    if f.view != "baseline":
        run = data.scenario_run(_active_scenario(f.view).to_json(), gj)
        if run.ok:
            result = run.result
            cmp = base if f.view in ("compare", "impact") else None
        else:
            st.error("The applied scenario has blocking issues; showing baseline. Open the scenario builder to fix: "
                     + " ".join(i.message for i in run.issues if i.level == "error")[:400])
            f = S.Filters(**{**f.__dict__, "view": "baseline"})

    section_overview(result, meta, f, geo, cmp)
    f = S.Filters(**{**S.current().__dict__, "view": f.view})
    section_trends(result, meta, f, geo, cmp)
    section_flows(result, meta, f, geo, cmp)
    section_growth(result, meta, f, geo, cmp)
    section_table(result, meta, f, geo, cmp)
