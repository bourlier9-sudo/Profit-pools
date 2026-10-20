"""Sankey figure builder. Lays out a `SankeyView`; computes no financials."""

from __future__ import annotations

import numpy as np
import pandas as pd
import plotly.graph_objects as go

from profit_pools.core.model.flows_view import CONSUMER, OTHER, SankeyView
from profit_pools.core.taxonomy import Metadata
from profit_pools.design import format as fmt
from profit_pools.design.theme import TEMPLATE_NAME
from profit_pools.design.tokens import (COLOR, FUNDING_COLOR, LAYER_COLOR, MARGIN_STOPS, OTHER as OTHER_COLOR,
                                        diverging)

# Payer LOB groups: steps of the payer identity hue (identity within the Payer layer).
LOB_GROUP_COLOR = {"COMM": "#1F6FB2", "MCR": "#3F8BCB", "MCD": "#76A9DA"}
FUNDING_GROUP_COLOR = {"GOV_FFS": "#9FAAB4"}
CONSUMER_COLOR = "#5E6B78"
DELTA_CLAMP = 0.10   # ±10% change maps to the ends of the diverging scale


def _rgba(hex_color: str, alpha: float) -> str:
    h = hex_color.lstrip("#")
    return f"rgba({int(h[0:2], 16)},{int(h[2:4], 16)},{int(h[4:6], 16)},{alpha})"


def node_color(meta: Metadata, column: str, key: str) -> str:
    if key == OTHER:
        return OTHER_COLOR
    if key == CONSUMER:
        return CONSUMER_COLOR
    if column.startswith("source"):
        if key in LOB_GROUP_COLOR:
            return LOB_GROUP_COLOR[key]
        if key in meta.lobs.lobs:
            return LOB_GROUP_COLOR.get(meta.lobs.group_of(key), LAYER_COLOR["PAYER"])
        if key in FUNDING_GROUP_COLOR:
            return FUNDING_GROUP_COLOR[key]
        return FUNDING_COLOR.get(key, OTHER_COLOR)
    return LAYER_COLOR.get(meta.taxonomy[key].layer_id, OTHER_COLOR)


ORDERS = {"auto": "Automatic", "size": "Largest first", "hierarchy": "Hierarchy order", "name": "A–Z"}


def positions(nodes: pd.DataFrame, column: pd.Series, order: str = "auto", pins: tuple[str, ...] = (),
              pad: float = 0.025, rank: pd.Series | None = None) -> tuple[list[float], list[float]] | None:
    """Explicit node x / y (Plotly centers) so a chosen order survives reruns and exports. `nodes` needs
    node_id, label, value; `column` is each node's column index. Pinned nodes go first in their column.
    "auto" returns None (Plotly's own layout)."""
    if order == "auto" and not pins:
        return None
    seq = rank.to_numpy() if rank is not None else np.arange(len(nodes))
    df = nodes.assign(col=column.to_numpy(), v=nodes["value"].abs().to_numpy(), seq=seq)
    ncol = max(int(df["col"].max()), 1)
    tot = df.groupby("col")["v"].sum()
    count = df.groupby("col").size()
    usable = 1 - pad * (count.max() - 1) - 0.02
    scale = usable / tot.max() if tot.max() > 0 else 0.0
    xs, ys = [0.0] * len(df), [0.0] * len(df)
    key = {"size": lambda d: (-d["v"]), "name": lambda d: d["label"].str.lower(), "hierarchy": lambda d: d["seq"],
           "auto": lambda d: d["seq"]}[order]
    for c, g in df.groupby("col"):
        g = g.assign(k=key(g), pin=~g["node_id"].isin(pins)).sort_values(["pin", "k"])
        cur = 0.01
        for i, r in g.iterrows():
            h = max(r["v"] * scale, 0.004)
            xs[df.index.get_loc(i)] = min(max(c / ncol, 0.001), 0.999)
            ys[df.index.get_loc(i)] = min(max(cur + h / 2, 0.001), 0.999)
            cur += h + pad
    return xs, ys


def build(view: SankeyView, meta: Metadata, *, delta: pd.Series | None = None, height: int | None = None,
          selected: str | None = None, impact: bool = False, impact_min: float = 0.5e6,
          order: str = "auto", pins: tuple[str, ...] = (), rank: pd.Series | None = None) -> go.Figure:
    """`impact`: draw only the change — link width = |Δ| vs baseline (teal up, red down); unchanged links
    and nodes drop out. Node labels then show the net Δ through each node (outflow, or inflow at the end)."""
    if impact and delta is not None:
        return _impact(view, meta, delta, height, impact_min)
    nodes = view.nodes.reset_index(drop=True)
    idx = {nid: i for i, nid in enumerate(nodes["node_id"])}
    total = view.total or 1.0
    colors = [node_color(meta, c, k) for c, k in zip(nodes["column"], nodes["key"])]
    labels = [f"{l}  {fmt.money(v)}" for l, v in zip(nodes["label"], nodes["value"])]
    node_hover = [f"<b>{l}</b><br>{fmt.money(v)} · {fmt.pct(v / total)} of total" +
                  (f"<br><i>{len(m)} items: {', '.join(m[:6])}{'…' if len(m) > 6 else ''}</i>" if k == OTHER else "")
                  for l, v, k, m in zip(nodes["label"], nodes["value"], nodes["key"], nodes["members"])]

    links = view.links.reset_index(drop=True)
    src = [idx[s] for s in links["source"]]
    tgt = [idx[t] for t in links["target"]]
    if delta is not None and len(links):
        base_val = links["value"] - delta.to_numpy()
        pct = np.where(base_val.abs() > 0, delta.to_numpy() / base_val.abs(), 0.0)
        lcolors = [_rgba(diverging(float(p) / DELTA_CLAMP), 0.6) for p in pct]
        delta_txt = [f"<br>vs baseline {fmt.money(d, signed=True)} ({fmt.pct(p, 1, signed=True)})"
                     for d, p in zip(delta.to_numpy(), pct)]
    else:
        # Negative values (losses, gain metric only) are drawn at |value| in red; node totals stay net.
        lcolors = [_rgba(colors[s], 0.32) if v >= 0 else _rgba(COLOR["bad"], 0.45)
                   for s, v in zip(src, links["value"])]
        delta_txt = [""] * len(links)
    if selected is not None:
        lcolors = [(_rgba(colors[s], 0.75) if f"{a}→{b}" == selected or a == selected or b == selected else c)
                   for s, a, b, c in zip(src, links["source"], links["target"], lcolors)]
    nl = nodes.set_index("node_id")["label"]
    link_hover = [f"<b>{nl[a]} → {nl[b]}</b><br>{fmt.money(v)} · {fmt.pct(ss)} of source · "
                  f"{fmt.pct(st)} of target{dt}"
                  for a, b, v, ss, st, dt in zip(links["source"], links["target"], links["value"],
                                                 links["share_of_source"], links["share_of_target"], delta_txt)]
    per_col = nodes.groupby("column").size().max() if len(nodes) else 1
    pos = positions(nodes, nodes["column"].map({c: i for i, c in enumerate(view.columns)}), order, pins,
                    rank=rank.reindex(nodes["node_id"]).fillna(1e9).reset_index(drop=True) if rank is not None else None)
    node = dict(label=labels, color=colors, pad=12, thickness=14, line=dict(width=0),
                customdata=node_hover, hovertemplate="%{customdata}<extra></extra>")
    if pos:
        node.update(x=pos[0], y=pos[1])
    fig = go.Figure(go.Sankey(
        arrangement="snap", valueformat=",.0f",
        node=node,
        link=dict(source=src, target=tgt, value=links["value"].abs().tolist(), color=lcolors,
                  customdata=link_hover, hovertemplate="%{customdata}<extra></extra>"),
    ))
    fig.update_layout(template=TEMPLATE_NAME, height=height or max(460, min(900, 30 * int(per_col) + 120)),
                      margin=dict(t=8, r=8, b=8, l=8), font=dict(size=12, color=COLOR["ink_1"]))
    return fig


def _impact(view: SankeyView, meta: Metadata, delta: pd.Series, height: int | None, impact_min: float) -> go.Figure:
    links = view.links.assign(d=delta.to_numpy()).reset_index(drop=True)
    links = links[links["d"].abs() >= impact_min]
    if links.empty:
        return go.Figure(layout=dict(template=TEMPLATE_NAME, height=height or 200))
    nodes = view.nodes[view.nodes["node_id"].isin(set(links["source"]) | set(links["target"]))].reset_index(drop=True)
    idx = {nid: i for i, nid in enumerate(nodes["node_id"])}
    out_d = links.groupby("source")["d"].sum()
    in_d = links.groupby("target")["d"].sum()
    node_d = [out_d.get(n, in_d.get(n, 0.0)) for n in nodes["node_id"]]
    colors = [node_color(meta, c, k) for c, k in zip(nodes["column"], nodes["key"])]
    labels = [f"{l}  {fmt.money(d, signed=True)}" for l, d in zip(nodes["label"], node_d)]
    nl = nodes.set_index("node_id")["label"]
    hover = [f"<b>{nl[a]} → {nl[b]}</b><br>Change vs baseline {fmt.money(d, signed=True)}"
             + (f" ({fmt.pct(d / (v - d), 1, signed=True)})" if abs(v - d) > 0 else "")
             + f"<br>Scenario {fmt.money(v)}"
             for a, b, v, d in zip(links["source"], links["target"], links["value"], links["d"])]
    lcolors = [_rgba(COLOR["accent"] if d > 0 else COLOR["bad"], 0.5) for d in links["d"]]
    per_col = nodes.groupby("column").size().max()
    fig = go.Figure(go.Sankey(
        arrangement="snap", valueformat=",.0f",
        node=dict(label=labels, color=colors, pad=12, thickness=14, line=dict(width=0),
                  hovertemplate="%{label}<extra></extra>"),
        link=dict(source=[idx[a] for a in links["source"]], target=[idx[b] for b in links["target"]],
                  value=links["d"].abs().tolist(), color=lcolors, customdata=hover,
                  hovertemplate="%{customdata}<extra></extra>"),
    ))
    fig.update_layout(template=TEMPLATE_NAME, height=height or max(360, min(900, 30 * int(per_col) + 120)),
                      margin=dict(t=8, r=8, b=8, l=8), font=dict(size=12, color=COLOR["ink_1"]))
    return fig


# --- "Follow the enterprise dollar" -------------------------------------------------------------------

ENT_COLUMN = {"segment": 0, "source": 0, "loss": 0, "asset": 1, "cost": 2, "gain": 2}


def _ent_columns(view) -> pd.Series:
    """Column per node: P&L view = sources | owned assets | outcomes; gain view = contributors | outcomes."""
    gain_view = not (view.nodes["kind"] == "source").any() and not (view.links["kind"] == "claims").any()
    if gain_view:
        return view.nodes["kind"].map(lambda k: 1 if k in ("gain", "cost") else 0)
    return view.nodes["kind"].map(ENT_COLUMN)


def enterprise(view, *, delta: pd.Series | None = None, order: str = "auto", pins: tuple[str, ...] = (),
               height: int | None = None) -> go.Figure:
    """Chart for core.model.enterprise_flow.EnterpriseFlow. Plans in payer blue, owned assets in the accent,
    costs neutral, losses red; links take the color of what they are (claims, cost, gain, loss)."""
    nodes = view.nodes.reset_index(drop=True)
    idx = {n: i for i, n in enumerate(nodes["node_id"])}
    node_fill = {"segment": LAYER_COLOR["PAYER"], "asset": COLOR["accent"], "source": OTHER_COLOR,
                 "cost": COLOR["line_strong"], "gain": MARGIN_STOPS[-1][1], "loss": COLOR["bad"]}
    colors = [node_fill[k] for k in nodes["kind"]]
    labels = [f"{l}  {fmt.money(v)}" for l, v in zip(nodes["label"], nodes["value"])]
    links = view.links.reset_index(drop=True)
    if delta is not None and len(links):
        d = delta.to_numpy()
        base = links["value"].to_numpy() - d
        pct = np.where(np.abs(base) > 0, d / np.abs(base), 0.0)
        lcolors = [_rgba(diverging(float(p) / DELTA_CLAMP), 0.6) for p in pct]
        extra = [f"<br>vs baseline {fmt.money(x, signed=True)}" for x in d]
    else:
        link_fill = {"claims": LAYER_COLOR["PAYER"], "source": OTHER_COLOR, "cost": COLOR["ink_3"],
                     "gain": COLOR["accent"], "loss": COLOR["bad"]}
        lcolors = [_rgba(link_fill[k], 0.45 if k in ("gain", "loss") else 0.25) for k in links["kind"]]
        extra = [""] * len(links)
    nl = nodes.set_index("node_id")["label"]
    hover = [f"<b>{nl[a]} → {nl[b]}</b><br>{fmt.money(v)}{e}"
             for a, b, v, e in zip(links["source"], links["target"], links["value"], extra)]
    pos = positions(nodes, _ent_columns(view).reset_index(drop=True), order, pins)
    node = dict(label=labels, color=colors, pad=14, thickness=14, line=dict(width=0),
                hovertemplate="%{label}<extra></extra>")
    if pos:
        node.update(x=pos[0], y=pos[1])
    fig = go.Figure(go.Sankey(
        arrangement="snap", valueformat=",.0f", node=node,
        link=dict(source=[idx[a] for a in links["source"]], target=[idx[b] for b in links["target"]],
                  value=links["value"].tolist(), color=lcolors, customdata=hover,
                  hovertemplate="%{customdata}<extra></extra>")))
    per_col = nodes["kind"].map(ENT_COLUMN).value_counts().max() if len(nodes) else 1
    fig.update_layout(template=TEMPLATE_NAME, height=height or max(460, min(900, 34 * int(per_col) + 120)),
                      margin=dict(t=8, r=8, b=8, l=8), font=dict(size=12, color=COLOR["ink_1"]))
    return fig
