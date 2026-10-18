"""Driver cube: the engine's only input.

Facts are converted to *drivers* (members, PMPMs, instances per member,
populations, supplemental amounts, margins, top-down levels) at their finest
grain. Derived values (revenue, claims, gain, flows) are never stored here;
the engine recomputes them, so history years reproduce the facts exactly and
forecast years follow the same identities.

Forecast years are projected from base-year drivers with annual rates from
baseline_forecast.yaml (or overrides supplied by the scenario engine).
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Callable, Mapping

import numpy as np
import pandas as pd

from profit_pools.core.config import load_config
from profit_pools.core.data.adapters import DataAdapter
from profit_pools.core.taxonomy import Metadata

PAYER_KEY = ["dataset", "year", "state", "county", "node_id"]
SPEND_KEY = ["dataset", "year", "state", "county", "source_id", "category"]
POP_KEY = ["dataset", "year", "state", "county", "source_id"]
SUPP_KEY = ["dataset", "year", "state", "source_id", "target_id"]
MARGIN_KEY = ["dataset", "year", "state", "node_id"]
TOP_KEY = ["dataset", "year", "node_id"]


@dataclass(frozen=True)
class DriverCube:
    payer: pd.DataFrame             # PAYER_KEY + lob, members, premium_pmpm, opex_pmpm
    spend: pd.DataFrame             # SPEND_KEY + source_kind, lob, target_id, allowed_pmpm, paid_ratio, inst_per_member
    population: pd.DataFrame        # POP_KEY + population
    supplemental: pd.DataFrame      # SUPP_KEY + amount
    recipient_margin: pd.DataFrame  # MARGIN_KEY + margin (NaN = unavailable)
    top_down: pd.DataFrame          # TOP_KEY + revenue, margin
    base_year: int
    last_year: int

    def frames(self) -> dict[str, pd.DataFrame]:
        return {k: getattr(self, k) for k in
                ("payer", "spend", "population", "supplemental", "recipient_margin", "top_down")}


def _na_str(s: pd.Series) -> pd.Series:
    return s.where(s.notna(), None)


def build_history(adapter: DataAdapter) -> DriverCube:
    """Drivers for the historical years, straight from the contract tables."""
    meta = adapter.metadata
    tax = meta.taxonomy
    m = adapter.pool_metrics()
    f = adapter.value_flows()
    pop = adapter.coverage_population()
    econ = m["NODE_ID"].map(lambda i: tax[i].economics)

    p = m[econ == "payer_pnl"]
    mm12 = p["ENROLLMENT"] * 12
    payer = pd.DataFrame({
        "dataset": p["DATASET"], "year": p["YEAR"], "state": p["STATE"], "county": p["COUNTY"],
        "node_id": p["NODE_ID"], "lob": p["LOB"], "members": p["ENROLLMENT"],
        "premium_pmpm": p["REVENUE"] / mm12,
        # Residual opex is derived against PAID claims (allowed − member cost share).
        "opex_pmpm": (p["REVENUE"] - p["CLAIMS_EXPENSE"] - p["OPERATING_GAIN"]) / mm12,
    }).reset_index(drop=True)

    population = pop.rename(columns={"DATASET": "dataset", "YEAR": "year", "STATE": "state",
                                     "COUNTY": "county", "FUNDING_SOURCE_ID": "source_id",
                                     "POPULATION": "population"})[[*POP_KEY, "population"]]

    claims_like = f[f["FLOW_KIND"] != "supplemental"]
    sp = pd.DataFrame({
        "dataset": claims_like["DATASET"], "year": claims_like["YEAR"], "state": claims_like["STATE"],
        "county": claims_like["COUNTY"], "source_id": claims_like["SOURCE_NODE_ID"],
        "category": claims_like["SPENDING_CATEGORY"],
        "source_kind": np.where(claims_like["FLOW_KIND"] == "payer_claims", "payer", "funding"),
        "lob": _na_str(claims_like["PAYER_LOB"]), "target_id": claims_like["TARGET_NODE_ID"],
        "amount": claims_like["ANNUAL_FLOW_AMOUNT"], "paid": claims_like["PAID_AMOUNT"],
        "instances": claims_like["INSTANCE_COUNT"],
    })
    denom = _denominators(payer, population)
    sp = sp.merge(denom, on=POP_KEY, how="left", validate="many_to_one")
    if sp["denom"].isna().any():
        raise ValueError("Spending cells without a member/population denominator")
    sp["allowed_pmpm"] = sp["amount"] / (sp["denom"] * 12)
    sp["paid_ratio"] = np.where(sp["amount"] > 0, sp["paid"] / sp["amount"], 1.0)
    sp["inst_per_member"] = sp["instances"] / sp["denom"]
    spend = sp[[*SPEND_KEY, "source_kind", "lob", "target_id", "allowed_pmpm", "paid_ratio", "inst_per_member"]]

    s = f[f["FLOW_KIND"] == "supplemental"]
    supplemental = pd.DataFrame({
        "dataset": s["DATASET"], "year": s["YEAR"], "state": s["STATE"], "source_id": s["SOURCE_NODE_ID"],
        "target_id": s["TARGET_NODE_ID"], "amount": s["ANNUAL_FLOW_AMOUNT"],
    }).reset_index(drop=True)

    r = m[m["NODE_ID"].map(lambda i: tax[i].receives_flows)]
    recipient_margin = pd.DataFrame({
        "dataset": r["DATASET"], "year": r["YEAR"], "state": r["STATE"], "node_id": r["NODE_ID"],
        "margin": r["OPERATING_MARGIN"],
    }).reset_index(drop=True)

    t = m[econ == "top_down"]
    top_down = pd.DataFrame({
        "dataset": t["DATASET"], "year": t["YEAR"], "node_id": t["NODE_ID"],
        "revenue": t["REVENUE"], "margin": t["OPERATING_MARGIN"],
    }).reset_index(drop=True)

    if (s["PAID_AMOUNT"] != s["ANNUAL_FLOW_AMOUNT"]).any():
        raise ValueError("Supplemental payments must be fully program-paid")
    return DriverCube(payer, spend.reset_index(drop=True), population.reset_index(drop=True),
                      supplemental, recipient_margin, top_down,
                      base_year=meta.periods.base_year, last_year=meta.periods.history_end)


def _denominators(payer: pd.DataFrame, population: pd.DataFrame) -> pd.DataFrame:
    """Members (payer sources) or population (funding sources) per spending source cell."""
    a = payer.rename(columns={"node_id": "source_id", "members": "denom"})[[*POP_KEY, "denom"]]
    b = population.rename(columns={"population": "denom"})[[*POP_KEY, "denom"]]
    return pd.concat([a, b], ignore_index=True)


# ---------------------------------------------------------------------------
# Baseline forecast rates
# ---------------------------------------------------------------------------

RateFn = Callable[[str, pd.DataFrame, int], np.ndarray]


class ForecastRates:
    """Hierarchical lookup of baseline annual rates (most specific key wins)."""

    def __init__(self, meta: Metadata, spec: Mapping | None = None):
        self.meta = meta
        self.spec = spec or load_config("baseline_forecast")
        self._cache: dict[tuple[str, str], float] = {}

    def _lookup(self, driver: str, keys: list[str | None]) -> float:
        table = self.spec[driver]
        for k in keys:
            if k is not None and k in table:
                return float(table[k])
        return float(table["default"])

    def _ancestor_keys(self, node_id: str) -> list[str]:
        return [n.id for n in reversed(self.meta.taxonomy.path(node_id))]

    def key_chain(self, driver: str, row_key: str) -> list[str | None]:
        if driver in ("enrollment_growth", "premium_trend", "opex_trend"):
            return [row_key, self.meta.lobs.group_of(row_key)]
        if driver in ("utilization_trend", "unit_cost_trend"):
            cat = self.meta.categories.get(row_key)
            return [row_key, *(self._ancestor_keys(cat.target) if cat else [])]
        if driver == "top_down_growth":
            return self._ancestor_keys(row_key)
        return [row_key]  # funding drivers

    def rate(self, driver: str, row_key: str) -> float:
        ck = (driver, row_key)
        if ck not in self._cache:
            self._cache[ck] = self._lookup(driver, self.key_chain(driver, row_key))
        return self._cache[ck]

    def __call__(self, driver: str, rows: pd.DataFrame, year: int) -> np.ndarray:
        """Rates for each row of a base-year frame (year-invariant in the baseline)."""
        col = _RATE_KEY_COLUMN[driver]
        return rows[col].map(lambda k: self.rate(driver, k)).to_numpy(dtype=float)


_RATE_KEY_COLUMN = {
    "enrollment_growth": "lob", "premium_trend": "lob", "opex_trend": "lob",
    "utilization_trend": "category", "unit_cost_trend": "category",
    "population_growth": "source_id", "supplemental_growth": "source_id",
    "top_down_growth": "node_id",
}


def _grow(base: pd.DataFrame, years: list[int], specs: dict[str, list[str]], rates: RateFn) -> pd.DataFrame:
    """Compound base-year levels forward: level_y = level_{y-1} × Π(1 + rate_y)."""
    out = []
    cur = base.copy()
    for y in years:
        cur = cur.copy()
        cur["year"] = y
        for col, drivers in specs.items():
            factor = np.ones(len(cur))
            for d in drivers:
                factor *= 1 + rates(d, cur, y)
            cur[col] = cur[col] * factor
        out.append(cur)
    return pd.concat(out, ignore_index=True) if out else base.iloc[0:0]


def project(cube: DriverCube, meta: Metadata, rates: RateFn | None = None) -> DriverCube:
    """Append forecast years (base_year+1 … forecast_end) to a history cube."""
    rates = rates or ForecastRates(meta)
    years = meta.periods.forecast_years
    base = cube.base_year

    def at_base(df):
        return df[df["year"] == base]

    payer = _grow(at_base(cube.payer), years,
                  {"members": ["enrollment_growth"], "premium_pmpm": ["premium_trend"], "opex_pmpm": ["opex_trend"]},
                  rates)
    spend = _grow(at_base(cube.spend), years,
                  {"allowed_pmpm": ["utilization_trend", "unit_cost_trend"], "inst_per_member": ["utilization_trend"]},
                  rates)
    population = _grow(at_base(cube.population), years, {"population": ["population_growth"]}, rates)
    supplemental = _grow(at_base(cube.supplemental), years, {"amount": ["supplemental_growth"]}, rates)
    margin = _grow(at_base(cube.recipient_margin), years, {}, rates)   # margins hold
    top = _grow(at_base(cube.top_down), years, {"revenue": ["top_down_growth"]}, rates)

    def cat(a, b):
        return pd.concat([a, b], ignore_index=True)

    return replace(cube, payer=cat(cube.payer, payer), spend=cat(cube.spend, spend),
                   population=cat(cube.population, population),
                   supplemental=cat(cube.supplemental, supplemental),
                   recipient_margin=cat(cube.recipient_margin, margin),
                   top_down=cat(cube.top_down, top), last_year=meta.periods.forecast_end)
