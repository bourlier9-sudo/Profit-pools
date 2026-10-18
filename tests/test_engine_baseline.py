"""Phase 2: baseline driver cube, forecast projection, engine identities and views."""

import numpy as np
import pandas as pd
import pytest

from profit_pools.core.data.adapters import SyntheticAdapter
from profit_pools.core.model.cube import ForecastRates, build_history, project
from profit_pools.core.model.engine import run, run_baseline
from profit_pools.core.model.views import Geo, cagr, node_series, rollup, select, totals

DATASETS = ["MARKET", "ENTERPRISE"]


@pytest.fixture(scope="module")
def adapter():
    return SyntheticAdapter()


@pytest.fixture(scope="module")
def result(adapter):
    return run_baseline(adapter)


@pytest.fixture(scope="module")
def m(adapter):
    return adapter.metadata


# --- history reproduces the facts exactly ------------------------------------

def test_history_reproduces_pool_facts(adapter, result):
    facts = adapter.pool_metrics()
    hist = result.pools[~result.pools["is_forecast"]]
    k = ["dataset", "year", "state", "node_id"]
    eng = hist.groupby(k)[["revenue"]].sum()
    eng["gain"] = hist[hist["margin_known"]].groupby(k)["gain"].sum()
    f = facts.rename(columns={"DATASET": "dataset", "YEAR": "year", "STATE": "state", "NODE_ID": "node_id"})
    fg = f.groupby(k)[["REVENUE", "OPERATING_GAIN"]].sum(min_count=1)
    j = fg.join(eng, how="outer")
    assert not j["revenue"].isna().any() and not j["REVENUE"].isna().any()
    np.testing.assert_allclose(j["revenue"], j["REVENUE"], rtol=1e-9)
    known = j["OPERATING_GAIN"].notna()
    np.testing.assert_allclose(j.loc[known, "gain"], j.loc[known, "OPERATING_GAIN"], rtol=1e-9)
    assert j.loc[~known, "gain"].isna().all()


def test_history_reproduces_payer_detail(adapter, result):
    f = adapter.pool_metrics()
    p = f[f["LAYER_ID"] == "PAYER"].set_index(["DATASET", "YEAR", "COUNTY", "NODE_ID"]).sort_index()
    e = result.pools[(result.pools["layer_id"] == "PAYER") & ~result.pools["is_forecast"]]
    e = e.set_index(["dataset", "year", "county", "node_id"]).sort_index()
    np.testing.assert_allclose(e["members"], p["ENROLLMENT"], rtol=1e-12)
    np.testing.assert_allclose(e["claims"], p["CLAIMS_EXPENSE"], rtol=1e-9)
    np.testing.assert_allclose(e["gain"], p["OPERATING_GAIN"], rtol=1e-9, atol=1e-3)


def test_history_reproduces_flows(adapter, result):
    f = adapter.value_flows()
    a = f.groupby(["DATASET", "YEAR", "FLOW_KIND", "TARGET_NODE_ID"])[["ANNUAL_FLOW_AMOUNT", "INSTANCE_COUNT"]].sum()
    h = result.flows[~result.flows["is_forecast"]]
    b = h.groupby(["dataset", "year", "flow_kind", "target_id"])[["amount", "instances"]].sum()
    np.testing.assert_allclose(b["amount"].sort_index(), a["ANNUAL_FLOW_AMOUNT"].sort_index(), rtol=1e-9)
    np.testing.assert_allclose(b["instances"].sort_index(), a["INSTANCE_COUNT"].sort_index(), rtol=1e-9)


# --- forecast identities -----------------------------------------------------

def test_forecast_years_present(result, m):
    assert sorted(result.pools["year"].unique()) == m.periods.all_years
    assert result.pools.loc[result.pools["year"] > 2025, "is_forecast"].all()


@pytest.mark.parametrize("ds", DATASETS)
def test_forecast_payer_identities(result, ds):
    p = result.pools[(result.pools["dataset"] == ds) & (result.pools["layer_id"] == "PAYER")]
    np.testing.assert_allclose(p["gain"], p["revenue"] - p["claims"] - p["opex"], rtol=1e-9)
    f = result.flows[(result.flows["dataset"] == ds) & (result.flows["flow_kind"] == "payer_claims")]
    sums = f.groupby(["year", "county", "source_id"])[["paid", "amount"]].sum()
    pc = p.set_index(["year", "county", "node_id"])
    np.testing.assert_allclose(pc.loc[sums.index, "claims"], sums["paid"], rtol=1e-9)      # claims = paid
    np.testing.assert_allclose(pc.loc[sums.index, "allowed"], sums["amount"], rtol=1e-9)


@pytest.mark.parametrize("ds", DATASETS)
def test_recipient_revenue_equals_inflows_every_year(result, ds):
    p = result.pools[(result.pools["dataset"] == ds) & result.pools["node_id"].str.match(r"^(PRV|RX_(?!PBM))")]
    f = result.flows[result.flows["dataset"] == ds]
    np.testing.assert_allclose(p.groupby(["year", "node_id"])["revenue"].sum().sort_index(),
                               f.groupby(["year", "target_id"])["amount"].sum().sort_index(), rtol=1e-9)
    known = p[p["margin_known"]]
    margin = known["gain"] / known["revenue"]
    # Constant margin: the forecast margin of a pool/state equals its base-year margin.
    k = known.assign(margin=margin).groupby(["state", "node_id", "year"])["margin"].first().unstack()
    np.testing.assert_allclose(k[2031], k[2025], rtol=1e-9)


def test_growth_compounds_from_base_year(result, m):
    p = result.pools[(result.pools["dataset"] == "MARKET") & (result.pools["node_id"] == "PAY_MA_SNP")]
    by = p.groupby("year")["members"].sum()
    np.testing.assert_allclose(by[2031] / by[2025], 1.07 ** 6, rtol=1e-9)        # SNP-specific rate
    ma = result.pools[(result.pools["dataset"] == "MARKET") & (result.pools["node_id"] == "PAY_MA_IND")]
    by = ma.groupby("year")["members"].sum()
    np.testing.assert_allclose(by[2027] / by[2025], 1.045 ** 2, rtol=1e-9)       # inherits MCR group rate


def test_allowed_pmpm_trend_is_utilization_times_unit_cost(adapter, m):
    cube = project(build_history(adapter), m)
    s = cube.spend[(cube.spend["category"] == "OP_ED") & (cube.spend["year"].isin([2025, 2026]))]
    s = s.set_index(["dataset", "state", "county", "source_id", "year"])["allowed_pmpm"].unstack()
    np.testing.assert_allclose(s[2026] / s[2025], 1.005 * 1.04, rtol=1e-12)     # PRV_HOSP rates


def test_hierarchical_rate_lookup(m):
    r = ForecastRates(m)
    assert r.rate("enrollment_growth", "MA_SNP") == 0.07           # LOB key
    assert r.rate("enrollment_growth", "MA_GRP") == 0.045          # → group
    assert r.rate("enrollment_growth", "COMM_SG") == 0.002
    assert r.rate("unit_cost_trend", "RX_SPEC_HEALTHSYS") == 0.07  # → ancestor RX_SPECIALTY
    assert r.rate("unit_cost_trend", "RX_MAIL") == 0.035           # no ancestor key → default
    assert r.rate("utilization_trend", "ASC") == 0.03              # → PRV_AMB
    assert r.rate("utilization_trend", "RX_CHAIN_BRAND") == 0.015  # → PHARMACY layer
    assert r.rate("top_down_growth", "HS_BH_UM") == 0.09           # → HS_BH
    assert r.rate("top_down_growth", "RX_PBM_GOV") == 0.05


def test_custom_rates_flow_through(adapter, m):
    """The scenario engine will swap the rate function; the engine needs nothing else."""
    base = ForecastRates(m)

    def rates(driver, rows, year):
        out = base(driver, rows, year)
        if driver == "enrollment_growth":
            out = out + 0.01
        return out

    alt = run(project(build_history(adapter), m, rates), m)
    b = run_baseline(adapter)

    def members(r):
        p = r.pools[(r.pools["dataset"] == "MARKET") & (r.pools["year"] == 2031)]
        return p["members"].sum()

    assert members(alt) > members(b)
    # Downstream: more members → more claims → more provider revenue.
    def prov(r):
        p = r.pools[(r.pools["dataset"] == "MARKET") & (r.pools["year"] == 2031) & (r.pools["layer_id"] == "PROVIDER")]
        return p["revenue"].sum()
    assert prov(alt) > prov(b)


# --- views ---------------------------------------------------------------------

@pytest.mark.parametrize("ds", DATASETS)
def test_rollup_reconciles_at_every_node(result, m, ds):
    year = 2027
    top = rollup(result, m, ds, year).rows.set_index("node_id")
    stack = list(top.index)
    while stack:
        nid = stack.pop()
        if all(not m.in_dataset(c.id, ds) for c in m.taxonomy.children(nid)) or m.taxonomy[nid].is_leaf:
            continue
        kids = rollup(result, m, ds, year, nid).rows
        parent = rollup(result, m, ds, year, m.taxonomy[nid].parent_id).rows.set_index("node_id").loc[nid]
        assert np.isclose(kids["revenue"].sum(), parent["revenue"], rtol=1e-9), nid
        assert np.isclose(kids["revenue_with_margin"].sum(), parent["revenue_with_margin"], rtol=1e-9), nid
        assert np.isclose(kids["share_of_parent"].sum(), 1.0)
        stack += [k for k in kids["node_id"] if not m.taxonomy[k].is_leaf]


def test_rollup_excludes_nodes_outside_dataset(result, m):
    ent = rollup(result, m, "ENTERPRISE", 2025).rows
    assert set(ent["node_id"]) == {"PAYER", "PROVIDER", "PHARMACY"}
    assert list(rollup(result, m, "ENTERPRISE", 2025, "PHARMACY").rows["node_id"]) == ["RX_DISP"]
    with pytest.raises(ValueError):
        rollup(result, m, "ENTERPRISE", 2025, "MANUFACTURER")


def test_geography_sums(result, m):
    nat = rollup(result, m, "MARKET", 2025, "PROVIDER").rows["revenue"].sum()
    states = sum(rollup(result, m, "MARKET", 2025, "PROVIDER", Geo("state", s)).rows["revenue"].sum()
                 for s in m.geography.states)
    assert np.isclose(nat, states, rtol=1e-9)
    st = rollup(result, m, "MARKET", 2025, "PAYER", Geo("state", "NVL")).rows["revenue"].sum()
    counties = sum(rollup(result, m, "MARKET", 2025, "PAYER", Geo("county", c)).rows["revenue"].sum()
                   for c, _, _ in m.geography.states["NVL"].counties)
    assert np.isclose(st, counties, rtol=1e-9)


def test_county_view_excludes_state_level_supplemental(result, m):
    state = rollup(result, m, "MARKET", 2025, "PRV_HOSP", Geo("state", "NVL")).rows["revenue"].sum()
    counties = sum(rollup(result, m, "MARKET", 2025, "PRV_HOSP", Geo("county", c)).rows["revenue"].sum()
                   for c, _, _ in m.geography.states["NVL"].counties)
    sup = result.flows
    sup = sup[(sup["dataset"] == "MARKET") & (sup["year"] == 2025) & (sup["state"] == "NVL")
              & (sup["flow_kind"] == "supplemental")]["amount"].sum()
    assert sup > 0 and np.isclose(state, counties + sup, rtol=1e-9)
    v = rollup(result, m, "MARKET", 2025, "PRV_HOSP", Geo("county", "NVL-01"))
    assert any("supplemental" in n for n in v.notes)


def test_top_down_unavailable_below_national(result, m):
    v = rollup(result, m, "MARKET", 2025, None, Geo("state", "ESH"))
    assert {u.node_id for u in v.unavailable} == {"MANUFACTURER", "HEALTH_SERVICES"}
    assert "PHARMACY" in set(v.rows["node_id"])          # dispensing is available
    v = rollup(result, m, "MARKET", 2025, "PHARMACY", Geo("state", "ESH"))
    assert [u.node_id for u in v.unavailable] == ["RX_PBM"]


def test_missing_margin_coverage(result, m):
    v = rollup(result, m, "MARKET", 2025, "PRV_HOSP_SPECIALTY", Geo("state", "WMR")).rows.set_index("node_id")
    assert np.isnan(v.loc["PRV_HOSP_REHAB", "margin"]) and v.loc["PRV_HOSP_REHAB", "margin_coverage"] == 0
    nat = rollup(result, m, "MARKET", 2025, "PRV_HOSP_SPECIALTY").rows.set_index("node_id")
    assert 0 < nat.loc["PRV_HOSP_REHAB", "margin_coverage"] < 1
    assert np.isfinite(nat.loc["PRV_HOSP_REHAB", "margin"])


def test_pmpms_recomputed_after_aggregation(result, m):
    v = rollup(result, m, "MARKET", 2025, "PAYER").rows.set_index("node_id")
    for nid, row in v.iterrows():
        assert np.isclose(row["revenue_pmpm"], row["revenue"] / (row["members"] * 12))
    prov = rollup(result, m, "MARKET", 2025, None).rows.set_index("node_id").loc["PROVIDER"]
    assert np.isnan(prov["members"]) and np.isnan(prov["revenue_pmpm"])


def test_totals_and_series(result, m):
    t = totals(result, m, "MARKET", 2025)
    layers = rollup(result, m, "MARKET", 2025).rows
    assert np.isclose(t["gross_revenue"], layers["revenue"].sum())
    assert np.isclose(t["operating_gain"], layers["gain"].sum())
    s = node_series(result, m, "MARKET", "PAYER").set_index("year")
    assert np.isclose(s.loc[2025, "revenue"], layers.set_index("node_id").loc["PAYER", "revenue"])
    assert s.loc[2026, "is_forecast"] and not s.loc[2025, "is_forecast"]


def test_cagr_rules():
    assert np.isclose(cagr(100, 121, 2), 0.1)
    assert np.isnan(cagr(-5, 10, 3)) and np.isnan(cagr(5, -1, 3)) and np.isnan(cagr(0, 10, 3))
    assert np.isnan(cagr(np.nan, 10, 3))


def test_engine_is_fast(adapter, m):
    import time
    cube = project(build_history(adapter), m)
    t = time.perf_counter()
    run(cube, m)
    assert time.perf_counter() - t < 3.0


# --- paid / allowed / consumer -----------------------------------------------

def test_flow_split_and_gain_attribution(result):
    f = result.flows
    np.testing.assert_allclose(f["consumer"], f["amount"] - f["paid"], rtol=1e-12)
    # Flow gain sums to recipient gain wherever the margin is known.
    p = result.pools[result.pools["revenue_paid"].notna() & result.pools["margin_known"]]
    a = p.groupby(["dataset", "year", "node_id"])["gain"].sum()
    b = f[f["margin"].notna()].groupby(["dataset", "year", "target_id"])["gain"].sum()
    b.index.names = a.index.names
    np.testing.assert_allclose(a.sort_index(), b.loc[a.index].sort_index(), rtol=1e-9)


@pytest.mark.parametrize("ds", DATASETS)
def test_spend_basis_views_reconcile(result, m, ds):
    from profit_pools.core.model.views import rollup
    allowed = rollup(result, m, ds, 2025, "PROVIDER").rows.set_index("node_id")
    paid = rollup(result, m, ds, 2025, "PROVIDER", basis="paid").rows.set_index("node_id")
    cons = rollup(result, m, ds, 2025, "PROVIDER", basis="consumer").rows.set_index("node_id")
    for col in ("revenue", "revenue_with_margin", "gain"):
        np.testing.assert_allclose(allowed[col], paid[col] + cons[col], rtol=1e-9)
    # The pool margin applies to every basis: identical at leaf × state grain (aggregates differ by mix).
    leaf_a = rollup(result, m, ds, 2025, "PRV_OP", Geo("state", "NVL")).rows.set_index("node_id")["margin"]
    leaf_p = rollup(result, m, ds, 2025, "PRV_OP", Geo("state", "NVL"), "paid").rows.set_index("node_id")["margin"]
    np.testing.assert_allclose(leaf_p, leaf_a.loc[leaf_p.index], rtol=1e-9)


def test_spend_basis_excludes_unsplit_pools(result, m):
    v = rollup(result, m, "MARKET", 2025, None, basis="consumer")
    assert set(v.rows["node_id"]) == {"PROVIDER", "PHARMACY"}
    assert {u.node_id for u in v.unavailable} == {"PAYER", "MANUFACTURER", "HEALTH_SERVICES"}
    v = rollup(result, m, "MARKET", 2025, "PHARMACY", basis="paid")
    assert [u.node_id for u in v.unavailable] == ["RX_PBM"]


@pytest.mark.parametrize("segment_by", ["hierarchy", "geography", "funding"])
@pytest.mark.parametrize("basis", ["allowed", "consumer"])
@pytest.mark.parametrize("parent", [None, "PRV_HOSP", "PAY_COMM"])
def test_marimekko_tiles_reconcile_to_rollup(result, m, segment_by, basis, parent):
    from profit_pools.core.model.views import marimekko_tiles
    if basis != "allowed" and parent == "PAY_COMM":
        pytest.skip("payer pools have no paid/consumer split")
    for geo in (Geo(), Geo("state", "NVL")):
        cols = rollup(result, m, "MARKET", 2025, parent, geo, basis).rows.set_index("node_id")
        tiles = marimekko_tiles(result, m, "MARKET", 2025, parent, geo, basis, segment_by).rows
        sums = tiles.groupby("col_id")[["revenue", "gain", "revenue_with_margin"]].sum()
        np.testing.assert_allclose(sums["revenue"], cols.loc[sums.index, "revenue"], rtol=1e-9)
        np.testing.assert_allclose(sums["revenue_with_margin"], cols.loc[sums.index, "revenue_with_margin"], rtol=1e-9)
        np.testing.assert_allclose(sums["gain"], cols.loc[sums.index, "gain"].fillna(0), rtol=1e-9, atol=1)
        assert set(sums.index) == set(cols.index)
        assert np.allclose(tiles.groupby("col_id")["share_in_col"].sum(), 1.0)


def test_funding_segments_name_every_component(result, m):
    from profit_pools.core.model.views import marimekko_tiles, revenue_components
    tiles = marimekko_tiles(result, m, "MARKET", 2025, "PRV_HOSP", segment_by="funding").rows
    labels = set(tiles["seg_label"])
    assert {"Commercial", "Medicare Advantage", "Medicaid Managed Care", "Uninsured", "Supplemental"} <= labels
    comp = revenue_components(result, m, "MARKET", 2025, "PRV_HOSP")
    np.testing.assert_allclose(comp["allowed"], comp["paid"] + comp["consumer"], rtol=1e-12)
    assert np.isclose(comp["share"].sum(), 1.0)
    unins = comp.set_index("funder").loc["Uninsured"]
    assert np.isclose(unins["paid"], 0.0)


def test_change_drivers_sum_to_parent_change(result, m):
    from profit_pools.core.model.views import change_drivers
    d = change_drivers(result, m, "MARKET", 2025, "PROVIDER")
    cur = rollup(result, m, "MARKET", 2025, None).rows.set_index("node_id").loc["PROVIDER", "revenue"]
    prev = rollup(result, m, "MARKET", 2024, None).rows.set_index("node_id").loc["PROVIDER", "revenue"]
    assert np.isclose(d["d_revenue"].sum(), cur - prev, rtol=1e-9)
    assert np.isclose(d["share_of_change"].sum(), 1.0)
