"""Generated 'so-what' headlines for slides and the HTML snapshot. Deterministic sentences built only from
view outputs — the same numbers the charts show — so a headline can never disagree with its chart."""

from __future__ import annotations

import numpy as np

from profit_pools.core.model import flows_view as FV
from profit_pools.core.model import growth as G
from profit_pools.core.model import views as V
from profit_pools.design import format as fmt
from profit_pools.export import ExportContext


def _pct(x: float) -> str:
    return fmt.pct(x, 0)


def mekko(ctx: ExportContext, year: int | None = None) -> str:
    year = year or ctx.year
    r = V.rollup(ctx.result, ctx.meta, ctx.dataset, year, ctx.focus, ctx.geo, ctx.basis).rows
    if r.empty or r["gain"].fillna(0).sum() <= 0:
        return "Where revenue and operating gain sit"
    g = r["gain"].fillna(0)
    gs = g / g.sum()
    lead = r.loc[gs.idxmax()]
    rs = lead["share_of_parent"]
    verb = "more than its" if gs.max() > rs + 0.02 else ("less than its" if gs.max() < rs - 0.02 else "in line with its")
    return (f"{lead['label']} earns {_pct(gs.max())} of operating gain on {_pct(rs)} of revenue — "
            f"{verb} share in {year}")


def shift(ctx: ExportContext, y0: int, y1: int) -> str:
    a = V.rollup(ctx.result, ctx.meta, ctx.dataset, y0, ctx.focus, ctx.geo, ctx.basis).rows.set_index("node_id")
    b = V.rollup(ctx.result, ctx.meta, ctx.dataset, y1, ctx.focus, ctx.geo, ctx.basis).rows.set_index("node_id")
    ga, gb = a["gain"].fillna(0), b["gain"].fillna(0)
    if ga.sum() <= 0 or gb.sum() <= 0:
        return f"Where profit moves, {y0}–{y1}"
    d = (gb / gb.sum()).sub(ga / ga.sum(), fill_value=0)
    up = d.idxmax()
    return (f"Profit shifts toward {b.loc[up, 'label']}: {fmt.pp(d.max())} of total operating gain "
            f"from {y0} to {y1}")


def trend(ctx: ExportContext, y0: int, y1: int) -> str:
    rows = []
    for n in V.children_in_dataset(ctx.meta, ctx.dataset, ctx.focus):
        s = V.node_series(ctx.result, ctx.meta, ctx.dataset, n.id, ctx.geo, ctx.basis).set_index("year")["revenue"]
        c = V.cagr(s.get(y0), s.get(y1), y1 - y0)
        if np.isfinite(c):
            rows.append((c, n.label))
    if not rows:
        return "Revenue trend by pool"
    c, lab = max(rows)
    return f"{lab} grows fastest: {fmt.pct(c, 1)} a year from {y0} to {y1}"


def attractiveness(ctx: ExportContext, y0: int, y1: int) -> str:
    df = G.attractiveness(ctx.result, ctx.meta, ctx.dataset, ctx.focus, ctx.geo, ctx.basis, y0, y1)
    if df.empty:
        return "Where to play: growth vs margin"
    ag, am = df.attrs.get("avg_growth"), df.attrs.get("avg_margin")
    top = df[(df["cagr"] > ag) & (df["margin"] > am)]
    if top.empty:
        return "No pool both grows faster and earns more than average"
    names = list(top.sort_values("gain", ascending=False)["label"])
    who = names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]
    grow, earn = ("grows", "earns") if len(names) == 1 else ("grow", "earn")
    return f"{who} {grow} faster and {earn} more than average"


def bridge(br) -> str:
    d = br[br["kind"] == "delta"]
    total = br["value"].iloc[-1] - br["value"].iloc[0]
    name = "operating gain" if br.attrs.get("metric", "gain") == "gain" else "revenue"
    if d.empty:
        return f"What drives {name}"
    big = d.loc[d["value"].abs().idxmax()]
    return (f"{name.capitalize()} {'rises' if total >= 0 else 'falls'} {fmt.money(abs(total))}; "
            f"{big['label'].lower()} is the largest driver ({fmt.money(big['value'], signed=True)})")


def flows(view: FV.SankeyView) -> str:
    if not len(view.links):
        return "Largest value flows"
    nl = view.nodes.set_index("node_id")["label"]
    top = view.links.loc[view.links["value"].idxmax()]
    return (f"{nl[top['source']]} → {nl[top['target']]} is the largest flow: {fmt.money(top['value'])}, "
            f"{fmt.pct(top['share_of_total'], 0)} of spend")


def scenario(ctx: ExportContext) -> str:
    cur = V.node_metrics(ctx.result, ctx.meta, ctx.dataset, ctx.year, ctx.focus, ctx.geo, ctx.basis)
    base = V.node_metrics(ctx.base, ctx.meta, ctx.dataset, ctx.year, ctx.focus, ctx.geo, ctx.basis)
    if cur is None or base is None:
        return f"Scenario: {ctx.scenario.name}"
    d = (cur["gain"] if np.isfinite(cur["gain"]) else 0) - (base["gain"] if np.isfinite(base["gain"]) else 0)
    return f"{ctx.scenario.name} changes operating gain by {fmt.money(d, signed=True)} in {ctx.year}"


def value_capture(t: dict) -> str:
    return (f"Owned assets add {fmt.money(t['owned_gain'])} to plan gain of {fmt.money(t['payer_gain'])}: "
            f"consolidated {fmt.money(t['consolidated_gain'])}")
