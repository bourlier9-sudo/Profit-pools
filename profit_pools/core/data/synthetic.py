"""Deterministic synthetic data generator — ENTIRELY FICTIONAL.

Produces the two logical fact tables (FACT_POOL_METRICS, FACT_VALUE_FLOWS)
for the historical years. The numbers are invented to be internally
consistent, not to resemble any real market.

Construction order (every identity below holds exactly):

  Payer cell (year × state × county × LOB)
    members                    synthetic
    allowed_c                  = allowed_pmpm_c × members × 12, per claims category c
    services_c                 = allowed_c / avg_allowed_per_service_c   (only where reliable)
    CLAIMS_EXPENSE             = Σ_c allowed_c
    REVENUE                    = CLAIMS_EXPENSE / MLR
    residual opex              = REVENUE × opex_ratio     (generator-only; never stored)
    OPERATING_GAIN             = REVENUE − CLAIMS_EXPENSE − residual opex
    opex flow → Payer Services = residual opex × outsourced share

  Recipient leaf (year × state)
    attributed                 = Σ inflows from payer cells
    unattributed residual      = attributed × (1 − s) / s,  s = attributable share
    REVENUE                    = attributed + residual
    OPERATING_GAIN             = REVENUE × OPERATING_MARGIN   (null if margin unavailable)

  Top-down leaf (year, national)
    REVENUE, OPERATING_MARGIN  top-down estimates; no flows, no LOB detail
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from profit_pools.core.data.contracts import (
    EXTERNAL_SOURCE,
    NATIONAL,
    POOL_METRICS_COLUMNS,
    VALUE_FLOWS_COLUMNS,
)
from profit_pools.core.taxonomy import Metadata, Node, default_metadata

DEFAULT_SEED = 20251018
BASE_YEAR_OF_PARAMS = 2019

# --- Payer parameters (by LOB) ----------------------------------------------
# national avg members (millions) in 2019, allowed PMPM 2019, MLR, opex ratio
PAYER_PARAMS: dict[str, dict[str, float]] = {
    "COMM_IND": dict(members=3.0, allowed_pmpm=430, mlr=0.820, opex=0.140),
    "COMM_SG": dict(members=4.0, allowed_pmpm=445, mlr=0.825, opex=0.128),
    "COMM_LG": dict(members=14.0, allowed_pmpm=480, mlr=0.860, opex=0.100),
    "MA_IND": dict(members=5.5, allowed_pmpm=905, mlr=0.862, opex=0.098),
    "MA_GRP": dict(members=1.2, allowed_pmpm=880, mlr=0.880, opex=0.090),
    "MA_SNP": dict(members=1.0, allowed_pmpm=1410, mlr=0.885, opex=0.097),
    "MCD_TANF": dict(members=9.0, allowed_pmpm=230, mlr=0.880, opex=0.090),
    "MCD_EXP": dict(members=4.5, allowed_pmpm=470, mlr=0.900, opex=0.080),
    "MCD_ABD": dict(members=2.0, allowed_pmpm=1390, mlr=0.930, opex=0.085),
}

# Enrollment index by LOB group and year (2019 = 1.0): Medicaid surge & unwind.
ENROLLMENT_PATH: dict[str, dict[int, float]] = {
    "COMM": {2019: 1.00, 2020: 0.98, 2021: 0.985, 2022: 0.99, 2023: 1.00, 2024: 1.005, 2025: 1.01},
    "MCR": {2019: 1.00, 2020: 1.07, 2021: 1.15, 2022: 1.23, 2023: 1.31, 2024: 1.38, 2025: 1.44},
    "MCD": {2019: 1.00, 2020: 1.08, 2021: 1.18, 2022: 1.24, 2023: 1.17, 2024: 1.05, 2025: 1.03},
}
# SNP and ACA grow faster than their group.
LOB_EXTRA_GROWTH = {"MA_SNP": 0.05, "COMM_IND": 0.03}

# MLR shock by LOB group and year (added to base MLR): 2020 utilization dip, 2023+ pressure.
MLR_SHOCK: dict[str, dict[int, float]] = {
    "COMM": {2019: 0.0, 2020: -0.035, 2021: 0.005, 2022: 0.008, 2023: 0.010, 2024: 0.018, 2025: 0.012},
    "MCR": {2019: 0.0, 2020: -0.030, 2021: 0.002, 2022: 0.004, 2023: 0.012, 2024: 0.030, 2025: 0.022},
    "MCD": {2019: 0.0, 2020: -0.040, 2021: -0.015, 2022: -0.005, 2023: 0.010, 2024: 0.025, 2025: 0.020},
}

# Spending mix by LOB group (shares of allowed PMPM; normalized in code).
SPEND_MIX: dict[str, dict[str, float]] = {
    "COMM": dict(IP_ACUTE=.17, OP_HOSP=.20, IP_PSYCH=.010, IP_REHAB=.005, PROF_PRIMARY=.08,
                 PROF_SPEC=.17, PROF_BH=.030, SNF=.005, HOME_HEALTH=.005, ASC=.040,
                 IMAGING=.030, RX_RETAIL=.10, RX_SPECIALTY=.12, RX_MAIL=.040),
    "MCR": dict(IP_ACUTE=.24, OP_HOSP=.15, IP_PSYCH=.010, IP_REHAB=.020, PROF_PRIMARY=.07,
                PROF_SPEC=.14, PROF_BH=.015, SNF=.060, HOME_HEALTH=.040, ASC=.030,
                IMAGING=.020, RX_RETAIL=.10, RX_SPECIALTY=.08, RX_MAIL=.030),
    "MCD": dict(IP_ACUTE=.20, OP_HOSP=.16, IP_PSYCH=.030, IP_REHAB=.005, PROF_PRIMARY=.09,
                PROF_SPEC=.10, PROF_BH=.070, SNF=.050, HOME_HEALTH=.040, ASC=.015,
                IMAGING=.015, RX_RETAIL=.12, RX_SPECIALTY=.09, RX_MAIL=.010),
}

# Annual allowed-PMPM trend by category (price + utilization combined).
CATEGORY_TREND = dict(IP_ACUTE=.045, OP_HOSP=.060, IP_PSYCH=.055, IP_REHAB=.040, PROF_PRIMARY=.035,
                      PROF_SPEC=.045, PROF_BH=.080, SNF=.030, HOME_HEALTH=.050, ASC=.065,
                      IMAGING=.040, RX_RETAIL=.025, RX_SPECIALTY=.110, RX_MAIL=.030)
# Average allowed per service (Commercial, 2019, price index 1.0) and its annual trend.
UNIT_PRICE = dict(IP_ACUTE=22000, OP_HOSP=900, IP_PSYCH=1500, IP_REHAB=25000, PROF_PRIMARY=150,
                  PROF_SPEC=210, PROF_BH=130, SNF=600, HOME_HEALTH=3000, ASC=3500, IMAGING=450,
                  RX_RETAIL=60, RX_SPECIALTY=4500, RX_MAIL=110)
PRICE_TREND = dict(IP_ACUTE=.035, OP_HOSP=.040, IP_PSYCH=.030, IP_REHAB=.030, PROF_PRIMARY=.025,
                   PROF_SPEC=.030, PROF_BH=.035, SNF=.025, HOME_HEALTH=.030, ASC=.035,
                   IMAGING=.020, RX_RETAIL=.010, RX_SPECIALTY=.070, RX_MAIL=.015)
LOB_GROUP_PRICE = {"COMM": 1.00, "MCR": 0.78, "MCD": 0.62}
COVID_UTIL_2020 = 0.90  # 2020 claims-volume dip applied to allowed spend

# --- Recipient parameters (by leaf) ------------------------------------------
# share of pool revenue covered by modeled payer LOBs (s), base operating margin
RECIPIENT_PARAMS: dict[str, dict[str, float]] = {
    "PRV_HOSP_ACUTE": dict(attributable=0.52, margin=0.048),
    "PRV_HOSP_PSYCH": dict(attributable=0.45, margin=-0.018),
    "PRV_HOSP_REHAB": dict(attributable=0.40, margin=0.085),
    "PRV_PHYS_PRIMARY": dict(attributable=0.60, margin=0.030),
    "PRV_PHYS_SPEC": dict(attributable=0.58, margin=0.092),
    "PRV_PHYS_BH": dict(attributable=0.50, margin=0.052),
    "PRV_PAC_SNF": dict(attributable=0.35, margin=0.012),
    "PRV_PAC_HH": dict(attributable=0.40, margin=0.071),
    "PRV_AMB_ASC": dict(attributable=0.70, margin=0.215),
    "PRV_AMB_IMG": dict(attributable=0.62, margin=0.118),
    "RX_RETAIL": dict(attributable=0.55, margin=0.026),
    "RX_SPECIALTY": dict(attributable=0.60, margin=0.044),
    "RX_MAIL": dict(attributable=0.65, margin=0.061),
    "HS_PAYER_SVC": dict(attributable=0.48, margin=0.142),
}
DEFAULT_RECIPIENT = dict(attributable=0.5, margin=0.05)
# Margin shocks (additive) by leaf and year.
MARGIN_SHOCK: dict[str, dict[int, float]] = {
    "PRV_HOSP_ACUTE": {2020: -0.025, 2021: 0.010, 2022: -0.032, 2023: -0.015, 2024: -0.004},
    "PRV_HOSP_PSYCH": {2020: -0.015, 2022: -0.020, 2023: -0.010},
    "PRV_PAC_SNF": {2020: -0.030, 2021: -0.025, 2022: -0.012},
    "PRV_PHYS_PRIMARY": {2020: -0.020},
}
# Missing margins: (leaf, state or None=all, years) — exercised by coverage UI.
MISSING_MARGINS: list[tuple[str, str | None, tuple[int, ...]]] = [
    ("PRV_HOSP_REHAB", "WMR", tuple(range(2019, 2026))),
    ("PRV_HOSP_REHAB", "LKD", tuple(range(2019, 2026))),
    ("PRV_AMB_IMG", None, (2019, 2020)),
]
PAYER_SVC_OUTSOURCED_SHARE = 0.20

# --- Top-down parameters (national) ------------------------------------------
TOP_DOWN_PARAMS: dict[str, dict[str, float]] = {
    "MFR_BRAND": dict(revenue=96e9, growth=0.068, margin=0.235),
    "MFR_GENERIC": dict(revenue=19e9, growth=0.012, margin=0.085),
    "MFR_DEVICE": dict(revenue=41e9, growth=0.048, margin=0.168),
    "HS_BH": dict(revenue=6.4e9, growth=0.092, margin=0.078),
    "HS_PBS": dict(revenue=8.8e9, growth=0.055, margin=0.052),
}
DEFAULT_TOP_DOWN = dict(revenue=5e9, growth=0.04, margin=0.06)
TOP_DOWN_MISSING_MARGIN: dict[str, tuple[int, ...]] = {"HS_BH": (2019, 2020)}

SCALE = 1e6  # PAYER_PARAMS members are in millions


@dataclass(frozen=True)
class SyntheticBundle:
    pool_metrics: pd.DataFrame
    value_flows: pd.DataFrame
    seed: int


def _level1(node: Node, meta: Metadata) -> str:
    path = meta.taxonomy.path(node.id)
    return path[1].id if len(path) > 1 else node.id


def generate(seed: int = DEFAULT_SEED, meta: Metadata | None = None) -> SyntheticBundle:
    meta = meta or default_metadata()
    tax = meta.taxonomy
    rng = np.random.default_rng(seed)
    years = meta.periods.history_years
    geo = meta.geography

    metric_rows: list[dict] = []
    flow_rows: list[dict] = []

    claims_cats = [c for c in meta.categories.values() if c.kind == "claims"]
    opex_cats = [c for c in meta.categories.values() if c.kind == "opex"]
    payer_leaves = [n for n in tax.leaves() if n.economics == "payer_pnl"]

    # Persistent cell-level effects so time series are smooth, not white noise.
    state_lob_mix = {(s, l.lob): rng.normal(1.0, 0.10) for s in geo.states for l in payer_leaves}
    cell_util = {(c, l.lob, s): rng.normal(1.0, 0.04)
                 for c in (x.id for x in claims_cats) for l in payer_leaves for s in geo.states}

    for year in years:
        t = year - BASE_YEAR_OF_PARAMS
        for state in geo.states.values():
            sw = geo.state_weights[state.id]
            for leaf in payer_leaves:
                lob = leaf.lob
                grp = meta.lobs.group_of(lob)
                if lob == "MCD_EXP" and (state.medicaid_expansion_year is None
                                         or year < state.medicaid_expansion_year):
                    continue  # LOB does not exist here: no rows (not zeros)
                p = PAYER_PARAMS.get(lob, dict(members=1.0, allowed_pmpm=500, mlr=0.86, opex=0.10))
                growth = ENROLLMENT_PATH[grp][year] * (1 + LOB_EXTRA_GROWTH.get(lob, 0.0)) ** t
                mlr = p["mlr"] + MLR_SHOCK[grp][year] + rng.normal(0, 0.006)
                if lob == "MCD_ABD" and state.id in ("SRG", "CLP"):
                    mlr += 0.025  # persistently loss-making cells
                opex_ratio = p["opex"] + rng.normal(0, 0.004)
                mix = SPEND_MIX[grp]
                mix_total = sum(mix.values())
                for county_id, _, cw in state.counties:
                    members = (p["members"] * SCALE * sw * cw * state_lob_mix[(state.id, lob)]
                               * growth * rng.normal(1.0, 0.015))
                    mm12 = members * 12
                    claims_total = 0.0
                    for cat in claims_cats:
                        share = mix.get(cat.id, 0.0) / mix_total
                        if share <= 0:
                            continue
                        pmpm = (p["allowed_pmpm"] * share * state.price_index
                                * (1 + CATEGORY_TREND.get(cat.id, 0.04)) ** t
                                * cell_util[(cat.id, lob, state.id)]
                                * (COVID_UTIL_2020 if year == 2020 else 1.0)
                                * rng.normal(1.0, 0.02))
                        allowed = pmpm * mm12
                        claims_total += allowed
                        services = unit = None
                        if cat.has_reliable_services(grp):
                            price = (UNIT_PRICE[cat.id] * LOB_GROUP_PRICE[grp] * state.price_index
                                     * (1 + PRICE_TREND[cat.id]) ** t)
                            services = allowed / price
                            unit = cat.service_unit
                        flow_rows.append(dict(
                            YEAR=year, GEO_LEVEL="county", STATE=state.id, COUNTY=county_id,
                            SOURCE_NODE_ID=leaf.id, TARGET_NODE_ID=cat.target, PAYER_LOB=lob,
                            SPENDING_CATEGORY=cat.id, FLOW_KIND="claims", ANNUAL_FLOW_AMOUNT=allowed,
                            SERVICE_COUNT=services, SERVICE_UNIT=unit,
                            ALLOCATION_METHOD="direct_claims_mapping", SOURCE_TYPE="observed"))
                    revenue = claims_total / mlr
                    opex = revenue * opex_ratio
                    gain = revenue - claims_total - opex
                    metric_rows.append(dict(
                        YEAR=year, GEO_LEVEL="county", STATE=state.id, COUNTY=county_id,
                        LAYER_ID=leaf.layer_id, POOL_ID=_level1(leaf, meta), SUBPOOL_ID=leaf.id,
                        LOB=lob, ENROLLMENT=members, REVENUE=revenue, CLAIMS_EXPENSE=claims_total,
                        OPERATING_GAIN=gain, OPERATING_MARGIN=gain / revenue,
                        REVENUE_PMPM=revenue / mm12, SPEND_PMPM=claims_total / mm12,
                        SOURCE_TYPE="observed", COVERAGE_LEVEL=leaf.coverage))
                    for cat in opex_cats:
                        flow_rows.append(dict(
                            YEAR=year, GEO_LEVEL="county", STATE=state.id, COUNTY=county_id,
                            SOURCE_NODE_ID=leaf.id, TARGET_NODE_ID=cat.target, PAYER_LOB=lob,
                            SPENDING_CATEGORY=cat.id, FLOW_KIND="opex",
                            ANNUAL_FLOW_AMOUNT=opex * PAYER_SVC_OUTSOURCED_SHARE * rng.normal(1.0, 0.03),
                            SERVICE_COUNT=None, SERVICE_UNIT=None,
                            ALLOCATION_METHOD="estimated_opex_share", SOURCE_TYPE="estimated"))

    flows = pd.DataFrame(flow_rows)

    # Recipients: revenue = attributed inflows + unattributed residual, at state grain.
    recipients = [n for n in tax.leaves() if n.receives_flows]
    attributed = flows.groupby(["YEAR", "STATE", "TARGET_NODE_ID"])["ANNUAL_FLOW_AMOUNT"].sum()
    state_margin = {(n.id, s): rng.normal(0, 0.008) for n in recipients for s in geo.states}
    resid_rows: list[dict] = []
    for leaf in recipients:
        rp = RECIPIENT_PARAMS.get(leaf.id, DEFAULT_RECIPIENT)
        for year in years:
            for state in geo.states.values():
                attr = float(attributed.get((year, state.id, leaf.id), 0.0))
                if attr <= 0:
                    continue
                s = float(np.clip(rp["attributable"] * rng.normal(1.0, 0.04), 0.05, 0.95))
                residual = attr * (1 - s) / s
                resid_rows.append(dict(
                    YEAR=year, GEO_LEVEL="state", STATE=state.id, COUNTY=None,
                    SOURCE_NODE_ID=EXTERNAL_SOURCE, TARGET_NODE_ID=leaf.id,
                    PAYER_LOB=meta.lobs.unmodeled_id, SPENDING_CATEGORY=None, FLOW_KIND="residual",
                    ANNUAL_FLOW_AMOUNT=residual, SERVICE_COUNT=None, SERVICE_UNIT=None,
                    ALLOCATION_METHOD="unattributed_residual", SOURCE_TYPE="estimated"))
                revenue = attr + residual
                missing = any(lf == leaf.id and (st is None or st == state.id) and year in yrs
                              for lf, st, yrs in MISSING_MARGINS)
                margin = None if missing else (rp["margin"] + MARGIN_SHOCK.get(leaf.id, {}).get(year, 0.0)
                                               + state_margin[(leaf.id, state.id)] + rng.normal(0, 0.004))
                metric_rows.append(dict(
                    YEAR=year, GEO_LEVEL="state", STATE=state.id, COUNTY=None,
                    LAYER_ID=leaf.layer_id, POOL_ID=_level1(leaf, meta), SUBPOOL_ID=leaf.id,
                    LOB=None, ENROLLMENT=None, REVENUE=revenue, CLAIMS_EXPENSE=None,
                    OPERATING_GAIN=None if margin is None else revenue * margin,
                    OPERATING_MARGIN=margin, REVENUE_PMPM=None, SPEND_PMPM=None,
                    SOURCE_TYPE="allocated", COVERAGE_LEVEL=leaf.coverage))

    # Top-down leaves: national only.
    for leaf in tax.leaves():
        if leaf.economics != "top_down":
            continue
        tp = TOP_DOWN_PARAMS.get(leaf.id, DEFAULT_TOP_DOWN)
        for year in years:
            t = year - BASE_YEAR_OF_PARAMS
            revenue = tp["revenue"] * (1 + tp["growth"]) ** t * rng.normal(1.0, 0.012)
            margin = (None if year in TOP_DOWN_MISSING_MARGIN.get(leaf.id, ())
                      else tp["margin"] + rng.normal(0, 0.006))
            metric_rows.append(dict(
                YEAR=year, GEO_LEVEL="national", STATE=NATIONAL, COUNTY=None,
                LAYER_ID=leaf.layer_id, POOL_ID=_level1(leaf, meta), SUBPOOL_ID=leaf.id,
                LOB=None, ENROLLMENT=None, REVENUE=revenue, CLAIMS_EXPENSE=None,
                OPERATING_GAIN=None if margin is None else revenue * margin,
                OPERATING_MARGIN=margin, REVENUE_PMPM=None, SPEND_PMPM=None,
                SOURCE_TYPE="estimated", COVERAGE_LEVEL=leaf.coverage))

    flows = pd.concat([flows, pd.DataFrame(resid_rows)], ignore_index=True)
    metrics = pd.DataFrame(metric_rows)
    return SyntheticBundle(
        pool_metrics=_conform(metrics, POOL_METRICS_COLUMNS),
        value_flows=_conform(flows, VALUE_FLOWS_COLUMNS),
        seed=seed,
    )


def _conform(df: pd.DataFrame, contract: tuple) -> pd.DataFrame:
    """Order columns and coerce dtypes per the contract (int64 / float64 / str with NaN nulls)."""
    df = df[[c.name for c in contract]].copy()
    for c in contract:
        if c.kind == "int":
            df[c.name] = df[c.name].astype("int64")
        elif c.kind == "float":
            df[c.name] = pd.to_numeric(df[c.name]).astype("float64")
        else:
            df[c.name] = df[c.name].astype("str")
    return df.reset_index(drop=True)


def summary(bundle: SyntheticBundle, meta: Metadata | None = None) -> pd.DataFrame:
    """National revenue / gain / margin by layer and year ($B). For CLI inspection."""
    meta = meta or default_metadata()
    m = bundle.pool_metrics
    g = m.groupby(["YEAR", "LAYER_ID"]).agg(
        revenue=("REVENUE", "sum"),
        gain=("OPERATING_GAIN", "sum"),
        gain_coverage=("OPERATING_GAIN", lambda s: s.notna().mean()),
    )
    g["margin_on_covered"] = g["gain"] / m[m["OPERATING_GAIN"].notna()].groupby(
        ["YEAR", "LAYER_ID"])["REVENUE"].sum()
    g[["revenue", "gain"]] /= 1e9
    return g.round(3)


if __name__ == "__main__":  # pragma: no cover - manual inspection helper
    from profit_pools.core.data.contracts import validate_bundle

    b = generate()
    issues = validate_bundle(b.pool_metrics, b.value_flows, default_metadata())
    pd.set_option("display.width", 160)
    print(f"FACT_POOL_METRICS rows: {len(b.pool_metrics):,}   FACT_VALUE_FLOWS rows: {len(b.value_flows):,}")
    print(summary(b).xs(2025, level="YEAR"))
    print(f"Validation issues: {len(issues)}")
    for i in issues:
        print(f"  [{i.level}] {i.code}: {i.message}")
