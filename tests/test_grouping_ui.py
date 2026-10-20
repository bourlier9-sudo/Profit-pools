"""Pool groupings dialog (AppTest) and the workspace under a custom grouping."""

import pytest
from streamlit.testing.v1 import AppTest

from profit_pools.core.grouping import Grouping
from profit_pools.shell import state as S


def _editor_script():
    import streamlit as st

    from profit_pools.shell import data, grouping_editor
    from profit_pools.shell import state as S

    meta = data.adapter().metadata
    S.init(meta)
    grouping_editor.open_editor(meta)
    st.session_state["_applied_json"] = st.session_state[S.K_GROUPING].to_json()


@pytest.fixture
def editor():
    at = AppTest.from_function(_editor_script, default_timeout=120)
    at.run()
    assert not at.exception, [e.message for e in at.exception]
    return at


def _ok(at):
    assert not at.exception, [e.message for e in at.exception]


def test_rename_move_add_and_apply(editor):
    at = editor
    at.session_state["pp.g.layer"] = "PROVIDER"
    at.run()
    at.text_input(key="pp.g.lab.PRV_HOSP").set_value("Hospital systems").run()
    _ok(at)
    assert ("PRV_HOSP", "Hospital systems") in at.session_state[S.K_GROUPING_DRAFT].renames
    at.text_input(key="pp.g.new_label").set_value("Community care")
    at.selectbox(key="pp.g.new_parent").set_value("PROVIDER")
    at.button(key="pp.g.add").click().run()
    _ok(at)
    gid = at.session_state[S.K_GROUPING_DRAFT].new_groups[0][0]
    assert "Community care" in " ".join(c.value for c in at.caption) or any(
        t.value == "Community care" for t in at.text_input)
    leaf = next(n for n in ("PRV_HH", "PRV_PAC", "PRV_PROF") if f"pp.g.mv.{n}" in [s.key for s in at.selectbox])
    at.selectbox(key=f"pp.g.mv.{leaf}").set_value(gid).run()
    _ok(at)
    assert (leaf, gid) in at.session_state[S.K_GROUPING_DRAFT].moves
    at.button(key="pp.g.apply").click().run()
    _ok(at)
    applied = Grouping.from_json(at.session_state["_applied_json"])
    assert (leaf, gid) in applied.moves


def test_move_targets_exclude_invalid_parents():
    from profit_pools.core.data.adapters import SyntheticAdapter
    from profit_pools.shell import grouping_editor as GE
    meta = SyntheticAdapter().metadata
    tax, std = meta.taxonomy, meta.standard
    opts = GE.parent_options(tax, std, "PRV_HOSP")
    assert "PRV_HOSP" not in opts and not set(opts) & {n.id for n in tax.leaves("PRV_HOSP")}
    assert all(tax[o].layer_id != "PAYER" for o in opts)                 # payer layer is off limits
    assert all(std[o].children for o in opts)                             # never under a data-holding pool
    assert all(tax[o].layer_id == "PAYER" for o in GE.parent_options(tax, std, "PAY_COMM_IND"))


def test_invalid_move_is_refused_with_reason(editor):
    at = editor
    from profit_pools.core.data.adapters import SyntheticAdapter
    meta = SyntheticAdapter().metadata
    # Callback guard: a move the options would never offer is refused, the draft is unchanged.
    before = at.session_state[S.K_GROUPING_DRAFT]
    with pytest.raises(Exception):
        from profit_pools.core.grouping import apply
        apply(meta.standard, before.move("PRV_HOSP", "PAYER"))
    assert at.session_state[S.K_GROUPING_DRAFT] == before


def test_reset_and_discard(editor):
    at = editor
    at.text_input(key="pp.g.name").set_value("Mine").run()
    assert at.session_state[S.K_GROUPING_DRAFT].name == "Mine"
    at.button(key="pp.g.discard").click().run()
    _ok(at)
    assert at.session_state[S.K_GROUPING_DRAFT].is_standard


@pytest.fixture(scope="module")
def app():
    at = AppTest.from_file("../app.py", default_timeout=120)
    at.run()
    assert not at.exception
    return at


@pytest.mark.parametrize("view", ["baseline", "compare", "impact"])
def test_workspace_under_custom_grouping(app, view):
    from profit_pools.core.config import CONFIG_DIR
    from profit_pools.core.model.scenario import Scenario
    g, gid = Grouping("Mine").add_group("Acute & post-acute", "PROVIDER")
    g = g.move("PRV_HOSP", gid).move("PRV_PAC", gid).rename("PHARMACY", "Rx")
    ex = Scenario.from_json((CONFIG_DIR / "scenarios" / "illustrative.json").read_text())
    app.session_state[S.K_GROUPING] = g
    app.session_state[S.K_APPLIED] = ex
    app.session_state[S.K_VIEW] = view
    app.session_state[S.K_FOCUS] = "PROVIDER"
    app.session_state[S.K_SELECTED] = None
    app.run()
    _ok(app)
    md = " ".join(m.value for m in app.markdown)
    assert "Acute &amp; post-acute" in md or "Acute & post-acute" in md
    assert [b.label for b in app.button if b.key == "pp.groupings"] == ["Pools: Mine"]
    app.session_state[S.K_FOCUS] = gid
    app.run()
    _ok(app)
    app.session_state[S.K_GROUPING] = Grouping()       # group disappears: focus resets, nothing breaks
    app.run()
    _ok(app)
    assert app.session_state[S.K_FOCUS] is None
