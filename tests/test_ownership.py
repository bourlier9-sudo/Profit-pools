"""Ownership and value capture (Phase 4b): definitions, consolidation identities, scenario propagation."""

import numpy as np
import pytest

from profit_pools.core.data.adapters import SyntheticAdapter
from profit_pools.core.model import ownership as O
from profit_pools.core.model.cube import build_history
from profit_pools.core.model.engine import run_baseline
from profit_pools.core.model.scenario import Assumption, Scenario, Scope, YearRange
from profit_pools.core.model.scenario_engine import run_scenario
from profit_pools.core.model.views import Geo, ownership_view

Y = 2028


@pytest.fixture(scope="module")
def adapter():
    return SyntheticAdapter()


@pytest.fixture(scope="module")
def m(adapter):
    return adapter.metadata


@pytest.fixture(scope="module")
def hist(adapter):
    return build_history(adapter)


@pytest.fixture(scope="module")
def base(adapter):
    return run_baseline(adapter)


def A(driver, method, value, node=None, **kw):
    return Assumption(driver, method, value, Scope(YearRange(2027, None), node_id=node, **kw))


def run(hist, m, *a):
    r = run_scenario(hist, m, Scenario("t", tuple(a)))
    assert r.ok, [i.message for i in r.issues if i.level == "error"]
    return r.result


def test_assets_are_valid_leaves(m):
    for a in O.assets(m):
        n = m.standard[a.node_id]
        assert not n.children and n.receives_flows
        assert set(a.states) <= set(m.geography.states)


def test_value_capture_definitions(base):
    o = base.ownership
    assert len(o) and set(o["dataset"]) == {"ENTERPRISE"}
    assert np.allclose(o["routed"], o["routing_share"] * o["enterprise_spend"])
    assert np.allclose(o["routed"], o["routed_paid"] + o["routed_consumer"])
    assert np.allclose(o["owned_revenue"], o["routed"] * (1 + o["third_party_ratio"]))
    assert np.allclose(o["external_spend"] + o["routed"], o["enterprise_spend"])
    k = o["owned_margin"].notna()
    assert np.allclose(o.loc[k, "owned_gain"], o.loc[k, "owned_revenue"] * o.loc[k, "owned_margin"])
    assert o.loc[~k, "owned_gain"].isna().all()            # null = unavailable, never 0


def test_enterprise_spend_matches_flows(base):
    o = base.ownership
    f = base.flows
    for (y, s, n), v in o.groupby(["year", "state", "node_id"])["enterprise_spend"].sum().items():
        ff = f[(f["dataset"] == "ENTERPRISE") & (f["year"] == y) & (f["state"] == s) & (f["target_id"] == n)
               & (f["flow_kind"] == "payer_claims")]
        assert v == pytest.approx(ff["amount"].sum())


@pytest.mark.parametrize("geo", ["US", "NVL", "WMR"])
def test_consolidation_identities(m, base, geo):
    v = ownership_view(base, m, Y, Geo.parse(m, geo))
    t = v.totals
    assert t["consolidated_gain"] == pytest.approx(t["payer_gain"] + t["owned_gain"])
    assert t["consolidated_revenue"] - t["consolidated_expense"] == pytest.approx(t["consolidated_gain"])
    assert t["consolidated_revenue"] == pytest.approx(
        t["payer_revenue"] + t["owned_third_party"] + t["owned_member_paid"])
    assert v.assets["owned_revenue"].sum() == pytest.approx(t["owned_revenue"])


def test_county_view_is_unavailable_not_zero(m, base):
    v = ownership_view(base, m, Y, Geo.parse(m, "NVL-01"))
    assert v.assets.empty and v.note


def test_routing_changes_only_ownership(m, hist, base):
    r = run(hist, m, A("routing_share", "pp", 0.10, "PRV_PHYS", dataset="ENTERPRISE"))
    assert np.allclose(r.pools["revenue"], base.pools["revenue"])            # spending unchanged
    assert np.allclose(r.pools["gain"].fillna(0), base.pools["gain"].fillna(0))
    key = ["year", "state", "asset_id"]
    rs = (r.ownership.set_index(key)["routing_share"] - base.ownership.set_index(key)["routing_share"]).reset_index()
    rs = rs.merge(r.ownership[key + ["node_id"]], on=key)
    hit = rs["node_id"].isin(["PRV_PHYS_PRIMARY", "PRV_PHYS_BH"]) & (rs["year"] >= 2027)
    assert np.allclose(rs.loc[hit, "routing_share"], 0.10) and np.allclose(rs.loc[~hit, "routing_share"], 0.0)


def test_owned_margin_does_not_cross_apply(m, hist, base):
    r = run(hist, m, A("owned_margin", "pp", 0.02))
    assert np.allclose(r.pools["gain"].fillna(0), base.pools["gain"].fillna(0))   # market margins untouched
    o, b = r.ownership, base.ownership
    k = (o["year"] >= 2027) & o["owned_margin"].notna()
    assert np.allclose((o["owned_margin"] - b["owned_margin"])[k], 0.02)
    r2 = run(hist, m, A("provider_margin", "pp", 0.02, "PRV_PHYS"))
    assert np.allclose(r2.ownership["owned_margin"].fillna(0), b["owned_margin"].fillna(0))


def test_spending_assumption_flows_into_owned_assets(m, hist, base):
    r = run(hist, m, A("allowed_pmpm", "pct", 0.10, "PRV_PHYS_PRIMARY", dataset="ENTERPRISE"))
    o, b = r.ownership, base.ownership
    k = (o["node_id"] == "PRV_PHYS_PRIMARY") & (o["year"] >= 2027)
    assert np.allclose(o.loc[k, "routed"], b.loc[k, "routed"] * 1.10)


@pytest.mark.parametrize("bad, code", [
    (A("routing_share", "pp", 0.1, "PRV_HOSP"), "eligibility"),
    (A("routing_share", "pp", 0.1, "PRV_PHYS", dataset="MARKET"), "dataset.ownership"),
    (A("routing_share", "pp", 0.1, "PRV_PHYS", geography=("NVL-01",)), "geography.ownership"),
    (A("routing_share", "set", 1.5, "PRV_PHYS"), "value"),
    (A("routing_share", "pp", 0.9, "RX_MAIL"), "result.routing"),
])
def test_ownership_validation(m, hist, bad, code):
    r = run_scenario(hist, m, Scenario("t", (bad,)))
    assert code in {i.code for i in r.issues if i.level == "error"}
