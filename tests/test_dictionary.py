"""Data dictionary: tables come from config / contracts and cover everything the app defines."""

import pytest
from streamlit.testing.v1 import AppTest

from profit_pools.core import dictionary as D
from profit_pools.core.config import load_config
from profit_pools.core.data import contracts
from profit_pools.core.data.adapters import SyntheticAdapter
from profit_pools.core.model.engine import run_baseline


@pytest.fixture(scope="module")
def world():
    a = SyntheticAdapter()
    return a.metadata, run_baseline(a)


def test_every_definition_is_listed(world):
    m, _ = world
    assert set(D.pools(m)["Id"]) == {n.id for n in m.taxonomy}
    assert set(D.metrics()["Id"]) == set(load_config("metrics")["metrics"])
    cat = load_config("drivers")
    assert set(D.levers()["Driver id"]) == set(cat["drivers"]) | set(cat["growth_drivers"])
    assert len(D.tables()) == sum(len(c) for c in (contracts.POOL_METRICS_COLUMNS, contracts.VALUE_FLOWS_COLUMNS,
                                                   contracts.COVERAGE_POPULATION_COLUMNS))
    assert {"Allowed spend", "Consolidated gain", "Routing (insourcing)"} <= set(D.glossary()["Term"])


def test_coverage_matrix_marks_unavailable(world):
    m, r = world
    c = D.coverage(r, m, "MARKET", 2025)
    vals = set(c.to_numpy().ravel())
    assert vals <= {"margin + services", "margin", "no margin", ""}
    assert "no margin" in vals                                         # the synthetic data has known gaps
    rx = [l.label for l in m.leaves_in_dataset("MARKET", "RX_DISP")]
    assert not (c.loc[rx] == "margin + services").any().any()          # pharmacy never has service counts
    assert len(D.coverage(r, m, "ENTERPRISE", 2025)) < len(c)          # enterprise excludes top-down pools


def _page():
    from profit_pools.modules.data_dictionary.page import render
    render()


@pytest.mark.parametrize("q", ["", "PMPM", "zzz-no-match"])
def test_page_renders(q):
    at = AppTest.from_function(_page, default_timeout=120).run()
    assert not at.exception
    at.text_input(key="pp.dict.q").set_value(q).run()
    assert not at.exception
    if q == "zzz-no-match":
        assert any("Nothing matches" in c.value for c in at.caption)
