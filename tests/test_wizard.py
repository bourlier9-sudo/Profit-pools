"""Guided assumption builder: every pool × lever it offers maps onto a valid, round-trippable assumption."""

import logging

import pytest
import streamlit as st

from profit_pools.core.data.adapters import get_adapter
from profit_pools.core.model.cube import build_history
from profit_pools.core.model.scenario import Scenario
from profit_pools.core.model.scenario_engine import run_scenario, validate
from profit_pools.shell import assumption_wizard as W

logging.getLogger("streamlit").setLevel(logging.ERROR)


@pytest.fixture(scope="module")
def adapter():
    return get_adapter()


def _combos(meta, datasets=("BOTH", "MARKET", "ENTERPRISE")):
    for ds in datasets:
        d = None if ds == "BOTH" else ds
        for w in W.where_options(meta, d):
            for cat in W.CATEGORIES:
                for lv in W.levers_for(meta, w, d, cat):
                    yield ds, w, cat, lv


def _build(meta, ds, w, cat, lv):
    ss = st.session_state
    W.load(meta, None)
    ss.update({W.W + "dataset": ds, W.W + "where": w, W.W + "category": cat, W.W + "lever": lv.id})
    W._on_lever_change()
    return W.build(meta, None)


def test_every_offered_lever_validates_and_round_trips(adapter):
    meta = adapter.metadata
    n = 0
    for ds, w, cat, lv in _combos(meta):
        a = _build(meta, ds, w, cat, lv)
        errors = [i.message for i in validate(meta, Scenario("t", (a,))) if i.level == "error"]
        assert not errors, (ds, w, lv.id, errors)
        W.load(meta, a)
        assert W.build(meta, a.id) == a, (ds, w, lv.id)
        n += 1
    assert n > 500


def test_offered_levers_match_data(adapter):
    """Each lever at layer level actually governs cells (no dead options)."""
    meta = adapter.metadata
    hist = build_history(adapter)
    layers = [n.id for n in meta.taxonomy.layers()]
    for ds, w, cat, lv in _combos(meta, ("MARKET",)):
        if w not in layers and not w.startswith(W.FUND):
            continue
        a = _build(meta, ds, w, cat, lv)
        run = run_scenario(hist, meta, Scenario("t", (a,)))
        e = next(x for x in run.ledger if x.assumption_id == a.id)
        assert e.status != "no match", (w, lv.id)


def test_enterprise_hides_market_only_levers(adapter):
    meta = adapter.metadata
    ids = {lv.id for _, _, _, lv in _combos(meta, ("ENTERPRISE",))}
    assert not ids & {"rec.fund", "rec.supp", "fund.pop", "fund.pmpm", "supp.amt"}
    assert "pay.shift" in ids
    assert all(t in meta.taxonomy for t in W.shift_targets(meta, "ENTERPRISE", "PAY_MCD"))


def test_pharmacy_never_offers_volume_levers(adapter):
    meta = adapter.metadata
    ids = {lv.id for lv in W.levers_for(meta, "PHARMACY", "MARKET", "financials")}
    assert "rec.util" not in ids and "rec.price" not in ids
    assert "rec.spend" in ids


def test_spend_sentence_names_payer_and_payee(adapter):
    meta = adapter.metadata
    a = _build(meta, "MARKET", "PRV_HOSP", "financials", W.LEVER_BY_ID["rec.price"])
    text = W.sentence(meta, a, W.LEVER_BY_ID["rec.price"])
    assert "all payers" in text and "Hospital" in text
