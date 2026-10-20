"""User pool groupings change roll-ups and labels — never values."""

import numpy as np
import pytest

from profit_pools.core.data.adapters import SyntheticAdapter
from profit_pools.core.grouping import Grouping, apply, regroup
from profit_pools.core.model.cube import build_history
from profit_pools.core.model.engine import relayer, run_baseline
from profit_pools.core.model.scenario import Assumption, Scenario, Scope, YearRange
from profit_pools.core.model.scenario_engine import run_scenario
from profit_pools.core.model.views import rollup
from profit_pools.core.taxonomy import TaxonomyError


@pytest.fixture(scope="module")
def adapter():
    return SyntheticAdapter()


@pytest.fixture(scope="module")
def base_meta(adapter):
    return adapter.metadata


@pytest.fixture(scope="module")
def base(adapter):
    return run_baseline(adapter)


def test_standard_grouping_is_identity(base_meta):
    m, w = regroup(base_meta, Grouping())
    assert [n.id for n in m.taxonomy] == [n.id for n in base_meta.taxonomy] and not w


def test_rename_move_and_new_group(base_meta):
    g = Grouping(name="Mine").rename("HST_DATA", "Data, AI & Interop")
    g, gid = g.add_group("Payer-facing tech", "HEALTH_SERVICES")
    g = g.move("HST_PAYER_PLATFORMS", gid).move("HS_CARE_MGMT", gid)
    m, warnings = regroup(base_meta, g)
    tax = m.taxonomy
    assert tax["HST_DATA"].label == "Data, AI & Interop"
    assert tax["HST_PAYER_PLATFORMS"].parent_id == gid and tax["HS_CARE_MGMT"].parent_id == gid
    assert set(tax.leaf_ids(gid)) == {"HS_CLAIMS_PLATFORM", "HST_PAYMENT_INTEGRITY", "HS_UM", "HS_CARE_COORD"}
    assert m.standard["HST_PAYER_PLATFORMS"].parent_id == "HST_SOFTWARE"     # standard kept intact
    assert sorted(l.id for l in tax.leaves()) == sorted(l.id for l in base_meta.taxonomy.leaves())


def test_move_across_layers_keeps_economics_and_dataset_rules(base_meta):
    g = Grouping().move("RX_PBM", "HEALTH_SERVICES")       # PBM services under HST
    m, _ = regroup(base_meta, g)
    assert m.taxonomy["RX_PBM_COMM"].layer_id == "HEALTH_SERVICES"
    assert m.taxonomy["RX_PBM_COMM"].economics == "top_down"
    assert not m.in_dataset("RX_PBM", "ENTERPRISE")         # exclusions follow leaves, not position
    assert m.in_dataset("PHARMACY", "ENTERPRISE")
    g2 = Grouping().move("PRV_HOSP_PSYCH", "HEALTH_SERVICES")
    m2, _ = regroup(base_meta, g2)
    assert m2.in_dataset("HEALTH_SERVICES", "ENTERPRISE")    # now contains an enterprise-visible leaf
    assert m2.supplemental_targets("FS_SUPPLEMENTAL") == base_meta.supplemental_targets("FS_SUPPLEMENTAL")


@pytest.mark.parametrize("g, msg", [
    (Grouping().move("PAYER", "PROVIDER"), "top-level"),
    (Grouping().move("PRV_HOSP", "PRV_HOSP_ACUTE"), "inside itself"),
    (Grouping().move("PRV_PHYS", "PRV_HOSP_PSYCH"), "holds data"),
    (Grouping().move("PAY_COMM", "PROVIDER"), "Payer segments"),
    (Grouping().move("PRV_HOSP", "PAYER"), "Payer segments"),
    (Grouping(new_groups=(("GRP_X", "X", "NOPE"),)), "unknown parent"),
])
def test_invalid_groupings_rejected(base_meta, g, msg):
    with pytest.raises(TaxonomyError, match=msg):
        apply(base_meta.taxonomy, g)


def test_emptied_groups_are_hidden(base_meta):
    g = Grouping().move("PRV_AMB_ASC", "PRV_PHYS").move("PRV_AMB_IMG", "PRV_PHYS")
    m, w = regroup(base_meta, g)
    assert "PRV_AMB" not in m.taxonomy and any("hidden" in x for x in w)


def test_values_unchanged_and_rollups_follow_grouping(base, base_meta):
    g, gid = Grouping().add_group("Facility care", "PROVIDER")
    g = g.move("PRV_HOSP", gid).move("PRV_PAC", gid).move("RX_PBM", "HEALTH_SERVICES")
    m, _ = regroup(base_meta, g)
    r = relayer(base, m)
    np.testing.assert_allclose(r.pools["revenue"], base.pools["revenue"])
    layers_b = rollup(base, base_meta, "MARKET", 2025).rows.set_index("node_id")["revenue"]
    layers_g = rollup(r, m, "MARKET", 2025).rows.set_index("node_id")["revenue"]
    assert np.isclose(layers_b.sum(), layers_g.sum())
    pbm = base.pools[(base.pools["dataset"] == "MARKET") & (base.pools["year"] == 2025)
                     & base.pools["node_id"].str.startswith("RX_PBM")]["revenue"].sum()
    assert np.isclose(layers_g["HEALTH_SERVICES"], layers_b["HEALTH_SERVICES"] + pbm)
    prov = rollup(r, m, "MARKET", 2025, "PROVIDER").rows.set_index("node_id")
    hosp = rollup(base, base_meta, "MARKET", 2025, "PROVIDER").rows.set_index("node_id")
    assert np.isclose(prov.loc[gid, "revenue"], hosp.loc["PRV_HOSP", "revenue"] + hosp.loc["PRV_PAC", "revenue"])


def test_forecast_unchanged_by_grouping(adapter, base_meta):
    g = Grouping().move("RX_SPECIALTY", "PHARMACY").move("PRV_PHYS_BH", "PRV_HOSP")
    m, _ = regroup(base_meta, g)
    h = build_history(adapter)
    a = run_scenario(h, base_meta, Scenario("s")).result
    b = run_scenario(h, m, Scenario("s")).result
    np.testing.assert_allclose(a.pools["revenue"], b.pools["revenue"], rtol=1e-12)


def test_scenarios_can_target_custom_groups(adapter, base_meta):
    g, gid = Grouping().add_group("Facility care", "PROVIDER")
    g = g.move("PRV_HOSP", gid).move("PRV_PAC", gid)
    m, _ = regroup(base_meta, g)
    sc = Scenario("s", (Assumption("unit_price", "pct", 0.05, Scope(YearRange(2027, 2027), node_id=gid)),))
    r = run_scenario(build_history(adapter), m, sc)
    assert r.ok and r.ledger[0].effective_cells > 0


def test_grouping_json_round_trip():
    g, gid = Grouping(name="Team view").add_group("Facility care", "PROVIDER")
    g = g.move("PRV_HOSP", gid).rename("PRV_HOSP", "Hospitals & health systems")
    assert Grouping.from_json(g.to_json()) == g
    with pytest.raises(ValueError):
        Grouping.from_json('{"schema_version": 7}')
