"""Growth analytics on a ModelResult: CAGRs, indexed series, year-over-year matrix and a revenue
growth bridge that splits the change into who-pays / how-much drivers.

Everything is read from engine output (pools and flows); nothing here re-derives economics. The
bridge reconciles exactly to the change in `node_series` revenue (allowed basis).
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from profit_pools.core.model.engine import ModelResult
from profit_pools.core.model.views import Geo, _geo_mask, cagr, children_in_dataset, node_series
from profit_pools.core.taxonomy import Metadata

# Bridge components in display order. Each is a definition, not an estimate:
COMPONENTS = {
    "enrollment": "Plan enrollment",                # payer pools: Δ members × starting revenue PMPM
    "premium": "Premium rate (per member)",         # payer pools: the rest of payer revenue change
    "covered_members": "Members covered by plans",  # recipients: Δ source-plan members × starting spend per member
    "utilization": "Utilization (services per member)",
    "unit_price": "Price per service",
    "spend_per_member": "Spend per member (no service counts)",
    "funding": "Uninsured & government FFS",        # population-based funding (no member counts)
    "supplemental": "Supplemental & state payments",
    "top_down": "Top-down market growth",
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


def revenue_bridge(result: ModelResult, meta: Metadata, dataset: str, node_id: str | None, geo: Geo,
                   y0: int, y1: int) -> pd.DataFrame:
    """Split the change in allowed revenue of `node_id` from y0 to y1 into COMPONENTS.

    Payer pools: revenue = members × revenue PMPM. Recipients' payer-claims inflows: amount = source-plan
    members × services per member × price per service (or × spend per member where there are no service
    counts), decomposed sequentially in that order. Population-funded, supplemental and top-down revenue
    are reported as their own lines. Cells present in only one year go to "mix". Σ components = Δ revenue.
    """
    leaves = meta.leaves_in_dataset(dataset, node_id)
    by_econ: dict[str, set[str]] = {}
    for l in leaves:
        by_econ.setdefault(l.economics, set()).add(l.id)
    p = result.pools
    p = p[(p["dataset"] == dataset) & p["year"].isin([y0, y1]) & _geo_mask(p, geo)]
    comp = dict.fromkeys(COMPONENTS, 0.0)
    keys = ["state", "county", "node_id"]

    def wide(df, cols, idx):
        w = df.pivot_table(index=idx, columns="year", values=cols, aggfunc="sum")
        return {c: (w[(c, y0)] if (c, y0) in w else pd.Series(0.0, index=w.index)).fillna(0.0) for c in cols} | {
            c + "1": (w[(c, y1)] if (c, y1) in w else pd.Series(0.0, index=w.index)).fillna(0.0) for c in cols}

    payers = p[p["node_id"].isin(by_econ.get("payer_pnl", set()))].fillna({"county": "_"})
    if len(payers):
        w = wide(payers, ["members", "revenue"], keys)
        m0, m1, r0, r1 = w["members"], w["members1"], w["revenue"], w["revenue1"]
        ok = (m0 > 0) & (m1 > 0)
        enroll = np.where(ok, (m1 - m0) * r0 / m0.where(ok, 1.0), 0.0)
        comp["enrollment"] += float(enroll.sum())
        comp["premium"] += float(np.where(ok, r1 - r0 - enroll, 0.0).sum())
        comp["mix"] += float(np.where(ok, 0.0, r1 - r0).sum())

    td = p[p["node_id"].isin(by_econ.get("top_down", set()))]
    comp["top_down"] += float(td.loc[td["year"] == y1, "revenue"].sum() - td.loc[td["year"] == y0, "revenue"].sum())

    rec = by_econ.get("margin_on_revenue", set())
    f = result.flows
    f = f[(f["dataset"] == dataset) & f["year"].isin([y0, y1]) & _geo_mask(f, geo) & f["target_id"].isin(rec)]
    if len(f):
        f = f.fillna({"county": "_", "lob": "_", "category": "_"})
        for kind, key in (("funding_pmpm", "funding"), ("supplemental", "supplemental")):
            k = f[f["flow_kind"] == kind]
            comp[key] += float(k.loc[k["year"] == y1, "amount"].sum() - k.loc[k["year"] == y0, "amount"].sum())
        pc = f[f["flow_kind"] == "payer_claims"]
        if len(pc):
            idx = ["state", "county", "source_id", "lob", "category", "target_id"]
            w = wide(pc.fillna({"instances": 0.0}), ["amount", "instances"], idx)
            mem = result.pools
            mem = mem[(mem["dataset"] == dataset) & mem["year"].isin([y0, y1]) & mem["members"].notna()]
            mem = mem.fillna({"county": "_"}).pivot_table(index=["state", "county", "node_id"], columns="year",
                                                         values="members", aggfunc="sum")
            look = pd.MultiIndex.from_arrays([w["amount"].index.get_level_values(i) for i in ("state", "county", "source_id")])
            m0 = pd.Series(mem[y0].reindex(look).to_numpy() if y0 in mem else np.nan, index=w["amount"].index).fillna(0.0)
            m1 = pd.Series(mem[y1].reindex(look).to_numpy() if y1 in mem else np.nan, index=w["amount"].index).fillna(0.0)
            a0, a1, i0, i1 = w["amount"], w["amount1"], w["instances"], w["instances1"]
            ok = (m0 > 0) & (m1 > 0) & (a0 > 0) & (a1 > 0)
            vol = np.where(ok, (m1 - m0) * a0 / m0.where(ok, 1.0), 0.0)
            inst = ok & (i0 > 0) & (i1 > 0)
            util = np.where(inst, m1 * (i1 / m1.where(ok, 1.0) - i0 / m0.where(ok, 1.0)) * a0 / i0.where(inst, 1.0), 0.0)
            rest = np.where(ok, a1 - a0 - vol - util, 0.0)
            comp["covered_members"] += float(vol.sum())
            comp["utilization"] += float(util.sum())
            comp["unit_price"] += float(np.where(inst, rest, 0.0).sum())
            comp["spend_per_member"] += float(np.where(ok & ~inst, rest, 0.0).sum())
            comp["mix"] += float(np.where(ok, 0.0, a1 - a0).sum())
    s = node_series(result, meta, dataset, node_id, geo).set_index("year")["revenue"]
    start, end = float(s.get(y0, 0.0)), float(s.get(y1, 0.0))
    rows = [{"key": "start", "label": f"Revenue {y0}", "value": start, "kind": "total"}]
    rows += [{"key": k, "label": COMPONENTS[k], "value": v, "kind": "delta"} for k, v in comp.items() if abs(v) >= 0.5e6]
    rows.append({"key": "end", "label": f"Revenue {y1}", "value": end, "kind": "total"})
    out = pd.DataFrame(rows)
    out.attrs["residual"] = end - start - sum(comp.values())   # 0 up to float error; tested
    out.attrs["components"] = comp
    return out
