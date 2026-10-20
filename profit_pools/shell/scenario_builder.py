"""Scenario builder — a contextual dialog opened from the global scenario chip.

Edits a DRAFT scenario in session state. Nothing reaches the workspace until
"Apply scenario"; blocking validation errors disable Apply. All economics are
done by the core scenario engine — this module only edits configuration.
"""

from __future__ import annotations

import hashlib
from dataclasses import replace

import streamlit as st

from profit_pools.core.config import CONFIG_DIR, load_config
from profit_pools.core.model.scenario import Scenario, ScenarioFormatError, library_from_json, library_to_json
from profit_pools.core.model.scenario_engine import summarize
from profit_pools.core.taxonomy import Metadata
from profit_pools.shell import assumption_wizard as wizard
from profit_pools.shell import data
from profit_pools.shell import state as S

NEW = wizard.NEW


def _md(text: str) -> str:
    """Escape '$' so Streamlit markdown never renders money as LaTeX."""
    return text.replace("$", "\\$")


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
# list + footer
# ---------------------------------------------------------------------------

def _status_badge(status: str) -> str:
    """Streamlit badge markdown for a ledger status (empty when the assumption applies cleanly)."""
    return {"applied": "", "partly superseded": ":orange-badge[:material/warning: Partly superseded]",
            "superseded": ":orange-badge[:material/warning: Fully superseded]",
            "conflict": ":red-badge[:material/block: Conflict]", "invalid": ":red-badge[:material/block: Invalid]",
            "no match": ":red-badge[:material/block: Matches no data]",
            "disabled": ":gray-badge[Disabled]"}.get(status, status)


def _assumption_list(meta: Metadata) -> bool:
    """Render rows; return True when the draft has blocking errors."""
    sc = _draft()
    ss = st.session_state
    if not sc.assumptions:
        st.caption("No assumptions yet. Add one below, or load the illustrative example.")
        return False
    run = data.scenario_run(sc.to_json(), S.grouping_json())
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
                badge = _status_badge(e.status if (e and a.enabled) else ("disabled" if not a.enabled else "applied"))
                mode = " · stack" if a.mode == "stack" else ""
                st.markdown(_md(f"**{n}.** {title}{summarize(meta, a)}{mode}")
                            + (f"  \n{badge}" if badge else ""),
                            unsafe_allow_html=True)
            with c2:
                with st.container(horizontal=True, gap="small", horizontal_alignment="right"):
                    st.button("", icon=":material/arrow_upward:", key=f"pp.b.up.{a.id}", help="Move up",
                              disabled=n == 1, on_click=lambda aid=a.id: _set_draft(_draft().move(aid, -1)))
                    st.button("", icon=":material/arrow_downward:", key=f"pp.b.dn.{a.id}", help="Move down",
                              disabled=n == len(sc.assumptions),
                              on_click=lambda aid=a.id: _set_draft(_draft().move(aid, 1)))
                    st.button("", icon=":material/edit:", key=f"pp.b.ed.{a.id}", help="Edit",
                              on_click=lambda aa=a: (wizard.load(meta, aa), ss.update({S.K_EDIT: aa.id})))
                    st.button("", icon=":material/content_copy:", key=f"pp.b.cp.{a.id}", help="Duplicate",
                              on_click=lambda aid=a.id: _set_draft(_draft().duplicate(aid)))
                    st.button("", icon=":material/delete:", key=f"pp.b.rm.{a.id}", help="Delete",
                              on_click=lambda aid=a.id: _set_draft(_draft().remove(aid)))
            for i in issues:
                (st.error if i.level == "error" else st.caption)(
                    _md((":material/warning: " if i.level == "warning" else "") + i.message))
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
    wizard.load(meta, None)
    st.session_state[S.K_EDIT] = NEW


def _cb_save(meta: Metadata, editing: str) -> None:
    a = wizard.build(meta, None if editing == NEW else editing)
    sc = _draft()
    _set_draft(sc.add(a) if editing == NEW else sc.update(a))
    st.session_state[S.K_EDIT] = None


def _cb_apply() -> None:
    ss = st.session_state
    ss[S.K_APPLIED] = _draft()
    if _draft().assumptions:
        S.save_to_library(_draft())           # applied scenarios are always available for comparison
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


# --- library -----------------------------------------------------------------------------------------

def _lib_key(name: str) -> str:
    return hashlib.sha1(name.encode()).hexdigest()[:8]


def _cb_lib_load(name: str) -> None:
    sc = next(s for s in S.library() if s.name == name)
    _replace_draft(sc)
    st.session_state[S.K_EDIT] = None


def _cb_lib_delete(name: str) -> None:
    st.session_state[S.K_LIBRARY] = [s for s in S.library() if s.name != name]
    st.session_state[S.K_COMPARE] = [n for n in st.session_state.get(S.K_COMPARE, []) if n != name]


def _cb_lib_save() -> None:
    sc = _draft()
    replaced = any(s.name == sc.name for s in S.library())
    S.save_to_library(sc)
    st.session_state["pp.b.lib_msg"] = ("ok", f"{'Updated' if replaced else 'Saved'} “{sc.name}” in the library.")


def _cb_lib_import() -> None:
    ss = st.session_state
    up = ss.get("pp.b.lib_import")
    if up is None:
        return
    try:
        items = library_from_json(up.getvalue().decode("utf-8"))
    except (ScenarioFormatError, UnicodeDecodeError) as e:
        ss["pp.b.lib_msg"] = ("error", f"Could not import: {e}")
        return
    for sc in items:
        S.save_to_library(sc)
    ss["pp.b.lib_msg"] = ("ok", f"Added {len(items)} scenario{'s' if len(items) != 1 else ''} to the library.")


def _library() -> None:
    ss = st.session_state
    lib = S.library()
    with st.expander(f"Scenario library ({len(lib)})", icon=":material/folder_open:"):
        st.caption("Saved for this session; compare them side by side at the bottom of the page. Download the "
                   "library to keep it and upload it next time. Configuration only, no data.")
        for sc in lib:
            k = _lib_key(sc.name)
            c1, c2 = st.columns([6, 2.4], vertical_alignment="center")
            with c1:
                n = len(sc.enabled)
                st.markdown(_md(f"**{sc.name}** · {n} assumption{'s' if n != 1 else ''}"
                                + (f"  \n<span class='pp-muted'>{sc.description}</span>" if sc.description else "")),
                            unsafe_allow_html=True)
            with c2:
                with st.container(horizontal=True, gap="small", horizontal_alignment="right"):
                    st.button("Load", key=f"pp.b.lib.load.{k}", on_click=_cb_lib_load, args=(sc.name,),
                              help="Replace the draft with this scenario.")
                    st.button("", icon=":material/delete:", key=f"pp.b.lib.del.{k}", on_click=_cb_lib_delete,
                              args=(sc.name,), help="Remove from the library.")
        with st.container(horizontal=True, gap="small", vertical_alignment="center"):
            st.button("Save draft to library", key="pp.b.lib.save", icon=":material/bookmark_add:",
                      on_click=_cb_lib_save, disabled=not _draft().assumptions,
                      help="Saves under the scenario name (replaces a saved scenario with the same name)."
                      if _draft().assumptions else "Add at least one assumption first.")
            st.download_button("Download library", data=library_to_json(lib), file_name="scenario_library.json",
                               mime="application/json", key="pp.b.lib.dl", on_click="ignore", disabled=not lib)
        st.file_uploader("Upload a library or scenario file", type=["json"], key="pp.b.lib_import",
                         on_change=_cb_lib_import)
        msg = ss.pop("pp.b.lib_msg", None)
        if msg:
            (st.success if msg[0] == "ok" else st.error)(msg[1])


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
            wizard.render(meta, editing, _cb_save)
    else:
        a1, a2, _ = st.columns([1.3, 1.6, 3])
        with a1:
            st.button("Add assumption", icon=":material/add:", key="pp.b.add", width="stretch",
                      on_click=_cb_new, args=(meta,))
        with a2:
            st.button("Load illustrative example", key="pp.b.example", width="stretch", on_click=_cb_example)

    _library()
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
