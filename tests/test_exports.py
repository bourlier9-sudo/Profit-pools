"""Exports (Phase 7): every file is valid and parses back to the same numbers the workspace shows."""

import io
import json
import re
import zipfile

import numpy as np
import pandas as pd
import pytest

from profit_pools.core.config import CONFIG_DIR
from profit_pools.core.data.adapters import SyntheticAdapter
from profit_pools.core.model import views as V
from profit_pools.core.model.cube import build_history
from profit_pools.core.model.engine import run_baseline
from profit_pools.core.model.scenario import Scenario
from profit_pools.core.model.scenario_engine import run_scenario
from profit_pools.core.model.views import Geo
from profit_pools.export import FICTIONAL, ExportContext, csv_export, html_snapshot, pptx_summary
from profit_pools.export import figures as F


@pytest.fixture(scope="module")
def setup():
    a = SyntheticAdapter()
    m = a.metadata
    base = run_baseline(a)
    sc = Scenario.from_json((CONFIG_DIR / "scenarios" / "illustrative.json").read_text())
    res = run_scenario(build_history(a), m, sc).result
    return m, base, res, sc


def ctx_for(setup, view="compare", dataset="MARKET", year=2028, geo="US", focus=None):
    m, base, res, sc = setup
    return ExportContext(m, base if view == "baseline" else res, base, view, dataset, year, Geo.parse(m, geo),
                         focus=focus, scenario=None if view == "baseline" else sc)


@pytest.mark.parametrize("view", ["baseline", "compare", "impact"])
@pytest.mark.parametrize("focus", [None, "PROVIDER"])
def test_analysis_csv_matches_rollup(setup, view, focus):
    ctx = ctx_for(setup, view, focus=focus)
    df = pd.read_csv(io.BytesIO(csv_export.analysis_csv(ctx)))
    r = V.rollup(ctx.result, ctx.meta, ctx.dataset, ctx.year, focus, ctx.geo).rows
    assert list(df["pool_id"]) == list(r["node_id"])
    assert np.allclose(df["revenue_usd"], r["revenue"])
    assert ("delta_revenue_usd" in df) == (view != "baseline")
    if view != "baseline":
        b = V.rollup(ctx.base, ctx.meta, ctx.dataset, ctx.year, focus, ctx.geo).rows
        assert np.allclose(df["delta_revenue_usd"], r["revenue"].to_numpy() - b["revenue"].to_numpy())


def test_unavailable_stays_empty_not_zero(setup):
    df = pd.read_csv(io.BytesIO(csv_export.analysis_csv(ctx_for(setup, "baseline"))))
    assert df.loc[df["pool_id"] == "MANUFACTURER", "enrollment"].isna().all()


@pytest.mark.parametrize("view", ["baseline", "compare"])
def test_scenario_zip_round_trip(setup, view):
    ctx = ctx_for(setup, view)
    z = zipfile.ZipFile(io.BytesIO(csv_export.scenario_zip(ctx)))
    names = set(z.namelist())
    assert {"pools.csv", "flows.csv", "ownership.csv", "README.txt"} <= names
    assert ("scenario.json" in names) == (view != "baseline")
    pools = pd.read_csv(z.open("pools.csv"))
    rev = ctx.base.pools.groupby("dataset")["revenue"].sum()
    assert np.allclose(pools.groupby("dataset")["baseline_revenue"].sum(), rev)
    flows = pd.read_csv(z.open("flows.csv"))
    assert flows["baseline_allowed"].sum() == pytest.approx(ctx.base.flows["amount"].sum())
    assert FICTIONAL in z.read("README.txt").decode()
    if view != "baseline":
        assert np.allclose(pools["delta_revenue"].fillna(0), (pools["scenario_revenue"] - pools["baseline_revenue"]).fillna(0))
        s = ctx.result.pools.groupby("dataset")["revenue"].sum()
        assert np.allclose(pools.groupby("dataset")["scenario_revenue"].sum(), s)
        assert Scenario.from_json(z.read("scenario.json").decode()).to_json() == ctx.scenario.to_json()
        json.loads(z.read("scenario.json"))
        assert "credential" not in z.read("scenario.json").decode().lower()


@pytest.mark.parametrize("view, dataset, geo", [("compare", "MARKET", "US"), ("baseline", "ENTERPRISE", "NVL"),
                                                ("impact", "MARKET", "NVL-01")])
def test_html_snapshot_is_self_contained(setup, view, dataset, geo):
    html = html_snapshot.build(ctx_for(setup, view, dataset, geo=geo))
    assert html.startswith("<!doctype html>") and html.rstrip().endswith("</html>")
    assert not re.search(r"<script[^>]+src=", html)           # nothing loaded from the network
    assert html.count("plotly.js v") >= 1 and html.count("Plotly.newPlot") >= 3
    assert FICTIONAL in html and "not a simulator" in html
    if dataset == "ENTERPRISE":
        assert "Consolidated gain" in html


@pytest.mark.skipif(not pptx_summary.available(), reason="python-pptx not installed")
@pytest.mark.parametrize("view, dataset, focus", [("compare", "MARKET", None), ("baseline", "ENTERPRISE", None),
                                                  ("impact", "MARKET", "PROVIDER")])
def test_pptx_opens_with_native_marimekko(setup, view, dataset, focus):
    from pptx import Presentation
    from pptx.enum.shapes import MSO_SHAPE_TYPE
    ctx = ctx_for(setup, view, dataset, focus=focus)
    prs = Presentation(io.BytesIO(pptx_summary.build(ctx)))
    slides = list(prs.slides)
    tiles = F.tiles(ctx)[0]
    rects = [s for s in slides[1].shapes if s.shape_type == MSO_SHAPE_TYPE.AUTO_SHAPE]
    assert len(rects) == len(tiles)                           # one native shape per Marimekko tile
    charts = [s for sl in slides for s in sl.shapes if s.has_chart]
    assert len(charts) >= 2
    text = " ".join(sh.text_frame.text for sl in slides for sh in sl.shapes if sh.has_text_frame)
    assert FICTIONAL in text
    titles = [sl.shapes[0].text_frame.text for sl in slides[1:]]
    assert ("Value capture: owned assets and the consolidated P&L" in titles) == (dataset == "ENTERPRISE")
    assert any(t.startswith("Scenario:") for t in titles) == (view != "baseline")
