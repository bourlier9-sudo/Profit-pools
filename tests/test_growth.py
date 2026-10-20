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


@pytest.mark.parametrize("metric", ["gain", "revenue"])
@pytest.mark.parametrize("dataset", ["MARKET", "ENTERPRISE"])
@pytest.mark.parametrize("node", NODES)
@pytest.mark.parametrize("years", [(2019, 2025), (2025, 2031), (2024, 2025)])
def test_bridge_reconciles(m, base, metric, dataset, node, years):
    if node is not None and not m.in_dataset(node, dataset):
        pytest.skip("not in dataset")
    for geo in (Geo(), Geo.parse(m, next(iter(m.geography.states))), Geo.parse(m, "NVL-01")):
        b = G.bridge(base, m, dataset, node, geo, *years, metric=metric,
                     by_child=G.bridge_kind(m, dataset, node) == "top_down")
        s = node_series(base, m, dataset, node, geo).set_index("year")[metric].fillna(0.0)
        start, end = s.get(years[0], 0.0), s.get(years[1], 0.0)
        assert b["value"].iloc[0] == pytest.approx(start) and b["value"].iloc[-1] == pytest.approx(end)
        assert abs(b.attrs["residual"]) < max(1.0, 1e-9 * abs(end))


def test_no_funder_lines_and_detail_follows_economics(m, base):
    """Who pays is not a growth driver: uninsured / FFS spending decomposes into people and spend per person."""
    assert not any("uninsured" in v.lower() or "ffs" in v.lower() for v in G.DRIVERS.values())
    pay = G.bridge(base, m, "MARKET", "PAYER", Geo(), 2025, 2031).attrs["drivers"]
    assert pay["enrollment"] and pay["premium"] and pay["medical_cost"] and pay["admin"]
    assert not pay["people"] and not pay["market"]
    rx = G.bridge(base, m, "MARKET", "RX_DISP", Geo(), 2025, 2031).attrs["drivers"]
    assert rx["utilization"] == 0 and rx["unit_price"] == 0 and rx["spend_per_person"] != 0   # no counts in Rx
    hst = G.bridge(base, m, "MARKET", "HEALTH_SERVICES", Geo(), 2025, 2031, by_child=True)
    kids = [n.id for n in m.taxonomy.children("HEALTH_SERVICES")]
    assert [k for k in hst["key"] if k.startswith("market:")] == [f"market:{k}" for k in kids
                                                                     if f"market:{k}" in set(hst["key"])]
    assert G.bridge_kind(m, "MARKET", None) == "overall" and G.bridge_kind(m, "MARKET", "PHARMACY") == "mixed"


def test_overall_gain_is_sum_of_layers(m, base):
    total = G.bridge(base, m, "MARKET", None, Geo(), 2025, 2031)
    parts = [G.bridge(base, m, "MARKET", l.id, Geo(), 2025, 2031) for l in m.taxonomy.layers()]
    for k in G.DRIVERS:
        assert total.attrs["drivers"][k] == pytest.approx(sum(p.attrs["drivers"][k] for p in parts), abs=1.0)


def test_price_assumption_lands_in_price_component(m, adapter, base):
    hist = build_history(adapter)
    a = Assumption("unit_price", "pct", 0.10, Scope(YearRange(2031, 2031), dataset="MARKET", node_id="PRV_HOSP"))
    res = run_scenario(hist, m, Scenario("t", (a,))).result
    for metric in ("revenue", "gain"):
        d = {k: G.bridge(res, m, "MARKET", "PRV_HOSP", Geo(), 2025, 2031, metric).attrs["drivers"][k]
             - G.bridge(base, m, "MARKET", "PRV_HOSP", Geo(), 2025, 2031, metric).attrs["drivers"][k]
             for k in G.DRIVERS}
        assert d["unit_price"] > 0
        assert abs(d["utilization"]) < 1 and abs(d["people"]) < 1 and abs(d["margin"]) < 1


def test_margin_assumption_lands_in_margin_component(m, adapter, base):
    hist = build_history(adapter)
    a = Assumption("provider_margin", "pp", 0.01, Scope(YearRange(2031, 2031), dataset="MARKET", node_id="PRV_HOSP"))
    res = run_scenario(hist, m, Scenario("t", (a,))).result
    d = {k: G.bridge(res, m, "MARKET", "PRV_HOSP", Geo(), 2025, 2031).attrs["drivers"][k]
         - G.bridge(base, m, "MARKET", "PRV_HOSP", Geo(), 2025, 2031).attrs["drivers"][k] for k in G.DRIVERS}
    assert d["margin"] > 0 and all(abs(v) < 1 for k, v in d.items() if k != "margin")


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


def test_attractiveness_definitions(m, base):
    a = G.attractiveness(base, m, "MARKET", None, Geo(), "allowed", 2025, 2031)
    s = node_series(base, m, "MARKET", "PAYER", Geo()).set_index("year")
    r = a.set_index("node_id").loc["PAYER"]
    assert r["cagr"] == pytest.approx(cagr(s["revenue"][2025], s["revenue"][2031], 6))
    assert r["margin"] == pytest.approx(s["margin"][2031]) and r["gain"] == pytest.approx(s["gain"][2031])
    tot = node_series(base, m, "MARKET", None, Geo()).set_index("year")
    assert a.attrs["avg_growth"] == pytest.approx(cagr(tot["revenue"][2025], tot["revenue"][2031], 6))
    deep = G.attractiveness(base, m, "MARKET", None, Geo(), "allowed", 2025, 2031, levels=2)
    assert len(deep) > len(a) and set(deep["layer_id"]) == set(a["node_id"])
