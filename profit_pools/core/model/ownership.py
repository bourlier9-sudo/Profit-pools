"""Ownership and value capture (Phase 4b). Definitions live in config/ownership.yaml.

`drivers()` builds the ownership driver frame (asset × state × year) that the scenario engine adjusts
(routing_share, owned_margin). `value_capture()` is the engine stage that turns those drivers and the
enterprise flows into owned-asset economics. Routing never changes spending or pool revenue; it only
splits enterprise spend between the owned asset and external providers in the same pool.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from profit_pools.core.config import load_config
from profit_pools.core.taxonomy import Metadata, TaxonomyError

OWN_KEY = ["dataset", "year", "state", "asset_id", "node_id"]
OUTPUT_COLUMNS = OWN_KEY + [
    "enterprise_spend", "routing_share", "third_party_ratio", "routed", "routed_paid", "routed_consumer", "third_party",
    "owned_revenue", "owned_margin", "market_margin", "owned_gain", "external_spend"]


@dataclass(frozen=True)
class Asset:
    id: str
    label: str
    node_id: str
    states: tuple[str, ...]
    routing_share: float
    margin_offset: float
    third_party_ratio: float


def config() -> dict:
    return load_config("ownership")


def assets(meta: Metadata) -> list[Asset]:
    """Owned assets, validated against the standard taxonomy and geography."""
    cfg = config()
    out = []
    for a in cfg.get("assets", []):
        node = a["node_id"]
        if node not in meta.standard or meta.standard[node].children or not meta.standard[node].receives_flows:
            raise TaxonomyError(f"Owned asset {a['id']}: {node} must be a provider or pharmacy leaf that receives flows.")
        bad = [s for s in a["states"] if s not in meta.geography.states]
        # An owned asset serves the enterprise's members, so it only operates where the enterprise has plans
        # (checked against the data in tests/test_ownership.py).
        if bad:
            raise TaxonomyError(f"Owned asset {a['id']}: unknown states {bad}")
        if not 0 <= a["routing_share"] <= 1 or a["third_party_ratio"] < 0:
            raise TaxonomyError(f"Owned asset {a['id']}: routing share must be 0–100% and third-party ratio ≥ 0.")
        out.append(Asset(a["id"], a["label"], node, tuple(a["states"]), float(a["routing_share"]),
                         float(a["margin_offset"]), float(a["third_party_ratio"])))
    return out


def dataset() -> str:
    return config()["organization"]["dataset"]


def owned_leaves(meta: Metadata) -> set[str]:
    return {a.node_id for a in assets(meta)}


def drivers(meta: Metadata, recipient_margin: pd.DataFrame, years: list[int]) -> pd.DataFrame:
    """Driver frame: one row per asset × state × year. owned_margin = the leaf's market margin in that
    state and year + the asset's offset (NaN where the market margin is unavailable)."""
    ds = dataset()
    rows = [(ds, y, s, a.id, a.node_id, a.routing_share, a.margin_offset, a.third_party_ratio)
            for a in assets(meta) for s in a.states for y in years]
    df = pd.DataFrame(rows, columns=["dataset", "year", "state", "asset_id", "node_id", "routing_share",
                                     "margin_offset", "third_party_ratio"])
    mk = recipient_margin[recipient_margin["dataset"] == "MARKET"][["year", "state", "node_id", "margin"]]
    df = df.merge(mk.rename(columns={"margin": "market_margin"}), on=["year", "state", "node_id"], how="left",
                  validate="many_to_one")
    df["owned_margin"] = df["market_margin"] + df["margin_offset"]
    return df.drop(columns="margin_offset")


def value_capture(flows: pd.DataFrame, own: pd.DataFrame | None) -> pd.DataFrame:
    """Engine stage: owned-asset economics from enterprise payer-claims inflows (state grain)."""
    if own is None or own.empty:
        return pd.DataFrame(columns=OUTPUT_COLUMNS)
    f = flows[(flows["flow_kind"] == "payer_claims") & flows["dataset"].isin(own["dataset"].unique())
              & flows["target_id"].isin(own["node_id"].unique())]
    spend = (f.groupby(["dataset", "year", "state", "target_id"])[["amount", "paid", "consumer"]].sum()
             .reset_index().rename(columns={"target_id": "node_id"}))
    df = own.merge(spend, on=["dataset", "year", "state", "node_id"], how="left", validate="many_to_one")
    df[["amount", "paid", "consumer"]] = df[["amount", "paid", "consumer"]].fillna(0.0)
    rs = df["routing_share"].clip(0, 1)
    df["enterprise_spend"] = df["amount"]
    df["routed"] = rs * df["amount"]
    df["routed_paid"] = rs * df["paid"]
    df["routed_consumer"] = rs * df["consumer"]
    df["third_party"] = df["third_party_ratio"] * df["routed"]
    df["owned_revenue"] = df["routed"] + df["third_party"]
    df["owned_gain"] = df["owned_revenue"] * df["owned_margin"]          # NaN when margin unavailable
    df["external_spend"] = df["amount"] - df["routed"]
    return df[OUTPUT_COLUMNS].reset_index(drop=True)


def consolidate(own: pd.DataFrame, payer: pd.DataFrame) -> dict[str, float]:
    """Consolidated P&L lines from owned-asset rows and enterprise payer pool rows (already filtered to
    one year and geography). Identity: consolidated gain = payer gain + owned gain."""
    pr = float(payer["revenue"].sum())
    claims = float(payer["claims"].sum())
    popex = float(payer["opex"].sum())
    pgain = float(payer["gain"].sum())
    known = own["owned_gain"].notna()
    rev_k = float(own.loc[known, "owned_revenue"].sum())
    out = {
        "payer_revenue": pr, "payer_claims": claims, "payer_opex": popex, "payer_gain": pgain,
        "owned_revenue": float(own["owned_revenue"].sum()),
        "owned_internal_paid": float(own["routed_paid"].sum()),
        "owned_member_paid": float(own["routed_consumer"].sum()),
        "owned_third_party": float(own["third_party"].sum()),
        "owned_gain": float(own["owned_gain"].sum()) if known.any() else np.nan,
        "owned_margin_coverage": rev_k / float(own["owned_revenue"].sum()) if own["owned_revenue"].sum() > 0 else np.nan,
        "enterprise_spend": float(own["enterprise_spend"].sum()),
        "routed": float(own["routed"].sum()),
        "external_spend": float(own["external_spend"].sum()),
    }
    out["elimination"] = out["owned_internal_paid"]
    out["consolidated_revenue"] = pr + out["owned_revenue"] - out["elimination"]
    og = 0.0 if np.isnan(out["owned_gain"]) else out["owned_gain"]
    out["owned_opex"] = float(own.loc[known, "owned_revenue"].sum()) - og
    out["consolidated_expense"] = claims - out["elimination"] + popex + out["owned_opex"] + float(
        own.loc[~known, "owned_revenue"].sum())
    out["consolidated_gain"] = pgain + og
    out["consolidated_margin"] = out["consolidated_gain"] / out["consolidated_revenue"] if out["consolidated_revenue"] > 0 else np.nan
    out["routed_share"] = out["routed"] / out["enterprise_spend"] if out["enterprise_spend"] > 0 else np.nan
    return out
