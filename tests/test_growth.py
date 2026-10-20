"""Growth analytics: the revenue bridge reconciles at every grain; CAGRs and indices follow their definitions."""

import numpy as np
import pytest

from profit_pools.core.data.adapters import SyntheticAdapter
from profit_pools.core.model import growth as G
from profit_pools.core.model.cube import build_history
from profit_pools.core.model.engine import run_baseline
from profit_pools.core.model.scenario import Assumption, Scenario, Scope, YearRange
from profit_pools.core.model.scenario_engine import run_scenario
from profit_pools.core.model.views import Geo, cagr, node_series


@pytest.fixture(scope="module")
def adapter():
    return SyntheticAdapter()


@pytest.fixture(scope="module")
def m(adapter):
    return adapter.metadata


@pytest.fixture(scope="module")
def base(adapter):
    return run_baseline(adapter)


NODES = [None, "PAYER", "PAY_MCD", "PROVIDER", "PRV_HOSP", "PHARMACY", "MANUFACTURER", "HEALTH_SERVICES"]


@pytest.mark.parametrize("dataset", ["MARKET", "ENTERPRISE"])
@pytest.mark.parametrize("node", NODES)
@pytest.mark.parametrize("years", [(2019, 2025), (2025, 2031), (2024, 2025)])
def test_revenue_bridge_reconciles(m, base, dataset, node, years):
    if node is not None and not m.in_dataset(node, dataset):
        pytest.skip("not in dataset")
    for geo in (Geo(), Geo.parse(m, next(iter(m.geography.states)))):
        b = G.revenue_bridge(base, m, dataset, node, geo, *years)
        s = node_series(base, m, dataset, node, geo).set_index("year")["revenue"]
        start, end = s.get(years[0], 0.0), s.get(years[1], 0.0)
        assert b["value"].iloc[0] == pytest.approx(start) and b["value"].iloc[-1] == pytest.approx(end)
        assert abs(b.attrs["residual"]) < max(1.0, 1e-9 * end)
        assert b["value"].iloc[1:-1].sum() == pytest.approx(end - start, abs=0.5e6 * len(G.COMPONENTS))


def test_bridge_components_follow_economics(m, base):
    pay = G.revenue_bridge(base, m, "MARKET", "PAYER", Geo(), 2025, 2031).attrs["components"]
    assert pay["enrollment"] and pay["premium"] and not pay["unit_price"] and not pay["top_down"]
    rx = G.revenue_bridge(base, m, "MARKET", "PHARMACY", Geo(), 2025, 2031).attrs["components"]
    assert rx["utilization"] == 0 and rx["unit_price"] == 0      # pharmacy has no service counts
    assert rx["spend_per_member"] != 0
    mfr = G.revenue_bridge(base, m, "MARKET", "MANUFACTURER", Geo(), 2025, 2031).attrs["components"]
    assert set(k for k, v in mfr.items() if v) == {"top_down"}


def test_price_assumption_lands_in_price_component(m, adapter, base):
    hist = build_history(adapter)
    a = Assumption("unit_price", "pct", 0.10, Scope(YearRange(2031, 2031), dataset="MARKET", node_id="PRV_HOSP"))
    res = run_scenario(hist, m, Scenario("t", (a,))).result
    d = {k: G.revenue_bridge(res, m, "MARKET", "PRV_HOSP", Geo(), 2025, 2031).attrs["components"][k]
         - G.revenue_bridge(base, m, "MARKET", "PRV_HOSP", Geo(), 2025, 2031).attrs["components"][k]
         for k in G.COMPONENTS}
    assert d["unit_price"] > 0
    assert abs(d["utilization"]) < 1 and abs(d["covered_members"]) < 1


def test_growth_table_and_index(m, base):
    t = G.growth_table(base, m, "MARKET", None, Geo(), "allowed", [(2019, 2025), (2025, 2031)])
    s = node_series(base, m, "MARKET", "PAYER", Geo()).set_index("year")
    row = t.set_index("node_id").loc["PAYER"]
    assert row["rev_2025_2031"] == pytest.approx(cagr(s["revenue"][2025], s["revenue"][2031], 6))
    assert row["d_margin"] == pytest.approx(s["margin"][2031] - s["margin"][2025])
    idx = G.indexed(base, m, "MARKET", None, Geo(), "allowed", 2025)
    assert np.allclose(idx.loc[idx["year"] == 2025, "index"], 100)
    y = G.yoy(base, m, "MARKET", None, Geo(), "allowed")
    assert y.loc["Payer", 2026] == pytest.approx(s["revenue"][2026] / s["revenue"][2025] - 1)


def test_index_is_nan_on_non_positive_base(m, base):
    idx = G.indexed(base, m, "MARKET", "PROVIDER", Geo(), "allowed", 2025, metric="gain")
    neg = idx[(idx["year"] == 2025) & (idx["value"] <= 0)]
    for nid in neg["node_id"]:
        assert idx.loc[idx["node_id"] == nid, "index"].isna().all()
