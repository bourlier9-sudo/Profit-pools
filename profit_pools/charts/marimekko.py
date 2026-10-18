"""Variable-width Marimekko.

Column width = the column's share of the parent's revenue; tile height = the
segment's share of its column; tile area ∝ revenue. Fill = operating margin on
the diverging scale (hatched when unavailable).

It is built as ONE Plotly bar trace with per-tile x / width / base, so
Streamlit's point selection works natively. This module only lays out values
it receives; it computes no financials.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
import plotly.graph_objects as go

from profit_pools.design import format as fmt
from profit_pools.design.theme import TEMPLATE_NAME
from profit_pools.design.tokens import CHART, COLOR, margin_color, text_on

COL_GAP = 0.0035   # horizontal gap between columns (fraction of width)
ROW_GAP = 0.004    # vertical gap between tiles


@dataclass(frozen=True)
class TileRef:
    col_id: str
    seg_key: str
    seg_node_id: str | None


def _hover(r, col_label: str, basis_label: str, est: bool, extra: str) -> str:
    lines = [f"<b>{r.seg_label}</b>" + (f"  <span style='color:{COLOR['ink_3']}'>in {col_label}</span>"
                                         if r.seg_label != col_label else "")]
    lines.append(f"{basis_label} {fmt.money(r.revenue)}  ·  {fmt.pct(r.share_in_col)} of column")
    if r.revenue_with_margin > 0:
        lines.append(f"Operating gain {fmt.money(r.gain)}  ·  margin {fmt.pct(r.margin)}")
        if r.margin_coverage < 0.999:
            lines.append(f"<i>Margin on {fmt.pct(r.margin_coverage, 0)} of revenue; remainder unavailable</i>")
    else:
        lines.append("<i>Operating margin unavailable</i>")
    if r.members == r.members and r.members > 0:  # not NaN
        lines.append(f"Enrollment {fmt.members(r.members)}  ·  revenue PMPM {fmt.pmpm(r.revenue / (r.members * 12))}")
    if est:
        lines.append("<i>Top-down estimate — no payer, geographic or flow detail</i>")
    if extra:
        lines.append(extra)
    return "<br>".join(lines)


def build(tiles: pd.DataFrame, col_labels: dict[str, str], *, selected: TileRef | None = None,
          estimate_cols: set[str] = frozenset(), basis_label: str = "Revenue",
          height: int | None = None) -> tuple[go.Figure, list[TileRef]]:
    """Return the figure and the tile reference for each bar point (in trace order)."""
    if tiles.empty:
        fig = go.Figure(layout=dict(template=TEMPLATE_NAME, height=height or CHART["marimekko_height"]))
        return fig, []

    cols = tiles.drop_duplicates("col_id")[["col_id", "col_share", "col_revenue"]].reset_index(drop=True)
    cols["x0"] = cols["col_share"].cumsum() - cols["col_share"]
    pos = cols.set_index("col_id")

    xs, ws, bases, hs, colors, patterns, texts, tcolors, hovers, refs = ([] for _ in range(10))
    for col_id, g in tiles.groupby("col_id", sort=False):
        x0 = pos.loc[col_id, "x0"]
        w = pos.loc[col_id, "col_share"]
        top = 1.0
        for r in g.itertuples(index=False):
            h = r.share_in_col
            y0 = top - h
            fill = margin_color(r.margin if r.revenue_with_margin > 0 else None)
            xs.append(x0 + w / 2)
            ws.append(max(w - COL_GAP, w * 0.6))
            bases.append(y0 + ROW_GAP / 2)
            hs.append(max(h - ROW_GAP, h * 0.5))
            colors.append(fill)
            patterns.append("/" if r.revenue_with_margin <= 0 else "")
            est = col_id in estimate_cols
            label = f"{r.seg_label}{' · est.' if est else ''}"
            texts.append(f"{label}<br>{fmt.money(r.revenue)}" if h > 0.09 else label)
            tcolors.append(text_on(fill))
            hovers.append(_hover(r, col_labels.get(col_id, col_id), basis_label, est, ""))
            refs.append(TileRef(col_id, r.seg_key, r.seg_node_id))
            top = y0

    # A selection is one tile, or a whole column when seg_key is None.
    sel_ids: list[int] = []
    if selected is not None:
        sel_ids = [i for i, ref in enumerate(refs) if ref.col_id == selected.col_id
                   and (selected.seg_key is None or ref.seg_key == selected.seg_key)]

    bar = go.Bar(
        x=xs, width=ws, base=bases, y=hs, orientation="v",
        marker=dict(color=colors, line=dict(width=0),
                    pattern=dict(shape=patterns, fgcolor=COLOR["missing_hatch"], size=6, solidity=0.25)),
        text=texts, textposition="inside", insidetextanchor="middle", textangle=0,
        textfont=dict(size=11, color=tcolors), cliponaxis=True,
        customdata=np.arange(len(refs)), hovertext=hovers, hovertemplate="%{hovertext}<extra></extra>",
        selectedpoints=sel_ids or None,
        selected=dict(marker=dict(opacity=1.0)), unselected=dict(marker=dict(opacity=0.72)),
        showlegend=False,
    )
    fig = go.Figure(bar)

    annotations = []
    for r in cols.itertuples(index=False):
        if r.col_share >= 0.045:
            annotations.append(dict(
                x=r.x0 + r.col_share / 2, y=1.0, yref="y", xref="x", yanchor="bottom", showarrow=False,
                text=(f"<b>{col_labels.get(r.col_id, r.col_id)}</b>" + (
                    f"  <span style='color:{COLOR['ink_3']}'>{fmt.pct(r.col_share, 0)}</span>"
                    if r.col_share >= 0.14 else "")),
                font=dict(size=12, color=COLOR["ink_1"]), yshift=4))
    shapes = []
    if sel_ids:
        i0 = sel_ids[0]
        shapes.append(dict(type="rect", xref="x", yref="y", line=dict(color=COLOR["ink_1"], width=2),
                           x0=xs[i0] - ws[i0] / 2, x1=xs[i0] + ws[i0] / 2,
                           y0=min(bases[i] for i in sel_ids), y1=max(bases[i] + hs[i] for i in sel_ids)))

    fig.update_layout(
        template=TEMPLATE_NAME, height=height or CHART["marimekko_height"], barmode="overlay",
        bargap=0, margin=dict(t=30, r=16, b=36, l=44), annotations=annotations, shapes=shapes,
        uniformtext=dict(minsize=9, mode="hide"), clickmode="event+select", dragmode=False,
        xaxis=dict(range=[0, 1], tickformat=".0%", tickvals=[0, .25, .5, .75, 1], fixedrange=True,
                   title=dict(text="Share of revenue (column width)", standoff=6, font=dict(color=COLOR["ink_3"]))),
        yaxis=dict(range=[0, 1.0], tickformat=".0%", tickvals=[0, .25, .5, .75, 1], fixedrange=True,
                   showgrid=False, title=dict(text="Share within column", standoff=4, font=dict(color=COLOR["ink_3"]))),
    )
    return fig, refs
