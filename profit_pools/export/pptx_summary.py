"""PPTX summary built from native PowerPoint objects (everything stays editable): KPI boxes, Marimekkos made of
shapes, native line / bar / bubble charts and tables. No image rendering, so no Kaleido or Chrome dependency.

Clean-deck rules (DESIGN_SYSTEM.md › PPTX): one idea per slide; a generated headline (the so-what) as the
title; a source line on every slide; Marimekko labels only where they fit, tiny segments folded into "Other",
column names and widths above. An optional user template (.pptx) supplies the master — fonts, colors, title
and footer positions. It is read from memory only and never stored.

The module is optional: `available()` is False when python-pptx is not installed and the UI hides it.
"""

from __future__ import annotations

import io

import numpy as np
import pandas as pd

from profit_pools.charts import trends
from profit_pools.charts.marimekko import tile_fill
from profit_pools.core.model import flows_view as FV
from profit_pools.core.model import growth as G
from profit_pools.core.model import views as V
from profit_pools.core.model.scenario_engine import summarize
from profit_pools.design import format as fmt
from profit_pools.design.tokens import (COLOR, LAYER_COLOR, MARGIN_MAX, MARGIN_MIN, OTHER, margin_color,
                                        text_on)
from profit_pools.export import FICTIONAL, ExportContext
from profit_pools.export import figures as F
from profit_pools.export import headlines as HL

try:
    from pptx import Presentation
    from pptx.chart.data import BubbleChartData, CategoryChartData
    from pptx.dml.color import RGBColor
    from pptx.enum.chart import (XL_CHART_TYPE, XL_LABEL_POSITION, XL_LEGEND_POSITION, XL_MARKER_STYLE,
                                XL_TICK_LABEL_POSITION)
    from pptx.enum.dml import MSO_PATTERN
    from pptx.enum.shapes import MSO_SHAPE, PP_PLACEHOLDER
    from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
    from pptx.util import Emu, Inches, Pt
    _OK = True
except ImportError:  # pragma: no cover - optional dependency
    _OK = False

W0, H0 = 13.333, 7.5          # reference canvas (16:9); every position below is in these inches
OTHER_SHARE = 0.03            # Marimekko segments below 3% of their column fold into "Other"


def available() -> bool:
    return _OK


def _rgb(hex_color: str):
    h = hex_color.lstrip("#")
    return RGBColor(int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))


class Deck:
    """Thin layout helper: positions are given on the 13.33 × 7.5 in reference canvas and scaled to the
    presentation's real size, so a 4:3 or custom-size template still lays out cleanly."""

    def __init__(self, ctx: ExportContext, template: bytes | None = None):
        self.ctx = ctx
        self.templated = template is not None
        self.prs = Presentation(io.BytesIO(template)) if template else Presentation()
        if template:
            _drop_slides(self.prs)
        else:
            self.prs.slide_width, self.prs.slide_height = Emu(int(Inches(W0))), Emu(int(Inches(H0)))
        self.sx = self.prs.slide_width / Inches(W0)
        self.sy = self.prs.slide_height / Inches(H0)
        self.font = None if template else "Calibri"     # a template's theme fonts win
        self.layout = _title_only_layout(self.prs)

    # geometry
    def X(self, v):  # noqa: N802 - short helpers keep layout code readable
        return Emu(int(Inches(v) * self.sx))

    def Y(self, v):  # noqa: N802
        return Emu(int(Inches(v) * self.sy))

    def run(self, run, size, bold=False, color=None):
        run.font.size, run.font.bold = Pt(size), bold
        if self.font:
            run.font.name = self.font
        run.font.color.rgb = _rgb(color or COLOR["ink_1"])

    def text(self, slide, x, y, w, h, text, size=12, bold=False, color=None, align=None, anchor=None):
        tb = slide.shapes.add_textbox(self.X(x), self.Y(y), self.X(w), self.Y(h))
        tf = tb.text_frame
        tf.word_wrap = True
        tf.margin_left = tf.margin_right = Inches(0.02)
        tf.margin_top = tf.margin_bottom = Inches(0.01)
        if anchor:
            tf.vertical_anchor = anchor
        for i, line in enumerate(text if isinstance(text, list) else [text]):
            p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
            if align:
                p.alignment = align
            self.run(p.add_run(), size, bold, color)
            p.runs[0].text = line
        return tb

    def slide(self, title: str, subtitle: str | None = None):
        s = self.prs.slides.add_slide(self.layout)
        ph = next((p for p in s.placeholders if p.placeholder_format.type in (PP_PLACEHOLDER.TITLE,
                                                                               PP_PLACEHOLDER.CENTER_TITLE)), None)
        for p in list(s.placeholders):
            if ph is None or p._element is not ph._element:
                p._element.getparent().remove(p._element)          # empty body placeholders would show prompts
        if ph is not None and self.templated:
            # Keep the template's title font and color, but place it in the title band and size it to fit.
            ph.left, ph.top, ph.width, ph.height = self.X(0.5), self.Y(0.26), self.X(W0 - 1), self.Y(0.68)
            ph.text = title
            size = _fit_size(title, W0 - 1, 0.68)
            for r in ph.text_frame.paragraphs[0].runs:
                r.font.size = Pt(size)
        else:
            if ph is not None:
                ph._element.getparent().remove(ph._element)
            self.text(s, 0.5, 0.32, W0 - 1, 0.62, title, 20, True)
        self.text(s, 0.5, 0.95, W0 - 1, 0.3, subtitle or self.ctx.title(), 11, color=COLOR["ink_3"])
        ln = s.shapes.add_connector(1, self.X(0.5), self.Y(1.28), self.X(W0 - 0.5), self.Y(1.28))
        ln.line.color.rgb = _rgb(COLOR["line"])
        ln.line.width = Pt(0.75)
        self.text(s, 0.5, H0 - 0.38, W0 - 1, 0.28,
                  f"Source: Profit Pools Explorer · {self.ctx.meta.datasets[self.ctx.dataset].label} · {FICTIONAL}",
                  8, color=COLOR["ink_3"])
        return s

    def table(self, slide, x, y, w, head, rows, col_w=None, size=10, numeric_from=1):
        shape = slide.shapes.add_table(len(rows) + 1, len(head), self.X(x), self.Y(y), self.X(w),
                                       self.Y(0.3 * (len(rows) + 1)))
        t = shape.table
        tbl_pr = shape._element.graphic.graphicData.tbl.tblPr
        tbl_pr.set("bandRow", "0")
        tbl_pr.set("firstRow", "0")
        if col_w:
            for i, cw in enumerate(col_w):
                t.columns[i].width = self.X(cw)
        for j, h in enumerate(head):
            self._cell(t.cell(0, j), h, size - 1, True, COLOR["ink_3"], j >= numeric_from)
        for i, r in enumerate(rows, start=1):
            bold = str(r[0]).startswith("=")
            for j, v in enumerate(r):
                self._cell(t.cell(i, j), v, size, bold, COLOR["ink_1"], j >= numeric_from)
        return t

    def _cell(self, cell, text, size, bold, color, right):
        cell.fill.background()
        cell.margin_left = cell.margin_right = Inches(0.05)
        cell.margin_top = cell.margin_bottom = Inches(0.03)
        p = cell.text_frame.paragraphs[0]
        p.alignment = PP_ALIGN.RIGHT if right else PP_ALIGN.LEFT
        r = p.add_run()
        r.text = str(text)
        self.run(r, size, bold, color)

    def save(self) -> bytes:
        buf = io.BytesIO()
        self.prs.save(buf)
        return buf.getvalue()


def _fit_size(text: str, width_in: float, height_in: float, sizes=(24, 22, 20, 18, 16)) -> int:
    """Largest size whose estimated wrapped height fits (average glyph ≈ 0.5 em; line height 1.15 em)."""
    for pt in sizes:
        per_line = max(int(width_in * 72 / (pt * 0.5)), 1)
        lines = -(-len(text) // per_line)
        if lines * pt * 1.15 / 72 <= height_in:
            return pt
    return sizes[-1]


def check_template(template: bytes) -> str | None:
    """None if the file can be used as a template, else a short reason."""
    try:
        prs = Presentation(io.BytesIO(template))
    except Exception:  # noqa: BLE001 - any parse failure means "not a usable .pptx"
        return "not a readable .pptx file."
    if not len(prs.slide_layouts):
        return "it has no slide layouts."
    return None


def _drop_slides(prs) -> None:
    """A template may ship sample slides; keep only its masters and layouts."""
    ids = prs.slides._sldIdLst
    for sld in list(ids):
        prs.part.drop_rel(sld.rId)
        ids.remove(sld)


def _title_only_layout(prs):
    layouts = list(prs.slide_layouts)
    for lay in layouts:
        if "title only" in lay.name.lower():
            return lay
    with_title = [lay for lay in layouts if any(p.placeholder_format.type == PP_PLACEHOLDER.TITLE
                                                for p in lay.placeholders)]
    if with_title:
        return min(with_title, key=lambda lay: len(lay.placeholders))
    return layouts[6] if len(layouts) > 6 else layouts[-1]


# ---------------------------------------------------------------------------------------------------------
# Slides
# ---------------------------------------------------------------------------------------------------------

def _kpis(d: Deck, s, y: float) -> None:
    items = F.kpis(d.ctx)
    if not items:
        return
    cw = (W0 - 1) / len(items)
    for i, (label, value, delta) in enumerate(items):
        x = 0.5 + i * cw
        d.text(s, x, y, cw - 0.15, 0.25, label.upper(), 9, color=COLOR["ink_3"])
        d.text(s, x, y + 0.24, cw - 0.15, 0.45, value, 24, True)
        d.text(s, x, y + 0.74, cw - 0.15, 0.3, delta, 9, color=COLOR["ink_2"])


def _fold(rows: pd.DataFrame) -> pd.DataFrame:
    """Fold segments below OTHER_SHARE of their column into one 'Other' tile (sums only)."""
    out = []
    for col, g in rows.groupby("col_id", sort=False):
        small = g["share_in_col"] < OTHER_SHARE
        if small.sum() >= 2:
            o = g[small]
            other = o.iloc[0].copy()
            for c in ("revenue", "gain", "revenue_with_margin", "share_in_col"):
                other[c] = o[c].sum()
            other["margin"] = other["gain"] / other["revenue_with_margin"] if other["revenue_with_margin"] > 0 else np.nan
            for c in ("d_revenue", "d_gain"):
                if c in o:
                    other[c] = o[c].sum()
            other["seg_label"], other["seg_key"], other["seg_node_id"] = "Other", "_other", None
            g = pd.concat([g[~small], other.to_frame().T], ignore_index=True)
        out.append(g)
    return pd.concat(out, ignore_index=True)


def _marimekko(d: Deck, s, rows: pd.DataFrame, labels: dict, est: set, x: float, y: float, w: float, h: float,
               impact: bool = False, title: str | None = None, label_min_w: float = 0.75) -> None:
    if rows.empty:
        d.text(s, x, y, w, 0.4, "No pools to show for this view.", 12, color=COLOR["ink_3"])
        return
    if title:
        d.text(s, x, y - 0.62, w, 0.28, title, 12, True)
    rows = _fold(rows)
    scale = float(rows["d_gain"].abs().max()) if impact and "d_gain" in rows else 0.0
    cols = rows.drop_duplicates("col_id")[["col_id", "col_share", "col_revenue"]]
    gap, x0 = 0.03, x
    for c in cols.itertuples():
        cw = c.col_share * w
        if cw >= 0.5:
            head = labels[c.col_id]
            d.text(s, x0, y - 0.34, cw, 0.32, [head, f"{fmt.pct(c.col_share, 0)} · {fmt.money(c.col_revenue)}"]
                   if cw >= 1.2 else head, 9 if cw >= 0.8 else 7, True, align=PP_ALIGN.CENTER,
                   anchor=MSO_ANCHOR.BOTTOM)
        top = y
        for r in rows[rows["col_id"] == c.col_id].itertuples():
            th = float(r.share_in_col) * h
            fill, affected = tile_fill(r, "impact" if impact else "margin", "d_gain", scale, V.IMPACT_MIN)
            shp = s.shapes.add_shape(MSO_SHAPE.RECTANGLE, d.X(x0), d.Y(top), d.X(max(cw - gap, 0.01)),
                                     d.Y(max(th - gap, 0.01)))
            shp.line.fill.background()
            shp.shadow.inherit = False
            if not impact and r.revenue_with_margin <= 0:          # margin unavailable → hatch
                shp.fill.patterned()
                shp.fill.pattern = MSO_PATTERN.WIDE_UPWARD_DIAGONAL
                shp.fill.fore_color.rgb = _rgb(COLOR["missing_hatch"])
                shp.fill.back_color.rgb = _rgb(COLOR["missing"])
            else:
                shp.fill.solid()
                shp.fill.fore_color.rgb = _rgb(fill)
            if cw >= label_min_w and th >= 0.3:
                tf = shp.text_frame
                tf.margin_left = tf.margin_right = Inches(0.04)
                tf.margin_top = tf.margin_bottom = Inches(0.02)
                tf.vertical_anchor = MSO_ANCHOR.MIDDLE
                tf.word_wrap = True
                lab = r.seg_label + (" (est.)" if c.col_id in est else "")
                val = ((fmt.money(r.d_gain, signed=True) if affected else "no change") if impact
                       else fmt.money(r.revenue))
                ink = text_on(fill) if not impact or affected else COLOR["ink_3"]
                for k, line in enumerate([lab] if th < 0.55 else [lab, val]):
                    p = tf.paragraphs[0] if k == 0 else tf.add_paragraph()
                    p.alignment = PP_ALIGN.CENTER
                    run = p.add_run()
                    run.text = line
                    d.run(run, 9 if k == 0 else 8, k == 0, ink)
            top += th
        x0 += cw


def _legend(d: Deck, s, x: float, y: float, impact: bool) -> None:
    if impact:
        d.text(s, x, y, 9, 0.25, "Color = change in operating gain vs baseline · teal up · red down · grey = not "
                                 "affected", 8, color=COLOR["ink_3"])
        return
    d.text(s, x, y, 1.3, 0.25, "Operating margin", 8, color=COLOR["ink_3"])
    stops = [MARGIN_MIN, -0.04, 0.0, 0.06, 0.12, MARGIN_MAX]
    for i, m in enumerate(stops):
        b = s.shapes.add_shape(MSO_SHAPE.RECTANGLE, d.X(x + 1.3 + i * 0.55), d.Y(y + 0.05), d.X(0.5), d.Y(0.12))
        b.fill.solid()
        b.fill.fore_color.rgb = _rgb(margin_color(m))
        b.line.fill.background()
        d.text(s, x + 1.3 + i * 0.55, y + 0.17, 0.55, 0.2, f"{m * 100:+.0f}%".replace("+0", "0"), 7,
               color=COLOR["ink_3"])
    d.text(s, x + 4.8, y, 7, 0.25, "Width = share of revenue · height = share within column · hatched = margin "
                                   "unavailable · (est.) = top-down estimate · small segments grouped as Other",
           8, color=COLOR["ink_3"])


def _line_chart(d: Deck, s, x, y, w, h) -> None:
    df = F.trend_frame(d.ctx)
    if df.empty:
        return
    years = sorted(df["year"].unique())
    cd = CategoryChartData()
    cd.categories = [str(int(yr)) for yr in years]
    ids = list(dict.fromkeys(df["node_id"]))
    for nid in ids:
        v = df[df["node_id"] == nid].set_index("year")["revenue"].reindex(years) / 1e9
        cd.add_series(d.ctx.meta.taxonomy[nid].short_label, [None if not np.isfinite(t) else round(float(t), 2) for t in v])
    ch = s.shapes.add_chart(XL_CHART_TYPE.LINE, d.X(x), d.Y(y), d.X(w), d.Y(h), cd).chart
    ch.has_title = False
    ch.has_legend = True
    ch.legend.position = XL_LEGEND_POSITION.TOP
    ch.legend.include_in_layout = False
    ch.legend.font.size = Pt(10)
    va = ch.value_axis
    va.has_major_gridlines = True
    va.major_gridlines.format.line.color.rgb = _rgb(COLOR["line"])
    va.tick_labels.number_format, va.tick_labels.number_format_is_linked = '"$"#,##0"B"', False
    va.tick_labels.font.size = Pt(9)
    va.format.line.fill.background()
    ch.category_axis.tick_labels.font.size = Pt(9)
    colors = trends.series_colors(ids, LAYER_COLOR if d.ctx.focus is None else {})
    for i, nid in enumerate(ids):
        ser = ch.series[i]
        ser.format.line.width = Pt(2.25)
        ser.format.line.color.rgb = _rgb(colors.get(nid, OTHER))
        ser.smooth = False
        ser.marker.style = XL_MARKER_STYLE.NONE


def _bar_chart(d: Deck, s, labels, values, x, y, w, h, fmt_code='"$"#,##0.0"B"') -> None:
    cd = CategoryChartData()
    cd.categories = labels
    cd.add_series("Change", values)
    ch = s.shapes.add_chart(XL_CHART_TYPE.BAR_CLUSTERED, d.X(x), d.Y(y), d.X(w), d.Y(h), cd).chart
    ch.has_legend = False
    ch.has_title = False
    plot = ch.plots[0]
    plot.gap_width = 55
    plot.has_data_labels = True
    plot.data_labels.number_format, plot.data_labels.number_format_is_linked = fmt_code, False
    plot.data_labels.font.size = Pt(9)
    plot.data_labels.position = XL_LABEL_POSITION.OUTSIDE_END
    ch.category_axis.reverse_order = True
    ch.category_axis.tick_label_position = XL_TICK_LABEL_POSITION.LOW     # names stay left of negative bars
    ch.category_axis.tick_labels.font.size = Pt(10)
    ch.category_axis.format.line.color.rgb = _rgb(COLOR["line_strong"])
    ch.value_axis.visible = False
    ch.value_axis.has_major_gridlines = False
    for i, v in enumerate(values):
        pt = ch.series[0].points[i]
        pt.format.fill.solid()
        pt.format.fill.fore_color.rgb = _rgb(COLOR["accent"] if v >= 0 else COLOR["bad"])


def _bubble_chart(d: Deck, s, df: pd.DataFrame, x, y, w, h) -> None:
    df = df[df["cagr"].notna() & df["margin"].notna()]
    cd = BubbleChartData()
    colors = trends.series_colors(list(df["node_id"]), LAYER_COLOR if d.ctx.focus is None else {})
    for r in df.itertuples():
        ser = cd.add_series(r.label)
        ser.add_data_point(round(float(r.cagr) * 100, 2), round(float(r.margin) * 100, 2), max(abs(float(r.gain)), 1.0))
    ch = s.shapes.add_chart(XL_CHART_TYPE.BUBBLE, d.X(x), d.Y(y), d.X(w), d.Y(h), cd).chart
    ch.has_title = False
    ch.has_legend = False
    plot = ch.plots[0]
    plot.bubble_scale = 60
    for i, r in enumerate(df.itertuples()):
        ser = ch.series[i]
        ser.format.fill.solid()
        ser.format.fill.fore_color.rgb = _rgb(COLOR["surface"] if r.gain < 0 else colors.get(r.node_id, OTHER))
        ser.format.line.color.rgb = _rgb(COLOR["bad"] if r.gain < 0 else COLOR["surface"])
        pt = ser.points[0]
        pt.data_label.has_text_frame = True
        pt.data_label.text_frame.text = r.label
        pt.data_label.position = XL_LABEL_POSITION.ABOVE
        pt.data_label.font.size = Pt(10)
    for ax, title in ((ch.value_axis, "Operating margin (%)"), (ch.category_axis, "Revenue growth, CAGR (%)")):
        ax.has_title = True
        ax.axis_title.text_frame.text = title
        ax.axis_title.text_frame.paragraphs[0].runs[0].font.size = Pt(10)
        ax.tick_labels.font.size = Pt(9)
        ax.tick_labels.number_format, ax.tick_labels.number_format_is_linked = '0"%"', False
        ax.has_major_gridlines = False


def build(ctx: ExportContext, template: bytes | None = None) -> bytes:
    if not _OK:
        raise RuntimeError("python-pptx is not installed")
    d = Deck(ctx, template)
    m, p = ctx.meta, ctx.meta.periods
    b, e = p.base_year, p.forecast_end
    impact = ctx.view == "impact"
    rows, labels, est = F.tiles(ctx)
    titles: list[str] = []

    # 1 — title
    s = d.prs.slides.add_slide(d.layout)
    for ph in list(s.placeholders):
        ph._element.getparent().remove(ph._element)
    d.text(s, 0.8, 2.5, W0 - 1.6, 0.8, "Profit Pools Explorer", 36, True)
    d.text(s, 0.8, 3.4, W0 - 1.6, 0.5, ctx.title(), 16, color=COLOR["ink_2"])
    d.text(s, 0.8, 4.2, W0 - 1.6, 0.4, FICTIONAL, 11, color=COLOR["ink_3"])
    bar = s.shapes.add_shape(MSO_SHAPE.RECTANGLE, d.X(0.8), d.Y(2.3), d.X(0.9), d.Y(0.06))
    bar.fill.solid()
    bar.fill.fore_color.rgb = _rgb(COLOR["accent"])
    bar.line.fill.background()

    # 2 — executive summary (filled once the other headlines exist)
    summary = d.slide("Executive summary")

    # 3 — Marimekko
    t = HL.mekko(ctx)
    titles.append(t)
    s = d.slide(t, f"{ctx.scope_label()} · {ctx.year} · {ctx.geo_label()}"
                   + (" · change vs baseline" if impact else ""))
    _marimekko(d, s, rows, labels, est, 0.5, 2.0, W0 - 1, 4.55, impact=impact)
    _legend(d, s, 0.5, 6.65, impact)

    # 4 — where profit moves (two Marimekkos, same scope)
    if not impact and ctx.year != e:
        y0, y1 = min(b, ctx.year), e
        t = HL.shift(ctx, y0, y1)
        titles.append(t)
        s = d.slide(t, f"{ctx.scope_label()} · operating margin by pool · {y0} vs {y1}")
        for i, yr in enumerate((y0, y1)):
            r_ = V.marimekko_tiles(ctx.result, m, ctx.dataset, yr, ctx.focus, ctx.geo, ctx.basis, ctx.segment_by).rows
            _marimekko(d, s, r_, {c: m.taxonomy[c].short_label for c in r_["col_id"].unique()}, est,
                       0.5 + i * 6.3, 2.2, 5.9, 4.2, title=str(yr), label_min_w=0.9)
        _legend(d, s, 0.5, 6.65, False)

    # 5 — where to play
    att = G.attractiveness(ctx.result, m, ctx.dataset, ctx.focus, ctx.geo, ctx.basis, b, e)
    if not att.empty and att["margin"].notna().any():
        t = HL.attractiveness(ctx, b, e)
        titles.append(t)
        s = d.slide(t, f"{ctx.scope_label()} · revenue CAGR {b}–{e} vs operating margin {e} · bubble = operating gain")
        _bubble_chart(d, s, att, 0.5, 1.45, W0 - 1, 5.25)
        d.text(s, 0.5, 6.72, W0 - 1, 0.25, f"Averages (revenue-weighted): {fmt.pct(att.attrs['avg_growth'], 1)} growth, "
                                           f"{fmt.pct(att.attrs['avg_margin'])} margin. Hollow red bubbles are losses.",
               8, color=COLOR["ink_3"])

    # 6 — revenue trend
    t = HL.trend(ctx, b, e)
    titles.append(t)
    s = d.slide(t, f"{ctx.scope_label()} · revenue, $B · forecast from {b + 1}")
    _line_chart(d, s, 0.5, 1.45, W0 - 1, 5.3)

    # 7 — what drives operating gain
    br = F.growth_bridge(ctx)
    dl = br[br["kind"] == "delta"]
    t = HL.bridge(br)
    titles.append(t)
    s = d.slide(t, f"{ctx.scope_label()} · operating gain {fmt.money(br['value'].iloc[0])} ({b}) → "
                   f"{fmt.money(br['value'].iloc[-1])} ({e})")
    if len(dl):
        _bar_chart(d, s, list(dl["label"]), [round(float(v) / 1e9, 2) for v in dl["value"]], 0.5, 1.45, W0 - 1, 5.2)
    d.text(s, 0.5, 6.72, W0 - 1, 0.25, "Sequential split at the finest grain; the steps sum exactly to the change.",
           8, color=COLOR["ink_3"])

    # 8 — largest value flows
    view = FV.build(ctx.result, m, ctx.dataset, ctx.year, ctx.geo, FV.SankeyConfig())
    if len(view.links):
        nl = view.nodes.set_index("node_id")["label"]
        top = view.links.sort_values("value", ascending=False).head(12)
        t = HL.flows(view)
        titles.append(t)
        s = d.slide(t, f"Allowed spending, {ctx.year} · {ctx.geo_label()} · total {fmt.money(view.total)}")
        d.table(s, 0.5, 1.5, W0 - 1, ["From", "To", "Allowed spend", "% of total", "% of source"],
                [[nl[r.source], nl[r.target], fmt.money(r.value), fmt.pct(r.share_of_total), fmt.pct(r.share_of_source)]
                 for r in top.itertuples()], col_w=[3.6, 3.6, 1.9, 1.6, 1.63], numeric_from=2)

    # 9 — scenario
    if ctx.has_scenario:
        t = HL.scenario(ctx)
        titles.append(t)
        s = d.slide(t, ctx.scenario.description or ctx.title())
        rows_ = [[f"{i}. {a.label}" if a.label else f"{i}.", summarize(m, a)]
                 for i, a in enumerate(ctx.scenario.enabled, start=1)]
        d.table(s, 0.5, 1.5, W0 - 1, ["Assumption", "What changes"], rows_, col_w=[3.4, W0 - 4.4], numeric_from=2)

    # 10 — value capture
    if ctx.dataset == "ENTERPRISE":
        v = V.ownership_view(ctx.result, m, ctx.year, ctx.geo)
        if not v.note:
            tt = v.totals
            t = HL.value_capture(tt)
            titles.append(t)
            s = d.slide(t, f"{ctx.year} · {ctx.geo_label()} · intra-organization claims eliminated")
            d.table(s, 0.5, 1.5, 5.4, ["", "Amount"], [
                ["Plan premium revenue", fmt.money(tt["payer_revenue"])],
                ["+ Owned-asset revenue", fmt.money(tt["owned_revenue"])],
                ["− Claims paid to own assets", fmt.money(-tt["elimination"])],
                ["= Consolidated revenue", fmt.money(tt["consolidated_revenue"])],
                ["Plan operating gain", fmt.money(tt["payer_gain"])],
                ["+ Owned-asset gain", fmt.money(tt["owned_gain"])],
                ["= Consolidated gain", fmt.money(tt["consolidated_gain"])]], col_w=[3.8, 1.6])
            d.table(s, 6.3, 1.5, W0 - 6.8, ["Owned asset", "Routed", "Owned revenue", "Margin", "Owned gain"],
                    [[r.label, fmt.pct(r.routing_share), fmt.money(r.owned_revenue), fmt.pct(r.owned_margin),
                      fmt.money(r.owned_gain)] for r in v.assets.itertuples()], col_w=[2.5, 0.9, 1.25, 0.85, 1.03])

    # 11 — notes
    s = d.slide("Notes and definitions", "How to read these numbers")
    d.text(s, 0.5, 1.5, W0 - 1, 5.0, [
        "Operating gain is additive across sectors; gross sector revenue is not (a plan's claims are a provider's "
        "revenue).",
        "Provider and pharmacy revenue = explicit inflows (plan claims, uninsured, government FFS, supplemental); "
        "gain = revenue × pool margin.",
        "Plan gain = premium − paid claims − admin cost. Allowed = everything received; paid = plan's share; "
        "consumer = allowed − paid.",
        "Top-down pools (manufacturers, health services & technology, PBM services) are national estimates without "
        "plan, geography or flow detail.",
        "Unavailable values are shown as hatched or '—', never as zero.",
        FICTIONAL], 12, color=COLOR["ink_2"])

    # executive summary: KPIs + the headlines above
    _kpis(d, summary, 1.5)
    d.text(summary, 0.5, 2.85, W0 - 1, 3.8, [f"•  {x}" for x in titles], 14, color=COLOR["ink_1"])
    return d.save()
