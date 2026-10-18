"""Deterministic synthetic data generator — ENTIRELY FICTIONAL.

Produces the three logical fact tables (FACT_POOL_METRICS, FACT_VALUE_FLOWS,
FACT_COVERAGE_POPULATION) for both datasets and all historical years. The
numbers are invented to be internally consistent, not to resemble any real
market or company.

Construction (every identity holds exactly):

ENTERPRISE (observed book), per year × county × LOB
    members                = market members × enterprise share
    allowed_c              = allowed_pmpm_c × members × 12           per claims category c
    paid_c                 = allowed_c × (1 − member cost share_c)
    instances_c            = allowed_c / avg allowed per instance    (categories with instances)
    ALLOWED_SPEND          = Σ_c allowed_c
    CLAIMS_EXPENSE         = Σ_c paid_c                              (payer-paid claims)
    REVENUE                = CLAIMS_EXPENSE / MLR
    OPERATING_GAIN         = REVENUE − CLAIMS_EXPENSE − residual opex

MARKET (estimated), per year × county × LOB
    members                synthetic market enrollment
    allowed_pmpm_c         = enterprise state-level PMPM_c × market factor   (PMPM benchmark)
    paid_c / allowed_c     = enterprise state-level paid ratio_c
    instances_c            = enterprise state-level instances per member × members
    REVENUE                = members × 12 × enterprise state premium PMPM × market factor
    OPERATING_GAIN         = REVENUE × estimated market margin
  plus non-pool funding (market only)
    uninsured / trad. Medicare flows = POPULATION × estimated PMPM_c   (uninsured: all self-pay)
    supplemental payments            = annual estimate by state, hospital leaves only

Recipients, per dataset × year × state × leaf
    REVENUE = Σ inflows ;  OPERATING_GAIN = REVENUE × pool margin (shared by datasets)

Top-down leaves (market only, national)
    REVENUE, OPERATING_MARGIN top-down estimates; no flows, no LOB detail
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

import numpy as np
import pandas as pd

from profit_pools.core.data.contracts import (
    COVERAGE_POPULATION_COLUMNS,
    LEVEL_COLUMNS,
    NATIONAL,
    POOL_METRICS_COLUMNS,
    VALUE_FLOWS_COLUMNS,
)
from profit_pools.core.taxonomy import Metadata, Node, default_metadata

DEFAULT_SEED = 20251018
PARAM_YEAR = 2019
SCALE = 1e6

# --- Payer parameters (by LOB): market members (M, 2019), allowed PMPM, MLR, opex ratio
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
DEFAULT_PAYER = dict(members=1.0, allowed_pmpm=500, mlr=0.86, opex=0.10)

ENROLLMENT_PATH: dict[str, dict[int, float]] = {
    "COMM": {2019: 1.00, 2020: 0.98, 2021: 0.985, 2022: 0.99, 2023: 1.00, 2024: 1.005, 2025: 1.01},
    "MCR": {2019: 1.00, 2020: 1.07, 2021: 1.15, 2022: 1.23, 2023: 1.31, 2024: 1.38, 2025: 1.44},
    "MCD": {2019: 1.00, 2020: 1.08, 2021: 1.18, 2022: 1.24, 2023: 1.17, 2024: 1.05, 2025: 1.03},
}
LOB_EXTRA_GROWTH = {"MA_SNP": 0.05, "COMM_IND": 0.03}
MLR_SHOCK: dict[str, dict[int, float]] = {
    "COMM": {2019: 0.0, 2020: -0.035, 2021: 0.005, 2022: 0.008, 2023: 0.010, 2024: 0.018, 2025: 0.012},
    "MCR": {2019: 0.0, 2020: -0.030, 2021: 0.002, 2022: 0.004, 2023: 0.012, 2024: 0.030, 2025: 0.022},
    "MCD": {2019: 0.0, 2020: -0.040, 2021: -0.015, 2022: -0.005, 2023: 0.010, 2024: 0.025, 2025: 0.020},
}

# Enterprise share of market enrollment by state, scaled by LOB group.
ENTERPRISE_SHARE = {"NVL": 0.30, "ESH": 0.22, "SRG": 0.18, "WMR": 0.0, "CLP": 0.26, "LKD": 0.15}
ENTERPRISE_GROUP_FACTOR = {"COMM": 1.0, "MCR": 0.75, "MCD": 1.25}
ENTERPRISE_ABSENT = {("SRG", "MA_SNP"), ("CLP", "MA_SNP")}  # no enterprise book in these cells
# Market benchmark adjustment: market PMPM = enterprise PMPM × factor.
MARKET_PMPM_FACTOR = {"COMM": 1.04, "MCR": 0.98, "MCD": 1.02}
MARKET_MARGIN_OFFSET = {"COMM": -0.004, "MCR": -0.006, "MCD": -0.002}

# Spending mix by LOB group (shares of allowed PMPM; normalized in code).
SPEND_MIX: dict[str, dict[str, float]] = {
    "COMM": dict(IP_MEDSURG=.145, IP_MATERNITY=.025, OP_ED=.05, OP_SURG=.08, OP_OTHER=.07, IP_PSYCH=.01,
                 IP_REHAB=.005, PROF_PRIMARY=.08, PROF_SPEC=.17, PROF_BH=.03, SNF=.005, HOME_HEALTH=.005,
                 ASC=.04, IMAGING=.03, RX_CHAIN_BRAND=.055, RX_CHAIN_GENERIC=.025, RX_INDEP_BRAND=.015,
                 RX_INDEP_GENERIC=.008, RX_SPEC_HEALTHSYS=.05, RX_SPEC_INDEP=.07, RX_MAIL=.04),
    "MCR": dict(IP_MEDSURG=.235, IP_MATERNITY=.002, OP_ED=.035, OP_SURG=.06, OP_OTHER=.055, IP_PSYCH=.01,
                IP_REHAB=.02, PROF_PRIMARY=.07, PROF_SPEC=.14, PROF_BH=.015, SNF=.06, HOME_HEALTH=.04,
                ASC=.03, IMAGING=.02, RX_CHAIN_BRAND=.055, RX_CHAIN_GENERIC=.02, RX_INDEP_BRAND=.015,
                RX_INDEP_GENERIC=.008, RX_SPEC_HEALTHSYS=.035, RX_SPEC_INDEP=.045, RX_MAIL=.03),
    "MCD": dict(IP_MEDSURG=.15, IP_MATERNITY=.05, OP_ED=.07, OP_SURG=.04, OP_OTHER=.05, IP_PSYCH=.03,
                IP_REHAB=.005, PROF_PRIMARY=.09, PROF_SPEC=.10, PROF_BH=.07, SNF=.05, HOME_HEALTH=.04,
                ASC=.015, IMAGING=.015, RX_CHAIN_BRAND=.06, RX_CHAIN_GENERIC=.03, RX_INDEP_BRAND=.02,
                RX_INDEP_GENERIC=.01, RX_SPEC_HEALTHSYS=.04, RX_SPEC_INDEP=.05, RX_MAIL=.01),
}
CATEGORY_TREND = dict(IP_MEDSURG=.045, IP_MATERNITY=.035, OP_ED=.05, OP_SURG=.065, OP_OTHER=.06,
                      IP_PSYCH=.055, IP_REHAB=.04, PROF_PRIMARY=.035, PROF_SPEC=.045, PROF_BH=.08, SNF=.03,
                      HOME_HEALTH=.05, ASC=.065, IMAGING=.04, RX_CHAIN_BRAND=.03, RX_CHAIN_GENERIC=.005,
                      RX_INDEP_BRAND=.025, RX_INDEP_GENERIC=.0, RX_SPEC_HEALTHSYS=.12, RX_SPEC_INDEP=.10,
                      RX_MAIL=.03)
# Average allowed per instance (Commercial, 2019, price index 1.0) and its annual trend.
INSTANCE_PRICE = dict(IP_MEDSURG=24000, IP_MATERNITY=14000, OP_ED=2200, OP_SURG=5200, OP_OTHER=650,
                      IP_PSYCH=11000, IP_REHAB=25000, PROF_PRIMARY=150, PROF_SPEC=210, PROF_BH=130,
                      SNF=12000, ASC=3500)
INSTANCE_PRICE_TREND = 0.032
LOB_GROUP_PRICE = {"COMM": 1.00, "MCR": 0.78, "MCD": 0.62}
COVID_2020 = 0.90

# Member cost share (share of allowed paid by the consumer) = LOB base × category factor.
MEMBER_SHARE_LOB = {"COMM_IND": 0.26, "COMM_SG": 0.19, "COMM_LG": 0.15, "MA_IND": 0.10, "MA_GRP": 0.08,
                    "MA_SNP": 0.03, "MCD_TANF": 0.01, "MCD_EXP": 0.02, "MCD_ABD": 0.01}
MEMBER_SHARE_CATEGORY = dict(IP_MEDSURG=0.55, IP_MATERNITY=0.75, OP_ED=1.1, OP_SURG=1.0, OP_OTHER=1.2,
                             IP_PSYCH=0.8, IP_REHAB=0.6, PROF_PRIMARY=1.4, PROF_SPEC=1.3, PROF_BH=1.4,
                             SNF=0.8, HOME_HEALTH=0.3, ASC=1.0, IMAGING=1.3, RX_CHAIN_BRAND=1.3,
                             RX_CHAIN_GENERIC=2.2, RX_INDEP_BRAND=1.3, RX_INDEP_GENERIC=2.2,
                             RX_SPEC_HEALTHSYS=0.45, RX_SPEC_INDEP=0.45, RX_MAIL=1.1)
# Non-pool funding: share of allowed paid by the consumer (uninsured = all self-pay).
FUNDING_MEMBER_SHARE = {"FS_UNINSURED": 1.0, "FS_TRAD_MCR": 0.17}

# --- Non-pool funding (market only) ------------------------------------------
FUNDING_PARAMS = {
    # base population (M, 2019), base PMPM (2019), mix group, Rx share multiplier
    "FS_UNINSURED": dict(population=5.5, pmpm=150, mix="MCD", rx_mult=0.3),
    "FS_TRAD_MCR": dict(population=9.5, pmpm=980, mix="MCR", rx_mult=0.4),
}
FUNDING_PATH = {
    "FS_UNINSURED": {2019: 1.0, 2020: 1.03, 2021: 0.97, 2022: 0.92, 2023: 0.95, 2024: 1.04, 2025: 1.06},
    "FS_TRAD_MCR": {2019: 1.0, 2020: 0.97, 2021: 0.94, 2022: 0.91, 2023: 0.89, 2024: 0.87, 2025: 0.86},
}
UNINSURED_STATE_FACTOR = {"SRG": 1.6, "CLP": 1.25}
SUPPLEMENTAL_RATE = {"SRG": 0.08}  # share of state hospital inflows; default below
DEFAULT_SUPPLEMENTAL_RATE = 0.04

# --- Recipient margins (pool property, shared by both datasets) --------------
RECIPIENT_MARGIN = dict(
    PRV_IP_MEDSURG=0.035, PRV_IP_MATERNITY=0.020, PRV_OP_ED=0.040, PRV_OP_SURG=0.110, PRV_OP_OTHER=0.080,
    PRV_HOSP_PSYCH=-0.018, PRV_HOSP_REHAB=0.085, PRV_PHYS_PRIMARY=0.030, PRV_PHYS_SPEC=0.092,
    PRV_PHYS_BH=0.052, PRV_PAC_SNF=0.012, PRV_PAC_HH=0.071, PRV_AMB_ASC=0.215, PRV_AMB_IMG=0.118,
    RX_CHAIN_BRAND=0.020, RX_CHAIN_GENERIC=0.050, RX_INDEP_BRAND=0.012, RX_INDEP_GENERIC=0.040,
    RX_SPEC_HEALTHSYS=0.070, RX_SPEC_INDEP=0.035, RX_MAIL=0.061,
)
DEFAULT_MARGIN = 0.05
# Additive shocks keyed by any ancestor-or-self node id.
MARGIN_SHOCK: dict[str, dict[int, float]] = {
    "PRV_HOSP_ACUTE": {2020: -0.025, 2021: 0.010, 2022: -0.032, 2023: -0.015, 2024: -0.004},
    "PRV_HOSP_PSYCH": {2020: -0.015, 2022: -0.020, 2023: -0.010},
    "PRV_PAC_SNF": {2020: -0.030, 2021: -0.025, 2022: -0.012},
    "PRV_PHYS_PRIMARY": {2020: -0.020},
}
MISSING_MARGINS: list[tuple[str, str | None, tuple[int, ...]]] = [
    ("PRV_HOSP_REHAB", "WMR", tuple(range(2019, 2026))),
    ("PRV_HOSP_REHAB", "LKD", tuple(range(2019, 2026))),
    ("PRV_AMB_IMG", None, (2019, 2020)),
]

# --- Top-down (market only, national): revenue 2019, growth, margin -----------
TOP_DOWN_PARAMS: dict[str, dict[str, float]] = {
    "MFR_BRAND": dict(revenue=96e9, growth=0.068, margin=0.235),
    "MFR_GENERIC": dict(revenue=19e9, growth=0.012, margin=0.085),
    "MFR_DEVICE": dict(revenue=41e9, growth=0.048, margin=0.168),
    "MFR_DIAG": dict(revenue=14e9, growth=0.040, margin=0.140),
    "RX_PBM_COMM": dict(revenue=6.0e9, growth=0.055, margin=0.050),
    "RX_PBM_GOV": dict(revenue=3.5e9, growth=0.060, margin=0.045),
    "HS_UM": dict(revenue=2.2e9, growth=0.060, margin=0.120),
    "HS_CARE_COORD": dict(revenue=1.8e9, growth=0.070, margin=0.080),
    "HS_CLAIMS_PLATFORM": dict(revenue=3.1e9, growth=0.050, margin=0.180),
    "HS_ANALYTICS": dict(revenue=1.6e9, growth=0.090, margin=0.150),
    "HS_VBC": dict(revenue=2.4e9, growth=0.120, margin=-0.030),
    "HS_RCM": dict(revenue=4.2e9, growth=0.050, margin=0.110),
    "HS_BH_UM": dict(revenue=0.9e9, growth=0.080, margin=0.100),
    "HS_BH_ENABLE": dict(revenue=0.7e9, growth=0.110, margin=0.040),
    "HS_BH_NETWORK": dict(revenue=1.1e9, growth=0.070, margin=0.090),
    "HS_BH_OTHER": dict(revenue=0.6e9, growth=0.060, margin=0.060),
}
DEFAULT_TOP_DOWN = dict(revenue=1e9, growth=0.04, margin=0.06)
TOP_DOWN_MISSING_MARGIN: dict[str, tuple[int, ...]] = {"HS_BH_OTHER": (2019, 2020)}


@dataclass(frozen=True)
class SyntheticBundle:
    pool_metrics: pd.DataFrame
    value_flows: pd.DataFrame
    coverage_population: pd.DataFrame
    seed: int


def _levels(node: Node, meta: Metadata) -> dict:
    ids = meta.taxonomy.level_ids(node.id, len(LEVEL_COLUMNS))
    return {"LAYER_ID": node.layer_id, **dict(zip(LEVEL_COLUMNS, ids)), "NODE_ID": node.id}


def _mix(group: str, rx_mult: float, meta: Metadata) -> dict[str, float]:
    raw = {c: v * (rx_mult if meta.taxonomy[meta.categories[c].target].layer_id == "PHARMACY" else 1.0)
           for c, v in SPEND_MIX[group].items() if c in meta.categories}
    total = sum(raw.values())
    return {c: v / total for c, v in raw.items()}


def generate(seed: int = DEFAULT_SEED, meta: Metadata | None = None) -> SyntheticBundle:
    meta = meta or default_metadata()
    tax = meta.taxonomy
    rng = np.random.default_rng(seed)
    years = meta.periods.history_years
    geo = meta.geography
    cats = list(meta.categories.values())
    payer_leaves = [n for n in tax.leaves() if n.economics == "payer_pnl"]
    group_of = {n.lob: meta.lobs.group_of(n.lob) for n in payer_leaves}
    mixes = {g: _mix(g, 1.0, meta) for g in SPEND_MIX}

    metric_rows: list[dict] = []
    flow_rows: list[dict] = []
    pop_rows: list[dict] = []

    # Persistent cell effects (smooth time series).
    state_lob_mix = {(s, n.lob): rng.normal(1.0, 0.10) for s in geo.states for n in payer_leaves}
    cell_util = {(c.id, n.lob, s): rng.normal(1.0, 0.04) for c in cats for n in payer_leaves for s in geo.states}
    county_share = {(cid, n.lob): rng.normal(1.0, 0.08) for s in geo.states.values()
                    for cid, _, _ in s.counties for n in payer_leaves}

    def lob_exists(state, lob, year):
        if lob == "MCD_EXP":
            return state.medicaid_expansion_year is not None and year >= state.medicaid_expansion_year
        return True

    # ---- ENTERPRISE book + market enrollment -------------------------------
    mkt_members: dict[tuple, float] = {}
    # enterprise state-level accumulators for benchmarks
    st_mm12 = defaultdict(float)          # (y, s, lob) → member-months
    st_allowed = defaultdict(float)       # (y, s, lob, cat)
    st_paid = defaultdict(float)          # (y, s, lob, cat)
    st_inst = defaultdict(float)          # (y, s, lob, cat)
    st_rev = defaultdict(float)           # (y, s, lob)
    st_gain = defaultdict(float)          # (y, s, lob)
    nat_mm12 = defaultdict(float)
    nat_allowed = defaultdict(float)
    nat_paid = defaultdict(float)
    nat_inst = defaultdict(float)
    nat_rev = defaultdict(float)
    nat_gain = defaultdict(float)
    nat_px = defaultdict(float)           # price-index-weighted mm for fallback scaling

    for year in years:
        t = year - PARAM_YEAR
        for state in geo.states.values():
            sw = geo.state_weights[state.id]
            for leaf in payer_leaves:
                lob, grp = leaf.lob, group_of[leaf.lob]
                if not lob_exists(state, lob, year):
                    continue
                p = PAYER_PARAMS.get(lob, DEFAULT_PAYER)
                growth = ENROLLMENT_PATH[grp][year] * (1 + LOB_EXTRA_GROWTH.get(lob, 0.0)) ** t
                share = 0.0 if (state.id, lob) in ENTERPRISE_ABSENT else (
                    ENTERPRISE_SHARE.get(state.id, 0.0) * ENTERPRISE_GROUP_FACTOR[grp])
                mlr = p["mlr"] + MLR_SHOCK[grp][year] + rng.normal(0, 0.006)
                if lob == "MCD_ABD" and state.id in ("SRG", "CLP"):
                    mlr += 0.025
                opex_ratio = p["opex"] + rng.normal(0, 0.004)
                for county_id, _, cw in state.counties:
                    m_members = (p["members"] * SCALE * sw * cw * state_lob_mix[(state.id, lob)]
                                 * growth * rng.normal(1.0, 0.015))
                    mkt_members[(year, state.id, county_id, lob)] = m_members
                    if share <= 0:
                        continue
                    members = m_members * float(np.clip(share * county_share[(county_id, lob)], 0.01, 0.9))
                    mm12 = members * 12
                    claims = 0.0
                    allowed_total = 0.0
                    for cat in cats:
                        sh = mixes[grp].get(cat.id, 0.0)
                        pmpm = (p["allowed_pmpm"] * sh * state.price_index
                                * (1 + CATEGORY_TREND.get(cat.id, 0.04)) ** t
                                * cell_util[(cat.id, lob, state.id)]
                                * (COVID_2020 if year == 2020 else 1.0) * rng.normal(1.0, 0.02))
                        allowed = pmpm * mm12
                        paid = allowed * (1 - min(0.8, MEMBER_SHARE_LOB.get(lob, 0.1)
                                                  * MEMBER_SHARE_CATEGORY.get(cat.id, 1.0) * rng.normal(1, 0.03)))
                        inst = None
                        if cat.has_instances:
                            price = (INSTANCE_PRICE[cat.id] * LOB_GROUP_PRICE[grp] * state.price_index
                                     * (1 + INSTANCE_PRICE_TREND) ** t)
                            inst = allowed / price
                            st_inst[(year, state.id, lob, cat.id)] += inst
                            nat_inst[(year, lob, cat.id)] += inst
                        claims += paid
                        allowed_total += allowed
                        st_allowed[(year, state.id, lob, cat.id)] += allowed
                        st_paid[(year, state.id, lob, cat.id)] += paid
                        nat_paid[(year, lob, cat.id)] += paid
                        nat_allowed[(year, lob, cat.id)] += allowed
                        flow_rows.append(dict(
                            DATASET="ENTERPRISE", YEAR=year, GEO_LEVEL="county", STATE=state.id,
                            COUNTY=county_id, SOURCE_NODE_ID=leaf.id, TARGET_NODE_ID=cat.target,
                            PAYER_LOB=lob, SPENDING_CATEGORY=cat.id, FLOW_KIND="payer_claims",
                            ANNUAL_FLOW_AMOUNT=allowed, PAID_AMOUNT=paid, INSTANCE_COUNT=inst,
                            ALLOCATION_METHOD="observed_claims", SOURCE_TYPE="observed"))
                    revenue = claims / mlr
                    gain = revenue - claims - revenue * opex_ratio
                    st_mm12[(year, state.id, lob)] += mm12
                    st_rev[(year, state.id, lob)] += revenue
                    st_gain[(year, state.id, lob)] += gain
                    nat_mm12[(year, lob)] += mm12
                    nat_rev[(year, lob)] += revenue
                    nat_gain[(year, lob)] += gain
                    nat_px[(year, lob)] += mm12 * state.price_index
                    metric_rows.append(dict(
                        DATASET="ENTERPRISE", YEAR=year, GEO_LEVEL="county", STATE=state.id, COUNTY=county_id,
                        **_levels(leaf, meta), LOB=lob, ENROLLMENT=members, REVENUE=revenue,
                        CLAIMS_EXPENSE=claims, ALLOWED_SPEND=allowed_total, OPERATING_GAIN=gain,
                        OPERATING_MARGIN=gain / revenue, REVENUE_PMPM=revenue / mm12,
                        ALLOWED_PMPM=allowed_total / mm12, PAID_PMPM=claims / mm12,
                        SOURCE_TYPE="observed", COVERAGE_LEVEL=leaf.coverage))

    # ---- MARKET payers from enterprise PMPM benchmarks ---------------------
    for (year, s, county_id, lob), members in mkt_members.items():
        grp = group_of[lob]
        leaf = tax.leaf_for_lob(lob)
        state = geo.states[s]
        mm12 = members * 12
        fac = MARKET_PMPM_FACTOR[grp]
        if st_mm12.get((year, s, lob), 0) > 0:
            base_mm = st_mm12[(year, s, lob)]
            allowed_src = lambda c: st_allowed[(year, s, lob, c)] / base_mm  # noqa: E731
            paid_ratio = lambda c: st_paid[(year, s, lob, c)] / st_allowed[(year, s, lob, c)]  # noqa: E731
            inst_src = lambda c: st_inst[(year, s, lob, c)] / base_mm  # noqa: E731
            prem_pmpm = st_rev[(year, s, lob)] / base_mm
            margin = st_gain[(year, s, lob)] / st_rev[(year, s, lob)]
            px = 1.0
        else:  # no enterprise presence: national benchmark re-priced to the state
            base_mm = nat_mm12[(year, lob)]
            px = state.price_index / (nat_px[(year, lob)] / base_mm)
            allowed_src = lambda c: nat_allowed[(year, lob, c)] / base_mm  # noqa: E731
            paid_ratio = lambda c: nat_paid[(year, lob, c)] / nat_allowed[(year, lob, c)]  # noqa: E731
            inst_src = lambda c: nat_inst[(year, lob, c)] / base_mm  # noqa: E731
            prem_pmpm = nat_rev[(year, lob)] / base_mm
            margin = nat_gain[(year, lob)] / nat_rev[(year, lob)]
        claims = 0.0
        allowed_total = 0.0
        for cat in cats:
            allowed = allowed_src(cat.id) * px * fac * mm12
            if allowed <= 0:
                continue
            paid = allowed * paid_ratio(cat.id)
            inst = inst_src(cat.id) * mm12 if cat.has_instances else None
            claims += paid
            allowed_total += allowed
            flow_rows.append(dict(
                DATASET="MARKET", YEAR=year, GEO_LEVEL="county", STATE=s, COUNTY=county_id,
                SOURCE_NODE_ID=leaf.id, TARGET_NODE_ID=cat.target, PAYER_LOB=lob,
                SPENDING_CATEGORY=cat.id, FLOW_KIND="payer_claims", ANNUAL_FLOW_AMOUNT=allowed,
                PAID_AMOUNT=paid, INSTANCE_COUNT=inst, ALLOCATION_METHOD="pmpm_benchmark",
                SOURCE_TYPE="estimated"))
        revenue = prem_pmpm * px * fac * mm12
        m_margin = margin + MARKET_MARGIN_OFFSET[grp] + rng.normal(0, 0.003)
        gain = revenue * m_margin
        metric_rows.append(dict(
            DATASET="MARKET", YEAR=year, GEO_LEVEL="county", STATE=s, COUNTY=county_id,
            **_levels(leaf, meta), LOB=lob, ENROLLMENT=members, REVENUE=revenue, CLAIMS_EXPENSE=claims,
            ALLOWED_SPEND=allowed_total, OPERATING_GAIN=gain, OPERATING_MARGIN=m_margin,
            REVENUE_PMPM=revenue / mm12, ALLOWED_PMPM=allowed_total / mm12, PAID_PMPM=claims / mm12,
            SOURCE_TYPE="estimated", COVERAGE_LEVEL=leaf.coverage))

    # ---- MARKET non-pool funding -------------------------------------------
    market_funding = meta.datasets["MARKET"].funding_sources
    for fs_id in market_funding:
        fs = meta.funding_sources[fs_id]
        if fs.basis != "population_pmpm":
            continue
        fp = FUNDING_PARAMS[fs_id]
        mix = _mix(fp["mix"], fp["rx_mult"], meta)
        for year in years:
            t = year - PARAM_YEAR
            for state in geo.states.values():
                sw = geo.state_weights[state.id]
                sfac = UNINSURED_STATE_FACTOR.get(state.id, 0.9) if fs_id == "FS_UNINSURED" else 1.0
                for county_id, _, cw in state.counties:
                    pop = fp["population"] * SCALE * sw * cw * sfac * FUNDING_PATH[fs_id][year] * rng.normal(1, 0.02)
                    pop_rows.append(dict(DATASET="MARKET", YEAR=year, GEO_LEVEL="county", STATE=state.id,
                                         COUNTY=county_id, FUNDING_SOURCE_ID=fs_id, POPULATION=pop,
                                         SOURCE_TYPE="estimated"))
                    for cat in cats:
                        sh = mix.get(cat.id, 0.0)
                        if sh <= 0:
                            continue
                        pmpm = (fp["pmpm"] * sh * state.price_index * (1 + CATEGORY_TREND.get(cat.id, 0.04)) ** t
                                * (COVID_2020 if year == 2020 else 1.0))
                        flow_rows.append(dict(
                            DATASET="MARKET", YEAR=year, GEO_LEVEL="county", STATE=state.id, COUNTY=county_id,
                            SOURCE_NODE_ID=fs_id, TARGET_NODE_ID=cat.target, PAYER_LOB=None,
                            SPENDING_CATEGORY=cat.id, FLOW_KIND="funding_pmpm",
                            ANNUAL_FLOW_AMOUNT=pmpm * pop * 12,
                            PAID_AMOUNT=pmpm * pop * 12 * (1 - FUNDING_MEMBER_SHARE.get(fs_id, 0.0)
                                                           * (1.4 if cat.id.startswith("RX_") and fs_id != "FS_UNINSURED" else 1.0)),
                            INSTANCE_COUNT=None,
                            ALLOCATION_METHOD="population_pmpm_estimate", SOURCE_TYPE="estimated"))

    flows = pd.DataFrame(flow_rows)

    for fs_id in market_funding:
        if meta.funding_sources[fs_id].basis != "lump_sum":
            continue
        targets = meta.supplemental_targets(fs_id)
        hosp = flows[(flows["DATASET"] == "MARKET") & flows["TARGET_NODE_ID"].isin(targets)]
        by = hosp.groupby(["YEAR", "STATE", "TARGET_NODE_ID"])["ANNUAL_FLOW_AMOUNT"].sum()
        sup_rows = []
        for (year, s, tgt), amt in by.items():
            rate = SUPPLEMENTAL_RATE.get(s, DEFAULT_SUPPLEMENTAL_RATE) * rng.normal(1.0, 0.05)
            sup_rows.append(dict(
                DATASET="MARKET", YEAR=year, GEO_LEVEL="state", STATE=s, COUNTY=None,
                SOURCE_NODE_ID=fs_id, TARGET_NODE_ID=tgt, PAYER_LOB=None, SPENDING_CATEGORY=None,
                FLOW_KIND="supplemental", ANNUAL_FLOW_AMOUNT=amt * rate, PAID_AMOUNT=amt * rate, INSTANCE_COUNT=None,
                ALLOCATION_METHOD="supplemental_estimate", SOURCE_TYPE="estimated"))
        flows = pd.concat([flows, pd.DataFrame(sup_rows)], ignore_index=True)

    # ---- Recipients: revenue = Σ inflows; margin is a pool property ---------
    recipients = [n for n in tax.leaves() if n.receives_flows]
    inflow = flows.groupby(["DATASET", "YEAR", "STATE", "TARGET_NODE_ID"])["ANNUAL_FLOW_AMOUNT"].sum()
    state_margin = {(n.id, s): rng.normal(0, 0.008) for n in recipients for s in geo.states}
    margins: dict[tuple, float | None] = {}
    for leaf in recipients:
        ancestors = [a.id for a in tax.path(leaf.id)]
        for year in years:
            for s in geo.states:
                missing = any(lf == leaf.id and (st is None or st == s) and year in yrs
                              for lf, st, yrs in MISSING_MARGINS)
                shock = sum(MARGIN_SHOCK.get(a, {}).get(year, 0.0) for a in ancestors)
                margins[(year, s, leaf.id)] = None if missing else (
                    RECIPIENT_MARGIN.get(leaf.id, DEFAULT_MARGIN) + shock + state_margin[(leaf.id, s)]
                    + rng.normal(0, 0.004))
    for (ds, year, s, leaf_id), revenue in inflow.items():
        leaf = tax[leaf_id]
        margin = margins[(year, s, leaf_id)]
        metric_rows.append(dict(
            DATASET=ds, YEAR=year, GEO_LEVEL="state", STATE=s, COUNTY=None, **_levels(leaf, meta),
            LOB=None, ENROLLMENT=None, REVENUE=revenue, CLAIMS_EXPENSE=None, ALLOWED_SPEND=None,
            OPERATING_GAIN=None if margin is None else revenue * margin, OPERATING_MARGIN=margin,
            REVENUE_PMPM=None, ALLOWED_PMPM=None, PAID_PMPM=None, SOURCE_TYPE="allocated",
            COVERAGE_LEVEL=leaf.coverage))

    # ---- Top-down leaves (market only) -------------------------------------
    for leaf in meta.leaves_in_dataset("MARKET"):
        if leaf.economics != "top_down":
            continue
        tp = TOP_DOWN_PARAMS.get(leaf.id, DEFAULT_TOP_DOWN)
        for year in years:
            revenue = tp["revenue"] * (1 + tp["growth"]) ** (year - PARAM_YEAR) * rng.normal(1.0, 0.012)
            margin = (None if year in TOP_DOWN_MISSING_MARGIN.get(leaf.id, ())
                      else tp["margin"] + rng.normal(0, 0.006))
            metric_rows.append(dict(
                DATASET="MARKET", YEAR=year, GEO_LEVEL="national", STATE=NATIONAL, COUNTY=None,
                **_levels(leaf, meta), LOB=None, ENROLLMENT=None, REVENUE=revenue, CLAIMS_EXPENSE=None,
                ALLOWED_SPEND=None, OPERATING_GAIN=None if margin is None else revenue * margin,
                OPERATING_MARGIN=margin, REVENUE_PMPM=None, ALLOWED_PMPM=None, PAID_PMPM=None,
                SOURCE_TYPE="estimated", COVERAGE_LEVEL=leaf.coverage))

    return SyntheticBundle(
        pool_metrics=_conform(pd.DataFrame(metric_rows), POOL_METRICS_COLUMNS),
        value_flows=_conform(flows, VALUE_FLOWS_COLUMNS),
        coverage_population=_conform(pd.DataFrame(pop_rows), COVERAGE_POPULATION_COLUMNS),
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
    sort = [c for c in ("DATASET", "YEAR", "STATE", "COUNTY") if c in df.columns]
    return df.sort_values(sort, kind="stable").reset_index(drop=True)


def summary(bundle: SyntheticBundle, dataset: str = "MARKET") -> pd.DataFrame:
    """National revenue / gain / margin by layer and year ($B). For CLI inspection."""
    m = bundle.pool_metrics[bundle.pool_metrics["DATASET"] == dataset]
    g = m.groupby(["YEAR", "LAYER_ID"]).agg(revenue=("REVENUE", "sum"), gain=("OPERATING_GAIN", "sum"))
    covered = m[m["OPERATING_GAIN"].notna()].groupby(["YEAR", "LAYER_ID"])["REVENUE"].sum()
    g["margin_on_covered"] = g["gain"] / covered
    g["gain_coverage"] = covered / g["revenue"]
    g[["revenue", "gain"]] /= 1e9
    return g.round(3)


if __name__ == "__main__":  # pragma: no cover - manual inspection helper
    from profit_pools.core.data.contracts import validate_bundle

    b = generate()
    meta = default_metadata()
    issues = validate_bundle(b.pool_metrics, b.value_flows, b.coverage_population, meta)
    pd.set_option("display.width", 160)
    print(f"rows: metrics {len(b.pool_metrics):,} · flows {len(b.value_flows):,} · "
          f"population {len(b.coverage_population):,}")
    for ds in meta.datasets:
        print(f"\n{ds} 2025")
        print(summary(b, ds).xs(2025, level="YEAR"))
    print(f"\nValidation issues: {len(issues)}")
    for i in issues:
        print(f"  [{i.level}] {i.code}: {i.message}")
