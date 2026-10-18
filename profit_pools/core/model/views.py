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

ADDITIVE = ["members", "revenue", "allowed", "claims", "opex", "gain", "revenue_with_margin",
            "payer_revenue", "payer_opex"]

# Spend basis: which part of allowed receipts to show for flow recipients.
#   allowed   everything the recipient receives (payer/program-paid + consumer)
#   paid      paid by payers and public programs
#   consumer  member cost sharing and self-pay (allowed − paid)
BASES = ("allowed", "paid", "consumer")


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


def select(result: ModelResult, dataset: str, geo: Geo, years: list[int] | int | None = None,
           basis: str = "allowed") -> pd.DataFrame:
    """Pool rows for a dataset/geography/years. A non-allowed `basis` keeps only flow recipients and
    scales revenue and gain to the paid or consumer part (the pool margin applies uniformly)."""
    if basis not in BASES:
        raise ValueError(f"Unknown basis {basis!r}")
    p = result.pools
    mask = (p["dataset"] == dataset) & _geo_mask(p, geo)
    if years is not None:
        mask &= p["year"].isin([years] if isinstance(years, int) else years)
    if basis != "allowed":
        mask &= p["revenue_paid"].notna()
    out = p.loc[mask].copy()
    if basis != "allowed":
        part = out["revenue_paid"] if basis == "paid" else out["revenue"] - out["revenue_paid"]
        frac = np.where(out["revenue"] > 0, part / out["revenue"], 0.0)
        out["gain"] = out["gain"] * frac
        out["opex"] = out["opex"] * frac
        out["revenue"] = part
    out["revenue_with_margin"] = np.where(out["margin_known"], out["revenue"], 0.0)
    # Per-member measures use payer rows only, so mixed aggregates never divide sector revenue by members.
    is_payer = out["members"].notna()
    out["payer_revenue"] = np.where(is_payer, out["revenue"], 0.0)
    out["payer_opex"] = np.where(is_payer, out["opex"], 0.0)
    out["gain"] = out["gain"].where(out["margin_known"], 0.0)
    return out


def _finish(g: pd.DataFrame) -> pd.DataFrame:
    """Recompute non-additive measures from additive parts."""
    g = g.copy()
    with np.errstate(divide="ignore", invalid="ignore"):
        g["margin"] = np.where(g["revenue_with_margin"] > 0, g["gain"] / g["revenue_with_margin"], np.nan)
        g["margin_coverage"] = np.where(g["revenue"] > 0, g["revenue_with_margin"] / g["revenue"], np.nan)
        mm12 = g["members"] * 12
        g["revenue_pmpm"] = np.where(mm12 > 0, g["payer_revenue"] / mm12, np.nan)
        g["paid_pmpm"] = np.where(mm12 > 0, g["claims"] / mm12, np.nan)
        g["allowed_pmpm"] = np.where(mm12 > 0, g["allowed"] / mm12, np.nan)
        g["mlr"] = np.where(g["members"] > 0, g["claims"] / g["payer_revenue"], np.nan)
    g.loc[g["revenue_with_margin"] <= 0, "gain"] = np.nan
    g.loc[~(g["members"] > 0), ["members", "claims", "allowed", "payer_revenue", "payer_opex"]] = np.nan
    return g


def _ancestor_map(meta: Metadata, depth: int) -> dict[str, str]:
    """leaf id → its ancestor at `depth` (the leaf itself if shallower than depth)."""
    out = {}
    for leaf in meta.taxonomy.leaves():
        path = meta.taxonomy.path(leaf.id)
        out[leaf.id] = path[min(depth, len(path) - 1)].id
    return out


def _unavailable(meta: Metadata, node_ids: list[str], geo: Geo, basis: str = "allowed") -> list[Unavailable]:
    out = []
    if basis != "allowed":
        for nid in node_ids:
            if not any(l.receives_flows for l in meta.taxonomy.leaves(nid)):
                out.append(Unavailable(nid, f"{meta.taxonomy[nid].label}: no paid / consumer split "
                                            f"(spend basis applies to provider and pharmacy pools)."))
        node_ids = [n for n in node_ids if n not in {u.node_id for u in out}]
    if geo.level == "national":
        return out
    order = {"national": 0, "state": 1, "county": 2}
    for nid in node_ids:
        leaves = meta.taxonomy.leaves(nid)
        if all(order[l.geo_level] < order[geo.level] for l in leaves) and not any(l.receives_flows for l in leaves):
            finest = max((l.geo_level for l in leaves), key=order.get)
            out.append(Unavailable(nid, f"{meta.taxonomy[nid].label}: {finest}-level estimate only; "
                                        f"not available for a {geo.level}."))
    return out


def children_in_dataset(meta: Metadata, dataset: str, parent_id: str | None):
    tax = meta.taxonomy
    nodes = tax.layers() if parent_id is None else tax.children(parent_id)
    return [n for n in nodes if meta.in_dataset(n.id, dataset)]


def rollup(result: ModelResult, meta: Metadata, dataset: str, year: int,
           parent_id: str | None = None, geo: Geo = Geo(), basis: str = "allowed") -> RollupView:
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

    df = select(result, dataset, geo, year, basis)
    amap = _ancestor_map(meta, depth)
    df["child"] = df["node_id"].map(amap)
    child_ids = [c.id for c in children]
    df = df[df["child"].isin(child_ids)]
    g = df.groupby("child")[ADDITIVE].sum(min_count=1)
    g = _finish(g.reindex([c for c in child_ids if c in g.index]))

    unavailable = _unavailable(meta, [c for c in child_ids if c not in g.index], geo, basis)
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
                geo: Geo = Geo(), basis: str = "allowed") -> pd.DataFrame:
    """Per-year metrics for one node (all in-dataset pools when node_id is None)."""
    df = select(result, dataset, geo, None, basis)
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


def change_drivers(result: ModelResult, meta: Metadata, dataset: str, year: int, parent_id: str | None,
                   geo: Geo = Geo(), basis: str = "allowed") -> pd.DataFrame:
    """Year-over-year change of each child of `parent_id` and its share of the parent's change."""
    cur = rollup(result, meta, dataset, year, parent_id, geo, basis).rows.set_index("node_id")
    if year - 1 not in set(result.pools["year"]):
        return pd.DataFrame()
    prev = rollup(result, meta, dataset, year - 1, parent_id, geo, basis).rows.set_index("node_id")
    out = pd.DataFrame({
        "label": cur["short_label"],
        "revenue": cur["revenue"],
        "d_revenue": cur["revenue"] - prev["revenue"].reindex(cur.index),
        "gain": cur["gain"],
        "d_gain": cur["gain"] - prev["gain"].reindex(cur.index),
        "d_margin_pp": (cur["margin"] - prev["margin"].reindex(cur.index)) * 100,
    })
    total = out["d_revenue"].sum()
    out["share_of_change"] = out["d_revenue"] / total if total else np.nan
    return out.sort_values("d_revenue", key=np.abs, ascending=False).reset_index()


# ---------------------------------------------------------------------------
# Marimekko tiles
# ---------------------------------------------------------------------------

SEGMENTATIONS = ("hierarchy", "geography", "funding")


def funder_of(meta: Metadata, flows: pd.DataFrame) -> tuple[pd.Series, pd.Series]:
    """(key, label) of who funds each flow: a payer LOB group or a non-pool funding source."""
    groups = flows["lob"].map(meta.lobs.group_of)
    key = groups.where(flows["source_kind"] == "payer", flows["source_id"])
    labels = {**{g: lbl for g, lbl in meta.lobs.groups.items()},
              **{f.id: f.short_label for f in meta.funding_sources.values()}}
    return key, key.map(labels)


def marimekko_tiles(result: ModelResult, meta: Metadata, dataset: str, year: int,
                    parent_id: str | None = None, geo: Geo = Geo(), basis: str = "allowed",
                    segment_by: str = "hierarchy") -> RollupView:
    """Columns = children of `parent_id`; tiles = segments within each column.

    Rows: col_id, seg_key, seg_label, seg_node_id (hierarchy only), revenue, gain, revenue_with_margin,
    margin, margin_coverage, members, share_in_col, col_revenue, col_share — values only (layout is
    the chart's job). Column totals equal `rollup()` for the same arguments.
    """
    if segment_by not in SEGMENTATIONS:
        raise ValueError(segment_by)
    tax = meta.taxonomy
    cols = rollup(result, meta, dataset, year, parent_id, geo, basis)
    col_ids = list(cols.rows["node_id"])
    depth = 0 if parent_id is None else tax[parent_id].depth + 1
    notes = list(cols.notes)

    if segment_by == "funding":
        f = result.flows
        f = f[(f["dataset"] == dataset) & (f["year"] == year) & _geo_mask(f, geo)].copy()
        f["col"] = f["target_id"].map(_ancestor_map(meta, depth))
        f = f[f["col"].isin(col_ids)]
        value = {"allowed": f["amount"], "paid": f["paid"], "consumer": f["consumer"]}[basis]
        frac = np.where(f["amount"] > 0, value / f["amount"], 0.0)
        f["seg_key"], f["seg_label"] = funder_of(meta, f)
        f["revenue"] = value
        f["gain"] = f["gain"].fillna(0.0) * frac
        f["revenue_with_margin"] = np.where(f["margin"].notna(), value, 0.0)
        f["members"] = np.nan
        seg = f.groupby(["col", "seg_key", "seg_label"])[["revenue", "gain", "revenue_with_margin", "members"]].sum(min_count=1)
        seg = seg.reset_index()
        # Pools that are not flow recipients (payers, top-down — incl. PBM inside Pharmacy) keep one
        # explicitly labeled segment per column, so columns still reconcile to rollup().
        other = select(result, dataset, geo, year, basis)
        other = other[other["revenue_paid"].isna()]
        other["col"] = other["node_id"].map(_ancestor_map(meta, depth))
        other = other[other["col"].isin(col_ids)]
        if len(other):
            o = other.groupby("col")[["revenue", "gain", "revenue_with_margin", "members"]].sum(min_count=1).reset_index()
            o["seg_key"], o["seg_label"] = "_unsplit", "Not split by funder"
            seg = pd.concat([seg, o], ignore_index=True)
            notes.append("Funding split applies to provider and dispensing-pharmacy pools; "
                         "payer and top-down pools appear as one unsplit segment.")
        seg["seg_node_id"] = None
    else:
        df = select(result, dataset, geo, year, basis)
        df["col"] = df["node_id"].map(_ancestor_map(meta, depth))
        df = df[df["col"].isin(col_ids)]
        if segment_by == "hierarchy":
            sub = _ancestor_map(meta, depth + 1)
            df["seg_key"] = df["node_id"].map(sub)
            df["seg_label"] = df["seg_key"].map(lambda i: tax[i].short_label)
        else:
            if geo.level == "national":
                df["seg_key"] = df["state"]
                lab = {s.id: s.label for s in meta.geography.states.values()} | {meta.geography.national_id: "National only"}
            elif geo.level == "state":
                df["seg_key"] = df["county"].fillna("_statewide")
                lab = {c: l for c, l, _ in meta.geography.states[geo.id].counties} | {"_statewide": "Statewide (supplemental)"}
            else:
                df["seg_key"] = df["county"]
                lab = {geo.id: meta.geography.county_label(geo.id)}
            df["seg_label"] = df["seg_key"].map(lab)
        seg = df.groupby(["col", "seg_key", "seg_label"])[["revenue", "gain", "revenue_with_margin", "members"]].sum(min_count=1)
        seg = seg.reset_index()
        seg["seg_node_id"] = seg["seg_key"] if segment_by == "hierarchy" else None

    seg = _finish(seg.assign(allowed=np.nan, claims=np.nan, opex=np.nan, payer_revenue=np.nan, payer_opex=np.nan))
    seg = seg[seg["revenue"] > 0]
    colrev = cols.rows.set_index("node_id")["revenue"]
    seg["col_revenue"] = seg["col"].map(colrev)
    seg["share_in_col"] = seg["revenue"] / seg["col_revenue"]
    seg["col_share"] = seg["col"].map(cols.rows.set_index("node_id")["share_of_parent"])
    seg["col_order"] = seg["col"].map({c: i for i, c in enumerate(col_ids)})
    seg = seg.sort_values(["col_order", "revenue"], ascending=[True, False]).drop(columns="col_order")
    seg = seg.rename(columns={"col": "col_id"}).reset_index(drop=True)
    keep = ["col_id", "seg_key", "seg_label", "seg_node_id", "revenue", "gain", "revenue_with_margin",
            "margin", "margin_coverage", "members", "share_in_col", "col_revenue", "col_share"]
    return RollupView(parent_id, seg[keep], cols.unavailable, notes)


def revenue_components(result: ModelResult, meta: Metadata, dataset: str, year: int, node_id: str,
                       geo: Geo = Geo()) -> pd.DataFrame:
    """Recipient revenue by funder with paid / consumer split (inspector: how revenue is built)."""
    leaves = {l.id for l in meta.leaves_in_dataset(dataset, node_id) if l.receives_flows}
    f = result.flows
    f = f[(f["dataset"] == dataset) & (f["year"] == year) & _geo_mask(f, geo) & f["target_id"].isin(leaves)].copy()
    if f.empty:
        return pd.DataFrame(columns=["funder", "allowed", "paid", "consumer", "share"])
    f["key"], f["funder"] = funder_of(meta, f)
    g = f.groupby(["key", "funder"])[["amount", "paid", "consumer"]].sum().reset_index()
    g = g.rename(columns={"amount": "allowed"})
    g["share"] = g["allowed"] / g["allowed"].sum()
    return g.sort_values("allowed", ascending=False).drop(columns="key").reset_index(drop=True)


def node_metrics(result: ModelResult, meta: Metadata, dataset: str, year: int, node_id: str | None,
                 geo: Geo = Geo(), basis: str = "allowed") -> pd.Series | None:
    """One node's metrics (all in-dataset pools when node_id is None); None if unavailable here."""
    if node_id is None:
        rows = rollup(result, meta, dataset, year, None, geo, basis).rows
        if rows.empty:
            return None
        g = rows[ADDITIVE].sum(min_count=1).to_frame().T
        out = _finish(g).iloc[0]
        out["share_of_parent"] = 1.0
        return out
    parent = meta.taxonomy[node_id].parent_id
    rows = rollup(result, meta, dataset, year, parent, geo, basis).rows.set_index("node_id")
    return rows.loc[node_id] if node_id in rows.index else None
