"""The single financial engine. Every chart, KPI, table and export reads its
output (`ModelResult`); nothing else computes financials.

Stages (ARCHITECTURE.md §5). Phase 2 runs them with no scenario assumptions;
the scenario engine (Phase 4) modifies the driver cube between stages.

  1  growth                   cube.project()          forecast-year driver levels
  5  payer financials         revenue = premium PMPM × mm; opex = opex PMPM × mm
  6  flows                    payer / funding flows = allowed PMPM × members|population × 12
                              paid = allowed × paid ratio; consumer = allowed − paid
                              supplemental = annual amounts (fully program-paid)
     payer claims             = Σ PAID payer flows;  gain = revenue − paid claims − opex
  7  recipient financials     revenue = Σ inflows (county grain where flows are county;
                              supplemental at state grain); gain = revenue × pool margin
  8  top-down                 gain = revenue × margin
  9  assemble                 one `pools` frame + one `flows` frame
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from profit_pools.core.data.adapters import DataAdapter
from profit_pools.core.model.cube import POP_KEY, DriverCube, _denominators, build_history, project
from profit_pools.core.taxonomy import Metadata

POOL_COLUMNS = [
    "dataset", "year", "is_forecast", "geo_level", "state", "county", "node_id", "layer_id", "lob",
    "members", "revenue", "revenue_paid", "allowed", "claims", "opex", "gain", "margin_known", "source_type",
]
FLOW_COLUMNS = [
    "dataset", "year", "is_forecast", "state", "county", "source_id", "source_kind", "flow_kind",
    "lob", "category", "target_id", "amount", "paid", "consumer", "instances", "margin", "gain",
]


@dataclass(frozen=True)
class ModelResult:
    """Engine output. Grain of `pools`:
      payer      dataset × year × county × payer leaf
      recipient  dataset × year × state × county(or None for state-level supplemental) × leaf
      top-down   dataset × year × national × leaf
    `gain` is NaN where the margin is unavailable (margin_known = False).

    Payer rows: `claims` = payer-paid claims, `allowed` = allowed spend (claims + member cost share).
    Recipient rows: `revenue` = allowed receipts, `revenue_paid` = payer/program-paid part.
    `flows.gain` = flow amount × target pool margin: the recipient gain associated with the flow
    (sums to recipient gain); `flows.paid` / `flows.consumer` split each flow by who pays.
    """

    pools: pd.DataFrame
    flows: pd.DataFrame
    base_year: int
    scenario_id: str | None = None


def run(cube: DriverCube, meta: Metadata, scenario_id: str | None = None) -> ModelResult:
    tax = meta.taxonomy
    base_year = cube.base_year
    payer_src = {d: ds.payer_source_type for d, ds in meta.datasets.items()}

    # Stage 5 — payer revenue and residual opex from drivers.
    payer = cube.payer.copy()
    mm12 = payer["members"] * 12
    payer["revenue"] = payer["premium_pmpm"] * mm12
    payer["opex"] = payer["opex_pmpm"] * mm12

    # Stage 6 — flows.
    spend = cube.spend.merge(_denominators(cube.payer, cube.population), on=POP_KEY,
                             how="left", validate="many_to_one")
    spend["amount"] = spend["allowed_pmpm"] * spend["denom"] * 12
    spend["paid"] = spend["amount"] * spend["paid_ratio"]
    spend["instances"] = spend["inst_per_member"] * spend["denom"]
    spend["flow_kind"] = np.where(spend["source_kind"] == "payer", "payer_claims", "funding_pmpm")
    supp = cube.supplemental.assign(county=None, source_kind="funding", flow_kind="supplemental",
                                    lob=None, category=None, instances=np.nan,
                                    paid=cube.supplemental["amount"])
    flows = pd.concat([spend.drop(columns=["denom", "allowed_pmpm", "paid_ratio", "inst_per_member"]), supp],
                      ignore_index=True)
    flows["consumer"] = flows["amount"] - flows["paid"]
    flows["is_forecast"] = flows["year"] > base_year
    flows = flows.merge(cube.recipient_margin.rename(columns={"node_id": "target_id"}),
                        on=["dataset", "year", "state", "target_id"], how="left", validate="many_to_one")
    flows["gain"] = flows["amount"] * flows["margin"]
    flows = flows[FLOW_COLUMNS]

    sums = (flows[flows["flow_kind"] == "payer_claims"]
            .groupby(["dataset", "year", "state", "county", "source_id"])[["paid", "amount"]].sum()
            .rename(columns={"paid": "claims", "amount": "allowed"}))
    sums.index.names = ["dataset", "year", "state", "county", "node_id"]
    payer = payer.merge(sums.reset_index(), on=["dataset", "year", "state", "county", "node_id"], how="left")
    payer[["claims", "allowed"]] = payer[["claims", "allowed"]].fillna(0.0)
    payer["gain"] = payer["revenue"] - payer["claims"] - payer["opex"]
    payer_rows = pd.DataFrame({
        "dataset": payer["dataset"], "year": payer["year"], "geo_level": "county",
        "state": payer["state"], "county": payer["county"], "node_id": payer["node_id"],
        "lob": payer["lob"], "members": payer["members"], "revenue": payer["revenue"],
        "revenue_paid": np.nan, "allowed": payer["allowed"], "claims": payer["claims"], "opex": payer["opex"], "gain": payer["gain"], "margin_known": True,
        "source_type": payer["dataset"].map(payer_src),
    })

    # Stage 7 — recipients: revenue = Σ inflows at the flow grain; gain at the pool margin.
    rec = (flows.groupby(["dataset", "year", "state", "county", "target_id"], dropna=False)[["amount", "paid"]]
           .sum().rename(columns={"amount": "revenue", "paid": "revenue_paid"}).reset_index()
           .rename(columns={"target_id": "node_id"}))
    rec = rec.merge(cube.recipient_margin, on=["dataset", "year", "state", "node_id"], how="left",
                    validate="many_to_one")
    rec["gain"] = rec["revenue"] * rec["margin"]
    rec_rows = pd.DataFrame({
        "dataset": rec["dataset"], "year": rec["year"],
        "geo_level": np.where(rec["county"].isna(), "state", "county"),
        "state": rec["state"], "county": rec["county"], "node_id": rec["node_id"], "lob": None,
        "members": np.nan, "revenue": rec["revenue"], "revenue_paid": rec["revenue_paid"],
        "allowed": np.nan, "claims": np.nan, "opex": rec["revenue"] - rec["gain"], "gain": rec["gain"], "margin_known": rec["margin"].notna(),
        "source_type": "allocated",
    })

    # Stage 8 — top-down.
    td = cube.top_down
    td_rows = pd.DataFrame({
        "dataset": td["dataset"], "year": td["year"], "geo_level": "national",
        "state": meta.geography.national_id, "county": None, "node_id": td["node_id"], "lob": None,
        "members": np.nan, "revenue": td["revenue"], "revenue_paid": np.nan, "allowed": np.nan,
        "claims": np.nan,
        "opex": td["revenue"] * (1 - td["margin"]), "gain": td["revenue"] * td["margin"],
        "margin_known": td["margin"].notna(), "source_type": "estimated",
    })

    # Stage 9 — assemble.
    pools = pd.concat([payer_rows, rec_rows, td_rows], ignore_index=True)
    pools["layer_id"] = pools["node_id"].map(lambda i: tax[i].layer_id)
    pools["is_forecast"] = pools["year"] > base_year
    pools["county"] = pools["county"].where(pools["county"].notna(), None)
    pools = pools[POOL_COLUMNS]
    return ModelResult(pools=pools, flows=flows, base_year=base_year, scenario_id=scenario_id)


def run_baseline(adapter: DataAdapter) -> ModelResult:
    """History + baseline forecast, no assumptions."""
    meta = adapter.metadata
    return run(project(build_history(adapter), meta), meta)
