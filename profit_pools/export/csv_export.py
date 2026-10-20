"""CSV exports. Money in dollars, rates as fractions, unavailable values left empty (never 0)."""

from __future__ import annotations

import io
import zipfile

import pandas as pd

from profit_pools.core.model import views as V
from profit_pools.export import FICTIONAL, ExportContext


def analysis_frame(ctx: ExportContext) -> pd.DataFrame:
    """The rows behind the current view: children of the focus, as in the summary table."""
    r = V.rollup(ctx.result, ctx.meta, ctx.dataset, ctx.year, ctx.focus, ctx.geo, ctx.basis).rows
    out = pd.DataFrame({
        "pool_id": r["node_id"], "pool": r["label"], "layer_id": r["layer_id"],
        "revenue_usd": r["revenue"], "share_of_parent": r["share_of_parent"],
        "operating_gain_usd": r["gain"], "operating_margin": r["margin"], "margin_coverage": r["margin_coverage"],
        "enrollment": r["members"], "revenue_pmpm_usd": r["revenue_pmpm"], "coverage": r["coverage"],
    })
    if ctx.compare is not None:
        b = V.rollup(ctx.compare, ctx.meta, ctx.dataset, ctx.year, ctx.focus, ctx.geo, ctx.basis).rows
        b = b.set_index("node_id").reindex(r["node_id"])
        out["baseline_revenue_usd"] = b["revenue"].to_numpy()
        out["delta_revenue_usd"] = out["revenue_usd"] - out["baseline_revenue_usd"].fillna(0)
        out["baseline_operating_gain_usd"] = b["gain"].to_numpy()
        out["delta_operating_gain_usd"] = out["operating_gain_usd"] - out["baseline_operating_gain_usd"]
    out.insert(0, "dataset", ctx.dataset)
    out.insert(1, "year", ctx.year)
    out.insert(2, "geography", ctx.geo_label())
    out.insert(3, "spend_basis", ctx.basis)
    return out


def analysis_csv(ctx: ExportContext) -> bytes:
    return analysis_frame(ctx).to_csv(index=False).encode("utf-8")


def _pools(res, meta) -> pd.DataFrame:
    p = res.pools
    g = p.groupby(["dataset", "year", "state", "node_id"], dropna=False)
    out = g[["members", "revenue", "claims", "opex"]].sum(min_count=1)
    out["gain"] = g["gain"].sum(min_count=1)
    return out


def scenario_frames(ctx: ExportContext) -> dict[str, pd.DataFrame]:
    """Pool, flow and ownership results (baseline, scenario, Δ) at state × leaf grain, all years."""
    meta = ctx.meta
    scen = ctx.result if ctx.has_scenario else None
    b = _pools(ctx.base, meta)
    pools = b.add_prefix("baseline_")
    if scen is not None:
        s = _pools(scen, meta).reindex(b.index)
        pools = pools.join(s.add_prefix("scenario_"))
        for c in ("revenue", "gain"):
            pools[f"delta_{c}"] = pools[f"scenario_{c}"] - pools[f"baseline_{c}"]
    pools = pools.reset_index()
    pools.insert(4, "pool", pools["node_id"].map(lambda i: meta.taxonomy[i].label))
    pools.insert(5, "layer_id", pools["node_id"].map(lambda i: meta.taxonomy[i].layer_id))

    fk = ["dataset", "year", "state", "source_id", "flow_kind", "target_id"]

    def flows(res):
        return (res.flows.groupby(fk, dropna=False)[["amount", "paid", "consumer", "gain"]].sum(min_count=1)
                .rename(columns={"amount": "allowed"}))

    fb = flows(ctx.base)
    fl = fb.add_prefix("baseline_")
    if scen is not None:
        fs = flows(scen).reindex(fb.index)
        fl = fl.join(fs.add_prefix("scenario_"))
        fl["delta_allowed"] = fl["scenario_allowed"] - fl["baseline_allowed"]
    fl = fl.reset_index()
    own_cols = ["enterprise_spend", "routing_share", "routed", "third_party", "owned_revenue", "owned_margin",
                "owned_gain", "external_spend"]
    ok = ["dataset", "year", "state", "asset_id", "node_id"]
    ob = ctx.base.ownership.set_index(ok)[own_cols]
    own = ob.add_prefix("baseline_")
    if scen is not None:
        own = own.join(scen.ownership.set_index(ok)[own_cols].reindex(ob.index).add_prefix("scenario_"))
        own["delta_owned_gain"] = own["scenario_owned_gain"] - own["baseline_owned_gain"]
    return {"pools": pools, "flows": fl, "ownership": own.reset_index()}


README = """Profit Pools Explorer, scenario outputs
{fictional}

Context: {title}

Files
  pools.csv      dataset x year x state x leaf pool: members, revenue, claims, opex, operating gain.
                 Payer revenue = premiums; recipient revenue = allowed receipts (all funders);
                 top-down pools are national (state = US).
  flows.csv      dataset x year x state x source x flow kind x recipient leaf: allowed, paid,
                 consumer (allowed - paid) and recipient gain at the pool margin.
  ownership.csv  enterprise owned assets x state x year: member spend, routing share, routed,
                 third-party, owned revenue, margin and gain, spend still going to external providers.
  scenario.json  the scenario configuration (no data), when a scenario is applied.

Columns are baseline_*, scenario_* and delta_* (scenario - baseline). Money in US dollars,
rates as fractions. Empty cells are unavailable, never zero.
"""


def scenario_zip(ctx: ExportContext) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, df in scenario_frames(ctx).items():
            z.writestr(f"{name}.csv", df.to_csv(index=False))
        z.writestr("README.txt", README.format(fictional=FICTIONAL, title=ctx.title()))
        if ctx.has_scenario:
            z.writestr("scenario.json", ctx.scenario.to_json())
    return buf.getvalue()
