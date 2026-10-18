"""Reconciliation and edge-case coverage of the fictional dataset."""

import numpy as np
import pandas as pd
import pytest

from profit_pools.core.data.contracts import (
    NATIONAL,
    validate_bundle,
    validate_pool_metrics,
    validate_value_flows,
)
from profit_pools.core.data.synthetic import generate

DATASETS = ["MARKET", "ENTERPRISE"]


def test_contract_validation_passes(bundle, meta):
    assert validate_bundle(bundle.pool_metrics, bundle.value_flows, bundle.coverage_population, meta) == []


def test_deterministic_for_seed(bundle, meta):
    again = generate(seed=bundle.seed, meta=meta)
    pd.testing.assert_frame_equal(bundle.pool_metrics, again.pool_metrics)
    pd.testing.assert_frame_equal(bundle.value_flows, again.value_flows)
    pd.testing.assert_frame_equal(bundle.coverage_population, again.coverage_population)


def rows(df, ds, **eq):
    out = df[df["DATASET"] == ds]
    for k, v in eq.items():
        out = out[out[k] == v]
    return out


@pytest.mark.parametrize("ds", DATASETS)
def test_pmpm_to_annual_conversion(bundle, ds):
    p = rows(bundle.pool_metrics, ds, LAYER_ID="PAYER")
    np.testing.assert_allclose(p["REVENUE"], p["REVENUE_PMPM"] * p["ENROLLMENT"] * 12, rtol=1e-12)
    np.testing.assert_allclose(p["CLAIMS_EXPENSE"], p["SPEND_PMPM"] * p["ENROLLMENT"] * 12, rtol=1e-12)


@pytest.mark.parametrize("ds", DATASETS)
def test_payer_residual_opex_is_positive(bundle, ds):
    p = rows(bundle.pool_metrics, ds, LAYER_ID="PAYER")
    resid = p["REVENUE"] - p["CLAIMS_EXPENSE"] - p["OPERATING_GAIN"]
    assert (resid / p["REVENUE"]).between(0.04, 0.2).all()


@pytest.mark.parametrize("ds", DATASETS)
def test_claims_flows_reconcile_to_payer_claims_at_every_grain(bundle, ds):
    p = rows(bundle.pool_metrics, ds, LAYER_ID="PAYER")
    claims = rows(bundle.value_flows, ds, FLOW_KIND="payer_claims")
    for keys in (["YEAR"], ["YEAR", "STATE"], ["YEAR", "PAYER_LOB"], ["YEAR", "STATE", "COUNTY"]):
        pk = [("LOB" if k == "PAYER_LOB" else k) for k in keys]
        a = p.groupby(pk)["CLAIMS_EXPENSE"].sum().sort_index()
        b = claims.groupby(keys)["ANNUAL_FLOW_AMOUNT"].sum().sort_index()
        np.testing.assert_allclose(a.to_numpy(), b.to_numpy(), rtol=1e-12)


@pytest.mark.parametrize("ds", DATASETS)
def test_recipient_revenue_is_sum_of_explicit_components(bundle, meta, ds):
    m = rows(bundle.pool_metrics, ds)
    rec = m[m["NODE_ID"].map(lambda i: meta.taxonomy[i].receives_flows)]
    inflow = rows(bundle.value_flows, ds).groupby("TARGET_NODE_ID")["ANNUAL_FLOW_AMOUNT"].sum()
    rev = rec.groupby("NODE_ID")["REVENUE"].sum()
    np.testing.assert_allclose(rev.sort_index(), inflow.loc[rev.index].sort_index(), rtol=1e-12)


def test_market_revenue_components(bundle, meta):
    f = rows(bundle.value_flows, "MARKET")
    assert set(f["FLOW_KIND"]) == {"payer_claims", "funding_pmpm", "supplemental"}
    # Every component is an explicit, named source — there is no plug.
    assert set(f.loc[f["FLOW_KIND"] != "payer_claims", "SOURCE_NODE_ID"]) == {
        "FS_UNINSURED", "FS_TRAD_MCR", "FS_SUPPLEMENTAL"}
    sup = f[f["FLOW_KIND"] == "supplemental"]
    assert set(sup["TARGET_NODE_ID"]) == set(meta.supplemental_targets("FS_SUPPLEMENTAL"))
    # Population-based components reconcile to population × PMPM via the population table.
    pop = bundle.coverage_population.set_index(["YEAR", "COUNTY", "FUNDING_SOURCE_ID"])["POPULATION"]
    fp = f[f["FLOW_KIND"] == "funding_pmpm"]
    per = fp.groupby(["YEAR", "COUNTY", "SOURCE_NODE_ID"])["ANNUAL_FLOW_AMOUNT"].sum()
    pmpm = per / (pop.loc[per.index].to_numpy() * 12)
    assert pmpm.groupby(level="SOURCE_NODE_ID").median().between(100, 2500).all()


def test_enterprise_view_is_a_subset_of_market(bundle, meta):
    m = bundle.pool_metrics
    ent = rows(m, "ENTERPRISE")
    assert set(ent["LAYER_ID"]) == {"PAYER", "PROVIDER", "PHARMACY"}
    assert not ent["NODE_ID"].str.startswith("RX_PBM").any()
    assert set(rows(bundle.value_flows, "ENTERPRISE")["FLOW_KIND"]) == {"payer_claims"}
    k = ["YEAR", "COUNTY", "LOB"]
    mk = rows(m, "MARKET", LAYER_ID="PAYER").set_index(k)["ENROLLMENT"]
    e = ent[ent["LAYER_ID"] == "PAYER"].set_index(k)["ENROLLMENT"]
    assert (e <= mk.loc[e.index]).all()
    # Every recipient's enterprise revenue is below its market revenue.
    rk = ["YEAR", "STATE", "NODE_ID"]
    mr = rows(m, "MARKET", LAYER_ID="PROVIDER").set_index(rk)["REVENUE"]
    er = rows(m, "ENTERPRISE", LAYER_ID="PROVIDER").set_index(rk)["REVENUE"]
    assert (er < mr.loc[er.index]).all()


def test_market_pmpm_is_benchmarked_on_enterprise(bundle):
    """Market allowed PMPM = enterprise state PMPM × a market factor (uniform within a state)."""
    f = bundle.value_flows
    p = bundle.pool_metrics
    key = ["YEAR", "STATE", "PAYER_LOB", "SPENDING_CATEGORY"]

    def pmpm(ds):
        flows = rows(f, ds, FLOW_KIND="payer_claims").groupby(key)["ANNUAL_FLOW_AMOUNT"].sum()
        mm = rows(p, ds, LAYER_ID="PAYER").groupby(["YEAR", "STATE", "LOB"])["ENROLLMENT"].sum() * 12
        mm.index.names = key[:3]
        return flows / mm.reindex(flows.index.droplevel(3)).to_numpy()

    ratio = (pmpm("MARKET") / pmpm("ENTERPRISE")).dropna()
    assert ratio.between(0.97, 1.05).all()


def test_gain_is_revenue_times_margin_for_recipients(bundle):
    m = bundle.pool_metrics
    r = m[(m["LAYER_ID"] != "PAYER") & m["OPERATING_MARGIN"].notna()]
    np.testing.assert_allclose(r["OPERATING_GAIN"], r["REVENUE"] * r["OPERATING_MARGIN"], rtol=1e-12)


def test_geography_aggregation_is_consistent(bundle):
    p = rows(bundle.pool_metrics, "MARKET", LAYER_ID="PAYER")
    for col in ("ENROLLMENT", "REVENUE", "CLAIMS_EXPENSE", "OPERATING_GAIN"):
        by_state = p.groupby(["YEAR", "STATE", "COUNTY"])[col].sum().groupby(level=["YEAR", "STATE"]).sum()
        np.testing.assert_allclose(by_state.groupby(level="YEAR").sum(), p.groupby("YEAR")[col].sum(), rtol=1e-12)


def test_instances_only_where_available_and_never_pharmacy(bundle, meta):
    f = bundle.value_flows
    inst = f[f["INSTANCE_COUNT"].notna()]
    assert len(inst) > 0
    assert set(inst["FLOW_KIND"]) == {"payer_claims"}
    layers = inst["TARGET_NODE_ID"].map(lambda t: meta.taxonomy[t].layer_id)
    assert set(layers) == {"PROVIDER"}
    assert not inst["SPENDING_CATEGORY"].isin(["IMAGING", "HOME_HEALTH"]).any()
    price = inst["ANNUAL_FLOW_AMOUNT"] / inst["INSTANCE_COUNT"]
    medsurg = price[inst["SPENDING_CATEGORY"] == "IP_MEDSURG"]
    assert medsurg.between(8_000, 60_000).all()


def test_top_down_pools_are_market_national_and_flowless(bundle, meta):
    m = bundle.pool_metrics
    td_ids = [n.id for n in meta.taxonomy.leaves() if n.is_top_down]
    td = m[m["NODE_ID"].isin(td_ids)]
    assert set(td["DATASET"]) == {"MARKET"}
    assert set(td["GEO_LEVEL"]) == {"national"} and set(td["STATE"]) == {NATIONAL}
    assert set(td_ids) == set(td["NODE_ID"])
    f = bundle.value_flows
    assert not f["TARGET_NODE_ID"].isin(td_ids).any() and not f["SOURCE_NODE_ID"].isin(td_ids).any()


def test_edge_cases_present(bundle):
    m = bundle.pool_metrics
    mkt = rows(m, "MARKET")
    assert (mkt.loc[mkt["LAYER_ID"] == "PAYER", "OPERATING_GAIN"] < 0).any()
    assert (mkt.loc[mkt["NODE_ID"] == "PRV_HOSP_PSYCH", "OPERATING_MARGIN"] < 0).all()
    assert (mkt.loc[mkt["NODE_ID"] == "HS_VBC", "OPERATING_MARGIN"] < 0).all()
    missing = mkt[mkt["OPERATING_MARGIN"].isna()]
    assert set(missing.loc[missing["NODE_ID"] == "PRV_HOSP_REHAB", "STATE"]) == {"WMR", "LKD"}
    assert set(missing.loc[missing["NODE_ID"] == "PRV_AMB_IMG", "YEAR"]) == {2019, 2020}
    assert set(missing.loc[missing["NODE_ID"] == "HS_BH_OTHER", "YEAR"]) == {2019, 2020}
    exp = mkt[mkt["LOB"] == "MCD_EXP"]
    assert "SRG" not in set(exp["STATE"])
    assert exp.loc[exp["STATE"] == "CLP", "YEAR"].min() == 2021
    ent = rows(m, "ENTERPRISE")
    assert "WMR" not in set(ent["STATE"])                       # no enterprise presence
    snp = ent[ent["LOB"] == "MA_SNP"]
    assert not set(snp["STATE"]) & {"SRG", "CLP"}               # enterprise SNP absent there


def test_uninsured_rises_with_medicaid_unwinding(bundle):
    pop = bundle.coverage_population
    u = pop[pop["FUNDING_SOURCE_ID"] == "FS_UNINSURED"].groupby("YEAR")["POPULATION"].sum()
    assert u[2024] > u[2022]


# --- the validator must catch corrupted data --------------------------------

def _codes(issues):
    return {i.code for i in issues}


def _vf(bundle, meta, flows=None, metrics=None):
    return validate_value_flows(flows if flows is not None else bundle.value_flows,
                                metrics if metrics is not None else bundle.pool_metrics,
                                bundle.coverage_population, meta)


def test_validator_detects_broken_pmpm_identity(bundle, meta):
    m = bundle.pool_metrics.copy()
    m.loc[m.index[m["LAYER_ID"] == "PAYER"][0], "REVENUE"] *= 1.01
    assert "payer.revenue_pmpm" in _codes(validate_pool_metrics(m, meta))


def test_validator_detects_unreconciled_claims(bundle, meta):
    f = bundle.value_flows.copy()
    f.loc[f.index[f["FLOW_KIND"] == "payer_claims"][0], "ANNUAL_FLOW_AMOUNT"] += 1e6
    assert {"reconcile.claims", "reconcile.recipient"} <= _codes(_vf(bundle, meta, flows=f))


def test_validator_detects_flow_to_top_down(bundle, meta):
    f = bundle.value_flows.copy()
    f.loc[f.index[0], "TARGET_NODE_ID"] = "RX_PBM_COMM"
    assert "flow.target" in _codes(_vf(bundle, meta, flows=f))


def test_validator_detects_pharmacy_instances(bundle, meta):
    f = bundle.value_flows.copy()
    f.loc[f.index[f["SPENDING_CATEGORY"] == "RX_MAIL"][0], "INSTANCE_COUNT"] = 100.0
    assert "instances.category" in _codes(_vf(bundle, meta, flows=f))


def test_validator_detects_funding_flows_in_enterprise(bundle, meta):
    f = bundle.value_flows.copy()
    f.loc[f.index[f["FLOW_KIND"] == "funding_pmpm"][0], "DATASET"] = "ENTERPRISE"
    assert "dataset.funding" in _codes(_vf(bundle, meta, flows=f))


def test_validator_detects_supplemental_to_non_hospital(bundle, meta):
    f = bundle.value_flows.copy()
    f.loc[f.index[f["FLOW_KIND"] == "supplemental"][0], "TARGET_NODE_ID"] = "PRV_PHYS_PRIMARY"
    assert "flow.supplemental_target" in _codes(_vf(bundle, meta, flows=f))


def test_validator_detects_excluded_node_in_enterprise(bundle, meta):
    m = bundle.pool_metrics.copy()
    m.loc[m.index[m["NODE_ID"] == "MFR_BRAND"][0], "DATASET"] = "ENTERPRISE"
    assert "dataset.excluded" in _codes(validate_pool_metrics(m, meta))


def test_validator_detects_wrong_level_columns(bundle, meta):
    m = bundle.pool_metrics.copy()
    m.loc[m.index[m["NODE_ID"] == "PRV_OP_ED"][0], "L3_ID"] = "PRV_IP"
    assert "taxonomy.path" in _codes(validate_pool_metrics(m, meta))


def test_validator_detects_wrong_geo_grain(bundle, meta):
    m = bundle.pool_metrics.copy()
    m.loc[m.index[m["NODE_ID"] == "MFR_BRAND"][0], ["GEO_LEVEL", "STATE"]] = ["state", "NVL"]
    assert "geo.grain" in _codes(validate_pool_metrics(m, meta))


def test_validator_detects_enterprise_exceeding_market(bundle, meta):
    m = bundle.pool_metrics.copy()
    idx = m.index[(m["DATASET"] == "ENTERPRISE") & (m["LAYER_ID"] == "PAYER")][0]
    m.loc[idx, ["ENROLLMENT", "REVENUE", "CLAIMS_EXPENSE", "OPERATING_GAIN"]] *= 50
    assert "dataset.subset" in _codes(validate_pool_metrics(m, meta))


@pytest.mark.parametrize("col", ["REVENUE", "DATASET"])
def test_validator_detects_missing_columns(bundle, meta, col):
    assert "schema.missing" in _codes(validate_pool_metrics(bundle.pool_metrics.drop(columns=[col]), meta))
