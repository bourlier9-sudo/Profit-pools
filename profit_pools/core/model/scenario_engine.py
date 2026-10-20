"""Scenario engine: applies a Scenario to the driver cube, then runs the ONE engine.

Pipeline (ARCHITECTURE.md §6):
  1  growth assumptions modify forecast rates → project()
  2  enrollment, uninsured / public population, enrollment shifts
  3  premium PMPM, residual opex PMPM, opex volume scaling, payer margin (solved last, in the engine)
  4  allowed PMPM (payer / funding), utilization, unit price, member cost share
  6  supplemental payments
  7  recipient (provider) margin
  8  top-down revenue and margin
 10  ownership: routing share, owned-asset margin
  then engine.run() derives every financial result.

Assumptions only ever change drivers. Each driver is consumed by one stage, so
an economic change is applied exactly once and reaches every view through the
engine.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace

import numpy as np
import pandas as pd

from profit_pools.core.config import load_config
from profit_pools.core.model import ownership
from profit_pools.core.model.cube import DriverCube, ForecastRates, project
from profit_pools.core.model.engine import ModelResult, run
from profit_pools.core.model.resolve import Issue, describe_scope, expand_sources, match, resolve
from profit_pools.core.model.scenario import Assumption, Scenario
from profit_pools.core.taxonomy import Metadata

# driver → (frame, column, kind, node column, row filter)
#   kind: additive (levels that sum) · intensive (per-cell PMPM) · factor (pct only, multiplies) ·
#         rate (margins, pp/set) · special
LEVEL_DRIVERS: dict[str, tuple] = {
    "enrollment": ("payer", "members", "additive", "node_id", None),
    "funding_population": ("population", "population", "additive", None, None),
    "premium_pmpm": ("payer", "premium_pmpm", "intensive", "node_id", None),
    "opex_pmpm": ("payer", "opex_pmpm", "intensive", "node_id", None),
    "opex_volume_scaling": ("payer", "opex_scaling", "rate", "node_id", None),
    "payer_margin": ("payer", None, "special", "node_id", None),
    "allowed_pmpm": ("spend", ("allowed_pmpm",), "factor", "target_id", "payer"),
    "funding_pmpm": ("spend", ("allowed_pmpm",), "factor", "target_id", "funding"),
    "utilization": ("spend", ("allowed_pmpm", "inst_per_member"), "factor", "target_id", "instances"),
    "unit_price": ("spend", ("allowed_pmpm",), "factor", "target_id", "instances"),
    "member_cost_share": ("spend", "paid_ratio", "special", "target_id", None),
    "supplemental_payments": ("supplemental", "amount", "additive", "target_id", None),
    "provider_margin": ("recipient_margin", "margin", "rate", "node_id", None),
    "top_down_revenue": ("top_down", "revenue", "additive", "node_id", None),
    "top_down_margin": ("top_down", "margin", "rate", "node_id", None),
    "routing_share": ("ownership", "routing_share", "rate", "node_id", None),
    "owned_margin": ("ownership", "owned_margin", "rate", "node_id", None),
}
STAGE_ORDER = ["enrollment", "funding_population", "enrollment_shift", "premium_pmpm", "opex_pmpm",
               "opex_volume_scaling", "payer_margin", "allowed_pmpm", "funding_pmpm", "utilization",
               "unit_price", "member_cost_share", "supplemental_payments", "provider_margin",
               "top_down_revenue", "top_down_margin", "routing_share", "owned_margin"]
GROWTH_NODE_COL = {"enrollment_growth": "node_id", "premium_trend": "node_id", "opex_trend": "node_id",
                   "utilization_trend": "target_id", "unit_cost_trend": "target_id",
                   "population_growth": None, "supplemental_growth": "target_id", "top_down_growth": "node_id"}
GROWTH_FRAME = {"enrollment_growth": "payer", "premium_trend": "payer", "opex_trend": "payer",
                "utilization_trend": "spend", "unit_cost_trend": "spend", "population_growth": "population",
                "supplemental_growth": "supplemental", "top_down_growth": "top_down"}
PERCENT_UNITS = {"ratio"}  # set-values entered as fractions


@dataclass
class LedgerEntry:
    assumption_id: str
    summary: str
    driver: str
    matched_cells: int = 0
    effective_cells: int = 0
    status: str = "applied"   # applied | partly superseded | superseded | conflict | invalid | no match


@dataclass
class ScenarioRun:
    scenario: Scenario
    result: ModelResult | None
    issues: list[Issue] = field(default_factory=list)
    ledger: list[LedgerEntry] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.result is not None and not any(i.level == "error" for i in self.issues)

    def issues_for(self, aid: str) -> list[Issue]:
        return [i for i in self.issues if i.assumption_id == aid]


def catalog() -> dict:
    return load_config("drivers")


def driver_spec(driver: str) -> dict:
    c = catalog()
    if driver in c["drivers"]:
        return c["drivers"][driver]
    if driver in c["growth_drivers"]:
        return {**c["growth_drivers"][driver], "methods": ["pp", "set"], "unit": "rate", "growth": True}
    raise KeyError(driver)


def is_growth(driver: str) -> bool:
    return driver in catalog()["growth_drivers"]


# ---------------------------------------------------------------------------
# Static validation
# ---------------------------------------------------------------------------

def _value_label(a: Assumption, spec: dict) -> str:
    v = a.value
    if a.method == "pct":
        return f"{v * 100:+.1f}%".replace("-", "−")
    if a.method == "pp":
        return f"{v * 100:+.1f} pp".replace("-", "−")
    unit = spec.get("unit")
    if unit in ("ratio", "rate"):
        return f"= {v * 100:.1f}%"
    if unit in ("usd_pmpm", "usd_per_instance"):
        return f"{'=' if a.method == 'set' else '+' if v >= 0 else '−'} ${abs(v):,.0f}"
    if unit == "usd_annual":
        return f"{'=' if a.method == 'set' else '+' if v >= 0 else '−'} ${abs(v) / 1e9:,.2f}B"
    return f"{'=' if a.method == 'set' else '+' if v >= 0 else '−'} {abs(v):,.0f}"


def summarize(meta: Metadata, a: Assumption) -> str:
    try:
        spec = driver_spec(a.driver)
    except KeyError:
        return a.label or a.driver
    parts = [describe_scope(meta, a.scope), spec["label"], _value_label(a, spec), a.scope.years.label()]
    if a.driver == "enrollment_shift" and a.target:
        tgt = meta.taxonomy[a.target].short_label if a.target in meta.taxonomy else (
            meta.funding_sources[a.target].short_label if a.target in meta.funding_sources else a.target)
        parts[2] = f"{_value_label(a, spec)} → {tgt}"
    return " · ".join(parts)


def _eligible_node(meta: Metadata, kind: str, node_id: str | None) -> bool:
    if node_id is None:
        return True
    leaves = meta.taxonomy.leaves(node_id)
    if kind == "payer":
        return all(l.economics == "payer_pnl" for l in leaves)
    if kind in ("spending", "recipient"):
        return any(l.receives_flows for l in leaves)
    if kind == "top_down":
        return any(l.economics == "top_down" for l in leaves)
    if kind == "ownership":
        return bool({l.id for l in leaves} & ownership.owned_leaves(meta))
    return True


def validate(meta: Metadata, scenario: Scenario) -> list[Issue]:
    issues: list[Issue] = []
    p = meta.periods
    for a in scenario.assumptions:
        if not a.enabled:
            continue

        def err(code, msg):
            issues.append(Issue("error", code, msg, a.id))

        try:
            spec = driver_spec(a.driver)
        except KeyError:
            err("driver.unknown", f"Unknown driver {a.driver!r}.")
            continue
        kind = spec.get("scope_kind")
        sc = a.scope
        if a.method not in spec["methods"]:
            err("method", f"{spec['label']} supports {', '.join(spec['methods'])}, not {a.method}.")
        if a.phase_in and a.method == "set":
            err("phase_in", "Phase-in applies to changes (%, pp, add), not to a set level.")
        if a.method == "set" and a.mode == "stack":
            err("mode", "A 'set' value replaces the driver level and cannot be stacked.")
        if sc.years.end is not None and sc.years.end < sc.years.start:
            err("years.order", "Year range ends before it starts.")
        if sc.years.start < p.history_start or (sc.years.end or sc.years.start) > p.forecast_end:
            err("years.range", f"Years must be within {p.history_start}–{p.forecast_end}.")
        if spec.get("growth") and sc.years.start <= p.base_year:
            err("years.growth", f"Growth assumptions apply to forecast years ({p.base_year + 1}+).")
        if sc.dataset is not None and sc.dataset not in meta.datasets:
            err("dataset", f"Unknown dataset {sc.dataset!r}.")
            continue
        if sc.node_id is not None:
            if sc.node_id not in meta.taxonomy:
                err("node", f"Pool group {sc.node_id!r} is not in the active pool grouping." if sc.node_id.startswith("GRP_")
                    else f"Unknown pool {sc.node_id!r}.")
                continue
            if sc.dataset and not meta.in_dataset(sc.node_id, sc.dataset):
                err("dataset.node", f"{meta.taxonomy[sc.node_id].label} is not part of the "
                                    f"{meta.datasets[sc.dataset].label} dataset.")
            if not _eligible_node(meta, kind, sc.node_id):
                err("eligibility", f"{spec['label']} cannot be applied to {meta.taxonomy[sc.node_id].label} "
                                   f"({_kind_text(kind)}).")
        if kind == "funding" and a.driver != "supplemental_payments" and sc.node_id is not None:
            err("eligibility", f"{spec['label']} is scoped by funding source, not by pool.")
        if kind == "ownership" and sc.dataset not in (None, ownership.dataset()):
            err("dataset.ownership", "Owned assets belong to the enterprise; scope this to the enterprise dataset.")
        if kind == "ownership" and any(g not in meta.geography.states for g in sc.geography):
            err("geography.ownership", "Owned assets are tracked by state; scope to states, not counties.")
        if kind == "top_down" and sc.dataset == "ENTERPRISE":
            err("dataset.top_down", "Top-down pools exist only in the total-market dataset.")
        try:
            lobs, funding = expand_sources(meta, sc.sources)
        except KeyError as e:
            err("sources", f"Unknown source {e.args[0]!r}.")
            lobs, funding = set(), set()
        if kind in ("payer", "top_down", "recipient", "ownership") and sc.sources:
            err("sources", f"{spec['label']} is not scoped by payer or funding source.")
        if a.driver == "allowed_pmpm" and funding:
            err("sources", "Use 'Uninsured / public-program PMPM' for funding sources; allowed PMPM applies to payer LOBs.")
        if a.driver in ("funding_pmpm", "funding_population"):
            if lobs:
                err("sources", f"{spec['label']} applies to funding sources, not payer LOBs.")
            if not funding:
                err("sources", "Choose at least one funding source (uninsured, Medicare FFS, Medicaid FFS).")
            if any(meta.funding_sources[f].basis != "population_pmpm" for f in funding):
                err("sources", "Supplemental payments have no population or PMPM; use 'Supplemental & state payments'.")
        if a.driver == "supplemental_payments" and (lobs or any(meta.funding_sources[f].basis != "lump_sum" for f in funding)):
            err("sources", "Supplemental payments are scoped to the supplemental funding source only.")
        if (funding or a.driver in ("funding_population", "funding_pmpm", "supplemental_payments")) and sc.dataset == "ENTERPRISE":
            err("dataset.funding", "Uninsured, government FFS and supplemental funding exist only in the total market.")
        for g in sc.geography:
            if g not in meta.geography.states:
                try:
                    meta.geography.state_of_county(g)
                except KeyError:
                    err("geography", f"Unknown geography {g!r}.")
        if kind == "top_down" and sc.geography:
            err("geography.top_down", "Top-down pools are national estimates; remove the geography filter.")
        if a.driver == "provider_margin" and any(g not in meta.geography.states for g in sc.geography):
            err("geography.margin", "Pool margins are state-level; scope margins to states, not counties.")
        if a.driver in ("utilization", "unit_price"):
            leaves = {l.id for l in meta.taxonomy.leaves(sc.node_id)} if sc.node_id else None
            has = [c for c in meta.categories.values() if c.has_instances and (leaves is None or c.target in leaves)]
            if not has:
                err("requires.instances", "No service-instance data in this scope (pharmacy never has "
                                          "instances); use allowed PMPM instead.")
        # values
        v, unit = a.value, spec.get("unit")
        if a.method == "pct" and v <= -1:
            err("value", "A percentage change must be greater than −100%.")
        if a.method == "set":
            if unit in ("ratio", "rate"):
                if not (-1 < v < 1) or (a.driver in ("member_cost_share", "opex_volume_scaling", "routing_share")
                                         and not 0 <= v <= 1):
                    err("value", "Enter a rate between 0% and 100% (margins between −100% and 100%).")
            elif v < 0:
                err("value", "A set value cannot be negative.")
        additive = LEVEL_DRIVERS.get(a.driver, (None, None, None))[2] == "additive" or a.driver == "enrollment_shift"
        if additive and a.method in ("set", "delta") and sc.dataset is None:
            err("dataset.total", "A total (set or add) must name one dataset — market and enterprise totals "
                                 "cannot be combined.")
        if a.method == "pp" and abs(v) >= 1:
            err("value", "A percentage-point change must be smaller than 100 pp.")
        if a.driver == "enrollment_shift":
            if a.method == "pct" and not 0 < v <= 1:
                err("value", "Shift between 0% and 100% of the segment's members.")
            if a.method == "delta" and v <= 0:
                err("value", "Shift a positive number of members.")
            t = a.target
            if not t:
                err("target", "Choose where the members move to.")
            elif t in meta.taxonomy:
                tn = meta.taxonomy[t]
                if not (tn.is_leaf and tn.economics == "payer_pnl"):
                    err("target", "Members can move to a payer segment (leaf) or a population-based funding source.")
                elif sc.node_id and t in meta.taxonomy.leaf_ids(sc.node_id):
                    err("target", "The destination is inside the source scope.")
            elif t in meta.funding_sources:
                if meta.funding_sources[t].basis != "population_pmpm":
                    err("target", "Supplemental payments have no members.")
                if sc.dataset == "ENTERPRISE":
                    err("target", "The enterprise dataset has no uninsured or government FFS population.")
            else:
                err("target", f"Unknown destination {t!r}.")
    return issues


def _kind_text(kind: str) -> str:
    return {"payer": "payer pools only", "spending": "provider and pharmacy pools only",
            "recipient": "provider and pharmacy pools only", "top_down": "top-down pools only",
            "funding": "funding sources only", "ownership": "pools with an owned asset only"}.get(kind, kind)


# ---------------------------------------------------------------------------
# Application
# ---------------------------------------------------------------------------

def phase_fraction(a: Assumption, years: np.ndarray, end_year: int) -> np.ndarray:
    """Linear phase-in: 1/n of the value in the first year of the range, the full value by its last year."""
    start = a.scope.years.start
    end = a.scope.years.end if a.scope.years.end is not None else end_year
    n = max(end - start + 1, 1)
    return np.clip((years - start + 1) / n, 0.0, 1.0)


def _apply_values(x: np.ndarray, a: Assumption, mask: np.ndarray, kind: str, groups: np.ndarray | None,
                  issues: list[Issue], years: np.ndarray | None = None, end_year: int = 0) -> np.ndarray:
    """Return x with assumption `a` applied on `mask` rows (phase-in scales the value by year)."""
    out = x.copy()
    if not mask.any():
        return out
    m = a.method
    v = a.value
    if a.phase_in and years is not None and m != "set":
        v = (a.value * phase_fraction(a, years, end_year))[mask]
    if m == "pct":
        out[mask] = x[mask] * (1 + v)
    elif m == "pp":
        out[mask] = x[mask] + v
    elif kind == "additive":  # set / delta distribute over the matched rows of each dataset-year
        g = pd.Series(x[mask]).groupby(groups[mask]).transform("sum").to_numpy()
        if (g <= 0).any():
            issues.append(Issue("error", "distribute", "Cannot distribute a total over cells that are zero.", a.id))
            return out
        out[mask] = x[mask] * v / g if m == "set" else x[mask] + v * x[mask] / g
    else:
        out[mask] = v if m == "set" else x[mask] + v
    return out


def _governed(x: np.ndarray, res, items: dict[int, Assumption], kind: str, groups, issues,
              years: np.ndarray | None = None, end_year: int = 0) -> np.ndarray:
    out = x.copy()
    for idx in np.unique(res.winner[res.winner >= 0]):
        out = _apply_values(out, items[idx], res.winner == idx, kind, groups, issues, years, end_year)
    for idx, mk in res.stacks:
        out = _apply_values(out, items[idx], mk, kind, groups, issues, years, end_year)
    return out


def _row_filter(frame: pd.DataFrame, f: str | None) -> np.ndarray | None:
    if f is None:
        return None
    if f == "instances":
        return frame["inst_per_member"].notna().to_numpy()
    return (frame["source_kind"] == f).to_numpy()


class ScenarioRates:
    """Wraps baseline forecast rates with growth assumptions (stage 1)."""

    def __init__(self, meta: Metadata, assumptions: list[tuple[int, Assumption]], issues: list[Issue],
                 ledger: dict[str, LedgerEntry]):
        self.base = ForecastRates(meta)
        self.meta = meta
        self.by_driver: dict[str, list[tuple[int, Assumption]]] = {}
        for idx, a in assumptions:
            self.by_driver.setdefault(a.driver, []).append((idx, a))
        self.issues = issues
        self.ledger = ledger
        self._seen: set[str] = set()

    def __call__(self, driver: str, rows: pd.DataFrame, year: int) -> np.ndarray:
        r = self.base(driver, rows, year)
        items = self.by_driver.get(driver)
        if not items:
            return r
        res = resolve(self.meta, rows, items, node_col=GROWTH_NODE_COL[driver], year=year)
        for i in res.issues:
            key = f"{i.assumption_id}:{i.code}"
            if key not in self._seen:
                self._seen.add(key)
                self.issues.append(i)
        for _, a in items:
            e = self.ledger[a.id]
            e.matched_cells += res.matched.get(a.id, 0)
            e.effective_cells += res.effective.get(a.id, 0)
        return _governed(r, res, dict(items), "rate", None, self.issues,
                         np.full(len(rows), year), self.meta.periods.forecast_end)


def _groups(frame: pd.DataFrame) -> np.ndarray:
    return (frame["dataset"].astype(str) + "|" + frame["year"].astype(str)).to_numpy()


def apply_scenario(hist: DriverCube, meta: Metadata, scenario: Scenario) -> tuple[DriverCube, list[Issue], dict]:
    issues = validate(meta, scenario)
    enabled = [(i, a) for i, a in enumerate(scenario.assumptions) if a.enabled]
    ledger = {a.id: LedgerEntry(a.id, summarize(meta, a), a.driver) for _, a in enabled}
    bad = {i.assumption_id for i in issues if i.level == "error"}
    usable = [(i, a) for i, a in enabled if a.id not in bad]

    growth = [(i, a) for i, a in usable if is_growth(a.driver)]
    cube = project(hist, meta, ScenarioRates(meta, growth, issues, ledger))
    frames = {k: v.copy() for k, v in cube.frames().items()}
    payer = frames["payer"]
    payer["members_ref"] = payer["members"]
    payer["opex_scaling"] = 1.0
    payer["margin_pp"] = 0.0
    payer["margin_set"] = np.nan

    level = {}
    for i, a in usable:
        if not is_growth(a.driver):
            level.setdefault(a.driver, []).append((i, a))

    for driver in STAGE_ORDER:
        items = level.get(driver)
        if not items:
            continue
        if driver == "enrollment_shift":
            _apply_shifts(meta, frames, items, issues, ledger)
            continue
        frame_name, col, kind, node_col, flt = LEVEL_DRIVERS[driver]
        df = frames[frame_name]
        res = resolve(meta, df, items, node_col=node_col, extra_mask=_row_filter(df, flt))
        issues.extend(res.issues)
        for _, a in items:
            ledger[a.id].matched_cells += res.matched.get(a.id, 0)
            ledger[a.id].effective_cells += res.effective.get(a.id, 0)
        imap = dict(items)
        if kind == "factor":
            yrs, end = df["year"].to_numpy(), meta.periods.forecast_end
            f = _governed(np.ones(len(df)), res, imap, "factor", None, issues, yrs, end)
            for c in col:
                df[c] = df[c].to_numpy() * f
        elif driver == "member_cost_share":
            cs = _governed(1 - df["paid_ratio"].to_numpy(), res, imap, "rate", None, issues,
                           df["year"].to_numpy(), meta.periods.forecast_end)
            if ((cs < -1e-12) | (cs > 1 + 1e-12)).any():
                for _, a in items:
                    issues.append(Issue("error", "result.cost_share", "Member cost share would leave the 0–100% range.", a.id))
            df["paid_ratio"] = 1 - np.clip(cs, 0, 1)
        elif driver == "payer_margin":
            mset = df["margin_set"].to_numpy().copy()
            mpp = df["margin_pp"].to_numpy().copy()
            frac = lambda a: (phase_fraction(a, df["year"].to_numpy(), meta.periods.forecast_end)  # noqa: E731
                              if a.phase_in else np.ones(len(df)))
            for idx in np.unique(res.winner[res.winner >= 0]):
                a, rows = imap[idx], res.winner == idx
                if a.method == "set":
                    mset[rows] = a.value
                else:
                    mpp[rows] += (a.value * frac(a))[rows]
            for idx, mk in res.stacks:
                mpp[mk] += (imap[idx].value * frac(imap[idx]))[mk]
            df["margin_set"], df["margin_pp"] = mset, mpp
        else:
            x = df[col].to_numpy(dtype=float)
            if kind == "rate" and driver == "provider_margin":
                missing = np.isnan(x) & (res.winner >= 0)
                for idx, mk in res.stacks:
                    missing |= np.isnan(x) & mk
                if missing.any():
                    for _, a in items:
                        if a.method == "pp":
                            issues.append(Issue("warning", "margin.missing",
                                                f"{int(missing.sum()):,} cells have no margin data; a pp change "
                                                f"leaves them unavailable.", a.id))
            df[col] = _governed(x, res, imap, kind, _groups(df), issues, df["year"].to_numpy(),
                                meta.periods.forecast_end)

    # result sanity checks (blocking)
    for name, cols in (("payer", ["members", "premium_pmpm", "opex_pmpm"]), ("spend", ["allowed_pmpm"]),
                       ("population", ["population"]), ("supplemental", ["amount"]), ("top_down", ["revenue"])):
        for c in cols:
            if (frames[name][c] < -1e-9).any():
                issues.append(Issue("error", "result.negative", f"The scenario makes {c.replace('_', ' ')} negative."))
    for name, c in (("recipient_margin", "margin"), ("top_down", "margin"), ("ownership", "owned_margin")):
        if name not in frames:
            continue
        mg = frames[name][c]
        if ((mg >= 1) | (mg <= -1)).any():
            issues.append(Issue("error", "result.margin", "The scenario pushes an operating margin beyond ±100%."))
    if "ownership" in frames:
        rs = frames["ownership"]["routing_share"]
        if ((rs < -1e-12) | (rs > 1 + 1e-12)).any():
            issues.append(Issue("error", "result.routing", "Routing share would leave the 0–100% range."))

    for _, a in enabled:
        e = ledger[a.id]
        errs = [i for i in issues if i.assumption_id == a.id and i.level == "error"]
        if a.id in bad or any(i.code != "conflict" for i in errs):
            e.status = "invalid"
        elif any(i.code == "conflict" for i in errs):
            e.status = "conflict"
        elif e.matched_cells == 0:
            e.status = "no match"
            issues.append(Issue("error", "no_match", "This assumption matches no data. Check the pool, sources, "
                                                    "geography, dataset and years.", a.id))
        elif e.effective_cells == 0:
            e.status = "superseded"
        elif e.effective_cells < e.matched_cells:
            e.status = "partly superseded"
    _cross_driver_warnings(enabled, issues)
    new = replace(cube, payer=frames["payer"], spend=frames["spend"], population=frames["population"],
                  supplemental=frames["supplemental"], recipient_margin=frames["recipient_margin"],
                  top_down=frames["top_down"], ownership=frames.get("ownership"))
    return new, issues, ledger


def _cross_driver_warnings(enabled: list[tuple[int, Assumption]], issues: list[Issue]) -> None:
    drivers = {a.driver for _, a in enabled}
    if "allowed_pmpm" in drivers and drivers & {"utilization", "unit_price"}:
        aid = next(a.id for _, a in enabled if a.driver == "allowed_pmpm")
        issues.append(Issue("warning", "compound.spend",
                            "Allowed PMPM and utilization / unit-price assumptions both change allowed spend; "
                            "where scopes overlap their effects multiply.", aid))
    stacks: dict[str, set[str]] = {}
    for _, a in enabled:
        if a.mode == "stack":
            stacks.setdefault(a.driver, set()).add(a.method)
    for d, methods in stacks.items():
        if "pct" in methods and methods & {"delta", "pp"}:
            aid = next(a.id for _, a in enabled if a.driver == d and a.mode == "stack")
            issues.append(Issue("warning", "order", "Stacked % and additive changes on the same driver are "
                                                    "order-sensitive; list order is applied.", aid))


def _apply_shifts(meta: Metadata, frames: dict, items, issues: list[Issue], ledger: dict) -> None:
    payer, pop = frames["payer"], frames["population"]
    key = ["dataset", "year", "county"]
    for _, a in items:
        mk = match(meta, payer, a.scope, node_col="node_id")
        ledger[a.id].matched_cells += int(mk.sum())
        if not mk.any():
            continue
        members = payer["members"].to_numpy()
        v = a.value * (phase_fraction(a, payer["year"].to_numpy(), meta.periods.forecast_end) if a.phase_in else 1.0)
        if a.method == "pct":
            moved = np.where(mk, members * v, 0.0)
        else:
            tot = pd.Series(np.where(mk, members, 0.0)).groupby(_groups(payer)).transform("sum").to_numpy()
            moved = np.where(mk & (tot > 0), v * members / np.where(tot > 0, tot, 1), 0.0)
            if (moved > members + 1e-9).any():
                issues.append(Issue("error", "shift.exceeds", "The shift moves more members than the segment has.", a.id))
                continue
        if a.target in meta.taxonomy:
            dest = payer.index[payer["node_id"] == a.target]
            dest_df, dest_col = payer, "members"
        else:
            dest = pop.index[pop["source_id"] == a.target]
            dest_df, dest_col = pop, "population"
        mv = payer.loc[mk, key].assign(moved=moved[mk]).groupby(key)["moved"].sum()
        dkeys = dest_df.loc[dest, key]
        dk = pd.MultiIndex.from_frame(dkeys)
        arrive = mv.reindex(dk).fillna(0.0).to_numpy()
        landed = mv.index.isin(dk)
        if not landed.all():
            lost = mv[~landed].sum()
            issues.append(Issue("warning", "shift.no_destination",
                                f"{lost:,.0f} member-years could not move (destination absent in some "
                                f"counties/years or datasets) and stay in the source.", a.id))
            stay = pd.MultiIndex.from_frame(payer.loc[mk, key]).isin(mv.index[~landed])
            sub = moved[mk]
            sub[stay] = 0.0
            moved[mk] = sub
        payer["members"] = members - moved
        counts = dkeys.groupby(key).transform("size").to_numpy() if len(dkeys) else np.array([])
        dest_df.loc[dest, dest_col] = dest_df.loc[dest, dest_col].to_numpy() + arrive / np.maximum(counts, 1)
        ledger[a.id].effective_cells += int((moved > 0).sum())


def run_scenario(hist: DriverCube, meta: Metadata, scenario: Scenario) -> ScenarioRun:
    cube, issues, ledger = apply_scenario(hist, meta, scenario)
    errors = [i for i in issues if i.level == "error"]
    result = None if errors else run(cube, meta, scenario.id)
    return ScenarioRun(scenario, result, issues, list(ledger.values()))


def bridge(hist: DriverCube, meta: Metadata, scenario: Scenario) -> list[tuple[Assumption | None, pd.DataFrame]]:
    """Sequential attribution: pools after enabling assumptions 1..k in list order.

    Element 0 is the baseline. Differences between consecutive elements are each assumption's
    contribution; they sum exactly to scenario − baseline. Order-dependent by design.
    """
    steps: list[tuple[Assumption | None, pd.DataFrame]] = []
    enabled = scenario.enabled
    base = run_scenario(hist, meta, scenario.with_assumptions([]))
    steps.append((None, base.result.pools))
    for k in range(1, len(enabled) + 1):
        sub = scenario.with_assumptions(enabled[:k])
        r = run_scenario(hist, meta, sub)
        if r.result is None:
            raise ValueError(f"Scenario prefix {k} is invalid: {[i.message for i in r.issues if i.level == 'error']}")
        steps.append((enabled[k - 1], r.result.pools))
    return steps


def preview(hist: DriverCube, meta: Metadata, scenario: Scenario, assumption_id: str, year: int,
            dataset: str) -> dict:
    """Effect of one assumption given the rest of the (draft) scenario: matched pools and Δ by layer."""
    a = next(x for x in scenario.assumptions if x.id == assumption_id)
    others = scenario.with_assumptions([x for x in scenario.assumptions if x.id != assumption_id and x.enabled])
    with_a = scenario.with_assumptions([*others.assumptions, replace(a, enabled=True)])
    r0, r1 = run_scenario(hist, meta, others), run_scenario(hist, meta, with_a)
    out = {"issues": [i for i in r1.issues if i.assumption_id == assumption_id],
           "ledger": next((e for e in r1.ledger if e.assumption_id == assumption_id), None),
           "leaves": [n.short_label for n in meta.leaves_in_dataset(dataset, a.scope.node_id)][:12]
           if a.scope.node_id else [], "delta": None}
    if r0.result is None or r1.result is None:
        return out

    def layer_totals(res: ModelResult) -> pd.DataFrame:
        p = res.pools[res.pools["dataset"] == dataset]
        p = p.assign(layer_id=p["node_id"].map({n.id: n.layer_id for n in meta.taxonomy}))   # active grouping
        return p.groupby(["year", "layer_id"])[["revenue", "gain"]].sum(min_count=1)

    d_all = layer_totals(r1.result).sub(layer_totals(r0.result), fill_value=0).fillna(0.0)
    d = d_all.xs(year, level="year") if year in d_all.index.get_level_values("year") else d_all.iloc[0:0]
    out["delta"] = d[(d["revenue"].abs() > 1) | (d["gain"].abs() > 1)]
    out["series"] = d_all.reset_index()          # year × layer Δ revenue / Δ gain, all years

    def owned(res: ModelResult) -> pd.Series:
        o = res.ownership
        return o[o["dataset"] == dataset].groupby("year")["owned_gain"].sum()

    out["owned_gain"] = owned(r1.result).sub(owned(r0.result), fill_value=0).fillna(0.0)   # Δ by year
    return out
