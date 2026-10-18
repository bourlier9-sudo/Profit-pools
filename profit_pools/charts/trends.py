"""Trend and composition charts. Values come from views; no financial math here."""

from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go

from profit_pools.design.theme import TEMPLATE_NAME
from profit_pools.design.tokens import CATEGORICAL, CHART, COLOR, OTHER

MAX_SERIES = len(CATEGORICAL)  # identity hues are never cycled; the tail folds into "Other"


def _forecast_band(fig: go.Figure, base_year: int, last_year: int) -> None:
    fig.add_vrect(x0=base_year + 0.5, x1=last_year + 0.5, fillcolor=COLOR["forecast_band"], line_width=0,
                  layer="below")
    fig.add_annotation(x=base_year + 0.6, y=0.99, yref="paper", yanchor="top", xanchor="left",
                       showarrow=False, text="Forecast", font=dict(size=11, color=COLOR["ink_3"]))


def series_colors(ids: list[str], fixed: dict[str, str] | None = None) -> dict[str, str]:
    """Colors follow the entity (taxonomy order), never its rank."""
    fixed = fixed or {}
    out, i = {}, 0
    for nid in ids:
        if nid in fixed:
            out[nid] = fixed[nid]
        elif i < MAX_SERIES:
            out[nid] = CATEGORICAL[i]
            i += 1
        else:
            out[nid] = OTHER
    return out


def lines(df: pd.DataFrame, value: str, *, label_col: str, id_col: str, colors: dict[str, str],
          base_year: int, yformat: str, hover_fmt, height: int | None = None,
          zero_line: bool = False) -> go.Figure:
    """One 2px line per entity; marker only at the selected year (handled by caller via `mark_year`)."""
    fig = go.Figure()
    for nid, g in df.groupby(id_col, sort=False):
        g = g.sort_values("year")
        fig.add_trace(go.Scatter(
            x=g["year"], y=g[value], mode="lines", name=g[label_col].iloc[0],
            line=dict(width=2, color=colors.get(nid, OTHER)),
            hovertext=[hover_fmt(v) for v in g[value]],
            hovertemplate="%{fullData.name}  %{x}: %{hovertext}<extra></extra>",
        ))
    years = sorted(df["year"].unique())
    if years:
        _forecast_band(fig, base_year, years[-1])
    fig.update_layout(template=TEMPLATE_NAME, height=height or CHART["trend_height"], hovermode="x unified",
                      margin=dict(t=36, r=8, b=28, l=56),
                      xaxis=dict(dtick=1, fixedrange=True), yaxis=dict(tickformat=yformat, fixedrange=True,
                                                                        zeroline=zero_line, zerolinecolor=COLOR["line_strong"]))
    return fig


def mark_year(fig: go.Figure, year: int) -> go.Figure:
    fig.add_vline(x=year, line_width=1, line_color=COLOR["ink_3"])
    return fig


def composition(df: pd.DataFrame, *, label_col: str, id_col: str, colors: dict[str, str], base_year: int,
                height: int | None = None) -> go.Figure:
    """100% stacked area of revenue shares over time."""
    fig = go.Figure()
    for nid, g in df.groupby(id_col, sort=False):
        g = g.sort_values("year")
        fig.add_trace(go.Scatter(
            x=g["year"], y=g["share"], mode="lines", stackgroup="one", name=g[label_col].iloc[0],
            line=dict(width=0.5, color="#FFFFFF"), fillcolor=colors.get(nid, OTHER),
            hovertemplate="%{fullData.name}  %{x}: %{y:.1%}<extra></extra>",
        ))
    years = sorted(df["year"].unique())
    if years:
        _forecast_band(fig, base_year, years[-1])
    fig.update_layout(template=TEMPLATE_NAME, height=height or CHART["composition_height"], hovermode="x unified",
                      margin=dict(t=36, r=8, b=28, l=48),
                      xaxis=dict(dtick=1, fixedrange=True),
                      yaxis=dict(range=[0, 1], tickformat=".0%", fixedrange=True))
    return fig
