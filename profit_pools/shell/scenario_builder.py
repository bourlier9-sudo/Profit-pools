"""Scenario builder — a contextual dialog opened from the global scenario chip.

Edits a DRAFT scenario in session state. Nothing reaches the workspace until
"Apply scenario"; blocking validation errors disable Apply. All economics are
done by the core scenario engine — this module only edits configuration.
"""

from __future__ import annotations

from dataclasses import replace

import streamlit as st

from profit_pools.core.config import CONFIG_DIR, load_config
from profit_pools.core.model.scenario import Assumption, Scenario, ScenarioFormatError, Scope, YearRange
from profit_pools.core.model.scenario_engine import driver_spec, is_growth, summarize
from profit_pools.core.taxonomy import Metadata
from profit_pools.design import format as fmt
from profit_pools.shell import data
from profit_pools.shell import state as S

NEW = "__new__"
ALL = "__all__"   # selectbox sentinel for "every eligible pool" (None would render as a placeholder)


def _md(text: str) -> str:
    """Escape '$' so Streamlit markdown never renders money as LaTeX."""
    return text.replace("$", "\\$")


DRIVER_GROUPS: list[tuple[str, list[str]]] = [
    ("Enrollment & coverage", ["enrollment", "enrollment_shift", "funding_population"]),
    ("Payer economics", ["premium_pmpm", "opex_pmpm", "opex_volume_scaling", "payer_margin"]),
    ("Spending & volume", ["allowed_pmpm", "funding_pmpm", "utilization", "unit_price", "member_cost_share",
                           "supplemental_payments"]),
    ("Margins", ["provider_margin"]),
    ("Top-down pools", ["top_down_revenue", "top_down_margin"]),
    ("Growth (forecast years)", ["enrollment_growth", "premium_trend", "opex_trend", "utilization_trend",
                                 "unit_cost_trend", "population_growth", "supplemental_growth", "top_down_growth"]),
]
METHOD_LABELS = {"pct": "% change", "pp": "pp change", "set": "Set to", "delta": "Add"}
F = "pp.b."   # widget key prefix for the assumption form


def _draft() -> Scenario:
    return st.session_state[S.K_DRAFT]


def _set_draft(sc: Scenario) -> None:
    st.session_state[S.K_DRAFT] = sc


def _replace_draft(sc: Scenario) -> None:
    """Swap in a whole new draft (example, import, discard, reset) and resync the header fields."""
    _set_draft(sc)
    st.session_state["pp.b.name"] = sc.name
    st.session_state["pp.b.desc"] = sc.description


# ---------------------------------------------------------------------------
# option builders
# ---------------------------------------------------------------------------

def _node_options(meta: Metadata, driver: str, dataset: str | None) -> list[str | None]:
    kind = driver_spec(driver).get("scope_kind")
    # Supplemental payments reach hospital leaves only: offer the hospital subtree and its ancestors.
    hosp_nodes = {a.id for leaf in meta.taxonomy.leaves("PRV_HOSP") for a in meta.taxonomy.path(leaf.id)}
    out: list[str] = [ALL]
    for n in meta.taxonomy:
        if dataset and not meta.in_dataset(n.id, dataset):
            continue
        leaves = meta.taxonomy.leaves(n.id)
        if kind == "payer" and all(l.economics == "payer_pnl" for l in leaves):
            out.append(n.id)
        elif kind in ("spending", "recipient") and any(l.receives_flows for l in leaves):
            if driver in ("utilization", "unit_price", "utilization_trend") and not any(
                    c.has_instances for c in meta.categories.values() if c.target in {l.id for l in leaves}):
                continue
            out.append(n.id)
        elif kind == "top_down" and any(l.economics == "top_down" for l in leaves):
            out.append(n.id)
        elif driver in ("supplemental_payments", "supplemental_growth") and n.id in hosp_nodes:
            out.append(n.id)
    seen, uniq = set(), []
    for o in out:
        if o not in seen:
            uniq.append(o)
            seen.add(o)
    return uniq


def _source_options(meta: Metadata, driver: str) -> list[str]:
    lobs = [*meta.lobs.groups, *meta.lobs.lobs]
    pop = [f.id for f in meta.funding_sources.values() if f.basis == "population_pmpm"]
    if driver in ("funding_pmpm", "funding_population", "population_growth"):
        return [*meta.funding_groups, *pop]
    if driver in ("supplemental_payments", "supplemental_growth"):
        return [f.id for f in meta.funding_sources.values() if f.basis == "lump_sum"]
    if driver == "allowed_pmpm":
        return lobs
    if driver in ("utilization", "unit_price", "member_cost_share", "utilization_trend", "unit_cost_trend"):
        return [*lobs, *meta.funding_groups, *pop]
    return []


def _source_label(meta: Metadata, s: str) -> str:
    if s in meta.lobs.groups:
        return f"{meta.lobs.groups[s]} (all)"
    if s in meta.lobs.lobs:
        return f"   {meta.lobs.lobs[s].label}"
    if s in meta.funding_groups:
        return f"{meta.funding_groups[s]} (all)"
    return meta.funding_sources[s].label


def _geo_options(meta: Metadata) -> list[str]:
    out = []
    for st_ in meta.geography.states.values():
        out.append(st_.id)
        out += [c for c, _, _ in st_.counties]
    return out


def _geo_label(meta: Metadata, g: str) -> str:
    return meta.geography.states[g].label if g in meta.geography.states else f"   {meta.geography.county_label(g)}"


def _node_label(meta: Metadata, n: str | None) -> str:
    if n in (None, ALL):
        return "All eligible pools"
    node = meta.taxonomy[n]
    return ("    " * node.depth) + node.label


def _unit_hint(driver: str, method: str) -> tuple[str, float]:
    """(suffix shown to the user, multiplier from entered value to stored value)."""
    unit = driver_spec(driver).get("unit")
    if method == "pct":
        return "%", 0.01
    if method == "pp":
        return "pp", 0.01
    if unit in ("ratio", "rate"):
        return "%", 0.01
    if unit in ("usd_pmpm", "usd_per_instance"):
        return "$ PMPM" if unit == "usd_pmpm" else "$ per instance", 1.0
    if unit == "usd_annual":
        return "$ millions / year", 1e6
    if unit == "members_avg":
        return "members", 1.0
    return "", 1.0


# ---------------------------------------------------------------------------
# form
# ---------------------------------------------------------------------------

def _load_form(meta: Metadata, a: Assumption | None) -> None:
    p = meta.periods
    a = a or Assumption("enrollment", "pct", 0.05, Scope(YearRange(p.base_year + 1, None)))
    ss = st.session_state
    ss[F + "driver"] = a.driver
    ss[F + "dataset"] = a.scope.dataset or "BOTH"
    ss[F + "node"] = a.scope.node_id or ALL
    ss[F + "sources"] = list(a.scope.sources)
    ss[F + "geo"] = list(a.scope.geography)
    ss[F + "start"] = a.scope.years.start
    ss[F + "end"] = a.scope.years.end if a.scope.years.end is not None else "onward"
    ss[F + "method"] = a.method
    _, mult = _unit_hint(a.driver, a.method)
    ss[F + "value"] = float(a.value / mult)
    ss[F + "mode"] = a.mode
    ss[F + "target"] = a.target
    ss[F + "label"] = a.label
    ss[F + "notes"] = a.notes


def _form_assumption(meta: Metadata, base_id: str | None) -> Assumption:
    ss = st.session_state
    driver, method = ss[F + "driver"], ss[F + "method"]
    _, mult = _unit_hint(driver, method)
    end = ss[F + "end"]
    scope = Scope(
        years=YearRange(int(ss[F + "start"]), None if end == "onward" else int(end)),
        dataset=None if ss[F + "dataset"] == "BOTH" else ss[F + "dataset"],
        node_id=None if ss.get(F + "node") in (None, ALL) else ss.get(F + "node"),
        sources=tuple(ss.get(F + "sources") or ()),
        geography=tuple(ss.get(F + "geo") or ()),
    )
    kw = dict(driver=driver, method=method, value=float(ss[F + "value"]) * mult, scope=scope,
              mode=ss[F + "mode"], target=ss.get(F + "target") if driver == "enrollment_shift" else None,
              label=ss.get(F + "label", "").strip(), notes=ss.get(F + "notes", "").strip())
    return Assumption(**kw, id=base_id) if base_id else Assumption(**kw)


def _on_driver_change(meta: Metadata) -> None:
    ss = st.session_state
    spec = driver_spec(ss[F + "driver"])
    if ss[F + "method"] not in spec["methods"]:
        ss[F + "method"] = spec["methods"][0]
    if ss.get(F + "node") not in _node_options(meta, ss[F + "driver"], None):
        ss[F + "node"] = ALL
    ss[F + "sources"] = [s for s in ss.get(F + "sources", []) if s in _source_options(meta, ss[F + "driver"])]
    if is_growth(ss[F + "driver"]) and ss[F + "start"] <= meta.periods.base_year:
        ss[F + "start"] = meta.periods.base_year + 1
    if spec.get("scope_kind") in ("funding", "top_down") or ss[F + "driver"] in ("funding_pmpm",):
        ss[F + "dataset"] = "MARKET"


def _assumption_form(meta: Metadata, editing: str) -> None:
    ss = st.session_state
    p = meta.periods
    drivers = [d for _, ds in DRIVER_GROUPS for d in ds]
    group_of = {d: g for g, ds in DRIVER_GROUPS for d in ds}
    st.markdown(f'<div class="pp-h3">{"New assumption" if editing == NEW else "Edit assumption"}</div>',
                unsafe_allow_html=True)
    c1, c2 = st.columns([3, 2])
    with c1:
        st.selectbox("What changes", drivers, key=F + "driver",
                     format_func=lambda d: f"{group_of[d]} · {driver_spec(d)['label']}",
                     on_change=_on_driver_change, args=(meta,))
    driver = ss[F + "driver"]
    spec = driver_spec(driver)
    kind = spec.get("scope_kind")
    with c2:
        locked = kind in ("funding", "top_down") or driver == "funding_pmpm"
        st.segmented_control("Dataset", ["BOTH", *meta.datasets], key=F + "dataset", required=True,
                             format_func=lambda d: "Both" if d == "BOTH" else meta.datasets[d].short_label,
                             disabled=locked, help="Total-market and enterprise drivers are independent inputs. "
                                                   "Funding sources and top-down pools exist only in the market.")
    if spec.get("note"):
        st.caption(spec["note"])

    ds = None if ss[F + "dataset"] == "BOTH" else ss[F + "dataset"]
    c1, c2 = st.columns(2)
    with c1:
        if kind != "funding" or driver in ("supplemental_payments", "supplemental_growth"):
            opts = _node_options(meta, driver, ds)
            if ss.get(F + "node") not in opts:
                ss[F + "node"] = ALL
            st.selectbox("Pool (any level)", opts, key=F + "node", format_func=lambda n: _node_label(meta, n),
                         help="Applies to every descendant of the chosen pool. More specific assumptions replace "
                              "broader ones on the cells they share.")
        else:
            ss[F + "node"] = ALL
    with c2:
        src = _source_options(meta, driver)
        if src:
            ss[F + "sources"] = [s for s in ss.get(F + "sources", []) if s in src]
            st.multiselect("Paid by (payer LOB / funding source)", src, key=F + "sources",
                           format_func=lambda s: _source_label(meta, s),
                           placeholder="All" if kind != "funding" else "Choose…")
        else:
            ss[F + "sources"] = []
    c1, c2, c3 = st.columns([2, 1, 1])
    with c1:
        if kind != "top_down" and driver != "top_down_growth":
            st.multiselect("Geography", _geo_options(meta), key=F + "geo", format_func=lambda g: _geo_label(meta, g),
                           placeholder="National (all)")
        else:
            ss[F + "geo"] = []
    years = p.forecast_years if is_growth(driver) else p.all_years
    with c2:
        if ss[F + "start"] not in years:
            ss[F + "start"] = years[0]
        st.selectbox("From", years, key=F + "start")
    with c3:
        ends = ["onward", *[y for y in p.all_years if y >= ss[F + "start"]]]
        if ss[F + "end"] not in ends:
            ss[F + "end"] = "onward"
        st.selectbox("Through", ends, key=F + "end", format_func=lambda e: "Onward" if e == "onward" else str(e))

    c1, c2, c3 = st.columns([2, 1.4, 1.6])
    with c1:
        if ss[F + "method"] not in spec["methods"]:
            ss[F + "method"] = spec["methods"][0]
        st.segmented_control("Method", spec["methods"], key=F + "method", required=True,
                             format_func=METHOD_LABELS.get)
    suffix, _ = _unit_hint(driver, ss[F + "method"])
    with c2:
        st.number_input(f"Value ({suffix})" if suffix else "Value", key=F + "value", step=0.5, format="%.2f")
    with c3:
        st.segmented_control("If it overlaps", ["replace", "stack"], key=F + "mode", required=True,
                             format_func={"replace": "Replace", "stack": "Stack"}.get,
                             help="Replace (default): overlapping assumptions on the same driver conflict — the "
                                  "more specific one wins; equally specific ones must be resolved. "
                                  "Stack: compounds with whatever else applies.")
    if driver == "enrollment_shift":
        targets = [n.id for n in meta.taxonomy.leaves("PAYER")] + [
            f.id for f in meta.funding_sources.values() if f.basis == "population_pmpm"]
        if ss.get(F + "target") not in targets:
            ss[F + "target"] = "FS_UNINSURED"
        st.selectbox("Members move to", targets, key=F + "target",
                     format_func=lambda t: meta.taxonomy[t].label if t in meta.taxonomy else
                     meta.funding_sources[t].label)
    c1, c2 = st.columns([1, 2])
    with c1:
        st.text_input("Label (optional)", key=F + "label")
    with c2:
        st.text_input("Notes", key=F + "notes")

    draft_a = _form_assumption(meta, None if editing == NEW else editing)
    st.caption(_md("Summary: " + summarize(meta, draft_a)))
    b1, b2, b3, _ = st.columns([1.2, 1.2, 1, 2])
    with b1:
        st.button("Save assumption" if editing != NEW else "Add assumption", type="primary", key=F + "save",
                  width="stretch", on_click=_cb_save, args=(meta, editing))
    with b2:
        if st.button("Preview impact", key=F + "preview", width="stretch"):
            ss[F + "show_preview"] = True
    with b3:
        st.button("Cancel", key=F + "cancel", width="stretch", on_click=lambda: ss.update({S.K_EDIT: None}))
    if ss.get(F + "show_preview"):
        _preview(meta, draft_a, editing)


def _cb_save(meta: Metadata, editing: str) -> None:
    a = _form_assumption(meta, None if editing == NEW else editing)
    sc = _draft()
    _set_draft(sc.add(a) if editing == NEW else sc.update(a))
    st.session_state[S.K_EDIT] = None


def _preview(meta: Metadata, a: Assumption, editing: str) -> None:
    sc = _draft()
    sc = sc.add(a) if editing == NEW else sc.update(a)
    year = a.scope.years.start if a.scope.years.start > meta.periods.base_year else st.session_state[S.K_YEAR]
    ds = a.scope.dataset or st.session_state[S.K_DATASET]
    out = data.assumption_preview(sc.to_json(), a.id, int(year), ds)
    errs = [i for i in out["issues"] if i.level == "error"]
    if errs:
        st.error(" ".join(i.message for i in errs))
        return
    led = out["ledger"]
    parts = [f"Preview for {year}, {meta.datasets[ds].short_label}"]
    if led:
        parts.append(f"{led.effective_cells:,} of {led.matched_cells:,} matched cells governed by this assumption")
    if out["leaves"]:
        parts.append("pools: " + ", ".join(out["leaves"]) + ("…" if len(out["leaves"]) == 12 else ""))
    st.caption(_md(" · ".join(parts)))
    d = out["delta"]
    if d is None or d.empty:
        st.caption("No change to revenue or gain in this year (check the year range).")
    else:
        rows = [f"**{meta.taxonomy[l].label}** revenue {fmt.money(r.revenue, signed=True)}, "
                f"gain {fmt.money(r.gain, signed=True)}" for l, r in d.iterrows()]
        st.markdown(_md(" · ".join(rows)))
    for i in out["issues"]:
        if i.level == "warning":
            st.caption("⚠ " + i.message)


# ---------------------------------------------------------------------------
# list + footer
# ---------------------------------------------------------------------------

def _status_badge(status: str) -> str:
    return {"applied": "", "partly superseded": "⚠ partly superseded", "superseded": "⚠ fully superseded",
            "conflict": "⛔ conflict", "invalid": "⛔ invalid", "no match": "⛔ matches no data"}.get(status, status)


def _assumption_list(meta: Metadata) -> bool:
    """Render rows; return True when the draft has blocking errors."""
    sc = _draft()
    ss = st.session_state
    if not sc.assumptions:
        st.caption("No assumptions yet. Add one below, or load the illustrative example.")
        return False
    run = data.scenario_run(sc.to_json())
    ledger = {e.assumption_id: e for e in run.ledger}
    blocking = any(i.level == "error" for i in run.issues)
    for n, a in enumerate(sc.assumptions, start=1):
        e = ledger.get(a.id)
        issues = run.issues_for(a.id)
        with st.container(border=True, key=f"pp.b.row.{a.id}"):
            c0, c1, c2 = st.columns([0.6, 6.4, 3.2], vertical_alignment="center")
            with c0:
                ss[f"pp.b.en.{a.id}"] = a.enabled
                st.toggle(" ", key=f"pp.b.en.{a.id}", label_visibility="collapsed",
                          on_change=lambda aid=a.id: _set_draft(_draft().toggle(aid, ss[f"pp.b.en.{aid}"])),
                          help="Enable / disable")
            with c1:
                title = (a.label + " — ") if a.label else ""
                badge = _status_badge(e.status) if (e and a.enabled) else ("disabled" if not a.enabled else "")
                mode = " · stack" if a.mode == "stack" else ""
                st.markdown(_md(f"**{n}.** {title}{summarize(meta, a)}{mode}")
                            + (f"  \n<span class='pp-muted'>{badge}</span>" if badge else ""),
                            unsafe_allow_html=True)
            with c2:
                with st.container(horizontal=True, gap="small", horizontal_alignment="right"):
                    st.button("", icon=":material/arrow_upward:", key=f"pp.b.up.{a.id}", help="Move up",
                              disabled=n == 1, on_click=lambda aid=a.id: _set_draft(_draft().move(aid, -1)))
                    st.button("", icon=":material/arrow_downward:", key=f"pp.b.dn.{a.id}", help="Move down",
                              disabled=n == len(sc.assumptions),
                              on_click=lambda aid=a.id: _set_draft(_draft().move(aid, 1)))
                    st.button("", icon=":material/edit:", key=f"pp.b.ed.{a.id}", help="Edit",
                              on_click=lambda aa=a: (_load_form(meta, aa), ss.update({S.K_EDIT: aa.id,
                                                                                        F + "show_preview": False})))
                    st.button("", icon=":material/content_copy:", key=f"pp.b.cp.{a.id}", help="Duplicate",
                              on_click=lambda aid=a.id: _set_draft(_draft().duplicate(aid)))
                    st.button("", icon=":material/delete:", key=f"pp.b.rm.{a.id}", help="Delete",
                              on_click=lambda aid=a.id: _set_draft(_draft().remove(aid)))
            for i in issues:
                (st.error if i.level == "error" else st.caption)(_md(("⚠ " if i.level == "warning" else "") + i.message))
            if a.notes:
                st.caption(_md(a.notes))
    general = [i for i in run.issues if i.assumption_id is None]
    for i in general:
        (st.error if i.level == "error" else st.caption)(i.message)
    return blocking


# --- callbacks: run before any widget is drawn, so they may reset widget state --------------

def _cb_name() -> None:
    ss = st.session_state
    _set_draft(replace(_draft(), name=ss["pp.b.name"] or "Untitled scenario", description=ss["pp.b.desc"]))


def _cb_example() -> None:
    _replace_draft(Scenario.from_json((CONFIG_DIR / "scenarios" / "illustrative.json").read_text()))
    st.session_state[S.K_EDIT] = None


def _cb_new(meta: Metadata) -> None:
    _load_form(meta, None)
    st.session_state[S.K_EDIT] = NEW
    st.session_state[F + "show_preview"] = False


def _cb_apply() -> None:
    ss = st.session_state
    ss[S.K_APPLIED] = _draft()
    ss[S.K_VIEW] = "compare" if _draft().enabled else "baseline"
    ss[S.K_EDIT] = None
    ss["pp.b.close"] = True


def _cb_discard(meta: Metadata) -> None:
    ap = S.applied()
    _replace_draft(ap if ap is not None else Scenario("Scenario A", base_year=meta.periods.base_year))
    st.session_state[S.K_EDIT] = None


def _cb_reset(meta: Metadata) -> None:
    ss = st.session_state
    ss[S.K_APPLIED] = None
    ss[S.K_VIEW] = "baseline"
    _replace_draft(Scenario("Scenario A", base_year=meta.periods.base_year))
    ss[S.K_EDIT] = None
    ss["pp.b.close"] = True


def _cb_import() -> None:
    ss = st.session_state
    up = ss.get("pp.b.import")
    if up is None:
        return
    try:
        imported = Scenario.from_json(up.getvalue().decode("utf-8"))
        _replace_draft(imported)
        ss["pp.b.import_msg"] = ("ok", f"Imported “{imported.name}” ({len(imported.assumptions)} assumptions) "
                                       f"into the draft.")
    except (ScenarioFormatError, UnicodeDecodeError) as e:
        ss["pp.b.import_msg"] = ("error", f"Could not import: {e}")


@st.dialog("Scenario builder", width="large")
def open_builder(meta: Metadata) -> None:
    ss = st.session_state
    if ss.pop("pp.b.close", False):
        st.rerun()  # full-app rerun closes the dialog and refreshes every chart
    sc = _draft()
    ss.setdefault("pp.b.name", sc.name)
    ss.setdefault("pp.b.desc", sc.description)
    c1, c2 = st.columns([1, 2])
    with c1:
        st.text_input("Scenario name", key="pp.b.name", on_change=_cb_name)
    with c2:
        st.text_input("Description", key="pp.b.desc", on_change=_cb_name)
    st.caption("Assumptions apply in order to drivers (enrollment, PMPMs, margins, growth). "
               "Every chart reads the same engine output. Overlaps conflict by default; the more "
               "specific scope wins and conflicts are flagged.")

    blocking = _assumption_list(meta)

    editing = ss.get(S.K_EDIT)
    if editing:
        with st.container(border=True):
            _assumption_form(meta, editing)
    else:
        a1, a2, _ = st.columns([1.3, 1.6, 3])
        with a1:
            st.button("Add assumption", icon=":material/add:", key="pp.b.add", width="stretch",
                      on_click=_cb_new, args=(meta,))
        with a2:
            st.button("Load illustrative example", key="pp.b.example", width="stretch", on_click=_cb_example)

    text = load_config("methodology")
    with st.expander(text["provider_revenue"]["title"]):
        st.markdown(text["provider_revenue"][ss[S.K_DATASET]])
        st.markdown(text["provider_revenue"]["volume"])
    with st.expander(text["payer_economics"]["title"]):
        st.markdown(text["payer_economics"]["body"])

    st.divider()
    dirty = S.draft_dirty()
    f1, f2, f3, f4 = st.columns(4)
    with f1:
        st.button("Apply scenario", type="primary", key="pp.b.apply", width="stretch", on_click=_cb_apply,
                  disabled=blocking or (not dirty and S.applied() is not None),
                  help="Blocking issues must be resolved first." if blocking else None)
    with f2:
        st.button("Discard edits", key="pp.b.discard", width="stretch", disabled=not dirty,
                  on_click=_cb_discard, args=(meta,))
    with f3:
        st.button("Reset to baseline", key="pp.b.reset", width="stretch", on_click=_cb_reset, args=(meta,),
                  disabled=S.applied() is None and not _draft().assumptions)
    with f4:
        st.download_button("Export JSON", data=_draft().to_json(), file_name=_filename(_draft()),
                           mime="application/json", key="pp.b.export", width="stretch", on_click="ignore",
                           help="Assumptions and configuration only — no data.")
    st.file_uploader("Import scenario JSON", type=["json"], key="pp.b.import", on_change=_cb_import,
                     help="Replaces the draft. Checked for schema version and structure.")
    msg = ss.pop("pp.b.import_msg", None)
    if msg:
        (st.success if msg[0] == "ok" else st.error)(msg[1])
    if dirty:
        st.caption("Unapplied edits — the workspace still shows the last applied scenario.")


def _filename(sc: Scenario) -> str:
    safe = "".join(ch if ch.isalnum() else "_" for ch in sc.name).strip("_").lower() or "scenario"
    return f"{safe}.json"


def chip_label() -> str:
    ap = S.applied()
    dirty = S.draft_dirty()
    if ap is None or not ap.enabled:
        base = "Baseline · build a scenario"
    else:
        n = len(ap.enabled)
        base = f"{ap.name} · {n} change{'s' if n != 1 else ''}"
    return base + (" · unapplied edits" if dirty else "")
