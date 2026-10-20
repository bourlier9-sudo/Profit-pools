"""Pool groupings — a dialog, separate from the scenario builder, to rename pools, move them under another
group, and add groups. Edits a DRAFT grouping; nothing changes until "Apply groupings".

A grouping only changes the hierarchy shown (roll-ups, labels). Values are identical under any grouping
(core/grouping.py). Invalid moves are refused with the reason and never reach the draft.
"""

from __future__ import annotations

from dataclasses import replace

import streamlit as st

from profit_pools.core.grouping import Grouping, apply, empty_groups
from profit_pools.core.taxonomy import Metadata, TaxonomyError
from profit_pools.shell import state as S

G = "pp.g."


def _draft() -> Grouping:
    return st.session_state[S.K_GROUPING_DRAFT]


def _try(g: Grouping, meta: Metadata) -> None:
    """Accept the edit only if the resulting taxonomy is valid."""
    try:
        apply(meta.standard, g)
    except TaxonomyError as e:
        st.session_state[G + "msg"] = ("error", str(e))
        return
    st.session_state[S.K_GROUPING_DRAFT] = g
    st.session_state.pop(G + "msg", None)


def _cb_rename(meta: Metadata, nid: str) -> None:
    label = (st.session_state[G + "lab." + nid] or "").strip()
    if label:
        _try(_draft().rename(nid, label), meta)


def _cb_move(meta: Metadata, nid: str) -> None:
    _try(_draft().move(nid, st.session_state[G + "mv." + nid]), meta)


def _cb_add(meta: Metadata) -> None:
    ss = st.session_state
    label = (ss.get(G + "new_label") or "").strip()
    if not label:
        ss[G + "msg"] = ("error", "Give the new group a name.")
        return
    g, _ = _draft().add_group(label, ss[G + "new_parent"])
    _try(g, meta)
    ss[G + "new_label"] = ""


def _cb_name() -> None:
    st.session_state[S.K_GROUPING_DRAFT] = replace(_draft(), name=st.session_state[G + "name"] or "Custom")


def _cb_apply() -> None:
    ss = st.session_state
    ss[S.K_GROUPING] = _draft()
    S.set_focus(None)          # pool ids in focus may no longer exist
    ss[G + "close"] = True


def _cb_discard() -> None:
    st.session_state[S.K_GROUPING_DRAFT] = st.session_state[S.K_GROUPING]
    st.session_state[G + "name"] = st.session_state[S.K_GROUPING].name
    st.session_state.pop(G + "msg", None)


def _cb_reset() -> None:
    st.session_state[S.K_GROUPING_DRAFT] = Grouping()
    st.session_state[G + "name"] = "Standard"
    st.session_state.pop(G + "msg", None)


def _cb_import(meta: Metadata) -> None:
    ss = st.session_state
    up = ss.get(G + "import")
    if up is None:
        return
    try:
        g = Grouping.from_json(up.getvalue().decode("utf-8"))
        apply(meta.standard, g)
    except (ValueError, TaxonomyError, UnicodeDecodeError) as e:
        ss[G + "msg"] = ("error", f"Could not import: {e}")
        return
    ss[S.K_GROUPING_DRAFT] = g
    ss[G + "name"] = g.name
    ss[G + "msg"] = ("ok", f"Imported “{g.name}” into the draft.")


def parent_options(tax, standard, nid: str, empties: list[tuple[str, str, str, str]] = ()) -> list[str]:
    """Groups a node may move under: same layer kind (payer segments stay in Payer), never itself, its own
    descendants, or a pool that holds data. Empty new groups count as groups."""
    node = tax[nid]
    below = {n.id for n in tax.leaves(nid)} | _descendants(tax, nid)
    is_payer = node.layer_id == "PAYER"
    out = []
    for n in tax:
        if n.id == nid or n.id in below or (n.id in standard and not standard[n.id].children):
            continue
        if (n.layer_id == "PAYER") != is_payer:
            continue
        out.append(n.id)
    out += [gid for gid, _, _, layer in empties if (layer == "PAYER") == is_payer]
    return out


def _descendants(tax, nid: str) -> set[str]:
    out, stack = set(), [c for c in tax[nid].children]
    while stack:
        c = stack.pop()
        out.add(c)
        stack += list(tax[c].children)
    return out


@st.dialog("Pool groupings", width="large")
def open_editor(meta: Metadata) -> None:
    ss = st.session_state
    if ss.pop(G + "close", False):
        st.rerun()
    draft = _draft()
    try:
        tax, warnings = apply(meta.standard, draft)
    except TaxonomyError as e:                      # an imported or stale draft; offer a way out
        st.error(f"This grouping is not valid: {e}")
        st.button("Reset to standard", key=G + "reset_bad", on_click=_cb_reset)
        return
    std = meta.standard
    ss.setdefault(G + "name", draft.name)
    st.text_input("Grouping name", key=G + "name", on_change=_cb_name)
    st.caption("Rename pools, move them under another group, or add your own groups. Groupings change how "
               "pools roll up in every chart; the numbers themselves never change. Pools that hold data "
               "cannot contain other pools, and plan segments stay in the Payer layer.")

    layers = tax.layer_ids
    ss.setdefault(G + "layer", layers[1] if len(layers) > 1 else layers[0])
    st.segmented_control("Layer", layers, key=G + "layer", required=True, format_func=lambda l: tax[l].short_label)
    layer = ss[G + "layer"]

    empties = empty_groups(tax, draft)
    empty_label = {gid: label for gid, label, _, _ in empties}

    def label_of(n):
        return tax[n].label if n in tax else empty_label[n] + " (empty)"

    rows = [n for n in tax if n.layer_id == layer and n.id != layer]
    with st.container(height=420, border=False):
        for n in rows:
            leaf = n.id in std and not std[n.id].children
            ss[G + "lab." + n.id] = n.label
            ss[G + "mv." + n.id] = n.parent_id
            c0, c1, c2 = st.columns([0.3 + 0.3 * (n.depth - 1), 4, 3.2], vertical_alignment="center", gap="small")
            with c0:
                st.markdown("<div class='pp-muted' style='text-align:right'>" + ("•" if leaf else "▸") + "</div>",
                            unsafe_allow_html=True)
            with c1:
                st.text_input("Name", key=G + "lab." + n.id, label_visibility="collapsed",
                              on_change=_cb_rename, args=(meta, n.id),
                              help="Holds data" if leaf else "Group")
            with c2:
                opts = parent_options(tax, std, n.id, empties)
                st.selectbox("Move to", opts, key=G + "mv." + n.id, label_visibility="collapsed",
                             format_func=lambda p: "Under " + label_of(p), on_change=_cb_move, args=(meta, n.id))
        for gid, label, parent, lay in empties:
            if lay != layer:
                continue
            ss[G + "lab." + gid] = label
            c0, c1, c2 = st.columns([0.3, 4, 3.2], vertical_alignment="center", gap="small")
            with c0:
                st.markdown("<div class='pp-muted' style='text-align:right'>▹</div>", unsafe_allow_html=True)
            with c1:
                st.text_input("Name", key=G + "lab." + gid, label_visibility="collapsed",
                              on_change=_cb_rename, args=(meta, gid))
            with c2:
                st.caption(f"New, empty · inside {label_of(parent)}. Move pools into it.")
    st.caption("• holds data · ▸ group · ▹ new empty group. Indentation shows the level.")

    a1, a2, a3 = st.columns([2.2, 2.2, 1.2], vertical_alignment="bottom")
    groups = [n.id for n in tax if n.layer_id == layer and not (n.id in std and not std[n.id].children)]
    groups += [gid for gid, _, _, lay in empties if lay == layer]
    if ss.get(G + "new_parent") not in groups:
        ss[G + "new_parent"] = layer
    with a1:
        st.text_input("New group", key=G + "new_label", placeholder="e.g. Ambulatory & virtual care")
    with a2:
        st.selectbox("Inside", groups, key=G + "new_parent", format_func=label_of)
    with a3:
        st.button("Add group", key=G + "add", icon=":material/add:", width="stretch", on_click=_cb_add, args=(meta,))

    msg = ss.get(G + "msg")
    if msg:
        (st.success if msg[0] == "ok" else st.error)(msg[1])
    for w in warnings:
        st.caption(":material/info: " + w)

    st.divider()
    applied = ss[S.K_GROUPING]
    dirty = draft.to_json() != applied.to_json()
    f1, f2, f3, f4 = st.columns(4)
    with f1:
        st.button("Apply groupings", type="primary", key=G + "apply", width="stretch", on_click=_cb_apply,
                  disabled=not dirty)
    with f2:
        st.button("Discard edits", key=G + "discard", width="stretch", on_click=_cb_discard, disabled=not dirty)
    with f3:
        st.button("Reset to standard", key=G + "reset", width="stretch", on_click=_cb_reset,
                  disabled=draft.is_standard)
    with f4:
        st.download_button("Export JSON", data=draft.to_json(), file_name="pool_grouping.json",
                           mime="application/json", key=G + "export", width="stretch", on_click="ignore",
                           help="Grouping only (renames, moves, groups) — no data.")
    st.file_uploader("Import grouping JSON", type=["json"], key=G + "import", on_change=_cb_import, args=(meta,))
    if dirty:
        st.caption("Unapplied edits — the workspace still shows the last applied grouping.")


def button_label() -> str:
    g = st.session_state.get(S.K_GROUPING)
    return "Pools: standard" if g is None or g.is_standard else f"Pools: {g.name}"
