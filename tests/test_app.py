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
                                   "MANUFACTURER", "HEALTH_SERVICES", "HST_SERVICES", "HS_CARE_MGMT"])
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


@pytest.mark.parametrize("which", ["all", "one"])
@pytest.mark.parametrize("size", ["pool", "change"])
@pytest.mark.parametrize("metric", ["d_gain", "d_revenue"])
def test_impact_view_renders(app, example, which, size, metric):
    app.session_state[S.K_APPLIED] = example
    app.session_state[S.K_DRAFT] = example
    app.session_state[S.K_VIEW] = "impact"
    app.session_state[S.K_DATASET] = "MARKET"
    app.session_state[S.K_YEAR] = 2028
    app.session_state[S.K_GEO] = "US"
    app.session_state[S.K_FOCUS] = None
    app.session_state[S.K_SELECTED] = None
    app.session_state[S.K_BASIS] = "allowed"
    app.session_state[S.K_SEGMENT] = "hierarchy"
    app.session_state[S.K_IMPACT_OF] = S.IMPACT_ALL if which == "all" else example.enabled[0].id
    app.session_state[S.K_IMPACT_SIZE] = size
    app.session_state[S.K_IMPACT_METRIC] = metric
    app.run()
    _ok(app)
    md = " ".join(m.value for m in app.markdown)
    assert "Impact only" in md and "Δ operating gain" in md
    assert "Not affected" in md or "No pool in this view changes" in " ".join(i.value for i in app.info)
    assert [s.value for s in app.selectbox if s.key == S.K_IMPACT_OF] == [app.session_state[S.K_IMPACT_OF]]


def test_impact_of_removed_assumption_falls_back_to_all(app, example):
    app.session_state[S.K_APPLIED] = example
    app.session_state[S.K_VIEW] = "impact"
    app.session_state[S.K_IMPACT_OF] = example.enabled[0].id
    app.run()
    app.session_state[S.K_APPLIED] = example.remove(example.enabled[0].id)   # e.g. deleted in the builder
    app.run()
    _ok(app)
    assert app.session_state[S.K_IMPACT_OF] == S.IMPACT_ALL


def test_chip_label_reflects_state(app, example):
    app.session_state[S.K_APPLIED] = None
    app.session_state[S.K_VIEW] = "baseline"
    app.run()
    labels = [b.label for b in app.button if b.key == "pp.chip"]
    assert labels and labels[0].startswith("Baseline")
    app.session_state[S.K_APPLIED] = example
    app.session_state[S.K_DRAFT] = example
    app.run()
    assert [b.label for b in app.button if b.key == "pp.chip"][0] == "Illustrative scenario · 5 changes"


# --- value flows (Sankey) --------------------------------------------------------

@pytest.mark.parametrize("metric", ["allowed", "gain"])
@pytest.mark.parametrize("sources", ["group", "both"])
@pytest.mark.parametrize("show", ["chart", "table"])
def test_flows_section_renders(app, metric, sources, show):
    app.session_state[S.K_APPLIED] = None
    app.session_state[S.K_VIEW] = "baseline"
    app.session_state[S.K_DATASET] = "MARKET"
    app.session_state[S.K_YEAR] = 2025
    app.session_state[S.K_GEO] = "US"
    app.session_state["pp.sk.metric"] = metric
    app.session_state["pp.sk.sources"] = sources
    app.session_state["pp.sk.show"] = show
    app.session_state["pp.sk.expand"] = ["PRV_HOSP"]
    app.session_state["pp.sk.sel"] = ("node", "recipient:PRV_HOSP_ACUTE")
    app.run()
    _ok(app)
    md = " ".join(x.value for x in app.markdown)
    assert "Value flows" in md and "every column sums to this total" in md
    assert "Pool revenue (all funders)" in md        # node inspector rendered


def test_flows_link_inspector_and_compare(app, example):
    app.session_state[S.K_APPLIED] = example
    app.session_state[S.K_DRAFT] = example
    app.session_state[S.K_VIEW] = "compare"
    app.session_state[S.K_YEAR] = 2030
    app.session_state["pp.sk.metric"] = "allowed"
    app.session_state["pp.sk.sources"] = "group"
    app.session_state["pp.sk.expand"] = []
    app.session_state["pp.sk.depth"] = 1
    app.session_state["pp.sk.sel"] = ("link", "source_group:COMM", "recipient:PRV_HOSP")
    app.run()
    _ok(app)
    md = " ".join(x.value for x in app.markdown)
    assert "Commercial → Hospitals" in md and "Δ vs baseline" in md and "Allowed PMPM (this flow)" in md


def test_flows_enterprise_and_county(app):
    app.session_state[S.K_APPLIED] = None
    app.session_state[S.K_DATASET] = "ENTERPRISE"
    app.session_state[S.K_GEO] = "NVL-01"
    app.session_state["pp.sk.consumer"] = True
    app.session_state["pp.sk.sel"] = None
    app.run()
    _ok(app)
    app.session_state[S.K_DATASET] = "MARKET"
    app.session_state[S.K_GEO] = "US"
    app.session_state["pp.sk.consumer"] = False


@pytest.mark.parametrize("span", [(2019, 2025), (2025, 2031), (2027, 2028)])
@pytest.mark.parametrize("metric", ["revenue", "gain"])
@pytest.mark.parametrize("focus", [None, "PHARMACY"])
def test_growth_section_renders(app, span, metric, focus):
    app.session_state[S.K_APPLIED] = None
    app.session_state[S.K_VIEW] = "baseline"
    app.session_state[S.K_DATASET] = "MARKET"
    app.session_state[S.K_FOCUS] = focus
    app.session_state[S.K_SELECTED] = None
    app.session_state["pp.growth.from"], app.session_state["pp.growth.to"] = span
    app.session_state["pp.growth.metric"] = metric
    app.run()
    _ok(app)
    md = " ".join(m.value for m in app.markdown)
    assert "Compound annual growth" in md and f"What drives operating gain, {span[0]}→{span[1]}" in md


@pytest.mark.parametrize("view", ["baseline", "compare"])
@pytest.mark.parametrize("geo", ["US", "NVL", "NVL-01"])
def test_value_capture_section(app, example, view, geo):
    from profit_pools.core.model.scenario import Assumption, Scope, YearRange
    sc = example.add(Assumption("routing_share", "pp", 0.10, Scope(YearRange(2027, None), dataset="ENTERPRISE",
                                                                   node_id="PRV_PHYS")))
    app.session_state[S.K_APPLIED] = sc if view != "baseline" else None
    app.session_state[S.K_VIEW] = view
    app.session_state[S.K_DATASET] = "ENTERPRISE"
    app.session_state[S.K_YEAR] = 2028
    app.session_state[S.K_GEO] = geo
    app.session_state[S.K_FOCUS] = None
    app.session_state[S.K_SELECTED] = None
    app.run()
    _ok(app)
    md = " ".join(m.value for m in app.markdown)
    if geo == "NVL-01":
        assert any("tracked by state" in i.value for i in app.info)
    else:
        assert "Consolidated gain" in md and "Owned assets" in md
        if view == "compare":
            assert "Δ gain" in md
    app.session_state[S.K_GEO] = "US"
    app.session_state[S.K_DATASET] = "MARKET"


@pytest.mark.parametrize("scenario", [False, True])
def test_export_menu(app, example, scenario):
    app.session_state[S.K_APPLIED] = example if scenario else None
    app.session_state[S.K_VIEW] = "compare" if scenario else "baseline"
    app.session_state[S.K_DATASET] = "MARKET"
    app.session_state[S.K_GEO] = "US"
    app.run()
    _ok(app)
    dl = app.get("download_button")
    labels = [d.proto.label for d in dl]
    assert {"CSV · rows behind this view", "Scenario outputs · ZIP of CSVs", "Scenario JSON",
            "HTML snapshot", "PowerPoint summary"} <= set(labels)
    js = next(d for d in dl if d.proto.label == "Scenario JSON")
    assert js.proto.disabled == (not scenario)      # no dead control: disabled with a reason


@pytest.mark.parametrize("target, metric", [("PAYER", "gain"), ("PROVIDER", "revenue"), ("HEALTH_SERVICES", "gain"),
                                            ("PHARMACY", "revenue")])
def test_growth_bridge_per_pool(app, target, metric):
    app.session_state[S.K_APPLIED] = None
    app.session_state[S.K_VIEW] = "baseline"
    app.session_state[S.K_DATASET] = "MARKET"
    app.session_state[S.K_FOCUS] = None
    app.session_state[S.K_SELECTED] = None
    app.session_state["pp.growth.from"], app.session_state["pp.growth.to"] = 2025, 2031
    app.run()                                    # redraw "Bridge for" with the all-pools options first
    app.session_state["pp.growth.bridge_node"] = target
    app.session_state["pp.growth.bridge_metric"] = metric
    app.run()
    _ok(app)
    md = " ".join(m.value for m in app.markdown)
    word = "operating gain" if metric == "gain" else "revenue"
    assert f"What drives {word}, 2025→2031" in md
    app.session_state["pp.growth.bridge_node"] = "__scope__"
    app.run()
    _ok(app)
    assert app.session_state["pp.growth.bridge_metric"] == "gain"        # overall is gain-only (additive)


def _flows_reset(app, dataset="MARKET"):
    for k, v in {S.K_APPLIED: None, S.K_VIEW: "baseline", S.K_DATASET: dataset, S.K_GEO: "US", S.K_FOCUS: None,
                 S.K_SELECTED: None, "pp.sk.mode": "spend", "pp.sk.expand": [], "pp.sk.depth": 1,
                 "pp.sk.order": "auto", "pp.sk.pins": [], "pp.sk.sel": None, "pp.sk.show": "chart"}.items():
        app.session_state[k] = v


def test_sankey_expand_tree_and_fold(app):
    _flows_reset(app)
    app.run()
    _ok(app)
    boxes = [c for c in app.checkbox if c.key and c.key.startswith("pp.sk.x.")]
    assert "pp.sk.x.PRV_HOSP" in {c.key for c in boxes} and "pp.sk.x.PRV_HOSP_ACUTE" not in {c.key for c in boxes}
    app.checkbox(key="pp.sk.x.PRV_HOSP").check().run()
    _ok(app)
    assert app.session_state["pp.sk.expand"] == ["PRV_HOSP"]
    assert "pp.sk.x.PRV_HOSP_ACUTE" in {c.key for c in app.checkbox}        # the next level appears
    app.checkbox(key="pp.sk.x.PRV_HOSP_ACUTE").check().run()
    app.checkbox(key="pp.sk.x.PRV_HOSP").uncheck().run()                      # folding removes descendants
    _ok(app)
    assert app.session_state["pp.sk.expand"] == []


def test_sankey_inspector_expand_buttons(app):
    _flows_reset(app)
    app.session_state["pp.sk.sel"] = ("node", "recipient:PRV_HOSP")
    app.run()
    _ok(app)
    app.button(key="pp.sk.open").click().run()
    _ok(app)
    assert "PRV_HOSP" in app.session_state["pp.sk.expand"]
    app.session_state["pp.sk.sel"] = ("node", "recipient:PRV_HOSP_ACUTE")
    app.run()
    app.button(key="pp.sk.fold").click().run()
    _ok(app)
    assert app.session_state["pp.sk.expand"] == []


@pytest.mark.parametrize("order", ["size", "hierarchy", "name"])
def test_sankey_arrange(app, order):
    _flows_reset(app)
    app.session_state["pp.sk.order"] = order
    app.session_state["pp.sk.pins"] = ["recipient:RX_DISP"]
    app.run()
    _ok(app)


@pytest.mark.parametrize("detail", ["group", "plan"])
@pytest.mark.parametrize("view", ["baseline", "compare"])
@pytest.mark.parametrize("show", ["chart", "table"])
def test_sankey_enterprise_dollar(app, example, detail, view, show):
    _flows_reset(app, "ENTERPRISE")
    app.run()
    app.session_state[S.K_APPLIED] = example if view == "compare" else None
    app.session_state[S.K_VIEW] = view
    app.session_state["pp.sk.mode"] = "enterprise"
    app.session_state["pp.sk.ent_detail"] = detail
    app.session_state["pp.sk.ent_focus"] = "gain" if detail == "plan" else "pnl"
    app.session_state["pp.sk.show"] = show
    app.run()
    _ok(app)
    md = " ".join(m.value for m in app.markdown)
    assert "enterprise operating gain" in md and "Same totals as Value capture" in md
    _flows_reset(app)
    app.run()
    assert app.session_state["pp.sk.mode"] == "spend"                   # market has no enterprise-dollar view


@pytest.mark.parametrize("dataset", ["MARKET", "ENTERPRISE"])
@pytest.mark.parametrize("pick", [0, 1, 3])
def test_compare_section(app, dataset, pick):
    app.session_state[S.K_APPLIED] = None
    app.session_state[S.K_VIEW] = "baseline"
    app.session_state[S.K_DATASET] = dataset
    app.session_state[S.K_GEO] = "US"
    app.session_state[S.K_FOCUS] = None
    app.session_state[S.K_SELECTED] = None
    app.session_state[S.K_YEAR] = 2029
    app.run()
    names = [s.name for s in app.session_state[S.K_LIBRARY]][:pick]
    app.session_state[S.K_COMPARE] = names
    app.run()
    _ok(app)
    md = " ".join(m.value for m in app.markdown)
    assert "Compare scenarios" in md
    if pick:
        assert all(n in md for n in names) and "Δ gain" in md
        assert ("Consolidated gain" in md) == (dataset == "ENTERPRISE")
    app.session_state[S.K_DATASET] = "MARKET"
