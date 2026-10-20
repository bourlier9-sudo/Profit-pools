"""Scenario library and side-by-side comparison."""

import pytest
from streamlit.testing.v1 import AppTest

from profit_pools.core.config import CONFIG_DIR
from profit_pools.core.data.adapters import SyntheticAdapter
from profit_pools.core.model import compare as C
from profit_pools.core.model.cube import build_history
from profit_pools.core.model.engine import run_baseline
from profit_pools.core.model.scenario import (Scenario, ScenarioFormatError, library_from_json, library_to_json)
from profit_pools.core.model.scenario_engine import run_scenario
from profit_pools.core.model.views import Geo, node_metrics
from profit_pools.shell import state as S

EXAMPLES = sorted((CONFIG_DIR / "scenarios").glob("*.json"))


@pytest.fixture(scope="module")
def world():
    a = SyntheticAdapter()
    m, hist = a.metadata, build_history(a)
    scs = [Scenario.from_json(p.read_text()) for p in EXAMPLES]
    res = {C.BASELINE: run_baseline(a)} | {s.name: run_scenario(hist, m, s).result for s in scs}
    return m, scs, res


def test_examples_are_valid_and_distinct(world):
    m, scs, res = world
    assert len(scs) >= 3 and len({s.name for s in scs}) == len(scs)
    assert all(r is not None for r in res.values())


def test_library_round_trip_and_single_file(world):
    _, scs, _ = world
    back = library_from_json(library_to_json(scs))
    assert [s.to_json() for s in back] == [s.to_json() for s in scs]
    assert library_from_json(scs[0].to_json())[0].to_json() == scs[0].to_json()
    with pytest.raises(ScenarioFormatError):
        library_from_json('{"schema_version": 9, "scenarios": []}')
    with pytest.raises(ScenarioFormatError):
        library_from_json('{"schema_version": 1, "scenarios": [{"name": "x"}]}')


@pytest.mark.parametrize("dataset, focus, geo", [("MARKET", None, "US"), ("MARKET", "PROVIDER", "NVL"),
                                                 ("ENTERPRISE", None, "US")])
def test_kpis_and_pool_deltas_reconcile(world, dataset, focus, geo):
    m, _, res = world
    g = Geo.parse(m, geo)
    k = C.kpi_table(res, m, dataset, 2029, focus, g).set_index("scenario")
    b = node_metrics(res[C.BASELINE], m, dataset, 2029, focus, g)
    for name, r in res.items():
        nm = node_metrics(r, m, dataset, 2029, focus, g)
        assert k.loc[name, "revenue"] == pytest.approx(nm["revenue"])
        assert k.loc[name, "d_gain"] == pytest.approx(nm["gain"] - b["gain"])
    d = C.pool_deltas(res, m, dataset, 2029, focus, g)
    for name in res:
        if name != C.BASELINE:
            assert d[d["scenario"] == name]["delta"].sum() == pytest.approx(k.loc[name, "d_gain"], abs=1.0)
    assert ("consolidated_gain" in k) == (dataset == "ENTERPRISE")


def _lib_script():
    import streamlit as st

    from profit_pools.core.model.scenario import Assumption, Scope, YearRange
    from profit_pools.shell import data, scenario_builder
    from profit_pools.shell import state as S
    S.init(data.adapter().metadata)
    if "seeded" not in st.session_state:
        st.session_state["seeded"] = True
        st.session_state[S.K_DRAFT] = S.library()[0].with_assumptions([
            Assumption("enrollment", "pct", 0.02, Scope(YearRange(2027, None), dataset="MARKET", node_id="PAY_MA"))])
    scenario_builder._library()


def test_library_save_load_delete():
    at = AppTest.from_function(_lib_script, default_timeout=120).run()
    assert not at.exception
    n0 = len(at.session_state[S.K_LIBRARY])
    name = at.session_state[S.K_DRAFT].name
    at.button(key="pp.b.lib.save").click().run()
    lib = at.session_state[S.K_LIBRARY]
    assert len(lib) == n0 and next(s for s in lib if s.name == name).assumptions[0].driver == "enrollment"
    other = lib[1].name
    from profit_pools.shell.scenario_builder import _lib_key
    at.button(key=f"pp.b.lib.load.{_lib_key(other)}").click().run()
    assert at.session_state[S.K_DRAFT].name == other
    at.button(key=f"pp.b.lib.del.{_lib_key(other)}").click().run()
    assert other not in [s.name for s in at.session_state[S.K_LIBRARY]]
