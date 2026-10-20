"""Scenario comparison charts. Scenario identity colors come from the categorical palette in a fixed order;
the baseline is neutral. Layout only."""

from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go

from profit_pools.design import format as fmt
from profit_pools.design.theme import TEMPLATE_NAME
from profit_pools.design.tokens import CATEGORICAL, COLOR


def scenario_colors(names: list[str], baseline: str) -> dict[str, str]:
    out, i = {}, 0
    for n in names:
        if n == baseline:
            out[n] = COLOR["ink_3"]
        else:
            out[n] = CATEGORICAL[i % len(CATEGORICAL)]
            i += 1
    return out


def pool_deltas(df: pd.DataFrame, colors: dict[str, str], metric_label: str = "operating gain") -> go.Figure:
    """Grouped horizontal bars: Δ vs baseline by pool, one bar per scenario."""
    fig = go.Figure()
    labels = list(dict.fromkeys(df["label"]))
    for name, g in df.groupby("scenario", sort=False):
        g = g.set_index("label").reindex(labels)
        fig.add_trace(go.Bar(y=labels, x=g["delta"] / 1e9, name=name, orientation="h",
                             marker=dict(color=colors.get(name)),
                             hovertext=[f"{name} · {l}: {fmt.money(v, signed=True)}" for l, v in zip(labels, g["delta"])],
                             hovertemplate="%{hovertext}<extra></extra>"))
    fig.update_layout(template=TEMPLATE_NAME, barmode="group", height=max(260, 46 * len(labels) + 80),
                      margin=dict(t=32, r=16, b=36, l=8), bargap=0.25, bargroupgap=0.05,
                      xaxis=dict(tickprefix="$", ticksuffix="B", title=f"Δ {metric_label} vs baseline",
                                 zeroline=True, zerolinecolor=COLOR["ink_3"], fixedrange=True),
                      yaxis=dict(autorange="reversed", fixedrange=True, automargin=True))
    return fig


def series(df: pd.DataFrame, colors: dict[str, str], base_year: int, baseline: str) -> go.Figure:
    fig = go.Figure()
    for name, g in df.groupby("scenario", sort=False):
        fig.add_trace(go.Scatter(x=g["year"], y=g["value"] / 1e9, mode="lines", name=name,
                                 line=dict(color=colors.get(name), width=2, dash="dash" if name == baseline else "solid"),
                                 hovertext=[fmt.money(v) for v in g["value"]],
                                 hovertemplate=f"{name} %{{x}}: %{{hovertext}}<extra></extra>"))
    years = sorted(df["year"].unique())
    if years:
        fig.add_vrect(x0=base_year + 0.5, x1=years[-1] + 0.5, fillcolor=COLOR["forecast_band"], line_width=0,
                      layer="below")
    fig.update_layout(template=TEMPLATE_NAME, height=300, margin=dict(t=32, r=8, b=28, l=56), hovermode="x unified",
                      xaxis=dict(dtick=1, fixedrange=True), yaxis=dict(tickprefix="$", ticksuffix="B", fixedrange=True))
    return fig
