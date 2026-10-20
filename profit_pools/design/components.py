"""Small HTML component builders (rendered with st.markdown(unsafe_allow_html=True)).

Pure string builders: no Streamlit import, no data access, easy to test.
"""

from __future__ import annotations

from html import escape

from profit_pools.design.tokens import COLOR, DELTA_MARGIN_CLAMP, MARGIN_MAX, MARGIN_MIN


def page_header(title: str, subtitle: str) -> str:
    return f'<div class="pp-title">{escape(title)}</div><div class="pp-subtitle">{escape(subtitle)}</div>'


def section_header(anchor: str, title: str, caption: str = "") -> str:
    cap = f'<div class="pp-caption">{escape(caption)}</div>' if caption else ""
    return f'<div class="pp-section" id="{anchor}"><div class="pp-h2">{escape(title)}</div>{cap}</div>'


def section_nav(items: list[tuple[str, str]]) -> str:
    links = "".join(f'<a href="#{a}">{escape(t)}</a>' for a, t in items)
    return f'<div class="pp-section-nav">{links}</div>'


def delta_html(text: str, direction: int | None) -> str:
    cls = {1: "pp-up", -1: "pp-down"}.get(direction or 0, "")
    return f'<span class="{cls}">{escape(text)}</span>'


def kpi_strip(items: list[tuple[str, str, str]]) -> str:
    """items: (label, value, delta_html — already escaped/composed)."""
    cells = "".join(
        f'<div class="pp-kpi"><div class="pp-kpi-label">{escape(l)}</div>'
        f'<div class="pp-kpi-value">{escape(v)}</div><div class="pp-kpi-delta">{d}</div></div>'
        for l, v, d in items)
    return f'<div class="pp-kpis">{cells}</div>'


def badges(items: list[tuple[str, str]]) -> str:
    """items: (text, css class among observed/estimated/allocated/projected/unavailable/'')."""
    return "".join(f'<span class="pp-badge {escape(c)}">{escape(t)}</span>' for t, c in items)


def note(text: str) -> str:
    return f'<div class="pp-note">{escape(text)}</div>'


def _gradient(ticks: list[str]) -> str:
    """The diverging gradient with its neutral tick placed where the scale is actually neutral."""
    z = 100 * -MARGIN_MIN / (MARGIN_MAX - MARGIN_MIN)
    return ('<span class="pp-grad-wrap"><span class="pp-grad"></span>'
            f'<span class="pp-grad-ticks"><span>{ticks[0]}</span>'
            f'<span style="position:absolute;left:{z:.0f}%;transform:translateX(-50%)">{ticks[1]}</span>'
            f'<span>{ticks[2]}</span></span></span>')


def margin_legend(show_estimate: bool = True, delta: bool = False) -> str:
    lo, hi = MARGIN_MIN * 100, MARGIN_MAX * 100
    ticks = [f"≤{lo:.0f}%", "0%", f"≥{hi:.0f}%"]
    title = "Operating margin"
    if delta:
        c = DELTA_MARGIN_CLAMP * 100
        ticks, title = [f"≤−{c:.0f} pp", "0", f"≥+{c:.0f} pp"], "Change in margin vs baseline"
    est = ('<span><span class="pp-swatch" style="border:none;background:none;width:auto;font-size:11px;'
           'color:var(--ink-3)">est.</span>Top-down estimate</span>') if show_estimate else ""
    return (
        '<div class="pp-legend">'
        f'<span class="pp-muted">{title}</span>'
        f'{_gradient(ticks)}'
        '<span><span class="pp-swatch pp-hatch"></span>Margin unavailable</span>'
        f'{est}'
        '<span class="pp-muted">Width = share of revenue · height = share within column · area ∝ revenue</span>'
        '</div>'
    )


def impact_legend(metric_label: str, max_abs: str, sized_by_change: bool) -> str:
    size = ("Area ∝ size of the change (increases and decreases both take space)" if sized_by_change
            else "Width = share of revenue · height = share within column")
    return (
        '<div class="pp-legend">'
        f'<span class="pp-muted">Change in {metric_label.lower()} (square-root scale)</span>'
        f'{_gradient([f"−{max_abs}", "0", f"+{max_abs}"])}'
        f'<span><span class="pp-swatch" style="background:{COLOR["line"]}"></span>Not affected</span>'
        f'<span class="pp-muted">{size}</span>'
        '</div>'
    )


def kv_grid(rows: list[tuple[str, str] | None]) -> str:
    """None inserts a hairline separator."""
    out = []
    for r in rows:
        if r is None:
            out.append('<div class="sep"></div>')
        else:
            out.append(f'<div class="k">{escape(r[0])}</div><div class="v">{escape(r[1])}</div>')
    return f'<div class="pp-kv">{"".join(out)}</div>'


def table(headers: list[str], rows: list[list[str]]) -> str:
    th = "".join(f"<th>{escape(h)}</th>" for h in headers)
    trs = "".join("<tr>" + "".join(f"<td>{escape(c)}</td>" for c in r) + "</tr>" for r in rows)
    return f'<table class="pp-comp"><thead><tr>{th}</tr></thead><tbody>{trs}</tbody></table>'


def banner(text: str) -> str:
    return f'<div class="pp-banner">{escape(text)}</div>'
