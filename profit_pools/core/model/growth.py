"""Growth analytics on a ModelResult: CAGRs, indexed series, year-over-year matrix and the growth
bridge, which splits the change in operating gain (or revenue) into economic drivers.

Everything is read from engine output (pools and flows); nothing here re-derives economics. The
bridge reconciles exactly to the change in `node_series` (allowed basis) for any node.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from profit_pools.core.model.engine import ModelResult
from profit_pools.core.model.views import Geo, _geo_mask, cagr, children_in_dataset, node_series
from profit_pools.core.taxonomy import Metadata

# Bridge drivers in display order. Each is a definition (cell-level, sequential), not an estimate.
#   Plans (per plan segment × county):   gain = members × (premium − medical cost − admin) per member
#   Providers & pharmacy (per inflow):   revenue = people × services per person × price per service
#                                        (spend per person where no service counts), gain = revenue × margin
#   Top-down pools:                      gain = market size × margin
DRIVERS = {
    "enrollment": "Plan enrollment",                 # Δ members × starting gain (revenue) per member
    "premium": "Premium rates",                      # members × Δ premium per member
    "medical_cost": "Medical cost per member",       # − members × Δ paid claims per member
    "admin": "Admin cost per member",                # − members × Δ residual opex per member
    "people": "People served",                       # Δ people behind each provider/pharmacy inflow
    "utilization": "Utilization (services per person)",
    "unit_price": "Price per service",
    "spend_per_person": "Spend per person",          # inflows without service counts (e.g. pharmacy)
    "supplemental": "Supplemental program payments",  # hospital lump sums (no people behind them)
    "market": "Top-down market growth",              # Δ market size × starting margin
    "margin": "Margin change",                       # revenue × Δ margin (providers, pharmacy, top-down)
    "mix": "New, exited or unmatched cells",
}


def growth_table(result: ModelResult, meta: Metadata, dataset: str, parent_id: str | None, geo: Geo,
                 basis: str, periods: list[tuple[int, int]], base: ModelResult | None = None) -> pd.DataFrame:
    """Per child of `parent_id`: revenue CAGR for each (start, end) period, gain CAGR and margin change over
    the last period, and (when `base` is given) the baseline's revenue CAGR for the last period."""
    rows = []
    for n in children_in_dataset(meta, dataset, parent_id):
        s = node_series(result, meta, dataset, n.id, geo, basis).set_index("year")
        if s.empty:
            continue
        row = {"node_id": n.id, "label": n.short_label}
        for a, b in periods:
            row[f"rev_{a}_{b}"] = cagr(s["revenue"].get(a), s["revenue"].get(b), b - a)
        a, b = periods[-1]
        row["gain_cagr"] = cagr(s["gain"].get(a), s["gain"].get(b), b - a)
        ma, mb = s["margin"].get(a), s["margin"].get(b)
        row["d_margin"] = mb - ma if ma is not None and mb is not None else np.nan
        if base is not None:
            sb = node_series(base, meta, dataset, n.id, geo, basis).set_index("year")
            row["rev_base"] = cagr(sb["revenue"].get(a), sb["revenue"].get(b), b - a) if not sb.empty else np.nan
        rows.append(row)
    return pd.DataFrame(rows)


def indexed(result: ModelResult, meta: Metadata, dataset: str, parent_id: str | None, geo: Geo, basis: str,
            index_year: int, metric: str = "revenue") -> pd.DataFrame:
    """Long frame (node_id, label, year, value, index) with index = 100 × value / value[index_year]. Index is
    NaN where the index-year value is ≤ 0 (an index on a non-positive base is not meaningful)."""
    out = []
    for n in children_in_dataset(meta, dataset, parent_id):
        s = node_series(result, meta, dataset, n.id, geo, basis)[["year", metric]].rename(columns={metric: "value"})
        if s.empty:
            continue
        b = s.loc[s["year"] == index_year, "value"]
        b = float(b.iloc[0]) if len(b) else np.nan
        s["index"] = 100 * s["value"] / b if np.isfinite(b) and b > 0 else np.nan
        s["node_id"], s["label"] = n.id, n.short_label
        out.append(s)
    return pd.concat(out, ignore_index=True) if out else pd.DataFrame(columns=["year", "value", "index", "node_id", "label"])


def yoy(result: ModelResult, meta: Metadata, dataset: str, parent_id: str | None, geo: Geo, basis: str,
        metric: str = "revenue") -> pd.DataFrame:
    """Children × years matrix of year-over-year growth (NaN where the prior year is ≤ 0 or missing)."""
    rows = {}
    for n in children_in_dataset(meta, dataset, parent_id):
        s = node_series(result, meta, dataset, n.id, geo, basis).set_index("year")[metric]
        if s.empty:
            continue
        prev = s.shift(1)
        rows[n.short_label] = (s / prev - 1).where(prev > 0)
    m = pd.DataFrame(rows).T
    return m.iloc[:, 1:] if m.shape[1] > 1 else m


def _wide(df: pd.DataFrame, idx: list[str], cols: list[str], y0: int, y1: int) -> dict[str, pd.Series]:
    w = df.pivot_table(index=idx, columns="year", values=cols, aggfunc="sum")
    out = {}
    for c in cols:
        for suffix, y in (("0", y0), ("1", y1)):
            out[c + suffix] = (w[(c, y)] if (c, y) in w else pd.Series(0.0, index=w.index)).fillna(0.0)
    return out


def bridge(result: ModelResult, meta: Metadata, dataset: str, node_id: str | None, geo: Geo, y0: int, y1: int,
           metric: str = "gain", by_child: bool = False) -> pd.DataFrame:
    """Split the change in `metric` ("gain" or "revenue") of `node_id` from y0 to y1 into DRIVERS.

    `by_child` (top-down pools): market growth is reported per child pool instead of as one line, since
    those pools have no driver detail. Rows: key, label, value, kind (total | delta). attrs["residual"] is
    the unexplained remainder (0 up to float error; tested) and attrs["drivers"] the full split.
    """
    if metric not in ("gain", "revenue"):
        raise ValueError(metric)
    g = metric == "gain"
    leaves = meta.leaves_in_dataset(dataset, node_id)
    econ: dict[str, set[str]] = {}
    for l in leaves:
        econ.setdefault(l.economics, set()).add(l.id)
    comp = dict.fromkeys(DRIVERS, 0.0)
    child_rows: list[tuple[str, str, float]] = []
    p = result.pools
    p = p[(p["dataset"] == dataset) & p["year"].isin([y0, y1]) & _geo_mask(p, geo)]

    # Plans
    pay = p[p["node_id"].isin(econ.get("payer_pnl", set()))].fillna({"county": "_"})
    if len(pay):
        w = _wide(pay, ["state", "county", "node_id"], ["members", "revenue", "claims", "opex"], y0, y1)
        m0, m1 = w["members0"], w["members1"]
        ok = (m0 > 0) & (m1 > 0)
        safe0, safe1 = m0.where(ok, 1.0), m1.where(ok, 1.0)
        r0, r1 = w["revenue0"] / safe0, w["revenue1"] / safe1
        c0, c1 = w["claims0"] / safe0, w["claims1"] / safe1
        o0, o1 = w["opex0"] / safe0, w["opex1"] / safe1
        per0 = (r0 - c0 - o0) if g else r0
        comp["enrollment"] += float(np.where(ok, (m1 - m0) * per0, 0.0).sum())
        comp["premium"] += float(np.where(ok, m1 * (r1 - r0), 0.0).sum())
        if g:
            comp["medical_cost"] += float(np.where(ok, -m1 * (c1 - c0), 0.0).sum())
            comp["admin"] += float(np.where(ok, -m1 * (o1 - o0), 0.0).sum())
        val = (w["revenue1"] - w["claims1"] - w["opex1"]) - (w["revenue0"] - w["claims0"] - w["opex0"]) if g \
            else w["revenue1"] - w["revenue0"]
        comp["mix"] += float(np.where(ok, 0.0, val).sum())

    # Top-down pools: market size × margin
    td = p[p["node_id"].isin(econ.get("top_down", set()))]
    if len(td):
        td = td.assign(margin=np.where(td["revenue"] > 0, td["gain"] / td["revenue"].where(td["revenue"] > 0, 1.0), np.nan))
        w = td.pivot_table(index="node_id", columns="year", values=["revenue", "margin"], aggfunc="first")
        R0 = w[("revenue", y0)].fillna(0.0) if ("revenue", y0) in w else pd.Series(0.0, index=w.index)
        R1 = w[("revenue", y1)].fillna(0.0) if ("revenue", y1) in w else pd.Series(0.0, index=w.index)
        M0 = w[("margin", y0)] if ("margin", y0) in w else pd.Series(np.nan, index=w.index)
        M1 = w[("margin", y1)] if ("margin", y1) in w else pd.Series(np.nan, index=w.index)
        known = M0.notna() & M1.notna()
        growth = (R1 - R0) * (M0.where(known, 0.0) if g else 1.0)
        if g:
            comp["margin"] += float((R1 * (M1 - M0)).where(known, 0.0).sum())
            comp["mix"] += float((R1 * M1.fillna(0) - R0 * M0.fillna(0)).where(~known, 0.0).sum())
        if by_child and node_id is not None:
            kids = children_in_dataset(meta, dataset, node_id) or [meta.taxonomy[node_id]]
            amap = {l.id: c.id for c in kids for l in meta.taxonomy.leaves(c.id)}
            by = growth.groupby(growth.index.map(lambda i: amap.get(i, node_id))).sum()
            for c in [k.id for k in kids]:
                if c in by:
                    child_rows.append((f"market:{c}", f"{meta.taxonomy[c].short_label} market growth", float(by[c])))
        else:
            comp["market"] += float(growth.sum())

    # Providers & pharmacy: per inflow cell
    rec = econ.get("margin_on_revenue", set())
    f = result.flows
    f = f[(f["dataset"] == dataset) & f["year"].isin([y0, y1]) & _geo_mask(f, geo) & f["target_id"].isin(rec)]
    if len(f):
        f = f.fillna({"county": "_", "lob": "_", "category": "_"}).assign(
            known=lambda d: d["margin"].notna(), instances=lambda d: d["instances"].fillna(0.0),
            people=lambda d: d["people"].fillna(0.0), margin=lambda d: d["margin"].fillna(0.0))
        idx = ["state", "county", "source_id", "lob", "category", "target_id", "flow_kind"]
        w = _wide(f, idx, ["amount", "instances", "people", "margin"], y0, y1)
        kn = f.pivot_table(index=idx, columns="year", values="known", aggfunc="min")
        k0 = kn[y0].reindex(w["amount0"].index).fillna(False).astype(bool) if y0 in kn else False
        k1 = kn[y1].reindex(w["amount0"].index).fillna(False).astype(bool) if y1 in kn else False
        a0, a1 = w["amount0"], w["amount1"]
        d0, d1, i0, i1 = w["people0"], w["people1"], w["instances0"], w["instances1"]
        mg0, mg1 = w["margin0"], w["margin1"]
        both = (a0 > 0) & (a1 > 0) & (k0 & k1 if g else True)
        supp = both & (w["amount0"].index.get_level_values("flow_kind") == "supplemental")
        perpeople = both & ~supp & (d0 > 0) & (d1 > 0)
        inst = perpeople & (i0 > 0) & (i1 > 0)
        scale = mg0 if g else 1.0
        vol = np.where(perpeople, (d1 - d0) * a0 / d0.where(perpeople, 1.0), 0.0)
        util = np.where(inst, d1 * (i1 / d1.where(perpeople, 1.0) - i0 / d0.where(perpeople, 1.0))
                        * a0 / i0.where(inst, 1.0), 0.0)
        rest = np.where(perpeople, a1 - a0 - vol - util, 0.0)
        comp["people"] += float((vol * scale).sum())
        comp["utilization"] += float((util * scale).sum())
        comp["unit_price"] += float((np.where(inst, rest, 0.0) * scale).sum())
        comp["spend_per_person"] += float((np.where(perpeople & ~inst, rest, 0.0) * scale).sum())
        comp["supplemental"] += float((np.where(supp, a1 - a0, 0.0) * scale).sum())
        other = both & ~supp & ~perpeople                      # amounts without people (should not occur)
        comp["mix"] += float((np.where(other, a1 - a0, 0.0) * scale).sum())
        if g:
            comp["margin"] += float(np.where(both, a1 * (mg1 - mg0), 0.0).sum())
            v0 = np.where(k0, a0 * mg0, 0.0)
            v1 = np.where(k1, a1 * mg1, 0.0)
            comp["mix"] += float(np.where(both, 0.0, v1 - v0).sum())
        else:
            comp["mix"] += float(np.where(both, 0.0, a1 - a0).sum())

    s = node_series(result, meta, dataset, node_id, geo).set_index("year")[metric].fillna(0.0)
    start, end = float(s.get(y0, 0.0)), float(s.get(y1, 0.0))
    name = "Operating gain" if g else "Revenue"
    rows = [{"key": "start", "label": f"{name} {y0}", "value": start, "kind": "total"}]
    rows += [{"key": k, "label": DRIVERS[k], "value": v, "kind": "delta"} for k, v in comp.items()
             if abs(v) >= 0.5e6 and k not in ("mix", "margin")]
    rows += [{"key": k, "label": lab, "value": v, "kind": "delta"} for k, lab, v in child_rows if abs(v) >= 0.5e6]
    rows += [{"key": k, "label": DRIVERS[k], "value": comp[k], "kind": "delta"} for k in ("margin", "mix")
             if abs(comp[k]) >= 0.5e6]
    rows.append({"key": "end", "label": f"{name} {y1}", "value": end, "kind": "total"})
    out = pd.DataFrame(rows)
    out.attrs["residual"] = end - start - sum(comp.values()) - sum(v for _, _, v in child_rows)
    out.attrs["drivers"] = comp
    out.attrs["metric"] = metric
    return out


def bridge_kind(meta: Metadata, dataset: str, node_id: str | None) -> str:
    """Which detail a pool's bridge shows: overall | plan | provider | top_down | mixed."""
    if node_id is None:
        return "overall"
    kinds = {l.economics for l in meta.leaves_in_dataset(dataset, node_id)}
    if kinds == {"payer_pnl"}:
        return "plan"
    if kinds == {"top_down"}:
        return "top_down"
    if kinds == {"margin_on_revenue"}:
        return "provider"
    return "mixed"


def attractiveness(result: ModelResult, meta: Metadata, dataset: str, parent_id: str | None, geo: Geo, basis: str,
                   y0: int, y1: int, levels: int = 1) -> pd.DataFrame:
    """Pools `levels` below `parent_id`: revenue CAGR y0→y1 (x), operating margin in y1 (y) and operating gain in
    y1 (bubble). Averages are revenue-weighted over the same pools so the quadrant lines are consistent."""
    nodes = children_in_dataset(meta, dataset, parent_id)
    for _ in range(levels - 1):
        nxt = []
        for n in nodes:
            kids = children_in_dataset(meta, dataset, n.id)
            nxt += kids or [n]
        nodes = nxt
    rows = []
    for n in nodes:
        s = node_series(result, meta, dataset, n.id, geo, basis).set_index("year")
        if s.empty or y1 not in s.index:
            continue
        r0, r1 = s["revenue"].get(y0, np.nan), s["revenue"].get(y1, np.nan)
        rows.append({"node_id": n.id, "label": n.short_label, "layer_id": n.layer_id,
                     "cagr": cagr(r0, r1, y1 - y0), "margin": s.loc[y1, "margin"], "gain": s.loc[y1, "gain"],
                     "revenue": r1, "revenue_start": r0})
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    ok = df["revenue"] > 0
    t0, t1 = df.loc[ok, "revenue_start"].sum(), df.loc[ok, "revenue"].sum()
    km = df["margin"].notna() & ok
    df.attrs["avg_growth"] = cagr(t0, t1, y1 - y0)
    df.attrs["avg_margin"] = (df.loc[km, "gain"].sum() / df.loc[km, "revenue"].sum()) if km.any() else np.nan
    return df
