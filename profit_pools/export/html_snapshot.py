"""Self-contained HTML snapshot: KPIs, charts and tables for the current view. It is a snapshot,
not a simulator: no scenario can be changed in it. Plotly JS is inlined once, so it opens offline."""

from __future__ import annotations

from datetime import date
from html import escape

import plotly.io as pio

from profit_pools.core.model.scenario_engine import summarize
from profit_pools.core.model import views as V
from profit_pools.design import format as fmt
from profit_pools.design.tokens import COLOR, FONT_FAMILY
from profit_pools.export import FICTIONAL, ExportContext
from profit_pools.export import figures as F
from profit_pools.export.csv_export import analysis_frame

CSS = f"""
body {{ font-family: {FONT_FAMILY}; color: {COLOR['ink_1']}; background: {COLOR['bg']}; margin: 0; }}
main {{ max-width: 1240px; margin: 0 auto; padding: 32px 24px 64px; }}
h1 {{ font-size: 28px; font-weight: 600; margin: 0 0 4px; }}
h2 {{ font-size: 18px; font-weight: 600; margin: 40px 0 8px; padding-top: 16px; border-top: 1px solid {COLOR['line']}; }}
.sub, .note {{ color: {COLOR['ink_3']}; font-size: 12px; }}
.banner {{ border-left: 3px solid {COLOR['accent']}; background: {COLOR['accent_weak']}; padding: 8px 12px;
          font-size: 13px; margin: 16px 0; }}
.kpis {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(170px, 1fr)); border-top: 1px solid {COLOR['line']};
        border-bottom: 1px solid {COLOR['line']}; margin: 16px 0; }}
.kpi {{ padding: 12px 16px; border-right: 1px solid {COLOR['line']}; }}
.kpi .l {{ font-size: 11px; letter-spacing: .04em; text-transform: uppercase; color: {COLOR['ink_3']}; }}
.kpi .v {{ font-size: 24px; font-weight: 600; font-variant-numeric: tabular-nums; }}
.kpi .d {{ font-size: 12px; color: {COLOR['ink_2']}; }}
table {{ border-collapse: collapse; width: 100%; font-size: 13px; font-variant-numeric: tabular-nums; }}
th {{ text-align: left; font-weight: 500; color: {COLOR['ink_3']}; font-size: 11px; text-transform: uppercase;
     letter-spacing: .03em; border-bottom: 1px solid {COLOR['line_strong']}; padding: 6px 8px; }}
td {{ border-bottom: 1px solid {COLOR['line']}; padding: 6px 8px; }}
td.n {{ text-align: right; }}
ol li {{ margin: 4px 0; font-size: 13px; }}
"""


def _table(head: list[str], rows: list[list[str]], numeric_from: int = 1) -> str:
    th = "".join(f"<th>{escape(h)}</th>" for h in head)
    body = "".join("<tr>" + "".join(f'<td class="{"n" if i >= numeric_from else ""}">{escape(c)}</td>'
                                    for i, c in enumerate(r)) + "</tr>" for r in rows)
    return f"<table><thead><tr>{th}</tr></thead><tbody>{body}</tbody></table>"


def build(ctx: ExportContext) -> str:
    F.ensure_template()
    parts: list[str] = []
    first = [True]

    def fig_html(fig) -> str:
        if fig is None:
            return '<p class="note">Not available for this view.</p>'
        html = pio.to_html(fig, full_html=False, include_plotlyjs="inline" if first[0] else False,
                           config={"displayModeBar": False, "responsive": True})
        first[0] = False
        return html

    parts.append(f"<h1>Profit Pools Explorer</h1><div class='sub'>{escape(ctx.title())}</div>")
    parts.append(f"<div class='banner'>Snapshot exported {date.today().isoformat()}. It is a static record of the "
                 f"view, not a simulator. {escape(FICTIONAL)}</div>")
    k = F.kpis(ctx)
    if k:
        parts.append("<div class='kpis'>" + "".join(
            f"<div class='kpi'><div class='l'>{escape(l)}</div><div class='v'>{escape(v)}</div>"
            f"<div class='d'>{escape(d)}</div></div>" for l, v, d in k) + "</div>")
    parts.append("<h2>Profit pools</h2><p class='note'>Column width = share of revenue; tile height = share "
                 "within the column; color = operating margin (red below 0%, teal above)"
                 + (", or the change versus baseline in the impact view" if ctx.view == "impact" else "")
                 + ". Hatched tiles have no margin data.</p>")
    parts.append(fig_html(F.marimekko_fig(ctx)))
    parts.append("<h2>Revenue trend</h2>" + fig_html(F.trend_fig(ctx)))
    parts.append("<h2>Value flows</h2><p class='note'>Allowed spending from payers and other funders to "
                 "providers and pharmacy. Top-down pools have no flows.</p>" + fig_html(F.sankey_fig(ctx)))
    p = ctx.meta.periods
    parts.append(f"<h2>What drives revenue growth, {p.base_year}–{p.forecast_end}</h2>" + fig_html(F.growth_fig(ctx)))
    parts.append("<h2>Year-over-year growth</h2>" + fig_html(F.yoy_fig(ctx)))

    a = analysis_frame(ctx)
    head = ["Pool", "Revenue", "Share", "Operating gain", "Margin", "Enrollment"]
    has_d = "delta_revenue_usd" in a
    if has_d:
        head += ["Δ revenue", "Δ gain"]
    rows = []
    for r in a.itertuples():
        row = [r.pool, fmt.money(r.revenue_usd), fmt.pct(r.share_of_parent), fmt.money(r.operating_gain_usd),
               fmt.pct(r.operating_margin), fmt.members(r.enrollment)]
        if has_d:
            row += [fmt.money(r.delta_revenue_usd, signed=True), fmt.money(r.delta_operating_gain_usd, signed=True)]
        rows.append(row)
    parts.append(f"<h2>Summary table · {escape(ctx.scope_label())}</h2>" + _table(head, rows))

    if ctx.dataset == "ENTERPRISE":
        v = V.ownership_view(ctx.result, ctx.meta, ctx.year, ctx.geo)
        if not v.note:
            t = v.totals
            parts.append("<h2>Value capture</h2>" + _table(["", "Amount"], [
                ["Plan premium revenue", fmt.money(t["payer_revenue"])],
                ["+ Owned-asset revenue", fmt.money(t["owned_revenue"])],
                ["− Claims paid to own assets (eliminated)", fmt.money(-t["elimination"])],
                ["= Consolidated revenue", fmt.money(t["consolidated_revenue"])],
                ["Plan operating gain", fmt.money(t["payer_gain"])],
                ["+ Owned-asset gain", fmt.money(t["owned_gain"])],
                ["= Consolidated gain", fmt.money(t["consolidated_gain"])]]))
    if ctx.has_scenario:
        items = "".join(f"<li>{escape(x.label + ' — ' if x.label else '')}{escape(summarize(ctx.meta, x))}"
                        f"{' (stack)' if x.mode == 'stack' else ''}</li>" for x in ctx.scenario.enabled)
        parts.append(f"<h2>Scenario: {escape(ctx.scenario.name)}</h2>"
                     + (f"<p class='note'>{escape(ctx.scenario.description)}</p>" if ctx.scenario.description else "")
                     + f"<ol>{items}</ol>")
    parts.append(f"<p class='note' style='margin-top:40px'>{escape(FICTIONAL)} Generated by Profit Pools "
                 f"Explorer, an independently developed prototype.</p>")
    return (f"<!doctype html><html lang='en'><head><meta charset='utf-8'><meta name='viewport' "
            f"content='width=device-width, initial-scale=1'><title>Profit Pools snapshot</title><style>{CSS}</style>"
            f"</head><body><main>{''.join(parts)}</main></body></html>")


def build_bytes(ctx: ExportContext) -> bytes:
    return build(ctx).encode("utf-8")
