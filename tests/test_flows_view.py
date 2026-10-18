"""Phase 5: Sankey views reconcile at every column, under every configuration."""

import itertools

import numpy as np
import pytest

from profit_pools.core.data.adapters import SyntheticAdapter
from profit_pools.core.model import flows_view as FV
from profit_pools.core.model.cube import build_history
from profit_pools.core.model.engine import run_baseline
from profit_pools.core.model.scenario import Assumption, Scenario, Scope, YearRange
from profit_pools.core.model.scenario_engine import run_scenario
from profit_pools.core.model.views import Geo, rollup

Y = 2025


@pytest.fixture(scope="module")
def adapter():
    return SyntheticAdapter()


@pytest.fixture(scope="module")
def m(adapter):
    return adapter.metadata


@pytest.fixture(scope="module")
def r(adapter):
    return run_baseline(adapter)


def cfg(**kw):
    return FV.SankeyConfig(**kw)


CONFIGS = [
    dict(metric=mt, source_columns=sc, recipient_columns=rc, recipient_depth=d, split_consumer=cs, min_share=ms)
    for mt, sc, rc, d, cs, ms in itertools.product(
        ["allowed", "paid", "consumer", "gain"],
        [("source_group",), ("source_detail",), ("source_group", "source_detail")],
        [("recipient",), ("recipient_layer", "recipient")],
        [0, 1, 4], [False, True], [0.0, 0.02])
]


@pytest.mark.parametrize("c", CONFIGS[::5])   # a broad, deterministic sample of the grid
@pytest.mark.parametrize("ds", ["MARKET", "ENTERPRISE"])
def test_every_column_sums_to_the_total(r, m, c, ds):
    v = FV.build(r, m, ds, Y, Geo(), cfg(**c))
    by_col = v.nodes.groupby("column")["value"].sum()
    np.testing.assert_allclose(by_col.to_numpy(), v.total, rtol=1e-9)
    # links between adjacent columns carry the total too, and match node values
    for a in v.columns[:-1]:
        lk = v.links[v.links["from_column"] == a]
        assert np.isclose(lk["value"].sum(), v.total, rtol=1e-9)
        out = lk.groupby("source")["value"].sum()
        nodes = v.nodes.set_index("node_id")["value"]
        np.testing.assert_allclose(out.to_numpy(), nodes.loc[out.index].to_numpy(), rtol=1e-9)


def test_allowed_total_equals_recipient_revenue(r, m):
    v = FV.build(r, m, "MARKET", Y, Geo(), cfg())
    rev = sum(rollup(r, m, "MARKET", Y, layer).rows.query("node_id != 'RX_PBM'")["revenue"].sum()
              for layer in ("PROVIDER", "PHARMACY"))
    assert np.isclose(v.total, rev, rtol=1e-9)
    v = FV.build(r, m, "MARKET", Y, Geo("state", "NVL"), cfg())
    rev = sum(rollup(r, m, "MARKET", Y, layer, Geo("state", "NVL")).rows.query("node_id != 'RX_PBM'")["revenue"].sum()
              for layer in ("PROVIDER", "PHARMACY"))
    assert np.isclose(v.total, rev, rtol=1e-9)


def test_paid_plus_consumer_equals_allowed(r, m):
    a, p, c = (FV.build(r, m, "MARKET", Y, Geo(), cfg(metric=x)).total for x in ("allowed", "paid", "consumer"))
    assert np.isclose(a, p + c, rtol=1e-9)


def test_consumer_split_keeps_total_and_isolates_consumer_spend(r, m):
    plain = FV.build(r, m, "MARKET", Y, Geo(), cfg())
    split = FV.build(r, m, "MARKET", Y, Geo(), cfg(split_consumer=True))
    assert np.isclose(plain.total, split.total)
    cons = split.nodes.set_index("node_id").loc["source_group:CONSUMER", "value"]
    assert np.isclose(cons, FV.build(r, m, "MARKET", Y, Geo(), cfg(metric="consumer")).total, rtol=1e-9)
    unins = split.nodes[split.nodes["key"] == "FS_UNINSURED"]
    assert unins.empty                       # uninsured is 100% self-pay → all of it moves to Consumers


def test_other_folding_reconciles_and_lists_members(r, m):
    v = FV.build(r, m, "MARKET", Y, Geo(), cfg(source_columns=("source_detail",), recipient_depth=4, min_share=0.03))
    other = v.nodes[v.nodes["key"] == FV.OTHER]
    assert len(other) == 2                   # one per column
    assert all(len(mem) > 1 for mem in other["members"])
    assert any("folded" in n for n in v.notes)


def test_expand_shows_children(r, m):
    v = FV.build(r, m, "MARKET", Y, Geo(), cfg(recipient_depth=1, expand=("PRV_HOSP",)))
    keys = set(v.nodes.loc[v.nodes["column"] == "recipient", "key"])
    assert {"PRV_HOSP_ACUTE", "PRV_HOSP_SPECIALTY"} <= keys and "PRV_HOSP" not in keys and "PRV_PHYS" in keys
    v2 = FV.build(r, m, "MARKET", Y, Geo(), cfg(recipient_depth=1, expand=("PRV_HOSP", "PRV_HOSP_ACUTE")))
    assert {"PRV_IP", "PRV_OP"} <= set(v2.nodes["key"])


def test_filters(r, m):
    comm = FV.build(r, m, "MARKET", Y, Geo(), cfg(source_filter=("COMM",)))
    assert set(comm.nodes.loc[comm.nodes["column"] == "source_group", "key"]) == {"COMM"}
    f = r.flows
    exp = f[(f["dataset"] == "MARKET") & (f["year"] == Y) & f["lob"].isin(m.lobs.in_group("COMM"))]["amount"].sum()
    assert np.isclose(comm.total, exp, rtol=1e-9)
    rx = FV.build(r, m, "MARKET", Y, Geo(), cfg(recipient_filter=("PHARMACY",)))
    assert set(rx.detail["layer"]) == {"PHARMACY"}


def test_gain_metric_matches_recipient_gain(r, m):
    v = FV.build(r, m, "MARKET", Y, Geo(), cfg(metric="gain"))
    gain = sum(rollup(r, m, "MARKET", Y, layer).rows.query("node_id != 'RX_PBM'")["gain"].sum()
               for layer in ("PROVIDER", "PHARMACY"))
    assert np.isclose(v.total, gain, rtol=1e-9)
    leaf = FV.build(r, m, "MARKET", Y, Geo(), cfg(metric="gain", recipient_depth=4))
    assert np.isclose(leaf.total, v.total, rtol=1e-9)
    psych = leaf.links[leaf.links["target"] == "recipient:PRV_HOSP_PSYCH"]
    assert (psych["value"] < 0).all()         # psychiatric hospitals lose money: negative links at leaf depth


def test_enterprise_has_only_payer_sources(r, m):
    v = FV.build(r, m, "ENTERPRISE", Y, Geo(), cfg())
    assert set(v.nodes.loc[v.nodes["column"] == "source_group", "key"]) <= set(m.lobs.groups)


def test_link_detail_pmpm_and_instances(r, m):
    v = FV.build(r, m, "MARKET", Y, Geo(), cfg(recipient_depth=1))
    d = FV.link_detail(v, m, r, "MARKET", Y, Geo(), "source_group:COMM", "recipient:PRV_HOSP")
    p = r.pools
    mm = p[(p["dataset"] == "MARKET") & (p["year"] == Y) & p["lob"].isin(m.lobs.in_group("COMM"))]["members"].sum()
    assert np.isclose(d["pmpm"], d["amount"] / (mm * 12))
    assert d["instances"] > 0 and 500 < d["allowed_per_instance"] < 20000
    assert np.isclose(d["amount"], d["paid"] + d["consumer"])
    g = FV.link_detail(v, m, r, "MARKET", Y, Geo(), "source_group:GOV_FFS", "recipient:PRV_HOSP")
    assert "pmpm" not in g and "instances" not in g            # funding sources: no members, no instances
    assert any("Population" in x for x in g["methods"])
    rx = FV.link_detail(v, m, r, "MARKET", Y, Geo(), "source_group:COMM", "recipient:RX_DISP")
    assert "instances" not in rx                               # pharmacy never has instances


def test_node_detail_recipient_pool_economics(r, m):
    v = FV.build(r, m, "MARKET", Y, Geo(), cfg(recipient_depth=1, source_filter=("COMM",)))
    d = FV.node_detail(v, m, r, "MARKET", Y, Geo(), "recipient:PRV_HOSP")
    hosp = rollup(r, m, "MARKET", Y, "PROVIDER").rows.set_index("node_id").loc["PRV_HOSP"]
    assert np.isclose(d["pool_revenue"], hosp["revenue"], rtol=1e-9)
    assert 0 < d["share_of_pool_revenue"] < 1                  # Commercial is one of several funders


def test_compare_links_follow_the_scenario(adapter, r, m):
    sc = Scenario("s", (Assumption("enrollment", "pct", 0.10, Scope(YearRange(Y, Y), dataset="MARKET",
                                                                       node_id="PAY_COMM")),))
    s = run_scenario(build_history(adapter), m, sc).result
    c = cfg(recipient_depth=1)
    v, b = FV.build(s, m, "MARKET", Y, Geo(), c), FV.build(r, m, "MARKET", Y, Geo(), c)
    d = FV.compare_links(v, b)
    comm = v.links["source"] == "source_group:COMM"
    np.testing.assert_allclose(d[comm].to_numpy(), 0.1 * (v.links.loc[comm, "value"] / 1.1).to_numpy(), rtol=1e-9)
    assert np.allclose(d[~comm].to_numpy(), 0.0)
