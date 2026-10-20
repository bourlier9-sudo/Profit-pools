"""Growth charts: revenue-growth bridge (waterfall) and year-over-year heatmap. Layout only."""

from __future__ import annotations

import numpy as np
import pandas as pd
import plotly.graph_objects as go

from profit_pools.design import format as fmt
from profit_pools.design.theme import TEMPLATE_NAME
from profit_pools.design.tokens import COLOR, MARGIN_MAX, MARGIN_MIN, MARGIN_STOPS


def bridge(df: pd.DataFrame) -> go.Figure:
    """`df` from growth.revenue_bridge: start total, signed components, end total. Horizontal, so the
    component names stay readable; the value axis starts near the smaller total (labelled), not at 0."""
    measure = ["absolute" if k == "total" else "relative" for k in df["kind"]]
    measure[-1] = "total"
    text = [fmt.money(v) if k == "total" else fmt.money(v, signed=True) for v, k in zip(df["value"], df["kind"])]
    fig = go.Figure(go.Waterfall(
        orientation="h", y=df["label"], x=df["value"] / 1e9, measure=measure, text=text,
        textposition="outside", cliponaxis=False,
        connector=dict(line=dict(color=COLOR["line_strong"], width=1)),
        increasing=dict(marker=dict(color=COLOR["accent"])), decreasing=dict(marker=dict(color=COLOR["bad"])),
        totals=dict(marker=dict(color=COLOR["ink_3"])),
        hovertext=text, hovertemplate="%{y}: %{hovertext}<extra></extra>"))
    running = df["value"].iloc[0] + np.cumsum(np.where(df["kind"] == "total", 0.0, df["value"]))
    lo = min(float(running.min()), float(df["value"].iloc[0]), float(df["value"].iloc[-1])) / 1e9
    hi = max(float(running.max()), float(df["value"].iloc[-1]), float(df["value"].iloc[0])) / 1e9
    pad = (hi - lo) or abs(hi) or 1.0
    fig.update_layout(template=TEMPLATE_NAME, height=max(220, 34 * len(df) + 60), showlegend=False,
                      margin=dict(t=8, r=64, b=28, l=8),
                      xaxis=dict(tickprefix="$", ticksuffix="B", tickformat=",.0f", fixedrange=True,
                                 range=[max(0.0, lo - pad * 0.5) if lo >= 0 else lo - pad * 0.2, hi + pad * 0.18]),
                      yaxis=dict(autorange="reversed", fixedrange=True, automargin=True))
    return fig


def yoy_heatmap(m: pd.DataFrame, base_year: int) -> go.Figure:
    """Rows = pools, columns = years; fill = growth on the shared diverging scale (0 = neutral)."""
    span = MARGIN_MAX - MARGIN_MIN
    scale = [[(x - MARGIN_MIN) / span, c] for x, c in MARGIN_STOPS]
    z = m.to_numpy(dtype=float)
    years = [int(y) for y in m.columns]
    text = [[fmt.pct(v, 1, signed=True) if np.isfinite(v) else fmt.DASH for v in row] for row in z]
    fig = go.Figure(go.Heatmap(
        z=np.clip(z, MARGIN_MIN, MARGIN_MAX), x=years, y=list(m.index), colorscale=scale, zmin=MARGIN_MIN,
        zmax=MARGIN_MAX, showscale=False, xgap=2, ygap=2, text=text, texttemplate="%{text}",
        textfont=dict(size=10), hovertemplate="%{y} %{x}: %{text}<extra></extra>"))
    fig.update_layout(template=TEMPLATE_NAME, height=max(160, 34 * len(m) + 60),
                      margin=dict(t=8, r=8, b=28, l=8),
                      xaxis=dict(tickvals=years, ticktext=[f"{y}{' f' if y > base_year else ''}" for y in years],
                                 fixedrange=True, showgrid=False),
                      yaxis=dict(autorange="reversed", fixedrange=True, automargin=True, showgrid=False))
    return fig
