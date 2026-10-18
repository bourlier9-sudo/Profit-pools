"""Section C — value flows (Sankey), its controls, flow table and inspector.

Streamlit returns no click events for Sankey charts, so selection is
synchronized through the flow table (row selection) and a node selector.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import streamlit as st

from profit_pools.charts import sankey
from profit_pools.core.model import flows_view as FV
from profit_pools.core.model.views import Geo
from profit_pools.core.taxonomy import Metadata
from profit_pools.design import components as ui
from profit_pools.design import format as fmt
from profit_pools.design.theme import PLOTLY_CONFIG
from profit_pools.shell import state as S

K = "pp.sk."
DEPTH_LABELS = ["Layer", "Level 1", "Level 2", "Level 3", "Level 4"]
SOURCE_MODES = {"group": ("source_group",), "detail": ("source_detail",), "both": ("source_group", "source_detail")}
SOURCE_MODE_LABELS = {"group": "Groups", "detail": "Detail", "both": "Groups → detail"}


def _init(meta: Metadata) -> None:
    for k, v in {"metric": "allowed", "sources": "group", "depth": 1, "layer_col": False, "expand": [],
                 "src_filter": [], "rec_filter": [], "min_share": 0.5, "consumer": False, "sel": None,
                 "show": "chart"}.items():
        st.session_state.setdefault(K + k, v)


def _config(meta: Metadata) -> FV.SankeyConfig:
    ss = st.session_state
    rec = ("recipient_layer", "recipient") if ss[K + "layer_col"] and ss[K + "depth"] > 0 else ("recipient",)
    return FV.SankeyConfig(
        metric=ss[K + "metric"], source_columns=SOURCE_MODES[ss[K + "sources"]], recipient_columns=rec,
        recipient_depth=int(ss[K + "depth"]), expand=tuple(ss[K + "expand"]),
        source_filter=tuple(ss[K + "src_filter"]), recipient_filter=tuple(ss[K + "rec_filter"]),
        split_consumer=bool(ss[K + "consumer"]) and ss[K + "metric"] == "allowed",
        min_share=float(ss[K + "min_share"]) / 100)


def _source_filter_options(meta: Metadata, dataset: str) -> list[str]:
    out = list(meta.lobs.groups)
    if meta.datasets[dataset].funding_sources:
        out += list(meta.funding_groups) + [f for f in meta.datasets[dataset].funding_sources
                                            if meta.funding_sources[f].group is None]
    return out


def _recipient_nodes(meta: Metadata, dataset: str, max_depth: int = 2) -> list[str]:
    return [n.id for n in meta.taxonomy
            if n.depth <= max_depth and not n.is_leaf and meta.in_dataset(n.id, dataset)
            and any(l.receives_flows for l in meta.taxonomy.leaves(n.id))]


def _indent(meta: Metadata, nid: str) -> str:
    n = meta.taxonomy[nid]
    return ("  " * n.depth) + n.label


def _controls(meta: Metadata, f: S.Filters) -> None:
    ss = st.session_state
    with st.container(horizontal=True, wrap=True, vertical_alignment="bottom", gap="medium"):
        st.segmented_control("Metric", list(FV.METRICS), key=K + "metric", required=True,
                             format_func={"allowed": "Allowed", "paid": "Payer-paid", "consumer": "Consumer",
                                          "gain": "Recipient gain"}.get,
                             help="Allowed = everything recipients receive; Payer-paid and Consumer split it; "
                                  "Recipient gain = flow × the receiving pool's operating margin.")
        st.segmented_control("Sources", list(SOURCE_MODES), key=K + "sources", required=True,
                             format_func=SOURCE_MODE_LABELS.get,
                             help="Payer LOB groups and funding groups, individual LOBs and sources, or both columns.")
        max_d = meta.max_levels
        st.select_slider("Recipient detail", options=list(range(max_d + 1)), key=K + "depth",
                         format_func=lambda d: DEPTH_LABELS[d], width=260,
                         help="Depth of the recipient hierarchy. Use Expand to open individual pools further.")
        st.toggle("Layer column", key=K + "layer_col", disabled=ss[K + "depth"] == 0,
                  help="Adds a Provider / Pharmacy column before the recipients.")
        st.toggle("Consumers as a source", key=K + "consumer", disabled=ss[K + "metric"] != "allowed",
                  help="Moves member cost sharing and self-pay out of each source into one 'Consumers' source "
                       "(allowed metric only). Totals are unchanged.")
        with st.popover("Filters & detail", icon=":material/filter_list:"):
            st.multiselect("Expand recipients", _recipient_nodes(meta, f.dataset, 3), key=K + "expand",
                           format_func=lambda n: _indent(meta, n), placeholder="e.g. Hospitals",
                           help="Shows the chosen pools one level deeper (repeat to go further).")
            st.multiselect("Only these sources", _source_filter_options(meta, f.dataset), key=K + "src_filter",
                           format_func=lambda s: FV.source_label(meta, s), placeholder="All sources")
            st.multiselect("Only these recipients", _recipient_nodes(meta, f.dataset, 2), key=K + "rec_filter",
                           format_func=lambda n: _indent(meta, n), placeholder="All recipients")
            st.slider("Fold nodes smaller than (% of total) into “Other”", 0.0, 5.0, step=0.25,
                      key=K + "min_share")
        st.space("stretch")
        st.segmented_control("Show", ["chart", "table"], key=K + "show", required=True,
                             format_func={"chart": "Sankey", "table": "Table"}.get, label_visibility="hidden")


def _flow_table(view: FV.SankeyView, delta: pd.Series | None, metric_label: str) -> pd.DataFrame:
    nl = view.nodes.set_index("node_id")["label"]
    t = pd.DataFrame({
        "From": view.links["source"].map(nl), "To": view.links["target"].map(nl),
        f"{metric_label} ($M)": view.links["value"] / 1e6,
        "% of total": view.links["share_of_total"] * 100,
        "% of source": view.links["share_of_source"] * 100,
        "% of target": view.links["share_of_target"] * 100,
    })
    if delta is not None:
        t["Δ vs baseline ($M)"] = delta.to_numpy() / 1e6
    return t


def _on_table_select(view_links: pd.DataFrame) -> None:
    ev = st.session_state.get(K + "table")
    rows = (ev or {}).get("selection", {}).get("rows", []) if isinstance(ev, dict) else (
        ev.selection.rows if ev is not None else [])
    if rows:
        r = view_links.iloc[rows[0]]
        st.session_state[K + "sel"] = ("link", r["source"], r["target"])
        st.session_state[K + "node"] = "__none__"


def _on_node_select() -> None:
    v = st.session_state.get(K + "node")
    st.session_state[K + "sel"] = None if v in (None, "__none__") else ("node", v)


def _inspector(view: FV.SankeyView, meta: Metadata, result, base_view, f: S.Filters, geo: Geo, metric_label: str):
    ss = st.session_state
    sel = ss.get(K + "sel")
    node_ids = list(view.nodes["node_id"])
    valid = sel is not None and (
        (sel[0] == "node" and sel[1] in node_ids) or
        (sel[0] == "link" and ((view.links["source"] == sel[1]) & (view.links["target"] == sel[2])).any()))
    if not valid:
        sel = None
    ss[K + "node"] = sel[1] if sel and sel[0] == "node" else "__none__"
    labels = view.nodes.set_index("node_id")["label"].to_dict()
    cols = view.nodes.set_index("node_id")["column"].to_dict()
    st.selectbox("Inspect a node", ["__none__", *node_ids], key=K + "node", on_change=_on_node_select,
                 format_func=lambda n: "Choose a node, or select a row in the flow table…" if n == "__none__"
                 else f"{FV.COLUMN_LABELS[cols[n]]} › {labels[n]}")
    if sel is None:
        st.markdown(ui.note("Select a flow (table row) or a node to see its source, destination, shares, "
                            "enrollment and PMPM, instances, related pool economics and attribution method."),
                    unsafe_allow_html=True)
        return
    html = ['<div class="pp-inspector">']
    if sel[0] == "link":
        d = FV.link_detail(view, meta, result, f.dataset, f.year, geo, sel[1], sel[2])
        html.append(f'<div class="pp-path">Flow</div><h3 class="pp-h3">{d["from"]} → {d["to"]}</h3>')
        kv = [(metric_label, fmt.money(d["value"])), ("Share of source", fmt.pct(d["share_of_source"])),
              ("Share of destination", fmt.pct(d["share_of_target"])), ("Share of total", fmt.pct(d["share_of_total"])),
              None, ("Allowed", fmt.money(d["amount"])), ("Payer / program-paid", fmt.money(d["paid"])),
              ("Consumer (cost sharing & self-pay)", fmt.money(d["consumer"])),
              None, ("Recipient gain attributed", fmt.money(d["gain"])), ("Implied recipient margin", fmt.pct(d["margin"]))]
        if "enrollment" in d:
            kv += [None, ("Source enrollment", fmt.members(d["enrollment"])), ("Allowed PMPM (this flow)", fmt.pmpm(d["pmpm"]))]
        if "instances" in d:
            kv += [("Service instances", fmt.count(d["instances"])),
                   ("Average allowed per instance", fmt.money(d["allowed_per_instance"]))]
        if base_view is not None:
            bl = base_view.links
            m = bl[(bl["source"] == sel[1]) & (bl["target"] == sel[2])]
            b = float(m["value"].iloc[0]) if len(m) else 0.0
            kv += [None, ("Δ vs baseline", fmt.money(d["value"] - b, signed=True))]
        html.append(ui.kv_grid(kv))
        html.append('<div class="pp-h3" style="margin-top:10px">How it is estimated</div>')
        html.append("".join(ui.note(m) for m in d["methods"]))
        if len(d["underlying_sources"]) > 1 or len(d["underlying_recipients"]) > 1:
            html.append(ui.note(f"Rolls up {d['rows']:,} underlying flows: "
                                f"{', '.join(d['underlying_sources'][:8])} → "
                                f"{', '.join(d['underlying_recipients'][:8])}"
                                f"{'…' if len(d['underlying_recipients']) > 8 else ''}."))
    else:
        d = FV.node_detail(view, meta, result, f.dataset, f.year, geo, sel[1])
        html.append(f'<div class="pp-path">{FV.COLUMN_LABELS[d["column"]]}</div><h3 class="pp-h3">{d["label"]}</h3>')
        kv = [(metric_label, fmt.money(d["value"])), ("Share of total", fmt.pct(d["share_of_total"])), None,
              ("Allowed", fmt.money(d["amount"])), ("Payer / program-paid", fmt.money(d["paid"])),
              ("Consumer", fmt.money(d["consumer"]))]
        if "enrollment" in d:
            kv += [None, ("Enrollment", fmt.members(d["enrollment"])), ("Allowed PMPM", fmt.pmpm(d["pmpm"])),
                   ("Paid PMPM", fmt.pmpm(d["paid_pmpm"]))]
        if "pool_revenue" in d:
            kv += [None, ("Pool revenue (all funders)", fmt.money(d["pool_revenue"])),
                   ("This node's share of pool revenue", fmt.pct(d["share_of_pool_revenue"])),
                   ("Pool operating gain", fmt.money(d["pool_gain"])), ("Pool margin", fmt.pct(d["pool_margin"]))]
        if "instances" in d:
            kv += [("Service instances", fmt.count(d["instances"])),
                   ("Average allowed per instance", fmt.money(d["allowed_per_instance"]))]
        if base_view is not None:
            bn = base_view.nodes.set_index("node_id")["value"]
            kv += [None, ("Δ vs baseline", fmt.money(d["value"] - float(bn.get(sel[1], 0.0)), signed=True))]
        html.append(ui.kv_grid(kv))
        if d["column"] == "recipient" or d["column"] == "recipient_layer":
            html.append(ui.note("Pool revenue includes every funder in the current geography and year; the node "
                                "value respects the Sankey's source filters and spend metric."))
        if len(d["members"]) > 1:
            html.append(ui.note("Includes: " + ", ".join(
                FV.source_label(meta, m) if d["column"].startswith("source") else meta.taxonomy[m].short_label
                for m in d["members"][:12])))
        if "counterparts" in d and len(d["counterparts"]):
            cp = d["counterparts"].head(6)
            html.append(ui.table(["Connected to", metric_label],
                                 [[r.label, fmt.money(r.value)] for r in cp.itertuples()]))
    st.markdown("".join(html) + "</div>", unsafe_allow_html=True)


def section_flows(result, meta: Metadata, f: S.Filters, geo: Geo, cmp=None) -> None:
    _init(meta)
    ss = st.session_state
    st.markdown(ui.section_header(
        "flows", "Value flows",
        f"How spending moves from payers and other funders to providers and pharmacy · "
        f"{meta.datasets[f.dataset].label} · {f.year} · {'National' if geo.level == 'national' else geo.id}"),
        unsafe_allow_html=True)
    _controls(meta, f)
    cfg = _config(meta)
    view = FV.build(result, meta, f.dataset, f.year, geo, cfg)
    metric_label = FV.METRIC_LABELS[cfg.metric]
    if view.nodes.empty or not len(view.links):
        st.info("No flows for this combination of filters, geography and dataset.")
        return
    base_view = FV.build(cmp, meta, f.dataset, f.year, geo, cfg) if cmp is not None else None
    delta = FV.compare_links(view, base_view) if base_view is not None else None

    sel = ss.get(K + "sel")
    sel_id = (f"{sel[1]}→{sel[2]}" if sel[0] == "link" else sel[1]) if sel else None
    if ss[K + "show"] == "chart":
        st.plotly_chart(sankey.build(view, meta, delta=delta, selected=sel_id), config=PLOTLY_CONFIG,
                        key="pp.sankey")
    notes = [f"Total {metric_label.lower()}: {fmt.money(view.total)} — every column sums to this total."]
    if cfg.metric != "gain":
        notes.append("Recipient revenue = payer claims + uninsured + government FFS + supplemental "
                     "(market). Top-down pools (manufacturer, health services, PBM services) have no flows.")
    else:
        notes.append("Link width is |gain|; red links are losses. Node totals are net.")
    if delta is not None:
        notes.append("Compare view: link color shows the % change vs baseline (red down, teal up, ±10% = full color).")
    st.markdown("".join(ui.note(n) for n in notes + view.notes), unsafe_allow_html=True)

    left, right = st.columns([7, 5], gap="large")
    with left:
        st.markdown('<div class="pp-h3">Flow table</div>', unsafe_allow_html=True)
        table = _flow_table(view, delta, metric_label)
        cfgcols = {
            f"{metric_label} ($M)": st.column_config.NumberColumn(format="$%,.0f"),
            "% of total": st.column_config.NumberColumn(format="%.1f%%"),
            "% of source": st.column_config.NumberColumn(format="%.1f%%"),
            "% of target": st.column_config.NumberColumn(format="%.1f%%"),
            "Δ vs baseline ($M)": st.column_config.NumberColumn(format="%+,.0f"),
        }
        links_sorted = view.links.reset_index(drop=True)
        st.dataframe(table, hide_index=True, width="stretch", height=380, column_config=cfgcols,
                     key=K + "table", on_select=lambda: _on_table_select(links_sorted), selection_mode="single-row")
        st.markdown(ui.note("Select a row to inspect that flow. The table reconciles to the chart."),
                    unsafe_allow_html=True)
    with right:
        _inspector(view, meta, result, base_view, f, geo, metric_label)
