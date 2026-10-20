"""Phase 8: one scenario changes every view consistently — KPIs, Marimekko, Sankey, trends, growth,
attribution, impact tiles, exports and groupings all read the same engine output and agree."""

import io

import numpy as np
import pandas as pd
import pytest

from profit_pools.core.config import CONFIG_DIR
from profit_pools.core.data.adapters import SyntheticAdapter
from profit_pools.core.grouping import Grouping, regroup
from profit_pools.core.model import flows_view as FV
from profit_pools.core.model import growth as G
from profit_pools.core.model import views as V
from profit_pools.core.model.cube import build_history
from profit_pools.core.model.engine import ModelResult, relayer, run_baseline
from profit_pools.core.model.scenario import Scenario
from profit_pools.core.model.scenario_engine import bridge, run_scenario
from profit_pools.core.model.views import Geo
from profit_pools.export import ExportContext, csv_export

Y = 2029


@pytest.fixture(scope="module")
def world():
    a = SyntheticAdapter()
    m = a.metadata
    hist = build_history(a)
    sc = Scenario.from_json((CONFIG_DIR / "scenarios" / "illustrative.json").read_text())
    return m, hist, run_baseline(a), run_scenario(hist, m, sc).result, sc


GEOS = ["US", "ESH"]


@pytest.mark.parametrize("dataset", ["MARKET", "ENTERPRISE"])
@pytest.mark.parametrize("geo", GEOS)
def test_kpi_marimekko_table_csv_agree(world, dataset, geo):
    m, _, base, res, sc = world
    g = Geo.parse(m, geo)
    kpi = V.node_metrics(res, m, dataset, Y, None, g)
    tiles = V.marimekko_tiles(res, m, dataset, Y, None, g).rows
    roll = V.rollup(res, m, dataset, Y, None, g).rows
    csv = pd.read_csv(io.BytesIO(csv_export.analysis_csv(
        ExportContext(m, res, base, "scenario", dataset, Y, g, scenario=sc))))
    assert tiles["revenue"].sum() == pytest.approx(kpi["revenue"])
    assert roll["revenue"].sum() == pytest.approx(kpi["revenue"])
    assert csv["revenue_usd"].sum() == pytest.approx(kpi["revenue"])
    assert np.nansum(tiles["gain"]) == pytest.approx(kpi["gain"])


@pytest.mark.parametrize("dataset", ["MARKET", "ENTERPRISE"])
@pytest.mark.parametrize("geo", GEOS)
def test_sankey_equals_recipient_revenue(world, dataset, geo):
    m, _, _, res, _ = world
    g = Geo.parse(m, geo)
    view = FV.build(res, m, dataset, Y, g, FV.SankeyConfig(recipient_depth=0))
    rec_leaves = {l.id for l in m.taxonomy.leaves() if l.receives_flows}
    p = res.pools
    p = p[(p["dataset"] == dataset) & (p["year"] == Y) & p["node_id"].isin(rec_leaves)
          & ((p["state"] == geo) if geo != "US" else True)]
    assert view.total == pytest.approx(p["revenue"].sum())
    rec = view.nodes[view.nodes["column"] == "recipient"]
    assert rec["value"].sum() == pytest.approx(view.total)


@pytest.mark.parametrize("node", [None, "PROVIDER", "PAYER"])
def test_trend_growth_and_kpi_agree(world, node):
    m, _, _, res, _ = world
    s = V.node_series(res, m, "MARKET", node, Geo()).set_index("year")
    assert s.loc[Y, "revenue"] == pytest.approx(V.node_metrics(res, m, "MARKET", Y, node, Geo())["revenue"])
    br = G.bridge(res, m, "MARKET", node, Geo(), 2025, 2031)
    assert br["value"].iloc[-1] == pytest.approx(s.loc[2031, "gain"])
    assert abs(br.attrs["residual"]) < 1.0


@pytest.mark.parametrize("node", [None, "PROVIDER", "PAY_MCD"])
def test_attribution_sums_to_kpi_delta(world, node):
    m, hist, base, res, sc = world
    steps = bridge(hist, m, sc)
    vals = [V.node_metrics(ModelResult(pools=p, flows=pd.DataFrame(), base_year=2025), m, "MARKET", Y, node, Geo())
            for _, p in steps]
    d_rev = vals[-1]["revenue"] - vals[0]["revenue"]
    kpi_d = (V.node_metrics(res, m, "MARKET", Y, node, Geo())["revenue"]
             - V.node_metrics(base, m, "MARKET", Y, node, Geo())["revenue"])
    assert d_rev == pytest.approx(kpi_d, abs=1.0)


def test_impact_tiles_sum_to_kpi_delta(world):
    m, _, base, res, _ = world
    rows = V.compare_tiles(res, base, m, "MARKET", Y, None, Geo()).rows
    d_gain = V.node_metrics(res, m, "MARKET", Y, None, Geo())["gain"] - V.node_metrics(base, m, "MARKET", Y, None, Geo())["gain"]
    assert rows["d_gain"].sum() == pytest.approx(d_gain, abs=1.0)
    lay = V.impact_layout(rows, "d_gain")
    assert lay["d_gain"].sum() == pytest.approx(rows.loc[rows["d_gain"].abs() >= V.IMPACT_MIN, "d_gain"].sum())


def test_grouping_never_changes_numbers(world):
    m, _, _, res, _ = world
    g, gid = Grouping("t").add_group("Acute & post-acute", "PROVIDER")
    gm, _ = regroup(m, g.move("PRV_HOSP", gid).move("PRV_PAC", gid))
    gr = relayer(res, gm)
    a = V.rollup(res, m, "MARKET", Y, None, Geo()).rows.set_index("node_id")
    b = V.rollup(gr, gm, "MARKET", Y, None, Geo()).rows.set_index("node_id")
    assert np.allclose(a["revenue"], b["revenue"].reindex(a.index))
    sub = V.rollup(gr, gm, "MARKET", Y, "PROVIDER", Geo()).rows.set_index("node_id")
    orig = V.rollup(res, m, "MARKET", Y, "PROVIDER", Geo()).rows.set_index("node_id")
    assert sub.loc[gid, "revenue"] == pytest.approx(orig.loc[["PRV_HOSP", "PRV_PAC"], "revenue"].sum())


def test_memoized_views_follow_new_results(world):
    """The roll-up cache is per result object: a new scenario run never reuses stale sums."""
    m, hist, base, res, sc = world
    a = V.node_metrics(res, m, "MARKET", Y, None, Geo())["revenue"]
    res2 = run_scenario(hist, m, sc.with_assumptions(sc.assumptions[:1])).result
    b = V.node_metrics(res2, m, "MARKET", Y, None, Geo())["revenue"]
    assert a != pytest.approx(b)
    assert V.node_metrics(base, m, "MARKET", Y, None, Geo())["revenue"] != pytest.approx(a)
