"""Guided assumption builder: Where → What → How much & when.

The wizard only ever offers levers that are economically valid for the chosen
pool, words them as trends with an explicit payer / payee (so a "revenue"
change is always somebody paying more), and maps the answers onto the scenario
engine's drivers. No economics happen here.
"""

from __future__ import annotations

from dataclasses import dataclass

import plotly.graph_objects as go
import streamlit as st

from profit_pools.core.model import ownership
from profit_pools.core.model.scenario import Assumption, Scenario, Scope, YearRange
from profit_pools.core.model.scenario_engine import driver_spec, is_growth, summarize
from profit_pools.core.taxonomy import Metadata
from profit_pools.design import format as fmt
from profit_pools.design.theme import PLOTLY_CONFIG, TEMPLATE_NAME
from profit_pools.design.tokens import COLOR, LAYER_COLOR, OTHER
from profit_pools.shell import data
from profit_pools.shell import state as S

W = "pp.w."
NEW = "__new__"
ALL = "__all__"
FUND = "FUND:"
CATEGORIES = {"enrollment": "Enrollment", "financials": "Financials", "growth": "Growth (forecast years)"}


@dataclass(frozen=True)
class Lever:
    id: str
    driver: str
    category: str
    applies: str            # payer | recipient | top_down | funding | supplemental | owned
    label: str
    pick: str | None = None  # "paid_to" (payer → recipients) · "paid_by_payers" · "paid_by_funding" · "paid_by_any"
    instances: bool = False  # needs service-instance data (never pharmacy)


LEVERS: list[Lever] = [
    # --- payer pools ------------------------------------------------------------
    Lever("pay.enroll", "enrollment", "enrollment", "payer", "Enrollment (members)"),
    Lever("pay.shift", "enrollment_shift", "enrollment", "payer", "Members switching coverage (to another plan or uninsured)"),
    Lever("pay.premium", "premium_pmpm", "financials", "payer", "Premium rate (what members and employers pay)"),
    Lever("pay.medcost", "allowed_pmpm", "financials", "payer", "Medical cost trend — what plans pay providers & pharmacy", "paid_to"),
    Lever("pay.price", "unit_price", "financials", "payer", "Reimbursement rates plans pay (price per service)", "paid_to", True),
    Lever("pay.util", "utilization", "financials", "payer", "Utilization by members (services per member)", "paid_to", True),
    Lever("pay.costshare", "member_cost_share", "financials", "payer", "Member cost sharing (out-of-pocket share)", "paid_to"),
    Lever("pay.admin", "opex_pmpm", "financials", "payer", "Administrative cost per member"),
    Lever("pay.scaling", "opex_volume_scaling", "financials", "payer", "How admin cost scales with membership"),
    Lever("pay.margin", "payer_margin", "financials", "payer", "Plan operating margin target (reached through admin cost)"),
    Lever("pay.g.enroll", "enrollment_growth", "growth", "payer", "Enrollment growth rate"),
    Lever("pay.g.premium", "premium_trend", "growth", "payer", "Premium trend"),
    Lever("pay.g.admin", "opex_trend", "growth", "payer", "Admin cost trend"),
    Lever("pay.g.util", "utilization_trend", "growth", "payer", "Medical utilization trend (plans' members)", "paid_to"),
    Lever("pay.g.price", "unit_cost_trend", "growth", "payer", "Reimbursement / unit-cost trend (what plans pay)", "paid_to"),
    # --- provider & pharmacy pools --------------------------------------------------
    Lever("rec.price", "unit_price", "financials", "recipient", "Reimbursement rate trend — price paid per service", "paid_by_payers", True),
    Lever("rec.util", "utilization", "financials", "recipient", "Utilization trend — services delivered", "paid_by_payers", True),
    Lever("rec.spend", "allowed_pmpm", "financials", "recipient", "Payment trend from plans (all services, per member)", "paid_by_payers"),
    Lever("rec.fund", "funding_pmpm", "financials", "recipient", "Payments from uninsured & government FFS (per person)", "paid_by_funding"),
    Lever("rec.supp", "supplemental_payments", "financials", "recipient", "Supplemental & state payments (hospitals)"),
    Lever("rec.margin", "provider_margin", "financials", "recipient", "Operating efficiency (margin) — revenue unchanged"),
    Lever("rec.g.util", "utilization_trend", "growth", "recipient", "Utilization trend (per year)", "paid_by_any"),
    Lever("rec.g.price", "unit_cost_trend", "growth", "recipient", "Reimbursement / unit-cost trend (per year)", "paid_by_any"),
    # --- owned assets inside provider & pharmacy pools (enterprise) --------------------
    Lever("own.route", "routing_share", "financials", "owned", "Share of our members' care delivered by owned assets"),
    Lever("own.margin", "owned_margin", "financials", "owned", "Owned-asset operating margin"),
    # --- top-down pools -------------------------------------------------------------
    Lever("td.size", "top_down_revenue", "financials", "top_down", "Market size (top-down estimate)"),
    Lever("td.margin", "top_down_margin", "financials", "top_down", "Operating margin (top-down estimate)"),
    Lever("td.g", "top_down_growth", "growth", "top_down", "Market growth rate"),
    # --- non-pool funders -------------------------------------------------------------
    Lever("fund.pop", "funding_population", "enrollment", "funding", "Covered lives / uninsured population"),
    Lever("fund.pmpm", "funding_pmpm", "financials", "funding", "Spending per person paid to providers & pharmacy", "paid_to"),
    Lever("fund.g", "population_growth", "growth", "funding", "Population growth rate"),
    Lever("supp.amt", "supplemental_payments", "financials", "supplemental", "Supplemental & state payments to hospitals", "paid_to"),
    Lever("supp.g", "supplemental_growth", "growth", "supplemental", "Supplemental payment growth rate"),
]
LEVER_BY_ID = {l.id: l for l in LEVERS}
METHOD_LABELS = {"pct": "% change", "pp": "pp change", "set": "Set to", "delta": "Add"}
GROWTH_METHOD_LABELS = {"pp": "Change annual rate by (pp)", "set": "Set annual rate to (%)"}


# ---------------------------------------------------------------------------
# where → what
# ---------------------------------------------------------------------------

def where_options(meta: Metadata, dataset: str | None) -> list[str]:
    out = [ALL]
    for n in meta.taxonomy:
        if dataset is None or meta.in_dataset(n.id, dataset):
            out.append(n.id)
    if dataset in (None, "MARKET"):
        out += [FUND + g for g in meta.funding_groups]
        out += [FUND + f for f, fs in meta.funding_sources.items()]
    return out


def where_label(meta: Metadata, w: str) -> str:
    if w == ALL:
        return "All pools"
    if w.startswith(FUND):
        k = w[len(FUND):]
        return "Funder · " + (meta.funding_groups[k] if k in meta.funding_groups else meta.funding_sources[k].label)
    n = meta.taxonomy[w]
    return ("    " * n.depth) + n.label


def where_kinds(meta: Metadata, w: str, dataset: str | None) -> set[str]:
    if w.startswith(FUND):
        k = w[len(FUND):]
        return {"supplemental"} if k in meta.funding_sources and meta.funding_sources[k].basis == "lump_sum" else {"funding"}
    leaves = (meta.leaves_in_dataset(dataset, None if w == ALL else w) if dataset
              else meta.taxonomy.leaves(None if w == ALL else w))
    kinds = set()
    for l in leaves:
        kinds.add({"payer_pnl": "payer", "margin_on_revenue": "recipient", "top_down": "top_down"}[l.economics])
    if dataset in (None, ownership.dataset()) and {l.id for l in leaves} & ownership.owned_leaves(meta):
        kinds.add("owned")
    return kinds


def levers_for(meta: Metadata, w: str, dataset: str | None, category: str) -> list[Lever]:
    kinds = where_kinds(meta, w, dataset)
    leaves = set() if w.startswith(FUND) else {l.id for l in meta.taxonomy.leaves(None if w == ALL else w)}
    has_inst = any(c.has_instances and c.target in leaves for c in meta.categories.values())
    supp = set(meta.supplemental_targets("FS_SUPPLEMENTAL")) if "FS_SUPPLEMENTAL" in meta.funding_sources else set()
    out = []
    for lv in LEVERS:
        if lv.category != category or lv.applies not in kinds:
            continue
        if lv.applies == "recipient" and lv.instances and not has_inst:
            continue
        if lv.id == "rec.supp" and not (leaves & supp):
            continue
        if lv.id in ("rec.fund", "rec.supp") and dataset == "ENTERPRISE":
            continue
        if lv.id == "pay.shift" and not shift_targets(meta, dataset, w):
            continue
        out.append(lv)
    return out


def _payer_lobs(meta: Metadata, w: str) -> tuple[str, ...]:
    """LOB scope for a payer-origin spend lever: () = every payer when the whole Payer layer is chosen."""
    if w in (ALL, "PAYER"):
        return tuple(meta.lobs.groups)
    lobs = sorted({l.lob for l in meta.taxonomy.leaves(w) if l.lob})
    for g in meta.lobs.groups:          # prefer a group id when the selection is exactly a group
        if sorted(meta.lobs.in_group(g)) == lobs:
            return (g,)
    return tuple(lobs)


def recipient_options(meta: Metadata, dataset: str | None, instances: bool, supplemental: bool) -> list[str]:
    out = [ALL]
    supp = set(meta.supplemental_targets("FS_SUPPLEMENTAL")) if supplemental else None
    for n in meta.taxonomy:
        leaves = meta.taxonomy.leaves(n.id)
        if not any(l.receives_flows for l in leaves):
            continue
        if dataset and not meta.in_dataset(n.id, dataset):
            continue
        if instances and not any(c.has_instances and c.target in {l.id for l in leaves} for c in meta.categories.values()):
            continue
        if supp is not None and not ({l.id for l in leaves} & supp):
            continue
        out.append(n.id)
    return out


def source_options(meta: Metadata, pick: str) -> list[str]:
    payers = [*meta.lobs.groups, *meta.lobs.lobs]
    funding = [*meta.funding_groups, *[f for f, fs in meta.funding_sources.items() if fs.basis == "population_pmpm"]]
    return {"paid_by_payers": payers, "paid_by_funding": funding, "paid_by_any": payers + funding}[pick]


def source_label(meta: Metadata, s: str) -> str:
    if s in meta.lobs.groups:
        return meta.lobs.groups[s] + " (all)"
    if s in meta.lobs.lobs:
        return "   " + meta.lobs.lobs[s].label
    if s in meta.funding_groups:
        return meta.funding_groups[s] + " (all)"
    return meta.funding_sources[s].label


def shift_targets(meta: Metadata, dataset: str | None, where: str) -> list[str]:
    """Destinations outside the source scope. Funding populations exist only in the total market, so the
    enterprise can shift members between its own plans only."""
    funders = [] if dataset == "ENTERPRISE" else [
        f for f, fs in meta.funding_sources.items() if fs.basis == "population_pmpm"]
    inside = {l.id for l in meta.taxonomy.leaves(None if where == ALL else where)}
    return funders + [n.id for n in meta.taxonomy.leaves("PAYER") if n.id not in inside]


def unit_hint(driver: str, method: str) -> tuple[str, float]:
    unit = driver_spec(driver).get("unit")
    if method == "pct":
        return "%", 0.01
    if method == "pp":
        return "pp", 0.01
    if unit in ("ratio", "rate"):
        return "%", 0.01
    if unit == "usd_pmpm":
        return "$ per member per month", 1.0
    if unit == "usd_per_instance":
        return "$ per service", 1.0
    if unit == "usd_annual":
        return "$ millions per year", 1e6
    if unit == "members_avg":
        return "members", 1.0
    return "", 1.0


# ---------------------------------------------------------------------------
# state ⇄ assumption
# ---------------------------------------------------------------------------

def _infer_origin(meta: Metadata, a: Assumption) -> tuple[str, str]:
    """(where, lever id) for an assumption — uses `origin` when the wizard created it."""
    candidates = [lv for lv in LEVERS if lv.driver == a.driver]
    if a.origin and "|" in a.origin:
        where, lever = a.origin.split("|", 1)
        if lever in LEVER_BY_ID and LEVER_BY_ID[lever].driver == a.driver and (
                where in meta.taxonomy or where == ALL or where.startswith(FUND)):
            return where, lever
    lv = candidates[0]
    if lv.applies in ("funding", "supplemental") and a.scope.sources:
        return FUND + a.scope.sources[0], lv.id
    return a.scope.node_id or ALL, lv.id


def load(meta: Metadata, a: Assumption | None) -> None:
    ss = st.session_state
    p = meta.periods
    if a is None:
        ss.update({W + "where": "PAYER", W + "dataset": "BOTH", W + "category": "enrollment",
                   W + "lever": "pay.enroll", W + "paid_to": ALL, W + "paid_by": [], W + "method": "pct",
                   W + "value": 5.0, W + "start": p.base_year + 1, W + "end": "onward", W + "phase": False,
                   W + "geo": [], W + "mode": "replace", W + "target": "FS_UNINSURED", W + "label": "",
                   W + "notes": "", W + "show_preview": False})
        return
    where, lever = _infer_origin(meta, a)
    lv = LEVER_BY_ID[lever]
    _, mult = unit_hint(a.driver, a.method)
    paid_to = ALL
    paid_by: list[str] = []
    if lv.pick == "paid_to":
        paid_to = a.scope.node_id or ALL
    elif lv.pick:
        paid_by = list(a.scope.sources)
    ss.update({W + "where": where, W + "dataset": a.scope.dataset or "BOTH", W + "category": lv.category,
               W + "lever": lever, W + "paid_to": paid_to, W + "paid_by": paid_by, W + "method": a.method,
               W + "value": float(a.value / mult), W + "start": a.scope.years.start,
               W + "end": a.scope.years.end if a.scope.years.end is not None else "onward",
               W + "phase": a.phase_in, W + "geo": list(a.scope.geography), W + "mode": a.mode,
               W + "target": a.target or "FS_UNINSURED", W + "label": a.label, W + "notes": a.notes,
               W + "show_preview": False})


def build(meta: Metadata, base_id: str | None) -> Assumption:
    ss = st.session_state
    lv = LEVER_BY_ID[ss[W + "lever"]]
    w = ss[W + "where"]
    ds = None if ss[W + "dataset"] == "BOTH" else ss[W + "dataset"]
    if lv.applies in ("funding", "supplemental", "top_down") or lv.id == "rec.fund":
        ds = "MARKET"
    if lv.applies == "owned":
        ds = ownership.dataset()
    node, sources = None, ()
    if lv.applies == "payer":
        if lv.pick == "paid_to":
            node = None if ss[W + "paid_to"] == ALL else ss[W + "paid_to"]
            sources = _payer_lobs(meta, w)
        else:
            node = None if w == ALL else w
    elif lv.applies == "recipient":
        node = None if w == ALL else w
        sources = tuple(ss.get(W + "paid_by") or ())
        if lv.id == "rec.supp":
            sources = ("FS_SUPPLEMENTAL",)
        if lv.id == "rec.fund" and not sources:
            sources = tuple(meta.funding_groups) + tuple(
                f for f, fs in meta.funding_sources.items() if fs.basis == "population_pmpm" and fs.group is None)
    elif lv.applies in ("funding", "supplemental"):
        sources = (w[len(FUND):],)
        if lv.pick == "paid_to":
            node = None if ss[W + "paid_to"] == ALL else ss[W + "paid_to"]
    else:
        node = None if w == ALL else w
    method = ss[W + "method"]
    _, mult = unit_hint(lv.driver, method)
    end = ss[W + "end"]
    scope = Scope(years=YearRange(int(ss[W + "start"]), None if end == "onward" else int(end)), dataset=ds,
                  node_id=node, sources=sources, geography=tuple(ss.get(W + "geo") or ()))
    target = None
    if lv.driver == "enrollment_shift":
        targets = shift_targets(meta, ds, w)
        target = ss[W + "target"] if ss[W + "target"] in targets else targets[0]
    kw = dict(driver=lv.driver, method=method, value=float(ss[W + "value"]) * mult, scope=scope,
              mode=ss[W + "mode"], target=target,
              label=(ss.get(W + "label") or "").strip(), notes=(ss.get(W + "notes") or "").strip(),
              phase_in=bool(ss.get(W + "phase")) and method != "set", origin=f"{w}|{lv.id}")
    return Assumption(**kw, id=base_id) if base_id else Assumption(**kw)


def sentence(meta: Metadata, a: Assumption, lv: Lever) -> str:
    """Plain-English statement of who pays whom, so cascades are never hidden."""
    def node_name(n):
        return "all providers & pharmacy" if n is None else meta.taxonomy[n].label

    def payers(srcs):
        if not srcs:
            return "all payers"
        return ", ".join(source_label(meta, s).strip().replace(" (all)", "") for s in srcs)

    v = summarize(meta, a).split(" · ")[2] if " · " in summarize(meta, a) else ""
    when = a.scope.years.label() + (" (phased in)" if a.phase_in else "")
    if lv.driver in ("allowed_pmpm", "unit_price", "utilization", "utilization_trend", "unit_cost_trend",
                     "member_cost_share", "funding_pmpm"):
        who = payers(a.scope.sources)
        what = {"allowed_pmpm": "pay", "unit_price": "pay a price per service", "utilization": "fund service volume",
                "utilization_trend": "fund utilization growth", "unit_cost_trend": "pay unit-cost growth",
                "member_cost_share": "shift cost sharing", "funding_pmpm": "pay per person"}[lv.driver]
        return f"{who} {what} {v} to {node_name(a.scope.node_id)}, {when}. {driver_spec(lv.driver).get('explains', '')}"
    return f"{summarize(meta, a)}. {driver_spec(lv.driver).get('explains', '')}"


# ---------------------------------------------------------------------------
# render
# ---------------------------------------------------------------------------

def _on_where_change(meta: Metadata) -> None:
    ss = st.session_state
    ds = None if ss[W + "dataset"] == "BOTH" else ss[W + "dataset"]
    cats = [c for c in CATEGORIES if levers_for(meta, ss[W + "where"], ds, c)]
    if ss[W + "category"] not in cats and cats:
        ss[W + "category"] = cats[0]
    _on_category_change(meta)


def _on_category_change(meta: Metadata) -> None:
    ss = st.session_state
    ds = None if ss[W + "dataset"] == "BOTH" else ss[W + "dataset"]
    lv = levers_for(meta, ss[W + "where"], ds, ss[W + "category"])
    if lv and ss[W + "lever"] not in {x.id for x in lv}:
        ss[W + "lever"] = lv[0].id
    _on_lever_change()


def _on_lever_change() -> None:
    ss = st.session_state
    lv = LEVER_BY_ID.get(ss[W + "lever"])
    if lv is None:
        return
    methods = driver_spec(lv.driver)["methods"]
    if ss[W + "method"] not in methods:
        ss[W + "method"] = methods[0]
    if is_growth(lv.driver) and isinstance(ss[W + "start"], int) and ss[W + "start"] <= 2025:
        ss[W + "start"] = 2026
    ss[W + "paid_by"] = []
    ss[W + "paid_to"] = ALL
    ss[W + "show_preview"] = False


def render(meta: Metadata, editing: str, on_save) -> None:
    ss = st.session_state
    p = meta.periods
    st.markdown(f'<div class="pp-h3">{"New assumption" if editing == NEW else "Edit assumption"}</div>',
                unsafe_allow_html=True)

    # 1 — Where
    st.markdown("**1 · Where does it apply?**")
    c1, c2 = st.columns([3, 2])
    with c2:
        st.segmented_control("Dataset", ["BOTH", *meta.datasets], key=W + "dataset", required=True,
                             format_func=lambda d: "Both" if d == "BOTH" else meta.datasets[d].short_label,
                             on_change=_on_where_change, args=(meta,))
    ds = None if ss[W + "dataset"] == "BOTH" else ss[W + "dataset"]
    with c1:
        opts = where_options(meta, ds)
        if ss[W + "where"] not in opts:
            ss[W + "where"] = opts[0]
        st.selectbox("Pool or funder", opts, key=W + "where", format_func=lambda w: where_label(meta, w),
                     on_change=_on_where_change, args=(meta,),
                     help="Any level of the hierarchy (your grouping included), or a funder outside the pools "
                          "(uninsured, government FFS, supplemental payments).")

    # 2 — What
    st.markdown("**2 · What changes?**")
    cats = [c for c in CATEGORIES if levers_for(meta, ss[W + "where"], ds, c)]
    if not cats:
        st.info("No assumptions apply to this selection in this dataset.")
        return
    if ss[W + "category"] not in cats:
        ss[W + "category"] = cats[0]
    st.segmented_control("Type", cats, key=W + "category", required=True, format_func=CATEGORIES.get,
                         on_change=_on_category_change, args=(meta,), label_visibility="collapsed")
    levers = levers_for(meta, ss[W + "where"], ds, ss[W + "category"])
    if ss[W + "lever"] not in {l.id for l in levers}:
        ss[W + "lever"] = levers[0].id
        _on_lever_change()
    st.radio("Lever", [l.id for l in levers], key=W + "lever", format_func=lambda i: LEVER_BY_ID[i].label,
             on_change=_on_lever_change, label_visibility="collapsed")
    lv = LEVER_BY_ID[ss[W + "lever"]]
    spec = driver_spec(lv.driver)
    st.caption(spec.get("explains", ""))
    if lv.pick == "paid_to":
        opts = recipient_options(meta, ds, lv.instances, lv.applies == "supplemental")
        if ss[W + "paid_to"] not in opts:
            ss[W + "paid_to"] = ALL
        st.selectbox("Paid to", opts, key=W + "paid_to",
                     format_func=lambda n: "All providers & pharmacy" if n == ALL else
                     ("    " * meta.taxonomy[n].depth) + meta.taxonomy[n].label)
    elif lv.pick:
        st.multiselect("Paid by", source_options(meta, lv.pick), key=W + "paid_by",
                       format_func=lambda s: source_label(meta, s), placeholder="All payers"
                       if lv.pick != "paid_by_funding" else "All uninsured & government FFS")
    if lv.driver == "enrollment_shift":
        targets = shift_targets(meta, ds, ss[W + "where"])
        if ss[W + "target"] not in targets:
            ss[W + "target"] = targets[0]
        st.selectbox("Members move to", targets, key=W + "target",
                     format_func=lambda t: meta.taxonomy[t].label if t in meta.taxonomy else
                     meta.funding_sources[t].label)

    # 3 — How much & when
    st.markdown("**3 · How much, and when?**")
    methods = spec["methods"]
    labels = GROWTH_METHOD_LABELS if is_growth(lv.driver) else METHOD_LABELS
    c1, c2, c3, c4 = st.columns([2, 1.3, 1, 1])
    with c1:
        if ss[W + "method"] not in methods:
            ss[W + "method"] = methods[0]
        st.segmented_control("Change", methods, key=W + "method", required=True, format_func=labels.get)
    suffix, _ = unit_hint(lv.driver, ss[W + "method"])
    with c2:
        st.number_input(f"Value ({suffix})" if suffix else "Value", key=W + "value", step=0.5, format="%.2f")
    years = p.forecast_years if is_growth(lv.driver) else p.all_years
    with c3:
        if ss[W + "start"] not in years:
            ss[W + "start"] = years[0]
        st.selectbox("From", years, key=W + "start")
    with c4:
        ends = ["onward", *[y for y in p.all_years if y >= ss[W + "start"]]]
        if ss[W + "end"] not in ends:
            ss[W + "end"] = "onward"
        st.selectbox("Through", ends, key=W + "end", format_func=lambda e: "Onward" if e == "onward" else str(e))
    if ss[W + "method"] != "set":
        st.toggle("Phase in gradually (reach the full value by the last year)", key=W + "phase")
    with st.expander("Advanced: geography, overlaps, label"):
        a1, a2 = st.columns([2, 1])
        with a1:
            if lv.applies != "top_down":
                state_only = lv.applies == "owned" or lv.driver == "provider_margin"   # state-grain drivers
                geo = [g for s_ in meta.geography.states.values()
                       for g in [s_.id, *([] if state_only else [c for c, _, _ in s_.counties])]]
                if any(g not in geo for g in ss.get(W + "geo") or []):
                    ss[W + "geo"] = [g for g in ss[W + "geo"] if g in geo]
                st.multiselect("Geography", geo, key=W + "geo", placeholder="National (all)",
                               format_func=lambda g: meta.geography.states[g].label if g in meta.geography.states
                               else "   " + meta.geography.county_label(g))
        with a2:
            st.segmented_control("If it overlaps another assumption", ["replace", "stack"], key=W + "mode",
                                 required=True, format_func={"replace": "Replace", "stack": "Stack"}.get,
                                 help="Replace (default): the more specific assumption wins and overlaps are "
                                      "flagged. Stack: compounds with whatever else applies.")
        b1, b2 = st.columns([1, 2])
        with b1:
            st.text_input("Label", key=W + "label")
        with b2:
            st.text_input("Notes", key=W + "notes")

    a = build(meta, None if editing == NEW else editing)
    st.info(sentence(meta, a, lv).replace("$", "\\$"))
    b1, b2, b3, _ = st.columns([1.2, 1.2, 1, 2])
    with b1:
        st.button("Add assumption" if editing == NEW else "Save assumption", type="primary", key=W + "save",
                  width="stretch", on_click=on_save, args=(meta, editing))
    with b2:
        if st.button("Preview impact", key=W + "preview", width="stretch"):
            ss[W + "show_preview"] = True
    with b3:
        st.button("Cancel", key=W + "cancel", width="stretch", on_click=lambda: ss.update({S.K_EDIT: None}))
    if ss.get(W + "show_preview"):
        _preview(meta, a, editing)


def _preview(meta: Metadata, a: Assumption, editing: str) -> None:
    sc: Scenario = st.session_state[S.K_DRAFT]
    sc = sc.add(a) if editing == NEW else sc.update(a)
    ds = a.scope.dataset or st.session_state[S.K_DATASET]
    year = max(a.scope.years.start, st.session_state[S.K_YEAR])
    out = data.assumption_preview(sc.to_json(), a.id, int(year), ds, S.grouping_json())
    errs = [i for i in out["issues"] if i.level == "error"]
    if errs:
        st.error(" ".join(i.message for i in errs).replace("$", "\\$"))
        return
    if a.driver in ("routing_share", "owned_margin"):
        og = out.get("owned_gain")
        if og is None or og.abs().max() < 1:
            st.caption("No change to owned-asset gain in any year — check the years, pool and geography.")
            return
        fig = go.Figure(go.Bar(x=og.index, y=og.to_numpy() / 1e6, marker_color=[
            COLOR["accent"] if v >= 0 else COLOR["bad"] for v in og.to_numpy()],
            hovertemplate="%{x}: %{y:$,.1f}M<extra></extra>"))
        fig.update_layout(template=TEMPLATE_NAME, height=200, margin=dict(t=28, r=8, b=24, l=48),
                          yaxis=dict(tickprefix="$", ticksuffix="M"), xaxis=dict(dtick=1),
                          title=dict(text="Change in owned-asset gain (this assumption)", font=dict(size=12), x=0))
        st.plotly_chart(fig, config=PLOTLY_CONFIG, key=W + "preview_chart")
        st.caption("Spending and pool revenue are unchanged; only who keeps the gain moves.")
        return
    series = out.get("series")
    if series is None or series.empty or (series[["revenue", "gain"]].abs().max().max() < 1):
        st.caption("No change to revenue or gain in any year — check the years, dataset and scope.")
        return
    fig = go.Figure()
    for layer, g in series.groupby("layer_id", sort=False):
        if g["gain"].abs().max() < 1 and g["revenue"].abs().max() < 1:
            continue
        fig.add_trace(go.Scatter(x=g["year"], y=g["gain"] / 1e9, mode="lines+markers", name=meta.taxonomy[layer].short_label,
                                 line=dict(width=2, color=LAYER_COLOR.get(layer, OTHER)), marker=dict(size=5),
                                 hovertemplate="%{fullData.name} %{x}: %{y:$,.2f}B<extra></extra>"))
    fig.update_layout(template=TEMPLATE_NAME, height=220, margin=dict(t=28, r=8, b=24, l=48),
                      yaxis=dict(tickprefix="$", ticksuffix="B", zeroline=True), xaxis=dict(dtick=1),
                      title=dict(text="Change in operating gain by layer (this assumption)", font=dict(size=12), x=0))
    st.plotly_chart(fig, config=PLOTLY_CONFIG, key=W + "preview_chart")
    d = out["delta"]
    if d is not None and not d.empty:
        st.caption(f"{year}: " + " · ".join(
            f"{meta.taxonomy[l].short_label} revenue {fmt.money(r.revenue, signed=True)}, gain {fmt.money(r.gain, signed=True)}"
            for l, r in d.iterrows()).replace("$", "\\$"))
    for i in out["issues"]:
        if i.level == "warning":
            st.caption("⚠ " + i.message.replace("$", "\\$"))
