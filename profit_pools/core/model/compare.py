"""Side-by-side comparison of several scenario results against the baseline. Assembles view outputs only
(node_metrics, rollup, node_series, ownership_view) — no new economics."""

from __future__ import annotations

import numpy as np
import pandas as pd

from profit_pools.core.model import ownership as own_mod
from profit_pools.core.model.engine import ModelResult
from profit_pools.core.model.views import Geo, node_metrics, node_series, ownership_view, rollup
from profit_pools.core.taxonomy import Metadata

BASELINE = "Baseline"


def kpi_table(results: dict[str, ModelResult], meta: Metadata, dataset: str, year: int, focus: str | None,
              geo: Geo, basis: str = "allowed") -> pd.DataFrame:
    """One row per scenario (baseline first): revenue, gain, margin, enrollment, revenue CAGR to the forecast end,
    enterprise consolidated gain (enterprise dataset), and Δ vs baseline for revenue and gain."""
    p = meta.periods
    rows = []
    for name, res in results.items():
        m = node_metrics(res, meta, dataset, year, focus, geo, basis)
        if m is None:
            continue
        s = node_series(res, meta, dataset, focus, geo, basis).set_index("year")["revenue"]
        b, e = p.base_year, p.forecast_end
        cg = (s[e] / s[b]) ** (1 / (e - b)) - 1 if b in s and e in s and s[b] > 0 and s[e] > 0 else np.nan
        row = {"scenario": name, "revenue": m["revenue"], "gain": m["gain"], "margin": m["margin"],
               "members": m.get("members", np.nan), "revenue_cagr": cg}
        if dataset == own_mod.dataset():
            ov = ownership_view(res, meta, year, geo)
            row["consolidated_gain"] = ov.totals.get("consolidated_gain", np.nan) if not ov.note else np.nan
        rows.append(row)
    df = pd.DataFrame(rows)
    if not df.empty and BASELINE in set(df["scenario"]):
        b = df.set_index("scenario").loc[BASELINE]
        df["d_revenue"] = df["revenue"] - b["revenue"]
        df["d_gain"] = df["gain"].fillna(0) - (0 if pd.isna(b["gain"]) else b["gain"])
        df["d_margin"] = df["margin"] - b["margin"]
    return df


def pool_deltas(results: dict[str, ModelResult], meta: Metadata, dataset: str, year: int, focus: str | None,
                geo: Geo, metric: str = "gain", basis: str = "allowed") -> pd.DataFrame:
    """Long frame (scenario, node_id, label, delta): Δ `metric` vs baseline by child of the focus."""
    base = rollup(results[BASELINE], meta, dataset, year, focus, geo, basis).rows.set_index("node_id")
    out = []
    for name, res in results.items():
        if name == BASELINE:
            continue
        r = rollup(res, meta, dataset, year, focus, geo, basis).rows.set_index("node_id")
        idx = base.index.union(r.index)
        d = r[metric].reindex(idx).fillna(0) - base[metric].reindex(idx).fillna(0)
        for nid, v in d.items():
            out.append({"scenario": name, "node_id": nid, "label": meta.taxonomy[nid].short_label, "delta": float(v)})
    return pd.DataFrame(out)


def series(results: dict[str, ModelResult], meta: Metadata, dataset: str, focus: str | None, geo: Geo,
           metric: str = "gain", basis: str = "allowed") -> pd.DataFrame:
    """Long frame (scenario, year, value) of `metric` over all years."""
    out = []
    for name, res in results.items():
        s = node_series(res, meta, dataset, focus, geo, basis)
        out.append(pd.DataFrame({"scenario": name, "year": s["year"], "value": s[metric]}))
    return pd.concat(out, ignore_index=True) if out else pd.DataFrame(columns=["scenario", "year", "value"])
