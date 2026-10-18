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
EXTERNAL_SOURCE = "EXT_UNATTRIBUTED"  # source node for unattributed residual flows

REL_TOL = 1e-9  # identities are exact by construction; this only absorbs float error


@dataclass(frozen=True)
class Column:
    name: str
    kind: Literal["int", "float", "str"]
    nullable: bool
    unit: str
    description: str


POOL_METRICS_COLUMNS: tuple[Column, ...] = (
    Column("YEAR", "int", False, "calendar year", "Fiscal = calendar year."),
    Column("GEO_LEVEL", "str", False, "-", "national / state / county — grain of this row."),
    Column("STATE", "str", False, "-", "State id, or 'US' for national rows."),
    Column("COUNTY", "str", True, "-", "County id; null for state and national rows."),
    Column("LAYER_ID", "str", False, "-", "Top-level taxonomy layer."),
    Column("POOL_ID", "str", False, "-", "Level-1 ancestor of the leaf (informational)."),
    Column("SUBPOOL_ID", "str", False, "-", "Leaf taxonomy node. The hierarchy is resolved from the taxonomy, not from these columns."),
    Column("LOB", "str", True, "-", "Payer LOB for payer rows; null for recipient and top-down rows."),
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
    Column("YEAR", "int", False, "calendar year", "Fiscal = calendar year."),
    Column("GEO_LEVEL", "str", False, "-", "county for payer-originated flows; state for residual flows."),
    Column("STATE", "str", False, "-", "State id."),
    Column("COUNTY", "str", True, "-", "Member county; null for state-level residual flows."),
    Column("SOURCE_NODE_ID", "str", False, "-", f"Payer leaf, or '{EXTERNAL_SOURCE}' for residual flows."),
    Column("TARGET_NODE_ID", "str", False, "-", "Recipient leaf (receives_flows = true)."),
    Column("PAYER_LOB", "str", False, "-", "Payer LOB, or 'UNMODELED' for residual flows."),
    Column("SPENDING_CATEGORY", "str", True, "-", "Category id; null for residual flows."),
    Column("FLOW_KIND", "str", False, "-", "claims / opex / residual."),
    Column("ANNUAL_FLOW_AMOUNT", "float", False, "USD/yr", "Annual amount (allowed $ for claims flows)."),
    Column("SERVICE_COUNT", "float", True, "services/yr", "Category-specific unit; null unless reliable."),
    Column("SERVICE_UNIT", "str", True, "-", "Unit id from spending_categories.yaml; null when SERVICE_COUNT is null."),
    Column("ALLOCATION_METHOD", "str", False, "-", "direct_claims_mapping / estimated_opex_share / unattributed_residual."),
    Column("SOURCE_TYPE", "str", False, "-", "observed / estimated."),
)

SOURCE_TYPES = {"observed", "allocated", "estimated"}
FLOW_KINDS = {"claims", "opex", "residual"}


@dataclass(frozen=True)
class Issue:
    level: Literal["error", "warning"]
    code: str
    message: str


def _close(a: pd.Series | np.ndarray, b: pd.Series | np.ndarray) -> np.ndarray:
    a = np.asarray(a, dtype=float)
    b = np.asarray(b, dtype=float)
    return np.isclose(a, b, rtol=REL_TOL, atol=1e-6)


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


def validate_pool_metrics(df: pd.DataFrame, meta: Metadata) -> list[Issue]:
    issues = _check_schema(df, POOL_METRICS_COLUMNS, "FACT_POOL_METRICS")
    if any(i.code == "schema.missing" for i in issues):
        return issues
    tax = meta.taxonomy

    key = ["YEAR", "STATE", "COUNTY", "SUBPOOL_ID", "LOB"]
    if df.duplicated(key).any():
        issues.append(Issue("error", "key.duplicate", "FACT_POOL_METRICS: duplicate rows for grain " + str(key)))

    unknown = sorted(set(df["SUBPOOL_ID"]) - set(tax.leaf_ids()))
    if unknown:
        issues.append(Issue("error", "taxonomy.unknown_leaf", f"Rows for non-leaf or unknown nodes: {unknown}"))
        return issues

    for leaf_id, g in df.groupby("SUBPOOL_ID"):
        leaf = tax[leaf_id]
        if (g["LAYER_ID"] != leaf.layer_id).any():
            issues.append(Issue("error", "taxonomy.layer", f"{leaf_id}: LAYER_ID mismatch"))
        if (g["GEO_LEVEL"] != leaf.geo_level).any():
            issues.append(Issue("error", "geo.grain", f"{leaf_id}: rows must be at {leaf.geo_level} grain"))
        if (g["COVERAGE_LEVEL"] != leaf.coverage).any():
            issues.append(Issue("error", "coverage.mismatch", f"{leaf_id}: COVERAGE_LEVEL differs from taxonomy"))

    nat = df["GEO_LEVEL"] == "national"
    if ((df.loc[nat, "STATE"] != NATIONAL) | df.loc[nat, "COUNTY"].notna()).any():
        issues.append(Issue("error", "geo.national", "National rows must have STATE='US' and null COUNTY"))
    st = df["GEO_LEVEL"] == "state"
    if df.loc[st, "COUNTY"].notna().any():
        issues.append(Issue("error", "geo.state", "State rows must have null COUNTY"))
    co = df["GEO_LEVEL"] == "county"
    if df.loc[co, "COUNTY"].isna().any():
        issues.append(Issue("error", "geo.county", "County rows must have COUNTY"))

    econ = df["SUBPOOL_ID"].map(lambda i: tax[i].economics)
    payer = df[econ == "payer_pnl"]
    if len(payer):
        mm12 = payer["ENROLLMENT"] * 12
        if not _close(payer["REVENUE"], payer["REVENUE_PMPM"] * mm12).all():
            issues.append(Issue("error", "payer.revenue_pmpm", "REVENUE ≠ REVENUE_PMPM × ENROLLMENT × 12"))
        if not _close(payer["CLAIMS_EXPENSE"], payer["SPEND_PMPM"] * mm12).all():
            issues.append(Issue("error", "payer.spend_pmpm", "CLAIMS_EXPENSE ≠ SPEND_PMPM × ENROLLMENT × 12"))
        if (payer["ENROLLMENT"] <= 0).any():
            issues.append(Issue("error", "payer.enrollment", "Payer rows must have positive enrollment"))
        if (payer["LOB"] != payer["SUBPOOL_ID"].map(lambda i: tax[i].lob)).any():
            issues.append(Issue("error", "payer.lob", "Payer LOB must match the taxonomy leaf"))
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
    return issues


def validate_value_flows(flows: pd.DataFrame, metrics: pd.DataFrame, meta: Metadata) -> list[Issue]:
    issues = _check_schema(flows, VALUE_FLOWS_COLUMNS, "FACT_VALUE_FLOWS")
    if any(i.code == "schema.missing" for i in issues):
        return issues
    tax = meta.taxonomy

    bad_kind = set(flows["FLOW_KIND"]) - FLOW_KINDS
    if bad_kind:
        issues.append(Issue("error", "flow.kind", f"Unknown FLOW_KIND {sorted(bad_kind)}"))
    if (flows["ANNUAL_FLOW_AMOUNT"] < 0).any():
        issues.append(Issue("error", "flow.negative", "Negative flow amounts"))

    targets = set(flows["TARGET_NODE_ID"])
    bad_t = sorted(t for t in targets if t not in tax or not tax[t].receives_flows or not tax[t].is_leaf)
    if bad_t:
        issues.append(Issue("error", "flow.target", f"Flows to nodes that cannot receive flows: {bad_t}"))
        return issues
    td = sorted({t for t in targets if tax[t].is_top_down})
    if td:
        issues.append(Issue("error", "flow.top_down", f"Flows attributed to top-down nodes: {td}"))

    # Category → target mapping and service-count reliability
    cat = flows["SPENDING_CATEGORY"]
    mapped = flows[cat.notna()]
    exp_target = mapped["SPENDING_CATEGORY"].map(lambda c: meta.categories[c].target)
    if (exp_target != mapped["TARGET_NODE_ID"]).any():
        issues.append(Issue("error", "flow.category_target", "Flow target differs from its category mapping"))
    svc = flows["SERVICE_COUNT"].notna()
    if (svc != flows["SERVICE_UNIT"].notna()).any():
        issues.append(Issue("error", "services.unit_pair", "SERVICE_COUNT and SERVICE_UNIT must be null together"))
    s = flows[svc]
    if len(s):
        ok = [
            meta.categories[c].has_reliable_services(meta.lobs.group_of(l))
            and meta.categories[c].service_unit == u
            for c, l, u in zip(s["SPENDING_CATEGORY"], s["PAYER_LOB"], s["SERVICE_UNIT"])
        ]
        if not all(ok):
            issues.append(Issue("error", "services.unreliable", "Service counts present where not reliable or wrong unit"))
        if (s["SERVICE_COUNT"] <= 0).any():
            issues.append(Issue("error", "services.nonpositive", "Service counts must be positive"))

    payer = metrics[metrics["SUBPOOL_ID"].map(lambda i: tax[i].economics) == "payer_pnl"]
    gkey = ["YEAR", "STATE", "COUNTY", "SOURCE_NODE_ID"]

    # 1) Payer claims expense = Σ claims flows from that payer cell.
    claims = (flows[flows["FLOW_KIND"] == "claims"]
              .groupby(gkey, dropna=False)["ANNUAL_FLOW_AMOUNT"].sum())
    p = payer.set_index(["YEAR", "STATE", "COUNTY", "SUBPOOL_ID"])
    p.index.names = gkey
    joined = p[["CLAIMS_EXPENSE"]].join(claims.rename("FLOWS"), how="outer")
    if joined.isna().any().any() or not _close(joined["CLAIMS_EXPENSE"], joined["FLOWS"]).all():
        issues.append(Issue("error", "reconcile.claims", "Payer CLAIMS_EXPENSE does not equal Σ claims flows"))

    # 2) Opex flows cannot exceed derived payer residual opex.
    opex = (flows[flows["FLOW_KIND"] == "opex"]
            .groupby(gkey, dropna=False)["ANNUAL_FLOW_AMOUNT"].sum())
    resid = p["REVENUE"] - p["CLAIMS_EXPENSE"] - p["OPERATING_GAIN"]
    j2 = resid.rename("RESID").to_frame().join(opex.rename("OPEX"), how="inner")
    if (j2["OPEX"] > j2["RESID"] + 1e-6).any():
        issues.append(Issue("error", "reconcile.opex", "Opex flows exceed derived residual operating expense"))

    # 3) Recipient revenue (at its own grain) = Σ inflows of every kind.
    rec = metrics[metrics["SUBPOOL_ID"].map(lambda i: tax[i].receives_flows)]
    inflow = flows.groupby(["YEAR", "STATE", "TARGET_NODE_ID"])["ANNUAL_FLOW_AMOUNT"].sum()
    r = rec.set_index(["YEAR", "STATE", "SUBPOOL_ID"])["REVENUE"]
    r.index.names = ["YEAR", "STATE", "TARGET_NODE_ID"]
    j3 = r.to_frame().join(inflow.rename("INFLOW"), how="outer")
    if j3.isna().any().any() or not _close(j3["REVENUE"], j3["INFLOW"]).all():
        issues.append(Issue("error", "reconcile.recipient", "Recipient REVENUE does not equal Σ inflows"))

    # 4) Residual flows only come from the external source and vice versa.
    resid_rows = flows["FLOW_KIND"] == "residual"
    if ((flows["SOURCE_NODE_ID"] == EXTERNAL_SOURCE) != resid_rows).any():
        issues.append(Issue("error", "flow.residual_source", f"Residual flows must originate at {EXTERNAL_SOURCE}"))
    if (flows.loc[resid_rows, "PAYER_LOB"] != meta.lobs.unmodeled_id).any():
        issues.append(Issue("error", "flow.residual_lob", "Residual flows must use the UNMODELED LOB"))
    return issues


def validate_bundle(metrics: pd.DataFrame, flows: pd.DataFrame, meta: Metadata) -> list[Issue]:
    return validate_pool_metrics(metrics, meta) + validate_value_flows(flows, metrics, meta)
