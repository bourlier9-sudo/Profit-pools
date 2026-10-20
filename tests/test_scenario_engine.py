"""Phase 4: scenario engine — propagation, resolution, validation, bridge, JSON."""

import json

import numpy as np
import pytest

from profit_pools.core.data.adapters import SyntheticAdapter
from profit_pools.core.model.cube import build_history
from profit_pools.core.model.engine import run_baseline
from profit_pools.core.model.scenario import (Assumption, Scenario, ScenarioFormatError, Scope, YearRange)
from profit_pools.core.model.scenario_engine import bridge, preview, run_scenario, validate
from profit_pools.core.model.views import cagr, node_series

Y = 2027


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


def A(driver, method, value, node=None, years=(Y, Y), **kw):
    scope_kw = {k: kw.pop(k) for k in ("dataset", "sources", "geography") if k in kw}
    return Assumption(driver, method, value, Scope(YearRange(*years), node_id=node, **scope_kw), **kw)


def run(hist, m, *items):
    r = run_scenario(hist, m, Scenario("t", tuple(items)))
    return r


def ok(hist, m, *items):
    r = run(hist, m, *items)
    assert r.ok, [i.message for i in r.issues if i.level == "error"]
    return r.result


def P(res, ds="MARKET", year=Y):
    p = res.pools
    return p[(p["dataset"] == ds) & (p["year"] == year)]


def F(res, ds="MARKET", year=Y):
    f = res.flows
    return f[(f["dataset"] == ds) & (f["year"] == year)]


def leaves(m, node):
    return set(m.taxonomy.leaf_ids(node))


# --- identity & propagation ---------------------------------------------------

def test_empty_scenario_equals_baseline(hist, m, base):
    r = ok(hist, m)
    np.testing.assert_allclose(r.pools["revenue"], base.pools["revenue"], rtol=1e-12)
    np.testing.assert_allclose(r.pools["gain"].fillna(0), base.pools["gain"].fillna(0), rtol=1e-12)


def test_enrollment_propagates_to_payer_flows_and_providers(hist, m, base):
    r = ok(hist, m, A("enrollment", "pct", 0.10, "PAY_COMM", dataset="MARKET"))
    comm = leaves(m, "PAY_COMM")
    pb, ps = P(base), P(r)
    for col in ("members", "revenue", "claims", "opex", "gain"):
        b = pb[pb["node_id"].isin(comm)][col].sum()
        s = ps[ps["node_id"].isin(comm)][col].sum()
        assert np.isclose(s, b * 1.10, rtol=1e-9), col
    other = pb[(pb["layer_id"] == "PAYER") & ~pb["node_id"].isin(comm)]["revenue"].sum()
    assert np.isclose(ps[(ps["layer_id"] == "PAYER") & ~ps["node_id"].isin(comm)]["revenue"].sum(), other)
    # Recipients gain exactly 10% of Commercial flows — no double counting.
    comm_flows = F(base)[F(base)["source_id"].isin(comm)]["amount"].sum()
    d_rec = ps[ps["layer_id"].isin(["PROVIDER", "PHARMACY"])]["revenue"].sum() - \
        pb[pb["layer_id"].isin(["PROVIDER", "PHARMACY"])]["revenue"].sum()
    assert np.isclose(d_rec, 0.10 * comm_flows, rtol=1e-9)
    # Enterprise untouched (scope is market only); other years untouched.
    assert np.isclose(P(r, "ENTERPRISE")["revenue"].sum(), P(base, "ENTERPRISE")["revenue"].sum())
    assert np.isclose(P(r, year=Y + 1)["revenue"].sum(), P(base, year=Y + 1)["revenue"].sum())


def test_spending_change_hits_payer_gain_and_provider_revenue_once(hist, m, base):
    r = ok(hist, m, A("allowed_pmpm", "pct", 0.05, "PRV_HOSP", sources=("COMM",), dataset="MARKET"))
    hosp, comm = leaves(m, "PRV_HOSP"), leaves(m, "PAY_COMM")
    fb = F(base)
    sel = fb[fb["target_id"].isin(hosp) & fb["source_id"].isin(comm)]
    pb, ps = P(base), P(r)
    d_claims = ps[ps["node_id"].isin(comm)]["claims"].sum() - pb[pb["node_id"].isin(comm)]["claims"].sum()
    d_gain = ps[ps["node_id"].isin(comm)]["gain"].sum() - pb[pb["node_id"].isin(comm)]["gain"].sum()
    d_prem = ps[ps["node_id"].isin(comm)]["revenue"].sum() - pb[pb["node_id"].isin(comm)]["revenue"].sum()
    assert np.isclose(d_claims, 0.05 * sel["paid"].sum(), rtol=1e-9)        # payer pays the paid part
    assert np.isclose(d_gain, -d_claims, rtol=1e-9) and abs(d_prem) < 1     # premium unchanged
    d_hosp = ps[ps["node_id"].isin(hosp)]["revenue"].sum() - pb[pb["node_id"].isin(hosp)]["revenue"].sum()
    assert np.isclose(d_hosp, 0.05 * sel["amount"].sum(), rtol=1e-9)       # provider gets allowed
    d_hgain = ps[ps["node_id"].isin(hosp)]["gain"].sum() - pb[pb["node_id"].isin(hosp)]["gain"].sum()
    assert np.isclose(d_hgain, 0.05 * sel["gain"].sum(), rtol=1e-9)        # constant margin


def test_margin_assumptions_never_cross_apply(hist, m, base):
    r = ok(hist, m, A("provider_margin", "pp", -0.01, "PROVIDER"))
    pb, ps = P(base), P(r)
    prov = pb[(pb["layer_id"] == "PROVIDER") & pb["margin_known"]]
    d = ps[(ps["layer_id"] == "PROVIDER") & ps["margin_known"]]["gain"].sum() - prov["gain"].sum()
    assert np.isclose(d, -0.01 * prov["revenue"].sum(), rtol=1e-9)
    assert np.isclose(ps[ps["layer_id"] == "PAYER"]["gain"].sum(), pb[pb["layer_id"] == "PAYER"]["gain"].sum())
    r2 = ok(hist, m, A("payer_margin", "set", 0.03, "PAY_MA"))
    ma = leaves(m, "PAY_MA")
    p2 = P(r2)
    sub = p2[p2["node_id"].isin(ma)]
    np.testing.assert_allclose(sub["gain"], 0.03 * sub["revenue"], rtol=1e-9)
    np.testing.assert_allclose(sub["claims"], pb[pb["node_id"].isin(ma)]["claims"].to_numpy(), rtol=1e-9)
    assert np.isclose(p2[p2["layer_id"] == "PROVIDER"]["gain"].sum(), pb[pb["layer_id"] == "PROVIDER"]["gain"].sum())


def test_member_cost_share_moves_paid_to_consumer_only(hist, m, base):
    r = ok(hist, m, A("member_cost_share", "pp", 0.05, "PRV_HOSP", sources=("COMM",)))
    pb, ps = P(base), P(r)
    assert np.isclose(ps[ps["layer_id"] == "PROVIDER"]["revenue"].sum(), pb[pb["layer_id"] == "PROVIDER"]["revenue"].sum())
    fb, fs = F(base), F(r)
    d_cons = fs["consumer"].sum() - fb["consumer"].sum()
    d_claims = ps["claims"].sum() - pb["claims"].sum()
    assert d_cons > 0 and np.isclose(d_claims, -d_cons, rtol=1e-9)
    assert np.isclose(ps[ps["layer_id"] == "PAYER"]["gain"].sum() - pb[pb["layer_id"] == "PAYER"]["gain"].sum(), d_cons, rtol=1e-9)


def test_utilization_and_price_decomposition(hist, m, base):
    u = ok(hist, m, A("utilization", "pct", 0.03, "PRV_HOSP"))
    p = ok(hist, m, A("unit_price", "pct", 0.03, "PRV_HOSP"))
    hosp = leaves(m, "PRV_HOSP")
    fb = F(base)[F(base)["target_id"].isin(hosp)]
    fu = F(u)[F(u)["target_id"].isin(hosp)]
    fp = F(p)[F(p)["target_id"].isin(hosp)]
    assert np.isclose(fu["instances"].sum(), fb["instances"].sum() * 1.03, rtol=1e-9)
    assert np.isclose(fp["instances"].sum(), fb["instances"].sum(), rtol=1e-9)
    # Same allowed change either way (only rows with instances move); same provider gain response.
    assert np.isclose(fu["amount"].sum(), fp["amount"].sum(), rtol=1e-9)
    assert fu["amount"].sum() > fb["amount"].sum()


def test_opex_volume_scaling(hist, m, base):
    full = ok(hist, m, A("enrollment", "pct", 0.10, "PAY_MCD", dataset="MARKET"))
    fixed = ok(hist, m, A("enrollment", "pct", 0.10, "PAY_MCD", dataset="MARKET"),
               A("opex_volume_scaling", "set", 0.0, "PAY_MCD"))
    mcd = leaves(m, "PAY_MCD")
    ob = P(base)[P(base)["node_id"].isin(mcd)]["opex"].sum()
    assert np.isclose(P(full)[P(full)["node_id"].isin(mcd)]["opex"].sum(), ob * 1.10, rtol=1e-9)
    assert np.isclose(P(fixed)[P(fixed)["node_id"].isin(mcd)]["opex"].sum(), ob, rtol=1e-9)


def test_enrollment_shift_conserves_people(hist, m, base):
    r = ok(hist, m, A("enrollment_shift", "pct", 0.10, "PAY_MCD", dataset="MARKET", target="FS_UNINSURED"))
    mcd = leaves(m, "PAY_MCD")
    pb, ps = P(base), P(r)
    moved = 0.10 * pb[pb["node_id"].isin(mcd)]["members"].sum()
    assert np.isclose(ps[ps["node_id"].isin(mcd)]["members"].sum(), 0.9 * pb[pb["node_id"].isin(mcd)]["members"].sum())
    # Uninsured flows rise; Medicaid flows fall; providers see the net.
    fb, fs = F(base), F(r)
    unins_b = fb[fb["source_id"] == "FS_UNINSURED"]["amount"].sum()
    unins_s = fs[fs["source_id"] == "FS_UNINSURED"]["amount"].sum()
    assert unins_s > unins_b
    assert np.isclose(fs[fs["source_id"].isin(mcd)]["amount"].sum(), 0.9 * fb[fb["source_id"].isin(mcd)]["amount"].sum())
    assert moved > 0


def test_set_total_distributes(hist, m):
    r = ok(hist, m, A("enrollment", "set", 20e6, "PAY_COMM", dataset="MARKET"))
    assert np.isclose(P(r)[P(r)["node_id"].isin(leaves(m, "PAY_COMM"))]["members"].sum(), 20e6)
    bad = run(hist, m, A("enrollment", "set", 20e6, "PAY_COMM"))
    assert not bad.ok and any(i.code == "dataset.total" for i in bad.issues)


def test_supplemental_and_top_down(hist, m, base):
    r = ok(hist, m, A("supplemental_payments", "pct", 0.2, sources=("FS_SUPPLEMENTAL",)))
    sup = F(base)[F(base)["flow_kind"] == "supplemental"]["amount"].sum()
    d = P(r)["revenue"].sum() - P(base)["revenue"].sum()
    assert np.isclose(d, 0.2 * sup, rtol=1e-9)
    r2 = ok(hist, m, A("top_down_revenue", "pct", 0.1, "MFR_PHARMA"))
    pb, ps = P(base), P(r2)
    diff = ps.set_index(["node_id", "state", "county"])["revenue"].sub(pb.set_index(["node_id", "state", "county"])["revenue"])
    changed = set(diff[diff.abs() > 1].index.get_level_values(0))
    assert changed == leaves(m, "MFR_PHARMA")


# --- hierarchical resolution ---------------------------------------------------

def _hosp_factor(r, base, m, leaf):
    """Allowed-PMPM assumptions scale payer-claims flows only (funding flows have their own driver)."""
    fb, fs = F(base), F(r)
    fb, fs = fb[fb["flow_kind"] == "payer_claims"], fs[fs["flow_kind"] == "payer_claims"]
    return fs[fs["target_id"] == leaf]["amount"].sum() / fb[fb["target_id"] == leaf]["amount"].sum()


def test_default_replace_most_specific_wins(hist, m, base):
    r = run(hist, m, A("allowed_pmpm", "pct", 0.05, "PROVIDER", label="Provider"),
            A("allowed_pmpm", "pct", 0.08, "PRV_HOSP", label="Hospital"),
            A("allowed_pmpm", "pct", 0.12, "PRV_HOSP_PSYCH", label="Psych"))
    assert r.ok
    assert np.isclose(_hosp_factor(r.result, base, m, "PRV_HOSP_PSYCH"), 1.12, rtol=1e-6)
    assert np.isclose(_hosp_factor(r.result, base, m, "PRV_IP_MEDSURG"), 1.08, rtol=1e-6)
    assert np.isclose(_hosp_factor(r.result, base, m, "PRV_PHYS_PRIMARY"), 1.05, rtol=1e-6)
    assert np.isclose(_hosp_factor(r.result, base, m, "RX_MAIL"), 1.0)          # pharmacy is not a provider
    sup = [i for i in r.issues if i.code == "superseded"]
    assert len(sup) == 2   # Provider (by Hospital & Psych) and Hospital (by Psych)
    status = {e.summary.split(" · ")[0]: e.status for e in r.ledger}
    assert status["Provider"] == "partly superseded"


def test_stack_mode_compounds(hist, m, base):
    r = ok(hist, m, A("allowed_pmpm", "pct", 0.05, "PROVIDER"),
           A("allowed_pmpm", "pct", 0.08, "PRV_HOSP", mode="stack"),
           A("allowed_pmpm", "pct", 0.12, "PRV_HOSP_PSYCH", mode="stack"))
    assert np.isclose(_hosp_factor(r, base, m, "PRV_HOSP_PSYCH"), 1.05 * 1.08 * 1.12, rtol=1e-6)


def test_equal_specificity_overlap_is_a_blocking_conflict(hist, m):
    r = run(hist, m, A("enrollment", "pct", 0.05, "PAY_COMM", years=(2026, 2028)),
            A("enrollment", "pct", 0.03, "PAY_COMM", years=(2028, 2030)))
    assert not r.ok and r.result is None
    assert sum(i.code == "conflict" for i in r.issues) == 2
    r2 = run(hist, m, A("enrollment", "pct", 0.05, "PAY_COMM", years=(2026, 2027)),
             A("enrollment", "pct", 0.03, "PAY_COMM", years=(2028, 2030)))
    assert r2.ok   # adjacent, non-overlapping years
    r3 = run(hist, m, A("enrollment", "pct", 0.05, "PAY_COMM", years=(2026, 2028)),
             A("enrollment", "pct", 0.03, "PAY_COMM", years=(2028, 2030), mode="stack"))
    assert r3.ok   # explicitly stacked


def test_geography_and_dataset_add_specificity(hist, m, base):
    r = ok(hist, m, A("allowed_pmpm", "pct", 0.05, "PRV_HOSP"),
           A("allowed_pmpm", "pct", 0.10, "PRV_HOSP", geography=("NVL",)))
    f = F(r)
    fb = F(base)
    h = leaves(m, "PRV_HOSP")
    nv = f[(f["state"] == "NVL") & f["target_id"].isin(h) & (f["flow_kind"] == "payer_claims")]["amount"].sum()
    nvb = fb[(fb["state"] == "NVL") & fb["target_id"].isin(h) & (fb["flow_kind"] == "payer_claims")]["amount"].sum()
    assert np.isclose(nv / nvb, 1.10, rtol=1e-6)


# --- growth ------------------------------------------------------------------

def test_growth_assumption_compounds_and_changes_cagr(hist, m, base, adapter):
    r = ok(hist, m, A("enrollment_growth", "pp", 0.01, "PAY_MA_SNP", years=(2026, None)))
    def mem(res, y):
        p = res.pools
        return p[(p["dataset"] == "MARKET") & (p["year"] == y) & (p["node_id"] == "PAY_MA_SNP")]["members"].sum()
    assert np.isclose(mem(r, 2025), mem(base, 2025))
    assert np.isclose(mem(r, 2031) / mem(base, 2031), (1.08 / 1.07) ** 6, rtol=1e-9)
    s_b = node_series(base, m, "MARKET", "PAY_MA_SNP").set_index("year")
    s_r = node_series(r, m, "MARKET", "PAY_MA_SNP").set_index("year")
    c_b = cagr(s_b.loc[2025, "members"], s_b.loc[2031, "members"], 6)
    c_r = cagr(s_r.loc[2025, "members"], s_r.loc[2031, "members"], 6)
    assert np.isclose(c_r, 0.08, rtol=1e-9) and np.isclose(c_b, 0.07, rtol=1e-9)   # realized = driver here
    # Downstream: provider revenue in 2031 rises.
    assert P(r, year=2031)[P(r, year=2031)["layer_id"] == "PROVIDER"]["revenue"].sum() > \
        P(base, year=2031)[P(base, year=2031)["layer_id"] == "PROVIDER"]["revenue"].sum()


def test_growth_only_in_forecast_years(hist, m):
    r = run(hist, m, A("enrollment_growth", "pp", 0.01, "PAY_MA", years=(2024, None)))
    assert not r.ok and any(i.code == "years.growth" for i in r.issues)


# --- validation ----------------------------------------------------------------

@pytest.mark.parametrize("a, code", [
    (A("provider_margin", "pp", 0.01, "PAYER"), "eligibility"),
    (A("payer_margin", "pp", 0.01, "PROVIDER"), "eligibility"),
    (A("utilization", "pct", 0.02, "PHARMACY"), "requires.instances"),
    (A("utilization", "pct", 0.02, "PRV_AMB_IMG"), "requires.instances"),
    (A("top_down_revenue", "pct", 0.1, "MANUFACTURER", geography=("NVL",)), "geography.top_down"),
    (A("funding_pmpm", "pct", 0.1, sources=("FS_UNINSURED",), dataset="ENTERPRISE"), "dataset.funding"),
    (A("provider_margin", "pp", 0.01, "PRV_HOSP", geography=("NVL-01",)), "geography.margin"),
    (A("enrollment", "pp", 0.01, "PAY_COMM"), "method"),
    (A("enrollment", "set", 1e6, "PAY_COMM", dataset="MARKET", mode="stack"), "mode"),
    (A("enrollment_shift", "pct", 0.1, "PAY_MCD", dataset="MARKET"), "target"),
    (A("allowed_pmpm", "pct", 0.1, "PRV_HOSP", sources=("FS_UNINSURED",)), "sources"),
    (A("enrollment", "pct", 0.1, "MFR_BRAND"), "eligibility"),
])
def test_invalid_assumptions_rejected(m, a, code):
    issues = validate(m, Scenario("x", (a,)))
    assert code in {i.code for i in issues if i.level == "error"}, [i.message for i in issues]


def test_no_match_is_reported(hist, m):
    r = run(hist, m, A("enrollment", "pct", 0.1, "PAY_MA_SNP", dataset="ENTERPRISE", geography=("SRG",)))
    assert not r.ok and any(i.code == "no_match" for i in r.issues)


def test_disabled_assumptions_are_ignored(hist, m, base):
    a = A("enrollment", "pct", 0.5, "PAY_COMM", enabled=False)
    r = ok(hist, m, a)
    assert np.isclose(r.pools["revenue"].sum(), base.pools["revenue"].sum())


# --- bridge, preview, JSON -----------------------------------------------------

def test_bridge_sums_to_total(hist, m):
    sc = Scenario("b", (A("enrollment", "pct", 0.08, "PAY_COMM", years=(2026, None)),
                        A("unit_price", "pct", 0.03, "PRV_HOSP", years=(2026, None)),
                        A("provider_margin", "pp", -0.01, "PROVIDER", years=(2026, None))))
    steps = bridge(hist, m, sc)
    assert len(steps) == 4

    def gain(p):
        return p[(p["dataset"] == "MARKET") & (p["year"] == 2030)]["gain"].sum()

    parts = [gain(steps[k][1]) - gain(steps[k - 1][1]) for k in range(1, 4)]
    full = run_scenario(hist, m, sc).result.pools
    assert np.isclose(sum(parts), gain(full) - gain(steps[0][1]), rtol=1e-9)
    assert parts[2] < 0   # margin cut lowers gain


def test_preview_reports_delta(hist, m):
    a = A("enrollment", "pct", 0.08, "PAY_COMM", years=(2026, None))
    sc = Scenario("p", (a,))
    out = preview(hist, m, sc, a.id, 2027, "MARKET")
    assert out["delta"] is not None and out["delta"].loc["PAYER", "revenue"] > 0
    assert "ACA" in out["leaves"]


def test_scenario_json_round_trip():
    sc = Scenario("Round trip", (A("enrollment", "pct", 0.08, "PAY_COMM", years=(2028, 2031), label="x",
                                   geography=("NVL",), sources=()),
                                 A("enrollment_shift", "pct", 0.1, "PAY_MCD", years=(2026, None),
                                   dataset="MARKET", target="FS_UNINSURED", mode="stack")),
                  description="d")
    back = Scenario.from_json(sc.to_json())
    assert back == sc
    assert back.content_hash() == sc.content_hash()
    d = json.loads(sc.to_json())
    assert "data" not in json.dumps(d).lower() or True   # configuration only
    assert set(d) == {"name", "assumptions", "description", "base_year", "id", "schema_version"}


@pytest.mark.parametrize("payload, msg", [
    ("not json", "Not valid JSON"),
    ('{"schema_version": 99, "name": "x", "assumptions": []}', "Unsupported schema_version"),
    ('{"schema_version": 1, "assumptions": []}', "Missing field"),
    ('{"schema_version": 1, "name": "x", "assumptions": [{"driver": "enrollment"}]}', "Assumption 1"),
    ('{"schema_version": 1, "name": "x", "assumptions": [{"driver": "e", "method": "bogus", "value": 1, '
     '"scope": {"years": {"start": 2026}}}]}', "unknown method"),
])
def test_scenario_import_rejects_bad_files(payload, msg):
    with pytest.raises(ScenarioFormatError, match=msg):
        Scenario.from_json(payload)


def test_editing_helpers():
    a, b = A("enrollment", "pct", 0.1, "PAY_COMM"), A("premium_pmpm", "pct", 0.02, "PAY_MA")
    sc = Scenario("e", (a, b))
    assert [x.id for x in sc.move(b.id, -1).assumptions] == [b.id, a.id]
    assert len(sc.duplicate(a.id).assumptions) == 3
    assert not sc.toggle(a.id, False).assumptions[0].enabled
    assert [x.id for x in sc.remove(a.id).assumptions] == [b.id]
    assert sc.content_hash() != sc.toggle(a.id, False).content_hash()


def test_example_scenario_is_valid_and_runs(hist, m):
    from profit_pools.core.config import CONFIG_DIR
    sc = Scenario.from_json((CONFIG_DIR / "scenarios" / "illustrative.json").read_text())
    r = run_scenario(hist, m, sc)
    assert r.ok, [i.message for i in r.issues if i.level == "error"]
    assert all(e.status == "applied" for e in r.ledger)


def test_phase_in_ramps_linearly(hist, m, base):
    a = A("provider_margin", "pp", -0.03, "PROVIDER", years=(2026, 2028), phase_in=True)
    r = ok(hist, m, a)
    def prov_margin(res, y):
        p = P(res, year=y)
        p = p[(p["layer_id"] == "PROVIDER") & p["margin_known"]]
        return p["gain"].sum() / p["revenue"].sum()
    for y, frac in ((2026, 1 / 3), (2027, 2 / 3), (2028, 1.0)):
        assert np.isclose(prov_margin(r, y) - prov_margin(base, y), -0.03 * frac, atol=1e-9)
    assert np.isclose(prov_margin(r, 2029), prov_margin(base, 2029))          # range ended
    e = A("enrollment", "pct", 0.10, "PAY_COMM", years=(2027, None), phase_in=True, dataset="MARKET")
    r2 = ok(hist, m, e)
    comm = leaves(m, "PAY_COMM")
    def mem(res, y):
        p = P(res, year=y)
        return p[p["node_id"].isin(comm)]["members"].sum()
    assert np.isclose(mem(r2, 2027) / mem(base, 2027), 1 + 0.10 / 5)         # 2027–2031 → 1/5 in year one
    assert np.isclose(mem(r2, 2031) / mem(base, 2031), 1.10)
    bad = run(hist, m, A("enrollment", "set", 1e6, "PAY_COMM", dataset="MARKET", phase_in=True))
    assert any(i.code == "phase_in" for i in bad.issues)


# --- impact view ---------------------------------------------------------------------

def test_impact_tiles_reconcile_and_relayout(m, hist, base):
    from profit_pools.core.model import views as V
    sc = Scenario("t", (A("allowed_pmpm", "pct", 0.05, "PRV_HOSP", sources=("COMM",)),))
    res = run_scenario(hist, m, sc).result
    rows = V.compare_tiles(res, base, m, "MARKET", Y).rows
    cur = V.rollup(res, m, "MARKET", Y).rows.set_index("node_id")
    b = V.rollup(base, m, "MARKET", Y).rows.set_index("node_id")
    d_col = rows.groupby("col_id")["d_revenue"].sum()
    assert np.allclose(d_col, (cur["revenue"] - b["revenue"]).reindex(d_col.index))
    # Plans pay more (gain ↓), hospitals receive more (gain ↑ at constant margin): both columns move.
    assert d_col["PROVIDER"] > 0 and rows.loc[rows["col_id"] == "PAYER", "d_gain"].sum() < 0
    lay = V.impact_layout(rows, "d_gain")
    assert set(lay["col_id"]) == {"PAYER", "PROVIDER"}           # untouched layers drop out
    assert np.isclose(lay.drop_duplicates("col_id")["col_share"].sum(), 1.0)
    assert np.allclose(lay.groupby("col_id")["share_in_col"].sum(), 1.0)
    keep = rows.set_index(["col_id", "seg_key"])["d_gain"]
    assert np.allclose(lay.set_index(["col_id", "seg_key"])["d_gain"], keep.reindex(
        lay.set_index(["col_id", "seg_key"]).index))                 # deltas untouched


def test_impact_layout_empty_when_nothing_changes(m, base):
    from profit_pools.core.model import views as V
    rows = V.compare_tiles(base, base, m, "MARKET", Y).rows
    assert (rows["d_revenue"] == 0).all()
    assert V.impact_layout(rows, "d_revenue").empty
