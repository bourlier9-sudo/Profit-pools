"""Reconciliation and edge-case coverage of the fictional dataset."""

import numpy as np
import pandas as pd
import pytest

from profit_pools.core.data.contracts import (
    EXTERNAL_SOURCE,
    NATIONAL,
    validate_bundle,
    validate_pool_metrics,
    validate_value_flows,
)
from profit_pools.core.data.synthetic import generate


def test_contract_validation_passes(bundle, meta):
    issues = validate_bundle(bundle.pool_metrics, bundle.value_flows, meta)
    assert issues == []


def test_deterministic_for_seed(bundle, meta):
    again = generate(seed=bundle.seed, meta=meta)
    pd.testing.assert_frame_equal(bundle.pool_metrics, again.pool_metrics)
    pd.testing.assert_frame_equal(bundle.value_flows, again.value_flows)
    other = generate(seed=bundle.seed + 1, meta=meta)
    assert not np.isclose(other.pool_metrics["REVENUE"].sum(), bundle.pool_metrics["REVENUE"].sum())


def payer_rows(bundle):
    m = bundle.pool_metrics
    return m[m["LAYER_ID"] == "PAYER"]


def test_pmpm_to_annual_conversion(bundle):
    p = payer_rows(bundle)
    np.testing.assert_allclose(p["REVENUE"], p["REVENUE_PMPM"] * p["ENROLLMENT"] * 12, rtol=1e-12)
    np.testing.assert_allclose(p["CLAIMS_EXPENSE"], p["SPEND_PMPM"] * p["ENROLLMENT"] * 12, rtol=1e-12)


def test_payer_residual_opex_is_positive_and_derivable(bundle):
    p = payer_rows(bundle)
    resid = p["REVENUE"] - p["CLAIMS_EXPENSE"] - p["OPERATING_GAIN"]
    assert (resid > 0).all()
    ratio = resid / p["REVENUE"]
    assert ratio.between(0.05, 0.2).all()


def test_claims_flows_reconcile_to_payer_claims_at_every_grain(bundle):
    p = payer_rows(bundle)
    f = bundle.value_flows
    claims = f[f["FLOW_KIND"] == "claims"]
    for keys in (["YEAR"], ["YEAR", "STATE"], ["YEAR", "PAYER_LOB"], ["YEAR", "STATE", "COUNTY"]):
        pk = [("LOB" if k == "PAYER_LOB" else k) for k in keys]
        a = p.groupby(pk)["CLAIMS_EXPENSE"].sum().sort_index()
        b = claims.groupby(keys)["ANNUAL_FLOW_AMOUNT"].sum().sort_index()
        np.testing.assert_allclose(a.to_numpy(), b.to_numpy(), rtol=1e-12)


def test_recipient_revenue_equals_attributed_plus_residual(bundle, meta):
    m = bundle.pool_metrics
    f = bundle.value_flows
    rec = m[m["SUBPOOL_ID"].map(lambda i: meta.taxonomy[i].receives_flows)]
    nat_rev = rec.groupby("SUBPOOL_ID")["REVENUE"].sum()
    inflow = f.groupby("TARGET_NODE_ID")["ANNUAL_FLOW_AMOUNT"].sum()
    np.testing.assert_allclose(nat_rev.sort_index(), inflow.loc[nat_rev.index].sort_index(), rtol=1e-12)
    # Residual is a real, explicit share: neither zero nor the whole pool.
    resid = f[f["FLOW_KIND"] == "residual"].groupby("TARGET_NODE_ID")["ANNUAL_FLOW_AMOUNT"].sum()
    share = (resid / nat_rev).dropna()
    assert share.between(0.2, 0.7).all()


def test_gain_is_revenue_times_margin_for_recipients(bundle):
    m = bundle.pool_metrics
    r = m[(m["LAYER_ID"] != "PAYER") & m["OPERATING_MARGIN"].notna()]
    np.testing.assert_allclose(r["OPERATING_GAIN"], r["REVENUE"] * r["OPERATING_MARGIN"], rtol=1e-12)


def test_geography_aggregation_is_consistent(bundle):
    """County → state → national sums agree for additive payer metrics."""
    p = payer_rows(bundle)
    for col in ("ENROLLMENT", "REVENUE", "CLAIMS_EXPENSE", "OPERATING_GAIN"):
        by_county = p.groupby(["YEAR", "STATE", "COUNTY"])[col].sum()
        by_state = by_county.groupby(level=["YEAR", "STATE"]).sum()
        national = by_state.groupby(level="YEAR").sum()
        np.testing.assert_allclose(national, p.groupby("YEAR")[col].sum(), rtol=1e-12)


def test_service_counts_reconcile_and_respect_reliability(bundle, meta):
    f = bundle.value_flows
    s = f[f["SERVICE_COUNT"].notna()]
    assert len(s) > 0
    # Unreliable categories never carry counts.
    assert not s["SPENDING_CATEGORY"].isin(["IP_REHAB", "HOME_HEALTH", "IMAGING"]).any()
    # Psych days only for Commercial and Medicare.
    psych = s[s["SPENDING_CATEGORY"] == "IP_PSYCH"]
    assert set(psych["PAYER_LOB"].map(meta.lobs.group_of)) == {"COMM", "MCR"}
    # Allowed = services × average allowed per service, with a plausible price.
    price = s["ANNUAL_FLOW_AMOUNT"] / s["SERVICE_COUNT"]
    assert (price > 0).all()
    acute = s[s["SPENDING_CATEGORY"] == "IP_ACUTE"]
    assert (acute["ANNUAL_FLOW_AMOUNT"] / acute["SERVICE_COUNT"]).between(8_000, 60_000).all()
    # A category has exactly one unit.
    assert (s.groupby("SPENDING_CATEGORY")["SERVICE_UNIT"].nunique() == 1).all()


def test_top_down_pools_are_national_and_unattributed(bundle, meta):
    m = bundle.pool_metrics
    td_ids = [n.id for n in meta.taxonomy.leaves() if n.is_top_down]
    td = m[m["SUBPOOL_ID"].isin(td_ids)]
    assert set(td["GEO_LEVEL"]) == {"national"} and set(td["STATE"]) == {NATIONAL}
    assert set(td["COVERAGE_LEVEL"]) == {"top_down"}
    assert not bundle.value_flows["TARGET_NODE_ID"].isin(td_ids).any()
    assert not bundle.value_flows["SOURCE_NODE_ID"].isin(td_ids).any()


def test_edge_cases_present(bundle):
    m = bundle.pool_metrics
    # Negative margins in both payer and provider layers.
    assert (m.loc[m["LAYER_ID"] == "PAYER", "OPERATING_GAIN"] < 0).any()
    assert (m.loc[m["SUBPOOL_ID"] == "PRV_HOSP_PSYCH", "OPERATING_MARGIN"] < 0).all()
    # Missing margins: rehab in two states, imaging early years, BH services top-down.
    missing = m[m["OPERATING_MARGIN"].isna()]
    assert set(missing.loc[missing["SUBPOOL_ID"] == "PRV_HOSP_REHAB", "STATE"]) == {"WMR", "LKD"}
    assert set(missing.loc[missing["SUBPOOL_ID"] == "PRV_AMB_IMG", "YEAR"]) == {2019, 2020}
    assert set(missing.loc[missing["SUBPOOL_ID"] == "HS_BH", "YEAR"]) == {2019, 2020}
    # A LOB that does not exist everywhere: no rows rather than zeros.
    exp = m[m["LOB"] == "MCD_EXP"]
    assert "SRG" not in set(exp["STATE"])
    assert exp.loc[exp["STATE"] == "CLP", "YEAR"].min() == 2021
    # Partially attributed health-services pool.
    f = bundle.value_flows
    psvc = f[f["TARGET_NODE_ID"] == "HS_PAYER_SVC"]
    assert set(psvc["FLOW_KIND"]) == {"opex", "residual"}


def test_opex_flows_bounded_by_payer_residual_opex(bundle):
    p = payer_rows(bundle).set_index(["YEAR", "STATE", "COUNTY", "SUBPOOL_ID"])
    resid = p["REVENUE"] - p["CLAIMS_EXPENSE"] - p["OPERATING_GAIN"]
    f = bundle.value_flows
    opex = f[f["FLOW_KIND"] == "opex"].groupby(["YEAR", "STATE", "COUNTY", "SOURCE_NODE_ID"])["ANNUAL_FLOW_AMOUNT"].sum()
    opex.index.names = resid.index.names
    assert (opex <= resid.loc[opex.index]).all()


# --- the validator must catch corrupted data --------------------------------

def _codes(issues):
    return {i.code for i in issues}


def test_validator_detects_broken_pmpm_identity(bundle, meta):
    m = bundle.pool_metrics.copy()
    idx = m.index[m["LAYER_ID"] == "PAYER"][0]
    m.loc[idx, "REVENUE"] *= 1.01
    assert "payer.revenue_pmpm" in _codes(validate_pool_metrics(m, meta))


def test_validator_detects_unreconciled_claims(bundle, meta):
    f = bundle.value_flows.copy()
    idx = f.index[f["FLOW_KIND"] == "claims"][0]
    f.loc[idx, "ANNUAL_FLOW_AMOUNT"] += 1e6
    codes = _codes(validate_value_flows(f, bundle.pool_metrics, meta))
    assert {"reconcile.claims", "reconcile.recipient"} <= codes


def test_validator_detects_flow_to_top_down(bundle, meta):
    f = bundle.value_flows.copy()
    f.loc[f.index[0], "TARGET_NODE_ID"] = "MFR_BRAND"
    assert "flow.target" in _codes(validate_value_flows(f, bundle.pool_metrics, meta))


def test_validator_detects_unreliable_service_counts(bundle, meta):
    f = bundle.value_flows.copy()
    idx = f.index[(f["SPENDING_CATEGORY"] == "IMAGING")][0]
    f.loc[idx, ["SERVICE_COUNT", "SERVICE_UNIT"]] = [100.0, "procedure"]
    assert "services.unreliable" in _codes(validate_value_flows(f, bundle.pool_metrics, meta))


def test_validator_detects_wrong_geo_grain(bundle, meta):
    m = bundle.pool_metrics.copy()
    idx = m.index[m["SUBPOOL_ID"] == "MFR_BRAND"][0]
    m.loc[idx, ["GEO_LEVEL", "STATE"]] = ["state", "NVL"]
    assert "geo.grain" in _codes(validate_pool_metrics(m, meta))


@pytest.mark.parametrize("col", ["REVENUE", "YEAR"])
def test_validator_detects_missing_columns(bundle, meta, col):
    m = bundle.pool_metrics.drop(columns=[col])
    assert "schema.missing" in _codes(validate_pool_metrics(m, meta))


def test_residual_flows_come_from_external_source(bundle):
    f = bundle.value_flows
    assert (f.loc[f["FLOW_KIND"] == "residual", "SOURCE_NODE_ID"] == EXTERNAL_SOURCE).all()
