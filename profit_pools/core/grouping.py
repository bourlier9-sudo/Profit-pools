"""User pool groupings: rename, move and add groups on top of the standard taxonomy.

A grouping is a small overlay (JSON) — never a change to data or config. Leaves keep
their ids and economics, so every engine result is identical under any grouping;
only roll-ups, labels and the hierarchy shown in charts change.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, replace
from typing import Any

from profit_pools.core.taxonomy import Metadata, Taxonomy, TaxonomyError

SCHEMA_VERSION = 1
_ATTRS = ("economics", "coverage", "geo_level", "receives_flows")


@dataclass(frozen=True)
class Grouping:
    name: str = "Standard"
    renames: tuple[tuple[str, str], ...] = ()           # (node id, new label)
    moves: tuple[tuple[str, str], ...] = ()             # (node id, new parent id)
    new_groups: tuple[tuple[str, str, str], ...] = ()   # (new id, label, parent id)
    schema_version: int = SCHEMA_VERSION

    @property
    def is_standard(self) -> bool:
        return not (self.renames or self.moves or self.new_groups)

    # editing helpers (immutable; later entries for the same node win)
    def rename(self, node_id: str, label: str) -> "Grouping":
        return replace(self, renames=tuple((n, l) for n, l in self.renames if n != node_id) + ((node_id, label),))

    def move(self, node_id: str, parent_id: str) -> "Grouping":
        return replace(self, moves=tuple((n, p) for n, p in self.moves if n != node_id) + ((node_id, parent_id),))

    def add_group(self, label: str, parent_id: str) -> tuple["Grouping", str]:
        base = "GRP_" + "".join(ch if ch.isalnum() else "_" for ch in label.upper())[:24]
        gid, i = base, 2
        taken = {g[0] for g in self.new_groups}
        while gid in taken:
            gid, i = f"{base}_{i}", i + 1
        return replace(self, new_groups=self.new_groups + ((gid, label, parent_id),)), gid

    def to_json(self) -> str:
        return json.dumps({"schema_version": self.schema_version, "name": self.name,
                           "renames": [list(x) for x in self.renames], "moves": [list(x) for x in self.moves],
                           "new_groups": [list(x) for x in self.new_groups]}, indent=2)

    def content_hash(self) -> str:
        return hashlib.sha1(self.to_json().encode()).hexdigest()[:12]

    @staticmethod
    def from_json(text: str) -> "Grouping":
        try:
            d = json.loads(text)
        except json.JSONDecodeError as e:
            raise ValueError(f"Not valid JSON: {e.msg}") from None
        if not isinstance(d, dict) or d.get("schema_version") != SCHEMA_VERSION:
            raise ValueError(f"Unsupported grouping file (schema_version must be {SCHEMA_VERSION}).")
        try:
            return Grouping(name=str(d.get("name", "Custom")),
                            renames=tuple((str(a), str(b)) for a, b in d.get("renames", [])),
                            moves=tuple((str(a), str(b)) for a, b in d.get("moves", [])),
                            new_groups=tuple((str(a), str(b), str(c)) for a, b, c in d.get("new_groups", [])))
        except (TypeError, ValueError):
            raise ValueError("Malformed grouping file.") from None


def _flat(tax: Taxonomy) -> dict[str, dict[str, Any]]:
    """Every node with explicit (non-inherited) attributes, parent and ordered children."""
    out = {}
    for n in tax:
        out[n.id] = {"id": n.id, "label": n.label, "short_label": n.short_label, "lob": n.lob,
                     "description": n.description, "parent": n.parent_id, "children": list(n.children),
                     **{a: getattr(n, a) for a in _ATTRS}}
    return out


def apply(tax: Taxonomy, g: Grouping) -> tuple[Taxonomy, list[str]]:
    """Return the regrouped taxonomy and human-readable warnings. Raises TaxonomyError on invalid moves."""
    nodes = _flat(tax)
    leaves = {n.id for n in tax.leaves()}
    warnings: list[str] = []

    pending = list(g.new_groups)
    while pending:
        progressed = False
        for gid, label, parent in list(pending):
            if parent in nodes:
                if gid in nodes:
                    raise TaxonomyError(f"Group id {gid} already exists.")
                if parent in leaves:
                    raise TaxonomyError(f"“{label}” cannot be created under a pool that holds data "
                                        f"({nodes[parent]['label']}).")
                p = nodes[parent]
                nodes[gid] = {"id": gid, "label": label, "short_label": label, "lob": None, "description": None,
                              "parent": parent, "children": [], **{a: p[a] for a in _ATTRS}}
                p["children"].append(gid)
                pending.remove((gid, label, parent))
                progressed = True
        if not progressed:
            raise TaxonomyError(f"New group(s) with unknown parent: {[x[1] for x in pending]}")

    def layer_of(nid):
        while nodes[nid]["parent"] is not None:
            nid = nodes[nid]["parent"]
        return nid

    def descendants(nid):
        out, stack = set(), list(nodes[nid]["children"])
        while stack:
            c = stack.pop()
            out.add(c)
            stack += nodes[c]["children"]
        return out

    for nid, parent in g.moves:
        if nid not in nodes or parent not in nodes:
            raise TaxonomyError(f"Unknown pool in move: {nid} → {parent}")
        if nodes[nid]["parent"] is None:
            raise TaxonomyError(f"{nodes[nid]['label']} is a top-level layer and cannot be moved.")
        if parent == nid or parent in descendants(nid):
            raise TaxonomyError(f"Cannot move {nodes[nid]['label']} inside itself.")
        if parent in leaves:
            raise TaxonomyError(f"{nodes[parent]['label']} holds data and cannot contain other pools; "
                                f"create a group instead.")
        payer_node = all(tax[l].economics == "payer_pnl" for l in _leaf_set(nodes, nid, leaves))
        if payer_node != (layer_of(parent) == "PAYER"):
            raise TaxonomyError("Payer segments stay in the Payer layer (their economics are different).")
        old = nodes[nid]["parent"]
        nodes[old]["children"].remove(nid)
        nodes[parent]["children"].append(nid)
        nodes[nid]["parent"] = parent

    for nid, label in g.renames:
        if nid in nodes and label.strip():
            nodes[nid]["label"] = nodes[nid]["short_label"] = label.strip()

    # Groups left without any data under them are dropped (they would render as empty pools).
    changed = True
    while changed:
        changed = False
        for nid in list(nodes):
            n = nodes[nid]
            if nid not in leaves and not n["children"] and n["parent"] is not None:
                warnings.append(f"“{n['label']}” has no pools under it, so it is hidden until you move one in.")
                nodes[n["parent"]]["children"].remove(nid)
                del nodes[nid]
                changed = True

    def build(nid):
        n = nodes[nid]
        d = {k: n[k] for k in ("id", "label", "short_label", *_ATTRS)}
        if n["lob"]:
            d["lob"] = n["lob"]
        if n["description"]:
            d["description"] = n["description"]
        if n["children"]:
            d["children"] = [build(c) for c in n["children"]]
        return d

    spec = {"layers": [build(l) for l in tax.layer_ids]}
    return Taxonomy(spec), warnings


def empty_groups(grouped: Taxonomy, g: Grouping) -> list[tuple[str, str, str, str]]:
    """New groups hidden because nothing is in them yet: (id, label, parent id, layer id). The editor
    still offers them as move targets."""
    labels = dict(g.renames)
    parents = {gid: parent for gid, _, parent in g.new_groups}
    out = []
    for gid, label, parent in g.new_groups:
        if gid in grouped:
            continue
        p = parent
        while p not in grouped and p in parents:
            p = parents[p]
        if p in grouped:
            out.append((gid, labels.get(gid, label), parent, grouped[p].layer_id))
    return out


def _leaf_set(nodes, nid, leaves):
    out, stack = set(), [nid]
    while stack:
        c = stack.pop()
        if c in leaves:
            out.add(c)
        stack += nodes[c]["children"]
    return out


def regroup(meta: Metadata, g: Grouping | None) -> tuple[Metadata, list[str]]:
    """Metadata whose taxonomy reflects the grouping; the standard taxonomy is kept as `base_taxonomy`."""
    base = meta.standard
    if g is None or g.is_standard:
        return replace(meta, taxonomy=base, base_taxonomy=base, grouping_name="Standard"), []
    tax, warnings = apply(base, g)
    return replace(meta, taxonomy=tax, base_taxonomy=base, grouping_name=g.name,
                   max_levels=max(meta.max_levels, tax.max_depth())), warnings
