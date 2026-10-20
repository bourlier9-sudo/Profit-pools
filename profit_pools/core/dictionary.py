"""Data dictionary: every definition the app uses, as tables, read from configuration and contracts (so it can
never drift from the engine). Pure Python; the Data dictionary page renders these frames."""

from __future__ import annotations

import io
import zipfile

import numpy as np
import pandas as pd

from profit_pools.core.config import load_config
from profit_pools.core.data import contracts
from profit_pools.core.model import ownership as own_mod
from profit_pools.core.model.engine import ModelResult
from profit_pools.core.taxonomy import Metadata

ECONOMICS = {"payer_pnl": "Plan P&L (premium − claims − admin)",
             "margin_on_revenue": "Inflows × pool margin (providers & pharmacy)",
             "top_down": "Top-down estimate (revenue and margin only)"}
COVERAGE = {"bottom_up": "Bottom-up", "allocated": "Allocated from flows", "top_down": "Top-down"}
SCOPE = {"payer": "Plan pools", "spending": "Provider & pharmacy pools (by payer)",
         "recipient": "Provider & pharmacy pools", "funding": "Funding sources", "top_down": "Top-down pools",
         "ownership": "Pools with an owned asset (enterprise)"}
METHODS = {"pct": "% change", "pp": "pp change", "set": "set to", "delta": "add"}


def glossary() -> pd.DataFrame:
    return pd.DataFrame(load_config("glossary")["terms"]).rename(columns={"term": "Term", "definition": "Definition"})


def pools(meta: Metadata) -> pd.DataFrame:
    owned = {a.node_id: a.label for a in own_mod.assets(meta)}
    rows = []
    for n in meta.taxonomy:
        path = " › ".join(x.short_label for x in meta.taxonomy.path(n.id))
        ds = [meta.datasets[d].short_label for d in meta.datasets if meta.in_dataset(n.id, d)]
        rows.append({"Pool": (" " * n.depth) + n.label, "Id": n.id, "Path": path,
                     "Level": "Layer" if n.depth == 0 else f"L{n.depth}", "Holds data": "Yes" if n.is_leaf else "",
                     "Economics": ECONOMICS[n.economics], "Coverage": COVERAGE.get(n.coverage, n.coverage),
                     "Geography": n.geo_level, "In value flows": "Yes" if n.receives_flows else "",
                     "Datasets": ", ".join(ds), "Owned asset": owned.get(n.id, ""),
                     "Description": (n.description or "").strip()})
    return pd.DataFrame(rows)


def metrics() -> pd.DataFrame:
    cfg = load_config("metrics")
    units = cfg.get("units", {})
    rows = []
    for k, m in cfg["metrics"].items():
        prov = m.get("provenance", {})
        rows.append({"Metric": m["label"], "Id": k, "Unit": units.get(m["unit"], {}).get("label", m["unit"]),
                     "Additive": "Yes" if m.get("additive") else "No — recomputed after aggregation",
                     "Formula": m.get("formula", ""),
                     "Plans": prov.get("payer_pnl", ""), "Providers & pharmacy": prov.get("margin_on_revenue", ""),
                     "Top-down": prov.get("top_down", "")})
    return pd.DataFrame(rows)


def levers() -> pd.DataFrame:
    cat = load_config("drivers")
    rows = []
    for k, d in cat["drivers"].items():
        rows.append({"Lever": d["label"], "Driver id": k, "Type": "Level", "Applies to": SCOPE.get(d["scope_kind"], ""),
                     "Changes": ", ".join(METHODS[x] for x in d["methods"]), "What it does": d.get("explains", ""),
                     "Pipeline stage": d.get("stage", "")})
    for k, d in cat["growth_drivers"].items():
        rows.append({"Lever": d["label"], "Driver id": k, "Type": "Growth (annual rate)",
                     "Applies to": SCOPE.get(d.get("scope_kind", ""), ""), "Changes": "pp change, set to",
                     "What it does": d.get("explains", "Changes the annual rate in forecast years; compounds."),
                     "Pipeline stage": 1})
    return pd.DataFrame(rows)


def funders(meta: Metadata) -> pd.DataFrame:
    rows = [{"Name": label, "Id": g, "Kind": "Plan group", "Basis": "members × PMPM",
             "Description": ", ".join(meta.lobs.lobs[l].label for l in meta.lobs.in_group(g))}
            for g, label in meta.lobs.groups.items()]
    rows += [{"Name": fs.label, "Id": f, "Kind": "Funding source",
              "Basis": "population × PMPM" if fs.basis == "population_pmpm" else "annual lump sum",
              "Description": fs.description} for f, fs in meta.funding_sources.items()]
    return pd.DataFrame(rows)


def tables() -> pd.DataFrame:
    rows = []
    for name, cols in (("FACT_POOL_METRICS", contracts.POOL_METRICS_COLUMNS),
                       ("FACT_VALUE_FLOWS", contracts.VALUE_FLOWS_COLUMNS),
                       ("FACT_COVERAGE_POPULATION", contracts.COVERAGE_POPULATION_COLUMNS)):
        for c in cols:
            rows.append({"Table": name, "Column": c.name, "Type": c.kind, "Nullable": "Yes" if c.nullable else "",
                         "Unit": c.unit, "Description": c.description})
    return pd.DataFrame(rows)


def coverage(result: ModelResult, meta: Metadata, dataset: str, year: int) -> pd.DataFrame:
    """Leaf × state matrix of what is available: 'margin + services', 'margin', 'no margin' or blank (no data).
    Null means unavailable — this is where the app shows a hatch or 'n/a'."""
    p = result.pools
    p = p[(p["dataset"] == dataset) & (p["year"] == year)]
    f = result.flows
    f = f[(f["dataset"] == dataset) & (f["year"] == year)]
    inst = f.groupby(["target_id", "state"])["instances"].apply(lambda s: bool((s.fillna(0) > 0).any()))
    g = p.groupby(["node_id", "state"])["margin_known"].agg(lambda s: bool(s.all()))
    states = [s for s in [*meta.geography.states, meta.geography.national_id] if s in set(p["state"])]
    out = {}
    for n in meta.leaves_in_dataset(dataset):
        row = {}
        for s in states:
            if (n.id, s) not in g.index:
                row[s] = ""
                continue
            m = g[(n.id, s)]
            has_i = bool(inst.get((n.id, s), False))
            row[s] = ("margin + services" if has_i else "margin") if m else "no margin"
        out[n.label] = row
    df = pd.DataFrame(out).T
    df.columns = [meta.geography.states[s].label if s in meta.geography.states else "National" for s in df.columns]
    return df.replace({np.nan: ""})


def zip_all(meta: Metadata, result: ModelResult, year: int) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, df in {"glossary": glossary(), "pools": pools(meta), "metrics": metrics(), "levers": levers(),
                         "funders": funders(meta), "data_tables": tables()}.items():
            z.writestr(f"{name}.csv", df.to_csv(index=False))
        for d in meta.datasets:
            z.writestr(f"coverage_{d.lower()}_{year}.csv", coverage(result, meta, d, year).to_csv())
        z.writestr("README.txt", "Profit Pools Explorer data dictionary. All data in the app is fictional.\n")
    return buf.getvalue()
