"""Read-only views over a `ModelResult`.

These functions only filter, group and sum engine output and recompute
non-additive ratios from additive parts. They never introduce new economics,
so every chart built on them agrees with the engine by construction.

Geography semantics
  national  all rows of the dataset
  state     rows in the state. National-only (top-down) pools are unavailable.
  county    payer rows and county-grain recipient rows. State-level supplemental
            payments and top-down pools are unavailable. Recipient gain at county
            grain applies the state pool margin (allocated).
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from profit_pools.core.model.engine import ModelResult
from profit_pools.core.taxonomy import Metadata

ADDITIVE = ["members", "revenue", "claims", "opex", "gain", "revenue_with_margin"]


@dataclass(frozen=True)
class Geo:
    level: str = "national"   # national | state | county
    id: str | None = None

    @staticmethod
    def parse(meta: Metadata, geo_id: str | None) -> "Geo":
        if geo_id in (None, "", meta.geography.national_id):
            return Geo()
        if geo_id in meta.geography.states:
            return Geo("state", geo_id)
        meta.geography.state_of_county(geo_id)  # raises KeyError if unknown
        return Geo("county", geo_id)


@dataclass(frozen=True)
class Unavailable:
    node_id: str
    reason: str


@dataclass(frozen=True)
class RollupView:
    parent_id: str | None
    rows: pd.DataFrame                    # one row per visible child; see rollup()
    unavailable: list[Unavailable] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def _geo_mask(df: pd.DataFrame, geo: Geo) -> pd.Series:
    if geo.level == "national":
        return pd.Series(True, index=df.index)
    if geo.level == "state":
        return df["state"] == geo.id
    return df["county"] == geo.id


def select(result: ModelResult, dataset: str, geo: Geo, years: list[int] | int | None = None) -> pd.DataFrame:
    p = result.pools
    mask = (p["dataset"] == dataset) & _geo_mask(p, geo)
    if years is not None:
        mask &= p["year"].isin([years] if isinstance(years, int) else years)
    out = p.loc[mask].copy()
    out["revenue_with_margin"] = np.where(out["margin_known"], out["revenue"], 0.0)
    out["gain"] = out["gain"].where(out["margin_known"], 0.0)
    return out


def _finish(g: pd.DataFrame) -> pd.DataFrame:
    """Recompute non-additive measures from additive parts."""
    g = g.copy()
    with np.errstate(divide="ignore", invalid="ignore"):
        g["margin"] = np.where(g["revenue_with_margin"] > 0, g["gain"] / g["revenue_with_margin"], np.nan)
        g["margin_coverage"] = np.where(g["revenue"] > 0, g["revenue_with_margin"] / g["revenue"], np.nan)
        mm12 = g["members"] * 12
        g["revenue_pmpm"] = np.where(mm12 > 0, g["revenue"] / mm12, np.nan)
        g["spend_pmpm"] = np.where(mm12 > 0, g["claims"] / mm12, np.nan)
        g["mlr"] = np.where(g["members"] > 0, g["claims"] / g["revenue"], np.nan)
    g.loc[g["revenue_with_margin"] <= 0, "gain"] = np.nan
    g.loc[~(g["members"] > 0), ["members", "claims"]] = np.nan
    return g


def _ancestor_map(meta: Metadata, depth: int) -> dict[str, str]:
    """leaf id → its ancestor at `depth` (the leaf itself if shallower than depth)."""
    out = {}
    for leaf in meta.taxonomy.leaves():
        path = meta.taxonomy.path(leaf.id)
        out[leaf.id] = path[min(depth, len(path) - 1)].id
    return out


def _unavailable_at_geo(meta: Metadata, node_ids: list[str], geo: Geo) -> list[Unavailable]:
    if geo.level == "national":
        return []
    out = []
    order = {"national": 0, "state": 1, "county": 2}
    for nid in node_ids:
        leaves = meta.taxonomy.leaves(nid)
        if all(order[l.geo_level] < order[geo.level] for l in leaves) and not any(l.receives_flows for l in leaves):
            finest = max((l.geo_level for l in leaves), key=order.get)
            out.append(Unavailable(nid, f"{meta.taxonomy[nid].label}: {finest}-level estimate only; "
                                        f"not available for a {geo.level}."))
    return out


def rollup(result: ModelResult, meta: Metadata, dataset: str, year: int,
           parent_id: str | None = None, geo: Geo = Geo()) -> RollupView:
    """Children of `parent_id` (layers when None) with additive and recomputed metrics.

    Columns: node_id, label, short_label, layer_id, is_leaf, members, revenue, claims, opex, gain,
             revenue_with_margin, margin, margin_coverage, revenue_pmpm, spend_pmpm, mlr,
             share_of_parent, coverage, economics.
    """
    tax = meta.taxonomy
    if parent_id is None:
        children = [n for n in tax.layers() if meta.in_dataset(n.id, dataset)]
        depth = 0
    else:
        if not meta.in_dataset(parent_id, dataset):
            raise ValueError(f"{parent_id} is excluded from dataset {dataset}")
        children = [n for n in tax.children(parent_id) if meta.in_dataset(n.id, dataset)]
        depth = tax[parent_id].depth + 1
        if not children:
            raise ValueError(f"{parent_id} is a leaf; nothing to roll up")

    df = select(result, dataset, geo, year)
    amap = _ancestor_map(meta, depth)
    df["child"] = df["node_id"].map(amap)
    child_ids = [c.id for c in children]
    df = df[df["child"].isin(child_ids)]
    g = df.groupby("child")[ADDITIVE].sum(min_count=1)
    g = _finish(g.reindex([c for c in child_ids if c in g.index]))

    unavailable = _unavailable_at_geo(meta, [c for c in child_ids if c not in g.index], geo)
    g = g[g["revenue"] > 0]
    total = g["revenue"].sum()
    g["share_of_parent"] = g["revenue"] / total if total > 0 else np.nan
    g.index.name = "node_id"
    g = g.reset_index()
    g["label"] = g["node_id"].map(lambda i: tax[i].label)
    g["short_label"] = g["node_id"].map(lambda i: tax[i].short_label)
    g["layer_id"] = g["node_id"].map(lambda i: tax[i].layer_id)
    g["is_leaf"] = g["node_id"].map(lambda i: not any(meta.in_dataset(c.id, dataset) for c in tax.children(i)))
    g["coverage"] = g["node_id"].map(lambda i: coverage_label(meta, i, dataset))
    g["economics"] = g["node_id"].map(lambda i: economics_label(meta, i, dataset))

    notes = []
    if (g["margin_coverage"] < 0.999).any():
        names = ", ".join(g.loc[g["margin_coverage"] < 0.999, "short_label"])
        notes.append(f"Margin unavailable for part of: {names}. Margins are computed on revenue with margin data.")
    if geo.level == "county" and any(tax[c].layer_id != "PAYER" for c in child_ids):
        notes.append("County view: recipient revenue excludes state-level supplemental payments; "
                     "gain applies the state pool margin.")
    return RollupView(parent_id, g, unavailable, notes)


def coverage_label(meta: Metadata, node_id: str, dataset: str) -> str:
    kinds = {l.coverage for l in meta.leaves_in_dataset(dataset, node_id)}
    return kinds.pop() if len(kinds) == 1 else "mixed"


def economics_label(meta: Metadata, node_id: str, dataset: str) -> str:
    kinds = {l.economics for l in meta.leaves_in_dataset(dataset, node_id)}
    return kinds.pop() if len(kinds) == 1 else "mixed"


def node_series(result: ModelResult, meta: Metadata, dataset: str, node_id: str | None,
                geo: Geo = Geo()) -> pd.DataFrame:
    """Per-year metrics for one node (all in-dataset pools when node_id is None)."""
    df = select(result, dataset, geo)
    if node_id is not None:
        leaves = {l.id for l in meta.leaves_in_dataset(dataset, node_id)}
        df = df[df["node_id"].isin(leaves)]
    g = df.groupby("year")[ADDITIVE].sum(min_count=1)
    g = _finish(g)
    g["is_forecast"] = g.index > result.base_year
    return g.reset_index()


def totals(result: ModelResult, meta: Metadata, dataset: str, year: int, geo: Geo = Geo()) -> dict:
    """Headline metrics. Revenue is gross sector revenue (includes inter-sector payments)."""
    view = rollup(result, meta, dataset, year, None, geo)
    r = view.rows
    payer = r[r["layer_id"] == "PAYER"]
    rwm = r["revenue_with_margin"].sum()
    return {
        "gross_revenue": r["revenue"].sum(),
        "operating_gain": r["gain"].sum(min_count=1),
        "operating_margin": r["gain"].sum() / rwm if rwm > 0 else np.nan,
        "margin_coverage": rwm / r["revenue"].sum() if r["revenue"].sum() > 0 else np.nan,
        "enrollment": payer["members"].sum() if len(payer) else np.nan,
        "payer_revenue": payer["revenue"].sum() if len(payer) else np.nan,
        "unavailable": view.unavailable,
    }


def cagr(start: float, end: float, years: int) -> float:
    """Realized CAGR from resulting values. NaN ("n/m") when either endpoint is ≤ 0 or missing."""
    if years <= 0 or start is None or end is None or not np.isfinite(start) or not np.isfinite(end):
        return np.nan
    if start <= 0 or end <= 0:
        return np.nan
    return (end / start) ** (1 / years) - 1
