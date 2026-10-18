"""Fictional logical table contracts and their validation.

These schemas are invented for this prototype. They are not representations
of any real corporate table. DATA_CONTRACTS.md documents the same columns;
this module is the executable source of truth.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import numpy as np
import pandas as pd

from profit_pools.core.taxonomy import Metadata

NATIONAL = "US"
LEVEL_COLUMNS = ("L1_ID", "L2_ID", "L3_ID", "L4_ID")

REL_TOL = 1e-9  # identities are exact by construction; this only absorbs float error


@dataclass(frozen=True)
class Column:
    name: str
    kind: Literal["int", "float", "str"]
    nullable: bool
    unit: str
    description: str


_GEO = (
    Column("DATASET", "str", False, "-", "MARKET or ENTERPRISE (datasets.yaml)."),
    Column("YEAR", "int", False, "calendar year", "Fiscal = calendar year."),
    Column("GEO_LEVEL", "str", False, "-", "national / state / county — grain of this row."),
    Column("STATE", "str", False, "-", "State id, or 'US' for national rows."),
    Column("COUNTY", "str", True, "-", "County id; null for state and national rows."),
)

POOL_METRICS_COLUMNS: tuple[Column, ...] = (
    *_GEO,
    Column("LAYER_ID", "str", False, "-", "Top-level taxonomy layer."),
    Column("L1_ID", "str", False, "-", "Level-1 node below the layer."),
    Column("L2_ID", "str", True, "-", "Level-2 node; null if the leaf is shallower."),
    Column("L3_ID", "str", True, "-", "Level-3 node; null if the leaf is shallower."),
    Column("L4_ID", "str", True, "-", "Level-4 node; null if the leaf is shallower."),
    Column("NODE_ID", "str", False, "-", "Leaf node = deepest non-null level. Must match the taxonomy path."),
    Column("LOB", "str", True, "-", "Payer LOB for payer rows; null otherwise."),
    Column("ENROLLMENT", "float", True, "avg members", "Average members in year (member-months / 12). Payer only."),
    Column("REVENUE", "float", False, "USD/yr", "Annual revenue."),
    Column("CLAIMS_EXPENSE", "float", True, "USD/yr", "Payer claims expense = allowed spend (see caveats). Payer only."),
    Column("OPERATING_GAIN", "float", True, "USD/yr", "Annual operating gain; null when margin unavailable."),
    Column("OPERATING_MARGIN", "float", True, "ratio", "Operating gain / revenue; null when unavailable."),
    Column("REVENUE_PMPM", "float", True, "USD PMPM", "REVENUE / (ENROLLMENT × 12). Payer only."),
    Column("SPEND_PMPM", "float", True, "USD PMPM", "CLAIMS_EXPENSE / (ENROLLMENT × 12). Payer only."),
    Column("SOURCE_TYPE", "str", False, "-", "observed / allocated / estimated."),
    Column("COVERAGE_LEVEL", "str", False, "-", "bottom_up / allocated / top_down."),
)

VALUE_FLOWS_COLUMNS: tuple[Column, ...] = (
    *_GEO,
    Column("SOURCE_NODE_ID", "str", False, "-", "Payer leaf (payer_claims) or funding source id (funding_pmpm, supplemental)."),
    Column("TARGET_NODE_ID", "str", False, "-", "Recipient leaf (receives_flows = true)."),
    Column("PAYER_LOB", "str", True, "-", "Payer LOB for payer_claims flows; null for funding-source flows."),
    Column("SPENDING_CATEGORY", "str", True, "-", "Claims category; null for supplemental flows."),
    Column("FLOW_KIND", "str", False, "-", "payer_claims / funding_pmpm / supplemental."),
    Column("ANNUAL_FLOW_AMOUNT", "float", False, "USD/yr", "Annual amount (allowed $ for claims-based flows)."),
    Column("INSTANCE_COUNT", "float", True, "instances/yr", "Service instances; null unless the category carries instances."),
    Column("ALLOCATION_METHOD", "str", False, "-", "observed_claims / pmpm_benchmark / population_pmpm_estimate / supplemental_estimate."),
    Column("SOURCE_TYPE", "str", False, "-", "observed / estimated."),
)

COVERAGE_POPULATION_COLUMNS: tuple[Column, ...] = (
    *_GEO,
    Column("FUNDING_SOURCE_ID", "str", False, "-", "Funding source with basis population_pmpm."),
    Column("POPULATION", "float", False, "avg persons", "Average covered (or uninsured) persons in year."),
    Column("SOURCE_TYPE", "str", False, "-", "estimated."),
)

SOURCE_TYPES = {"observed", "allocated", "estimated"}
FLOW_KINDS = {"payer_claims", "funding_pmpm", "supplemental"}
ALLOCATION_METHODS = {"observed_claims", "pmpm_benchmark", "population_pmpm_estimate", "supplemental_estimate"}


@dataclass(frozen=True)
class Issue:
    level: Literal["error", "warning"]
    code: str
    message: str


def _close(a, b) -> np.ndarray:
    return np.isclose(np.asarray(a, dtype=float), np.asarray(b, dtype=float), rtol=REL_TOL, atol=1e-6)


def _check_schema(df: pd.DataFrame, cols: tuple[Column, ...], table: str) -> list[Issue]:
    issues: list[Issue] = []
    names = [c.name for c in cols]
    missing = [n for n in names if n not in df.columns]
    if missing:
        return [Issue("error", "schema.missing", f"{table}: missing columns {missing}")]
    extra = [c for c in df.columns if c not in names]
    if extra:
        issues.append(Issue("warning", "schema.extra", f"{table}: unexpected columns {extra}"))
    for c in cols:
        s = df[c.name]
        if not c.nullable and s.isna().any():
            issues.append(Issue("error", "schema.null", f"{table}.{c.name}: nulls in non-nullable column"))
        nn = s.dropna()
        if c.kind in ("int", "float") and len(nn) and not pd.api.types.is_numeric_dtype(nn):
            issues.append(Issue("error", "schema.type", f"{table}.{c.name}: expected numeric"))
    return issues


def _check_geo(df: pd.DataFrame, table: str) -> list[Issue]:
    issues = []
    nat = df["GEO_LEVEL"] == "national"
    if ((df.loc[nat, "STATE"] != NATIONAL) | df.loc[nat, "COUNTY"].notna()).any():
        issues.append(Issue("error", "geo.national", f"{table}: national rows need STATE='US', null COUNTY"))
    if df.loc[df["GEO_LEVEL"] == "state", "COUNTY"].notna().any():
        issues.append(Issue("error", "geo.state", f"{table}: state rows must have null COUNTY"))
    if df.loc[df["GEO_LEVEL"] == "county", "COUNTY"].isna().any():
        issues.append(Issue("error", "geo.county", f"{table}: county rows must have COUNTY"))
    return issues


def validate_pool_metrics(df: pd.DataFrame, meta: Metadata) -> list[Issue]:
    issues = _check_schema(df, POOL_METRICS_COLUMNS, "FACT_POOL_METRICS")
    if any(i.code == "schema.missing" for i in issues):
        return issues
    tax = meta.taxonomy

    bad_ds = sorted(set(df["DATASET"]) - set(meta.datasets))
    if bad_ds:
        issues.append(Issue("error", "dataset.unknown", f"Unknown DATASET values {bad_ds}"))
        return issues
    key = ["DATASET", "YEAR", "STATE", "COUNTY", "NODE_ID", "LOB"]
    if df.duplicated(key).any():
        issues.append(Issue("error", "key.duplicate", f"FACT_POOL_METRICS: duplicate rows for grain {key}"))

    unknown = sorted(set(df["NODE_ID"]) - set(tax.leaf_ids()))
    if unknown:
        issues.append(Issue("error", "taxonomy.unknown_leaf", f"Rows for non-leaf or unknown nodes: {unknown}"))
        return issues

    for (ds, leaf_id), g in df.groupby(["DATASET", "NODE_ID"]):
        leaf = tax[leaf_id]
        if not meta.in_dataset(leaf_id, ds):
            issues.append(Issue("error", "dataset.excluded", f"{leaf_id}: excluded from {ds} but has rows"))
        expected = [leaf.layer_id, *tax.level_ids(leaf_id, len(LEVEL_COLUMNS))]
        actual = g[["LAYER_ID", *LEVEL_COLUMNS]].drop_duplicates()
        if len(actual) != 1 or [None if pd.isna(v) else v for v in actual.iloc[0]] != expected:
            issues.append(Issue("error", "taxonomy.path", f"{leaf_id}: LAYER/L1–L4 columns differ from taxonomy path"))
        if (g["GEO_LEVEL"] != leaf.geo_level).any():
            issues.append(Issue("error", "geo.grain", f"{leaf_id}: rows must be at {leaf.geo_level} grain"))
        if (g["COVERAGE_LEVEL"] != leaf.coverage).any():
            issues.append(Issue("error", "coverage.mismatch", f"{leaf_id}: COVERAGE_LEVEL differs from taxonomy"))
    issues += _check_geo(df, "FACT_POOL_METRICS")

    econ = df["NODE_ID"].map(lambda i: tax[i].economics)
    payer = df[econ == "payer_pnl"]
    if len(payer):
        mm12 = payer["ENROLLMENT"] * 12
        if not _close(payer["REVENUE"], payer["REVENUE_PMPM"] * mm12).all():
            issues.append(Issue("error", "payer.revenue_pmpm", "REVENUE ≠ REVENUE_PMPM × ENROLLMENT × 12"))
        if not _close(payer["CLAIMS_EXPENSE"], payer["SPEND_PMPM"] * mm12).all():
            issues.append(Issue("error", "payer.spend_pmpm", "CLAIMS_EXPENSE ≠ SPEND_PMPM × ENROLLMENT × 12"))
        if (payer["ENROLLMENT"] <= 0).any():
            issues.append(Issue("error", "payer.enrollment", "Payer rows must have positive enrollment"))
        if (payer["LOB"] != payer["NODE_ID"].map(lambda i: tax[i].lob)).any():
            issues.append(Issue("error", "payer.lob", "Payer LOB must match the taxonomy leaf"))
        expected_src = payer["DATASET"].map(lambda d: meta.datasets[d].payer_source_type)
        if (payer["SOURCE_TYPE"] != expected_src).any():
            issues.append(Issue("error", "payer.source_type", "Payer SOURCE_TYPE differs from dataset definition"))
        # The enterprise book is a subset of the market.
        if {"MARKET", "ENTERPRISE"} <= set(payer["DATASET"]):
            k = ["YEAR", "COUNTY", "LOB"]
            mk = payer[payer["DATASET"] == "MARKET"].set_index(k)["ENROLLMENT"]
            ent = payer[payer["DATASET"] == "ENTERPRISE"].set_index(k)["ENROLLMENT"]
            j = ent.to_frame("ENT").join(mk.rename("MKT"), how="left")
            if j["MKT"].isna().any() or (j["ENT"] > j["MKT"] + 1e-6).any():
                issues.append(Issue("error", "dataset.subset", "Enterprise enrollment exceeds market enrollment"))
    nonpayer = df[econ != "payer_pnl"]
    if nonpayer[["ENROLLMENT", "CLAIMS_EXPENSE", "REVENUE_PMPM", "SPEND_PMPM", "LOB"]].notna().any().any():
        issues.append(Issue("error", "recipient.payer_fields", "Payer-only fields populated on non-payer rows"))

    has_m = df["OPERATING_MARGIN"].notna()
    if (has_m != df["OPERATING_GAIN"].notna()).any():
        issues.append(Issue("error", "margin.gain_pair", "OPERATING_GAIN and OPERATING_MARGIN must be null together"))
    m = df[has_m]
    if not _close(m["OPERATING_GAIN"], m["REVENUE"] * m["OPERATING_MARGIN"]).all():
        issues.append(Issue("error", "margin.identity", "OPERATING_GAIN ≠ REVENUE × OPERATING_MARGIN"))
    if (df["REVENUE"] < 0).any():
        issues.append(Issue("error", "revenue.negative", "Negative revenue"))
    bad_src = set(df["SOURCE_TYPE"]) - SOURCE_TYPES
    if bad_src:
        issues.append(Issue("error", "source_type", f"Unknown SOURCE_TYPE {sorted(bad_src)}"))

    # A recipient pool's margin is a property of the pool, shared by both datasets.
    rec = nonpayer[nonpayer["NODE_ID"].map(lambda i: tax[i].receives_flows)]
    if {"MARKET", "ENTERPRISE"} <= set(rec["DATASET"]):
        k = ["YEAR", "STATE", "NODE_ID"]
        mk = rec[rec["DATASET"] == "MARKET"].set_index(k)["OPERATING_MARGIN"]
        ent = rec[rec["DATASET"] == "ENTERPRISE"].set_index(k)["OPERATING_MARGIN"]
        j = ent.to_frame("ENT").join(mk.rename("MKT"), how="left")
        same = (j["ENT"].isna() & j["MKT"].isna()) | _close(j["ENT"].fillna(np.inf), j["MKT"].fillna(-np.inf))
        if not same.all():
            issues.append(Issue("error", "dataset.margin", "Enterprise recipient margins differ from market margins"))
    return issues


def validate_coverage_population(pop: pd.DataFrame, meta: Metadata) -> list[Issue]:
    issues = _check_schema(pop, COVERAGE_POPULATION_COLUMNS, "FACT_COVERAGE_POPULATION")
    if any(i.code == "schema.missing" for i in issues):
        return issues
    issues += _check_geo(pop, "FACT_COVERAGE_POPULATION")
    for (ds, src), g in pop.groupby(["DATASET", "FUNDING_SOURCE_ID"]):
        fs = meta.funding_sources.get(src)
        if fs is None or fs.basis != "population_pmpm":
            issues.append(Issue("error", "population.source", f"{src}: not a population-based funding source"))
            continue
        if src not in meta.datasets[ds].funding_sources:
            issues.append(Issue("error", "dataset.funding", f"{src}: not part of dataset {ds}"))
        if (g["GEO_LEVEL"] != fs.geo_level).any():
            issues.append(Issue("error", "geo.grain", f"{src}: population rows must be at {fs.geo_level} grain"))
    if (pop["POPULATION"] <= 0).any():
        issues.append(Issue("error", "population.nonpositive", "Population must be positive"))
    if pop.duplicated(["DATASET", "YEAR", "STATE", "COUNTY", "FUNDING_SOURCE_ID"]).any():
        issues.append(Issue("error", "key.duplicate", "FACT_COVERAGE_POPULATION: duplicate rows"))
    return issues


def validate_value_flows(flows: pd.DataFrame, metrics: pd.DataFrame, pop: pd.DataFrame,
                         meta: Metadata) -> list[Issue]:
    issues = _check_schema(flows, VALUE_FLOWS_COLUMNS, "FACT_VALUE_FLOWS")
    if any(i.code == "schema.missing" for i in issues):
        return issues
    tax = meta.taxonomy
    issues += _check_geo(flows, "FACT_VALUE_FLOWS")

    for col, allowed in (("FLOW_KIND", FLOW_KINDS), ("ALLOCATION_METHOD", ALLOCATION_METHODS),
                         ("SOURCE_TYPE", SOURCE_TYPES), ("DATASET", set(meta.datasets))):
        bad = set(flows[col]) - allowed
        if bad:
            issues.append(Issue("error", f"flow.{col.lower()}", f"Unknown {col} {sorted(bad)}"))
    if (flows["ANNUAL_FLOW_AMOUNT"] < 0).any():
        issues.append(Issue("error", "flow.negative", "Negative flow amounts"))

    targets = set(flows["TARGET_NODE_ID"])
    bad_t = sorted(t for t in targets if t not in tax or not tax[t].receives_flows or not tax[t].is_leaf)
    if bad_t:
        issues.append(Issue("error", "flow.target", f"Flows to nodes that cannot receive flows: {bad_t}"))
        return issues
    for ds, g in flows.groupby("DATASET"):
        excl = sorted({t for t in g["TARGET_NODE_ID"] if not meta.in_dataset(t, ds)})
        if excl:
            issues.append(Issue("error", "dataset.excluded", f"{ds}: flows to excluded nodes {excl}"))

    kind = flows["FLOW_KIND"]
    pc = flows[kind == "payer_claims"]
    fund = flows[kind != "payer_claims"]

    # payer_claims: payer leaf source, LOB matches, category maps to target.
    src_ok = pc["SOURCE_NODE_ID"].map(lambda s: s in tax and tax[s].economics == "payer_pnl" and tax[s].is_leaf)
    if not src_ok.all():
        issues.append(Issue("error", "flow.payer_source", "payer_claims flows must originate at a payer leaf"))
    elif (pc["PAYER_LOB"] != pc["SOURCE_NODE_ID"].map(lambda s: tax[s].lob)).any():
        issues.append(Issue("error", "flow.payer_lob", "PAYER_LOB differs from the source payer leaf"))

    # funding flows: funding-source origin, null LOB, allowed in the dataset.
    if len(fund):
        known = fund["SOURCE_NODE_ID"].isin(list(meta.funding_sources))
        if not known.all():
            issues.append(Issue("error", "flow.funding_source", "Funding flows must originate at a funding source"))
        else:
            in_ds = [s in meta.datasets[d].funding_sources for s, d in zip(fund["SOURCE_NODE_ID"], fund["DATASET"])]
            if not all(in_ds):
                issues.append(Issue("error", "dataset.funding", "Funding flows in a dataset that excludes the source"))
            basis = fund["SOURCE_NODE_ID"].map(lambda s: meta.funding_sources[s].basis)
            expected_kind = basis.map({"population_pmpm": "funding_pmpm", "lump_sum": "supplemental"})
            if (expected_kind != fund["FLOW_KIND"]).any():
                issues.append(Issue("error", "flow.kind_basis", "FLOW_KIND inconsistent with funding source basis"))
        if fund["PAYER_LOB"].notna().any():
            issues.append(Issue("error", "flow.funding_lob", "Funding-source flows must have null PAYER_LOB"))
        sup = fund[fund["FLOW_KIND"] == "supplemental"]
        ok = [t in meta.supplemental_targets(s) for s, t in zip(sup["SOURCE_NODE_ID"], sup["TARGET_NODE_ID"])]
        if not all(ok):
            issues.append(Issue("error", "flow.supplemental_target", "Supplemental payments to non-eligible leaves"))
        if sup["SPENDING_CATEGORY"].notna().any() or sup["INSTANCE_COUNT"].notna().any():
            issues.append(Issue("error", "flow.supplemental_fields", "Supplemental flows carry no category or instances"))

    mapped = flows[flows["SPENDING_CATEGORY"].notna()]
    if (mapped["SPENDING_CATEGORY"].map(lambda c: meta.categories[c].target) != mapped["TARGET_NODE_ID"]).any():
        issues.append(Issue("error", "flow.category_target", "Flow target differs from its category mapping"))
    if (kind != "supplemental").any() and flows.loc[kind != "supplemental", "SPENDING_CATEGORY"].isna().any():
        issues.append(Issue("error", "flow.category_missing", "Claims-based flows need a SPENDING_CATEGORY"))

    inst = flows[flows["INSTANCE_COUNT"].notna()]
    if len(inst):
        if not inst["SPENDING_CATEGORY"].map(lambda c: meta.categories[c].has_instances).all():
            issues.append(Issue("error", "instances.category", "Instances present for a category without instance data"))
        if (inst["INSTANCE_COUNT"] <= 0).any():
            issues.append(Issue("error", "instances.nonpositive", "Instance counts must be positive"))

    # 1) Payer claims expense = Σ payer_claims flows from that payer cell.
    tax_econ = metrics["NODE_ID"].map(lambda i: tax[i].economics)
    payer = metrics[tax_econ == "payer_pnl"]
    gkey = ["DATASET", "YEAR", "STATE", "COUNTY", "SOURCE_NODE_ID"]
    claims = pc.groupby(gkey, dropna=False)["ANNUAL_FLOW_AMOUNT"].sum()
    p = payer.set_index(["DATASET", "YEAR", "STATE", "COUNTY", "NODE_ID"])
    p.index.names = gkey
    joined = p[["CLAIMS_EXPENSE"]].join(claims.rename("FLOWS"), how="outer")
    if joined.isna().any().any() or not _close(joined["CLAIMS_EXPENSE"], joined["FLOWS"]).all():
        issues.append(Issue("error", "reconcile.claims", "Payer CLAIMS_EXPENSE does not equal Σ payer_claims flows"))

    # 2) Population-based funding flows need a population row.
    fp = flows[kind == "funding_pmpm"]
    if len(fp):
        pk = ["DATASET", "YEAR", "STATE", "COUNTY", "SOURCE_NODE_ID"]
        have = set(map(tuple, pop.rename(columns={"FUNDING_SOURCE_ID": "SOURCE_NODE_ID"})[pk].itertuples(index=False)))
        need = set(map(tuple, fp[pk].drop_duplicates().itertuples(index=False)))
        if need - have:
            issues.append(Issue("error", "reconcile.population", "funding_pmpm flows without a population row"))

    # 3) Recipient revenue (at its own grain) = Σ inflows of every kind.
    rec = metrics[metrics["NODE_ID"].map(lambda i: tax[i].receives_flows)]
    rkey = ["DATASET", "YEAR", "STATE", "TARGET_NODE_ID"]
    inflow = flows.groupby(rkey)["ANNUAL_FLOW_AMOUNT"].sum()
    r = rec.set_index(["DATASET", "YEAR", "STATE", "NODE_ID"])["REVENUE"]
    r.index.names = rkey
    j3 = r.to_frame().join(inflow.rename("INFLOW"), how="outer")
    if j3.isna().any().any() or not _close(j3["REVENUE"], j3["INFLOW"]).all():
        issues.append(Issue("error", "reconcile.recipient", "Recipient REVENUE does not equal Σ inflows"))
    return issues


def validate_bundle(metrics: pd.DataFrame, flows: pd.DataFrame, pop: pd.DataFrame,
                    meta: Metadata) -> list[Issue]:
    return (validate_pool_metrics(metrics, meta)
            + validate_coverage_population(pop, meta)
            + validate_value_flows(flows, metrics, pop, meta))
