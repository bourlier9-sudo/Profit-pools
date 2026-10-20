"""'Follow the enterprise dollar' — the integrated enterprise's P&L as a flow (Phase 9).

One currency throughout (enterprise dollars), read from `ModelResult` only:

  plan segment premium ─┬─► outside providers        (paid claims not routed to owned assets)
                        ├─► owned asset              (paid claims routed to it)
                        ├─► plan admin cost          (residual opex)
                        └─► enterprise operating gain (plan gain, when positive)
  members' cost sharing ──► owned asset
  other payers' patients ─► owned asset
  owned asset ─┬─► owned-asset costs
               └─► enterprise operating gain          (owned gain, when positive)

A plan segment or asset with a loss cannot fund all of its costs: the shortfall flows from a "Losses" source
straight to the cost node, so plans stay in the first column, every node balances, and the
enterprise-gain node's label is the net consolidated gain (= ownership_view totals).
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from profit_pools.core.model import ownership as own_mod
from profit_pools.core.model.engine import ModelResult
from profit_pools.core.model.views import Geo, _geo_mask
from profit_pools.core.taxonomy import Metadata

GAIN = "ENT:GAIN"
EXTERNAL = "ENT:EXTERNAL"
ADMIN = "ENT:ADMIN"
OWNED_COST = "ENT:OWNED_COST"
MEMBER_CS = "ENT:MEMBER_CS"
THIRD_PARTY = "ENT:THIRD_PARTY"
LOSSES = "ENT:LOSSES"
FIXED_LABELS = {GAIN: "Enterprise operating gain", EXTERNAL: "Outside providers (claims)",
                ADMIN: "Plan admin cost", OWNED_COST: "Owned-asset costs",
                MEMBER_CS: "Members' cost sharing", THIRD_PARTY: "Other payers' patients",
                LOSSES: "Losses (funded by the enterprise)"}
# column: 0 = sources, 1 = owned assets, 2 = outcomes (Plotly lays columns out by link structure)
KIND = {GAIN: "gain", EXTERNAL: "cost", ADMIN: "cost", OWNED_COST: "cost", MEMBER_CS: "source",
        THIRD_PARTY: "source", LOSSES: "loss"}


@dataclass
class EnterpriseFlow:
    nodes: pd.DataFrame          # node_id, label, kind (segment | asset | source | cost | gain | loss), value
    links: pd.DataFrame          # source, target, value, kind
    totals: dict                 # premium, consolidated_gain, plan_gain, owned_gain, …
    notes: list[str] = field(default_factory=list)
    note: str | None = None      # why the view is empty


def build(result: ModelResult, meta: Metadata, year: int, geo: Geo = Geo(), detail: str = "group",
          focus: str = "pnl") -> EnterpriseFlow:
    """`detail`: "group" = plan groups one level below the Payer layer; "plan" = plan segments (leaves).
    `focus`: "pnl" = every premium dollar; "gain" = only who contributes operating gain — each plan segment and
    owned asset flows into enterprise operating gain, loss-makers into the Losses node (same totals)."""
    full = _build(result, meta, year, geo, detail)
    if focus == "pnl" or full.note:
        return full
    L = full.links
    keep = L[(L["target"] == GAIN)].copy()
    # Loss-making plans / assets: |gain| from the entity to the Losses node (reads as a drag on the total).
    seg_gain = {n: v for n, v in full.totals.get("_segment_gain", {}).items() if v < 0}
    asset_gain = {n: v for n, v in full.totals.get("_asset_gain", {}).items() if v < 0}
    neg = [(n, LOSSES, -v, "loss") for n, v in {**seg_gain, **asset_gain}.items()]
    L2 = pd.concat([keep, pd.DataFrame(neg, columns=["source", "target", "value", "kind"])], ignore_index=True)
    L2 = L2[L2["value"] > 0.5].reset_index(drop=True)
    ids = set(L2["source"]) | set(L2["target"])
    nodes = full.nodes[full.nodes["node_id"].isin(ids)].copy()
    out_v = L2.groupby("source")["value"].sum()
    nodes["value"] = [full.totals["consolidated_gain"] if n == GAIN else
                      (full.totals["losses_gain_view"] if n == LOSSES else float(out_v.get(n, 0.0)))
                      for n in nodes["node_id"]]
    nodes.loc[nodes["node_id"] == LOSSES, "kind"] = "cost"
    nodes.loc[nodes["node_id"] == LOSSES, "label"] = "Losses"
    notes = ["Operating gain only: each plan and owned asset that makes money flows into enterprise operating gain; "
             "loss-makers flow to Losses. Enterprise gain = contributions − losses."]
    return EnterpriseFlow(nodes.reset_index(drop=True), L2, full.totals, notes)


def _build(result: ModelResult, meta: Metadata, year: int, geo: Geo, detail: str) -> EnterpriseFlow:
    ds = own_mod.dataset()
    if geo.level == "county":
        return EnterpriseFlow(pd.DataFrame(), pd.DataFrame(), {}, note="The enterprise P&L is tracked by state; "
                                                                        "choose a state or National.")
    tax = meta.taxonomy
    payer_layer = next((l.id for l in tax.layers() if all(x.economics == "payer_pnl" for x in tax.leaves(l.id))), None)
    p = result.pools
    p = p[(p["dataset"] == ds) & (p["year"] == year) & _geo_mask(p, geo) & p["members"].notna()]
    if p.empty or payer_layer is None:
        return EnterpriseFlow(pd.DataFrame(), pd.DataFrame(), {}, note="No enterprise plans in this view.")

    def seg_of(leaf: str) -> str:
        path = tax.path(leaf)
        return path[min(1, len(path) - 1)].id if detail == "group" else leaf

    seg = p.assign(seg=p["node_id"].map(seg_of)).groupby("seg")[["revenue", "claims", "opex", "gain"]].sum()

    o = result.ownership
    o = o[(o["dataset"] == ds) & (o["year"] == year)]
    if geo.level == "state":
        o = o[o["state"] == geo.id]
    # Paid claims routed to each owned asset, by plan segment: routing share × paid flow (same rule as ownership.py).
    f = result.flows
    f = f[(f["dataset"] == ds) & (f["year"] == year) & (f["flow_kind"] == "payer_claims") & _geo_mask(f, geo)]
    f = f.merge(o[["state", "node_id", "asset_id", "routing_share"]].rename(columns={"node_id": "target_id"}),
                on=["state", "target_id"], how="inner")
    f["routed_paid"] = f["paid"] * f["routing_share"].clip(0, 1)
    f["seg"] = f["source_id"].map(seg_of)
    to_asset = f.groupby(["seg", "asset_id"])["routed_paid"].sum()

    labels = {a.id: a.label for a in own_mod.assets(meta)}
    links: list[tuple[str, str, float, str]] = []
    loss_total = 0.0
    for s, r in seg.iterrows():
        routed = float(to_asset.loc[s].sum()) if s in to_asset.index.get_level_values(0) else 0.0
        loss = max(-float(r["gain"]), 0.0)
        # A loss-making segment's premium cannot cover its costs: the shortfall reaches outside providers
        # from the Losses node, so the segment stays a source and every node balances.
        links.append((s, EXTERNAL, float(r["claims"]) - routed - loss, "claims"))
        if loss:
            links.append((LOSSES, EXTERNAL, loss, "loss"))
            loss_total += loss
        if s in to_asset.index.get_level_values(0):
            for a, v in to_asset.loc[s].items():
                links.append((s, a, float(v), "claims"))
        links.append((s, ADMIN, float(r["opex"]), "cost"))
        if r["gain"] > 0:
            links.append((s, GAIN, float(r["gain"]), "gain"))
    og = o.groupby("asset_id")[["routed_consumer", "third_party", "owned_revenue", "owned_gain"]].sum(min_count=1)
    for a, r in og.iterrows():
        links.append((MEMBER_CS, a, float(r["routed_consumer"]), "source"))
        links.append((THIRD_PARTY, a, float(r["third_party"]), "source"))
        g = 0.0 if pd.isna(r["owned_gain"]) else float(r["owned_gain"])
        links.append((a, OWNED_COST, float(r["owned_revenue"]) - max(g, 0.0), "cost"))   # owned opex (its share)
        if g > 0:
            links.append((a, GAIN, g, "gain"))
        elif g < 0:
            links.append((LOSSES, OWNED_COST, -g, "loss"))
            loss_total += -g
    L = pd.DataFrame(links, columns=["source", "target", "value", "kind"])
    L = L[L["value"] > 0.5].reset_index(drop=True)

    plan_gain = float(seg["gain"].sum())
    owned_gain = float(og["owned_gain"].sum()) if len(og) and og["owned_gain"].notna().any() else 0.0
    a_gain = {a: (0.0 if pd.isna(r["owned_gain"]) else float(r["owned_gain"])) for a, r in og.iterrows()}
    s_gain = {s: float(r["gain"]) for s, r in seg.iterrows()}
    totals = {"premium": float(seg["revenue"].sum()), "plan_gain": plan_gain, "owned_gain": owned_gain,
              "consolidated_gain": plan_gain + owned_gain, "losses": loss_total,
              "owned_revenue": float(og["owned_revenue"].sum()) if len(og) else 0.0,
              "_segment_gain": s_gain, "_asset_gain": a_gain,
              "losses_gain_view": -sum(v for v in [*s_gain.values(), *a_gain.values()] if v < 0)}

    ids = list(dict.fromkeys([*L["source"], *L["target"]]))
    out_v = L.groupby("source")["value"].sum()
    in_v = L.groupby("target")["value"].sum()
    rows = []
    for n in ids:
        if n in FIXED_LABELS:
            label, kind = FIXED_LABELS[n], KIND[n]
        elif n in labels:
            label, kind = labels[n], "asset"
        else:
            label, kind = tax[n].short_label, "segment"
        value = totals["consolidated_gain"] if n == GAIN else float(max(out_v.get(n, 0.0), in_v.get(n, 0.0)))
        rows.append((n, label, kind, value))
    nodes = pd.DataFrame(rows, columns=["node_id", "label", "kind", "value"])
    notes = []
    if loss_total > 0:
        notes.append(f"Plans or assets with a loss ({_m(loss_total)}): their costs exceed revenue, and the shortfall "
                     f"flows from the red 'Losses' node. The enterprise-gain label is net of these losses.")
    if len(og) and og["owned_gain"].isna().any():
        notes.append("Some owned assets have no margin data here; their revenue is shown as cost.")
    return EnterpriseFlow(nodes, L, totals, notes)


def _m(x: float) -> str:
    return f"${x / 1e9:,.1f}B" if abs(x) >= 1e9 else f"${x / 1e6:,.0f}M"


def compare(view: EnterpriseFlow, base: EnterpriseFlow) -> pd.Series:
    """Δ value per link (scenario − baseline), aligned to view.links."""
    if view.links.empty:
        return pd.Series(dtype=float)
    b = base.links.set_index(["source", "target"])["value"] if not base.links.empty else pd.Series(dtype=float)
    idx = pd.MultiIndex.from_frame(view.links[["source", "target"]])
    return pd.Series(view.links["value"].to_numpy() - b.reindex(idx).fillna(0).to_numpy(), index=view.links.index)


def table(view: EnterpriseFlow) -> pd.DataFrame:
    lab = view.nodes.set_index("node_id")["label"]
    return pd.DataFrame({"From": view.links["source"].map(lab), "To": view.links["target"].map(lab),
                         "Amount": view.links["value"], "Kind": view.links["kind"]})

