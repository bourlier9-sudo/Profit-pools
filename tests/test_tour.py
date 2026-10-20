"""Guided tour: starts once per session, steps forward and back, can be skipped, and its widget targets exist."""

from streamlit.testing.v1 import AppTest

from profit_pools.shell import state as S
from profit_pools.shell import tour


def test_css_class_matches_streamlit_rule():
    assert tour.css_class("pp.chip") == "st-key-pp-chip"
    assert tour.css_class("pp_globalbar") == "st-key-pp_globalbar"
    assert tour.css_class("pp.growth.attract") == "st-key-pp-growth-attract"


def test_tour_flow():
    at = AppTest.from_file("../app.py", default_timeout=180).run()
    assert not at.exception
    assert at.session_state["pp.tour.step"] == 0                       # auto-starts on the first visit
    at.button(key="pp.tour.next").click().run()
    assert at.session_state["pp.tour.step"] == 1
    at.button(key="pp.tour.back").click().run()
    assert at.session_state["pp.tour.step"] == 0
    for _ in range(40):
        b = at.button(key="pp.tour.next")
        last = b.label == "Done"
        b.click().run()
        assert not at.exception
        if last:
            break
    assert at.session_state["pp.tour.step"] is None
    at.run()
    assert at.session_state["pp.tour.step"] is None                    # does not restart in the same session
    at.button(key="pp.tour.start").click().run()
    assert at.session_state["pp.tour.step"] == 0
    at.button(key="pp.tour.skip").click().run()
    assert at.session_state["pp.tour.step"] is None


def test_widget_targets_exist():
    at = AppTest.from_file("../app.py", default_timeout=180).run()
    keys = {w.key for w in [*at.button, *at.selectbox, *at.multiselect, *at.get("button_group")] if w.key}
    for k in (S.K_DATASET, "pp.chip", "pp.groupings", S.K_COMPARE, "pp.tour.start"):
        assert k in keys, k
    selectors = " ".join(s.selector or "" for s in tour.steps())
    assert "Compare or isolate the change" not in [s.title for s in tour.steps()]    # no scenario applied here
    for k in ("pp.chip", "pp.groupings", "pp.growth.attract", "pp.growth.bridge", "pp_export"):
        assert tour.css_class(k) in selectors
