"""UI smoke tests: every control combination renders without exceptions (Streamlit AppTest)."""

import pytest
from streamlit.testing.v1 import AppTest

from profit_pools.shell import state as S


@pytest.fixture(scope="module")
def app():
    at = AppTest.from_file("../app.py", default_timeout=120)
    at.run()
    assert not at.exception, [e.message for e in at.exception]
    return at


def _ok(at):
    assert not at.exception, [e.message for e in at.exception]


def test_default_render(app):
    md = " ".join(m.value for m in app.markdown)
    assert "Profit Pools Explorer" in md and "Gross revenue" in md.replace("GROSS", "Gross")
    assert [b.label for b in app.button if b.key == "pp.chip"] == ["Baseline · build a scenario"]


@pytest.mark.parametrize("dataset", ["MARKET", "ENTERPRISE"])
@pytest.mark.parametrize("basis", ["allowed", "paid", "consumer"])
@pytest.mark.parametrize("segment", ["hierarchy", "geography", "funding"])
def test_control_combinations(app, dataset, basis, segment):
    app.session_state[S.K_DATASET] = dataset
    app.session_state[S.K_BASIS] = basis
    app.session_state[S.K_SEGMENT] = segment
    app.session_state[S.K_FOCUS] = None
    app.session_state[S.K_SELECTED] = None
    app.run()
    _ok(app)


@pytest.mark.parametrize("geo", ["US", "SRG", "WMR", "NVL-01"])
@pytest.mark.parametrize("year", [2019, 2025, 2031])
def test_geography_and_years(app, geo, year):
    app.session_state[S.K_DATASET] = "ENTERPRISE" if geo == "WMR" else "MARKET"  # WMR: no enterprise presence
    app.session_state[S.K_GEO] = geo
    app.session_state[S.K_YEAR] = year
    app.session_state[S.K_BASIS] = "allowed"
    app.session_state[S.K_SEGMENT] = "geography"
    app.run()
    _ok(app)
    app.session_state[S.K_GEO] = "US"


@pytest.mark.parametrize("focus", ["PAYER", "PAY_COMM", "PRV_HOSP", "PRV_IP", "PHARMACY", "RX_PBM",
                                   "MANUFACTURER", "HEALTH_SERVICES", "HS_BH_PROGRAMS"])
def test_drill_every_level(app, focus):
    app.session_state[S.K_DATASET] = "MARKET"
    app.session_state[S.K_YEAR] = 2025
    app.session_state[S.K_SEGMENT] = "hierarchy"
    app.session_state[S.K_BASIS] = "allowed"
    app.session_state[S.K_FOCUS] = focus
    app.session_state[S.K_SELECTED] = None
    app.run()
    _ok(app)
    crumbs = [b.label for b in app.button if b.key and b.key.startswith("pp.crumb.")]
    assert crumbs[0] == "All pools" and len(crumbs) >= 2


def test_selection_drives_inspector_and_drill(app):
    app.session_state[S.K_FOCUS] = None
    app.session_state[S.K_SELECTED] = ("PROVIDER", "PRV_HOSP", "PRV_HOSP")
    app.run()
    _ok(app)
    md = " ".join(m.value for m in app.markdown)
    assert "Hospitals" in md and "Revenue by funding source" in md
    drill = next(b for b in app.button if b.key == "pp.drill.col")
    drill.click().run()
    _ok(app)
    assert app.session_state[S.K_FOCUS] == "PROVIDER"
    assert app.session_state[S.K_SELECTED] == ("PRV_HOSP", None, "PRV_HOSP")
    up = next(b for b in app.button if b.key == "pp.crumb.None")
    up.click().run()
    assert app.session_state[S.K_FOCUS] is None


def test_dataset_switch_resets_excluded_focus(app):
    app.session_state[S.K_DATASET] = "MARKET"
    app.session_state[S.K_FOCUS] = "MANUFACTURER"
    app.run()
    app.session_state[S.K_DATASET] = "ENTERPRISE"
    app.run()
    _ok(app)
    assert app.session_state[S.K_FOCUS] is None


# --- scenario views -------------------------------------------------------------

@pytest.fixture
def example():
    from profit_pools.core.config import CONFIG_DIR
    from profit_pools.core.model.scenario import Scenario
    return Scenario.from_json((CONFIG_DIR / "scenarios" / "illustrative.json").read_text())


@pytest.mark.parametrize("view", ["scenario", "compare"])
@pytest.mark.parametrize("focus", [None, "PROVIDER", "PAY_COMM"])
@pytest.mark.parametrize("basis", ["allowed", "consumer"])
def test_scenario_views_render(app, example, view, focus, basis):
    app.session_state[S.K_APPLIED] = example
    app.session_state[S.K_DRAFT] = example
    app.session_state[S.K_VIEW] = view
    app.session_state[S.K_DATASET] = "MARKET"
    app.session_state[S.K_YEAR] = 2030
    app.session_state[S.K_GEO] = "US"
    app.session_state[S.K_FOCUS] = focus
    app.session_state[S.K_SELECTED] = None
    app.session_state[S.K_BASIS] = basis
    app.session_state[S.K_SEGMENT] = "hierarchy"
    app.session_state[S.K_COLOR_BY] = "delta_margin"
    app.run()
    _ok(app)
    md = " ".join(m.value for m in app.markdown)
    assert "scenario" in md.lower()
    if focus == "PAY_COMM" and basis == "consumer":
        assert "no paid / consumer split" in md      # payer pools: nothing to compare on this basis
    elif view == "compare":
        assert "vs baseline" in md and "Why the scenario differs from baseline" in md


def test_chip_label_reflects_state(app, example):
    from profit_pools.shell.scenario_builder import chip_label
    app.session_state[S.K_APPLIED] = None
    app.session_state[S.K_VIEW] = "baseline"
    app.run()
    labels = [b.label for b in app.button if b.key == "pp.chip"]
    assert labels and labels[0].startswith("Baseline")
    app.session_state[S.K_APPLIED] = example
    app.session_state[S.K_DRAFT] = example
    app.run()
    assert [b.label for b in app.button if b.key == "pp.chip"][0] == "Illustrative scenario · 5 changes"
