"""'Follow the enterprise dollar': every node balances and the flow reconciles to Value capture."""

import numpy as np
import pandas as pd
import pytest

from profit_pools.charts import sankey
from profit_pools.core.data.adapters import SyntheticAdapter
from profit_pools.core.model import enterprise_flow as EF
from profit_pools.core.model.cube import build_history
from profit_pools.core.model.engine import run_baseline
from profit_pools.core.model.scenario import Assumption, Scenario, Scope, YearRange
from profit_pools.core.model.scenario_engine import run_scenario
from profit_pools.core.model.views import Geo, ownership_view


@pytest.fixture(scope="module")
def world():
    a = SyntheticAdapter()
    return a.metadata, build_history(a), run_baseline(a)


def _check(view, ov):
    if view.note:                                                   # e.g. the enterprise has no plans in this state
        assert ov.note or not ov.totals.get("payer_revenue")
        return
    L = view.links
    assert (L["value"] > 0).all()
    inn, out = L.groupby("target")["value"].sum(), L.groupby("source")["value"].sum()
    for n in set(inn.index) & set(out.index):                       # pass-through nodes balance
        assert inn[n] == pytest.approx(out[n], rel=1e-9, abs=1.0)
    segs = set(view.nodes.loc[view.nodes["kind"] == "segment", "node_id"])
    assert segs and not segs & set(inn.index)                       # plans are always sources
    t = ov.totals
    assert view.totals["consolidated_gain"] == pytest.approx(t["consolidated_gain"])
    assert inn[EF.GAIN] - view.totals["losses"] == pytest.approx(t["consolidated_gain"], abs=1.0)
    assert out[list(segs)].sum() == pytest.approx(t["payer_revenue"])     # segment outflows = premiums
    assets = view.nodes.loc[view.nodes["kind"] == "asset", "node_id"]
    assert inn[list(assets)].sum() == pytest.approx(t["owned_revenue"])


@pytest.mark.parametrize("geo", ["US", "NVL", "WMR"])
@pytest.mark.parametrize("detail", ["group", "plan"])
@pytest.mark.parametrize("year", [2019, 2025, 2031])
def test_balances_and_reconciles(world, geo, detail, year):
    m, _, base = world
    g = Geo.parse(m, geo)
    _check(EF.build(base, m, year, g, detail), ownership_view(base, m, year, g))


def test_premiums_equal_plan_revenue(world):
    m, _, base = world
    v = EF.build(base, m, 2025, Geo(), "plan")
    segs = v.nodes.loc[v.nodes["kind"] == "segment", "node_id"]
    prem = v.links[v.links["source"].isin(segs)].groupby("source")["value"].sum()
    p = base.pools
    rev = p[(p["dataset"] == "ENTERPRISE") & (p["year"] == 2025) & p["node_id"].isin(segs)].groupby("node_id")
    # a segment's outflows equal its premiums whether or not it makes a loss (shortfall comes from Losses)
    assert np.allclose(prem.reindex(rev["revenue"].sum().index), rev["revenue"].sum())


def test_insourcing_moves_dollars_to_owned_assets(world):
    m, hist, base = world
    a = Assumption("routing_share", "pp", 0.15, Scope(YearRange(2027, None), dataset="ENTERPRISE", node_id="PRV_PHYS"))
    res = run_scenario(hist, m, Scenario("t", (a,))).result
    v0, v1 = EF.build(base, m, 2028, Geo()), EF.build(res, m, 2028, Geo())
    ext = lambda v: v.links.loc[v.links["target"] == EF.EXTERNAL, "value"].sum()   # noqa: E731
    assert ext(v1) < ext(v0)
    assert v1.totals["owned_gain"] > v0.totals["owned_gain"]
    assert v1.totals["premium"] == pytest.approx(v0.totals["premium"])          # spending unchanged
    d = EF.compare(v1, v0)
    assert len(d) == len(v1.links) and (d.abs() > 0).any()


def test_county_is_unavailable(world):
    m, _, base = world
    assert EF.build(base, m, 2025, Geo.parse(m, "NVL-01")).note


def test_positions_follow_order_and_pins():
    nodes = pd.DataFrame({"node_id": list("abcd"), "label": ["d", "c", "b", "a"], "value": [1.0, 4.0, 2.0, 3.0]})
    col = pd.Series([0, 0, 1, 1])
    assert sankey.positions(nodes, col, "auto") is None
    x, y = sankey.positions(nodes, col, "size")
    assert y[1] < y[0] and y[3] < y[2] and x[0] < x[2]
    x, y = sankey.positions(nodes, col, "size", pins=("a",))
    assert y[0] < y[1]
    x, y = sankey.positions(nodes, col, "name")
    assert y[1] < y[0]


@pytest.mark.parametrize("geo", ["US", "NVL"])
@pytest.mark.parametrize("detail", ["group", "plan"])
def test_gain_focus_nets_to_consolidated_gain(world, geo, detail):
    m, _, base = world
    g = Geo.parse(m, geo)
    v = EF.build(base, m, 2025, g, detail, "gain")
    L = v.links
    assert set(L["kind"]) <= {"gain", "loss"}
    net = L.loc[L["target"] == EF.GAIN, "value"].sum() - L.loc[L["target"] == EF.LOSSES, "value"].sum()
    assert net == pytest.approx(ownership_view(base, m, 2025, g).totals["consolidated_gain"], abs=1.0)
    assert set(v.nodes.loc[v.nodes["kind"] == "asset", "node_id"]) <= set(L["source"])   # assets feed the total
