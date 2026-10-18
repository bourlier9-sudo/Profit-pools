"""Value-flow (Sankey) views over a `ModelResult`.

Pure filter / map / sum over `result.flows` — no new economics. Every column
of the Sankey carries the same grand total, whatever the granularity, the
"Other" threshold or the consumer split, so roll-ups always reconcile.

Columns (left → right, each optional except one source and one recipient column):
  source_group   LOB group, funding group (Government-managed FFS) or stand-alone funding source
  source_detail  individual LOB or funding source
  recipient_layer
  recipient      taxonomy node at the chosen depth, with selected nodes expanded further
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from profit_pools.core.model.engine import ModelResult
from profit_pools.core.model.views import Geo, _geo_mask
from profit_pools.core.taxonomy import Metadata

CONSUMER = "CONSUMER"
OTHER = "__other__"
METRICS = ("allowed", "paid", "consumer", "gain")
METRIC_LABELS = {"allowed": "Allowed spend", "paid": "Payer-paid spend", "consumer": "Consumer spend",
                 "gain": "Recipient operating gain"}
SOURCE_COLS = ("source_group", "source_detail")
RECIPIENT_COLS = ("recipient_layer", "recipient")


@dataclass(frozen=True)
class SankeyConfig:
    metric: str = "allowed"
    source_columns: tuple[str, ...] = ("source_group",)
    recipient_columns: tuple[str, ...] = ("recipient",)
    recipient_depth: int = 1                    # 0 = layer, 1 = L1, …
    expand: tuple[str, ...] = ()                # recipient nodes shown one or more levels deeper
    source_filter: tuple[str, ...] = ()         # source group / detail ids; empty = all
    recipient_filter: tuple[str, ...] = ()      # taxonomy nodes; empty = all recipients
    split_consumer: bool = False                # show consumer spend as its own source (allowed metric)
    min_share: float = 0.0                      # nodes below this share of the total fold into "Other"


@dataclass
class SankeyView:
    columns: list[str]
    nodes: pd.DataFrame       # node_id, column, key, label, value, members (list of underlying keys)
    links: pd.DataFrame       # source, target, value, + detail columns (see build)
    total: float
    detail: pd.DataFrame      # filtered flow rows with display keys (for the inspector)
    notes: list[str] = field(default_factory=list)


def source_group_of(meta: Metadata, f: pd.DataFrame) -> tuple[pd.Series, pd.Series]:
    """(group key, detail key) for every flow row."""
    lob_group = f["lob"].map(meta.lobs.group_of)
    fund_group = f["source_id"].map(lambda s: (meta.funding_sources[s].group or s) if s in meta.funding_sources else None)
    group = lob_group.where(f["source_kind"] == "payer", fund_group)
    detail = f["lob"].where(f["source_kind"] == "payer", f["source_id"])
    return group, detail


def source_label(meta: Metadata, key: str) -> str:
    if key == CONSUMER:
        return "Consumers (cost sharing & self-pay)"
    if key == OTHER:
        return "Other sources"
    if key in meta.lobs.groups:
        return meta.lobs.groups[key]
    if key in meta.lobs.lobs:
        return meta.lobs.lobs[key].label
    if key in meta.funding_groups:
        return meta.funding_groups[key]
    if key in meta.funding_sources:
        return meta.funding_sources[key].short_label
    return key


def recipient_display_map(meta: Metadata, depth: int, expand: tuple[str, ...]) -> dict[str, str]:
    """leaf → displayed node: ancestor at `depth`, descending further through expanded nodes."""
    tax = meta.taxonomy
    out = {}
    ex = set(expand)
    for leaf in tax.leaves():
        if not leaf.receives_flows:
            continue
        path = tax.path(leaf.id)
        i = min(depth, len(path) - 1)
        while path[i].id in ex and i < len(path) - 1:
            i += 1
        out[leaf.id] = path[i].id
    return out


def filtered_flows(result: ModelResult, meta: Metadata, dataset: str, year: int, geo: Geo,
                   cfg: SankeyConfig) -> pd.DataFrame:
    f = result.flows
    f = f[(f["dataset"] == dataset) & (f["year"] == year) & _geo_mask(f, geo)].copy()
    f["group"], f["detail"] = source_group_of(meta, f)
    if cfg.source_filter:
        keep = set(cfg.source_filter)
        f = f[f["group"].isin(keep) | f["detail"].isin(keep)]
    if cfg.recipient_filter:
        leaves = {l for n in cfg.recipient_filter for l in meta.taxonomy.leaf_ids(n)}
        f = f[f["target_id"].isin(leaves)]
    if cfg.metric == "gain":
        f["value"] = f["gain"].fillna(0.0)
    else:
        f["value"] = f["amount" if cfg.metric == "allowed" else cfg.metric]
    f["layer"] = f["target_id"].map(lambda t: meta.taxonomy[t].layer_id)
    f["recipient"] = f["target_id"].map(recipient_display_map(meta, cfg.recipient_depth, cfg.expand))
    if cfg.split_consumer and cfg.metric == "allowed":
        paid = f.copy()
        paid["value"] = f["paid"]
        cons = f.copy()
        cons["value"] = f["consumer"]
        cons["group"] = cons["detail"] = CONSUMER
        f = pd.concat([paid, cons], ignore_index=True)
    return f[f["value"] != 0]


def _fold(series_total: pd.Series, min_share: float, total: float) -> dict[str, str]:
    """key → key, or → OTHER when the key's total is below min_share of the grand total."""
    if min_share <= 0 or total == 0:
        return {k: k for k in series_total.index}
    small = series_total.abs() < min_share * abs(total)
    if small.sum() <= 1:          # folding a single node into "Other" hides it for no gain
        return {k: k for k in series_total.index}
    return {k: (OTHER if small[k] else k) for k in series_total.index}


def build(result: ModelResult, meta: Metadata, dataset: str, year: int, geo: Geo, cfg: SankeyConfig) -> SankeyView:
    if cfg.metric not in METRICS:
        raise ValueError(cfg.metric)
    src_cols = [c for c in SOURCE_COLS if c in cfg.source_columns] or ["source_group"]
    rec_cols = [c for c in RECIPIENT_COLS if c in cfg.recipient_columns] or ["recipient"]
    columns = src_cols + rec_cols
    f = filtered_flows(result, meta, dataset, year, geo, cfg)
    notes: list[str] = []
    key_of = {"source_group": "group", "source_detail": "detail", "recipient_layer": "layer", "recipient": "recipient"}
    total = float(f["value"].sum())

    # "Other" folding per column (node-level, so every column keeps the grand total)
    members: dict[tuple[str, str], list[str]] = {}
    for col in columns:
        k = key_of[col]
        tot = f.groupby(k)["value"].sum()
        mapping = _fold(tot, cfg.min_share, total)
        f[col] = f[k].map(mapping)
        for orig, disp in mapping.items():
            members.setdefault((col, disp), []).append(orig)
        if any(v == OTHER for v in mapping.values()):
            n = sum(v == OTHER for v in mapping.values())
            notes.append(f"{n} {_col_label(col).lower()} below {cfg.min_share:.1%} of the total are folded into “Other”.")

    def label(col: str, key: str) -> str:
        if key == OTHER:
            n = len(members.get((col, key), []))
            return f"Other ({n})"
        if col.startswith("source"):
            return source_label(meta, key)
        return meta.taxonomy[key].short_label

    node_rows, link_rows = [], []
    for col in columns:
        g = f.groupby(col)["value"].sum().sort_values(ascending=False)
        for key, v in g.items():
            node_rows.append(dict(node_id=f"{col}:{key}", column=col, key=key, label=label(col, key), value=v,
                                  members=members.get((col, key), [key])))
    nodes = pd.DataFrame(node_rows)
    agg = {"value": ("value", "sum"), "amount": ("amount", "sum"), "paid": ("paid", "sum"),
           "consumer": ("consumer", "sum"), "gain": ("gain", "sum"), "instances": ("instances", "sum"),
           "inst_rows": ("instances", "count"), "rows": ("value", "size")}
    for a, b in zip(columns, columns[1:]):
        lk = f.groupby([a, b]).agg(**agg).reset_index()
        lk["source"] = a + ":" + lk[a].astype(str)
        lk["target"] = b + ":" + lk[b].astype(str)
        lk["from_column"], lk["to_column"] = a, b
        link_rows.append(lk.drop(columns=[a, b]))
    links = pd.concat(link_rows, ignore_index=True) if link_rows else pd.DataFrame()
    if len(links):
        out_tot = links.groupby("source")["value"].transform("sum")
        in_tot = links.groupby("target")["value"].transform("sum")
        links["share_of_source"] = links["value"] / out_tot
        links["share_of_target"] = links["value"] / in_tot
        links["share_of_total"] = links["value"] / total if total else np.nan
        links["has_all_instances"] = links["inst_rows"] == links["rows"]
    if cfg.metric == "gain" and f["margin"].isna().any():
        notes.append("Gain excludes flows into pools without margin data.")
    if cfg.split_consumer and cfg.metric != "allowed":
        notes.append("The consumer split applies to the allowed-spend metric only.")
    return SankeyView(columns, nodes, links, total, f, notes)


def _col_label(col: str) -> str:
    return {"source_group": "Source groups", "source_detail": "Sources", "recipient_layer": "Recipient layers",
            "recipient": "Recipients"}[col]


COLUMN_LABELS = {c: _col_label(c) for c in (*SOURCE_COLS, *RECIPIENT_COLS)}


def node_detail(view: SankeyView, meta: Metadata, result: ModelResult, dataset: str, year: int, geo: Geo,
                node_id: str) -> dict:
    """Inspector facts for a node: totals, counterparts, members/PMPM (payer), pool economics (recipient)."""
    n = view.nodes.set_index("node_id").loc[node_id]
    col, key = n["column"], n["key"]
    f = view.detail[view.detail[col] == key]
    out = {"label": n["label"], "column": col, "value": float(n["value"]),
           "share_of_total": float(n["value"]) / view.total if view.total else np.nan,
           "members": n["members"], "methods": sorted(f["flow_kind"].unique()),
           "amount": float(f["amount"].sum()), "paid": float(f["paid"].sum()), "consumer": float(f["consumer"].sum())}
    if len(view.links):
        side = "target" if col.startswith("source") else "source"
        own = "source" if side == "target" else "target"
        cp = view.links[view.links[own] == node_id].copy()
        cp["label"] = cp[side].map(view.nodes.set_index("node_id")["label"])
        out["counterparts"] = cp.sort_values("value", ascending=False)[["label", "value"]]
    if col.startswith("source"):
        lobs = [m for m in n["members"] if m in meta.lobs.lobs or m in meta.lobs.groups]
        lob_ids = {l for m in lobs for l in ([m] if m in meta.lobs.lobs else meta.lobs.in_group(m))}
        if lob_ids:
            p = result.pools
            p = p[(p["dataset"] == dataset) & (p["year"] == year) & _geo_mask(p, geo) & p["lob"].isin(lob_ids)]
            mm = float(p["members"].sum())
            if mm > 0:
                out["enrollment"] = mm
                out["pmpm"] = out["amount"] / (mm * 12)
                out["paid_pmpm"] = out["paid"] / (mm * 12)
    else:
        leaves = {l for m in n["members"] if m in meta.taxonomy for l in meta.taxonomy.leaf_ids(m)}
        p = result.pools
        p = p[(p["dataset"] == dataset) & (p["year"] == year) & _geo_mask(p, geo) & p["node_id"].isin(leaves)]
        rev = float(p["revenue"].sum())
        known = p[p["margin_known"]]
        out["pool_revenue"] = rev
        out["pool_gain"] = float(known["gain"].sum())
        out["pool_margin"] = out["pool_gain"] / float(known["revenue"].sum()) if known["revenue"].sum() > 0 else np.nan
        out["share_of_pool_revenue"] = out["amount"] / rev if rev > 0 else np.nan
    if f["instances"].notna().all() and len(f):
        out["instances"] = float(f["instances"].sum())
        out["allowed_per_instance"] = out["amount"] / out["instances"] if out["instances"] > 0 else np.nan
    return out


def compare_links(view: SankeyView, base_view: SankeyView) -> pd.Series:
    """Δ value per link (scenario − baseline), aligned to view.links; links absent in baseline compare to 0."""
    if not len(view.links):
        return pd.Series(dtype=float)
    b = base_view.links.set_index(["source", "target"])["value"] if len(base_view.links) else pd.Series(dtype=float)
    idx = pd.MultiIndex.from_frame(view.links[["source", "target"]])
    return pd.Series(view.links["value"].to_numpy() - b.reindex(idx).fillna(0).to_numpy(), index=view.links.index)


FLOW_METHOD = {
    ("payer_claims", "MARKET"): "Payer claims: market enrollment × PMPM benchmark (from enterprise experience)",
    ("payer_claims", "ENTERPRISE"): "Payer claims: observed enterprise allowed amounts",
    ("funding_pmpm", "MARKET"): "Population × estimated PMPM (uninsured / government FFS)",
    ("supplemental", "MARKET"): "Supplemental & state payments: annual estimate by state (hospitals only)",
}


def _lob_members(meta: Metadata, result: ModelResult, dataset: str, year: int, geo: Geo,
                 keys: list[str]) -> float:
    lob_ids = {l for m in keys for l in ([m] if m in meta.lobs.lobs else meta.lobs.in_group(m))
               if m in meta.lobs.lobs or m in meta.lobs.groups}
    if not lob_ids:
        return 0.0
    p = result.pools
    p = p[(p["dataset"] == dataset) & (p["year"] == year) & _geo_mask(p, geo) & p["lob"].isin(lob_ids)]
    return float(p["members"].sum())


def link_detail(view: SankeyView, meta: Metadata, result: ModelResult, dataset: str, year: int, geo: Geo,
                source: str, target: str) -> dict:
    """Inspector facts for one link (possibly a roll-up of many underlying flows)."""
    nodes = view.nodes.set_index("node_id")
    a, b = nodes.loc[source], nodes.loc[target]
    f = view.detail[(view.detail[a["column"]] == a["key"]) & (view.detail[b["column"]] == b["key"])]
    lk = view.links[(view.links["source"] == source) & (view.links["target"] == target)].iloc[0]
    out = {"from": a["label"], "to": b["label"], "value": float(lk["value"]),
           "share_of_source": float(lk["share_of_source"]), "share_of_target": float(lk["share_of_target"]),
           "share_of_total": float(lk["share_of_total"]), "amount": float(f["amount"].sum()),
           "paid": float(f["paid"].sum()), "consumer": float(f["consumer"].sum()),
           "gain": float(f["gain"].sum()), "rows": int(len(f)),
           "methods": sorted({FLOW_METHOD.get((k, dataset), k) for k in f["flow_kind"].unique()}),
           "underlying_sources": sorted({source_label(meta, x) for x in f["detail"].unique()}),
           "underlying_recipients": sorted({meta.taxonomy[t].short_label for t in f["target_id"].unique()})}
    known = f[f["margin"].notna()]
    out["margin"] = float(known["gain"].sum() / known["amount"].sum()) if known["amount"].sum() > 0 else np.nan
    if f["instances"].notna().all() and len(f) and f["instances"].sum() > 0:
        out["instances"] = float(f["instances"].sum())
        out["allowed_per_instance"] = out["amount"] / out["instances"]
    src_keys = a["members"] if a["column"].startswith("source") else []
    # Per-member measures only when every underlying source is a payer LOB (not funding / consumers).
    if src_keys and set(f["source_kind"]) == {"payer"} and CONSUMER not in src_keys:
        mm = _lob_members(meta, result, dataset, year, geo, list(f["detail"].unique()))
        if mm > 0:
            out["enrollment"] = mm
            out["pmpm"] = out["amount"] / (mm * 12)
    return out
