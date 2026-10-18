"""Sankey figure builder. Lays out a `SankeyView`; computes no financials."""

from __future__ import annotations

import numpy as np
import pandas as pd
import plotly.graph_objects as go

from profit_pools.core.model.flows_view import CONSUMER, OTHER, SankeyView
from profit_pools.core.taxonomy import Metadata
from profit_pools.design import format as fmt
from profit_pools.design.theme import TEMPLATE_NAME
from profit_pools.design.tokens import COLOR, FUNDING_COLOR, LAYER_COLOR, OTHER as OTHER_COLOR, diverging

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


def build(view: SankeyView, meta: Metadata, *, delta: pd.Series | None = None, height: int | None = None,
          selected: str | None = None) -> go.Figure:
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
    fig = go.Figure(go.Sankey(
        arrangement="snap", valueformat=",.0f",
        node=dict(label=labels, color=colors, pad=12, thickness=14, line=dict(width=0),
                  customdata=node_hover, hovertemplate="%{customdata}<extra></extra>"),
        link=dict(source=src, target=tgt, value=links["value"].abs().tolist(), color=lcolors,
                  customdata=link_hover, hovertemplate="%{customdata}<extra></extra>"),
    ))
    fig.update_layout(template=TEMPLATE_NAME, height=height or max(460, min(900, 30 * int(per_col) + 120)),
                      margin=dict(t=8, r=8, b=8, l=8), font=dict(size=12, color=COLOR["ink_1"]))
    return fig
