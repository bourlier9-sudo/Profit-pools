"""Hierarchical scope matching and deterministic resolution of overlapping assumptions.

Rules (ARCHITECTURE.md §6.2), per driver and per cell:

* `replace` (default): overlapping replace-assumptions CONFLICT. The most
  specific scope wins; the others are reported as superseded on those cells.
  Equally specific replace-assumptions on the same cells are a blocking
  conflict.
* `stack`: compounds on top of whatever applies, in list order.

Specificity = (taxonomy depth, source specificity, geography specificity,
dataset specificity), compared lexicographically. Year ranges do not add
specificity: two equally specific assumptions whose years overlap conflict.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from profit_pools.core.model.scenario import Assumption, Scope
from profit_pools.core.taxonomy import Metadata


@dataclass
class Issue:
    level: str          # "error" | "warning" | "info"
    code: str
    message: str
    assumption_id: str | None = None


@dataclass
class Resolution:
    """Which assumption governs each row, for one driver."""
    winner: np.ndarray                       # index into the assumption list, −1 = none
    stacks: list[tuple[int, np.ndarray]]     # (assumption index, mask) in list order
    matched: dict[str, int] = field(default_factory=dict)      # assumption id → rows matched
    effective: dict[str, int] = field(default_factory=dict)    # assumption id → rows where it applies
    issues: list[Issue] = field(default_factory=list)


def expand_sources(meta: Metadata, sources: tuple[str, ...]) -> tuple[set[str], set[str]]:
    """Split source ids into (LOB ids, funding-source ids), expanding groups."""
    lobs, funding = set(), set()
    for s in sources:
        if s in meta.lobs.lobs:
            lobs.add(s)
        elif s in meta.lobs.groups:
            lobs |= set(meta.lobs.in_group(s))
        elif s in meta.funding_sources:
            funding.add(s)
        elif s in meta.funding_groups:
            funding |= {f.id for f in meta.funding_sources.values() if f.group == s}
        else:
            raise KeyError(s)
    return lobs, funding


def specificity(meta: Metadata, scope: Scope) -> int:
    depth = -1 if scope.node_id is None else meta.taxonomy[scope.node_id].depth
    if not scope.sources:
        src = 0
    elif all(s in meta.lobs.groups or s in meta.funding_groups for s in scope.sources):
        src = 1
    else:
        src = 2
    if not scope.geography:
        geo = 0
    elif all(g in meta.geography.states for g in scope.geography):
        geo = 1
    else:
        geo = 2
    ds = 0 if scope.dataset is None else 1
    return (depth + 1) * 1000 + src * 100 + geo * 10 + ds


def describe_scope(meta: Metadata, scope: Scope) -> str:
    parts = [meta.taxonomy[scope.node_id].short_label if scope.node_id else "All"]
    if scope.sources:
        names = []
        for s in scope.sources:
            names.append(meta.lobs.lobs[s].label if s in meta.lobs.lobs else meta.lobs.groups.get(s)
                         or (meta.funding_sources[s].short_label if s in meta.funding_sources else meta.funding_groups.get(s, s)))
        parts.append("from " + ", ".join(names))
    if scope.geography:
        g = meta.geography
        parts.append("in " + ", ".join(g.states[x].label if x in g.states else g.county_label(x) for x in scope.geography))
    if scope.dataset:
        parts.append(f"({meta.datasets[scope.dataset].short_label})")
    return " ".join(parts)


def match(meta: Metadata, frame: pd.DataFrame, scope: Scope, *, node_col: str | None,
          year: int | None = None) -> np.ndarray:
    """Boolean mask of frame rows inside the scope.

    node_col: column holding the leaf to test against scope.node_id (None = frame has no taxonomy node).
    Source filters apply to `lob` (payer sources) and `source_id` (funding sources) when present.
    """
    m = np.ones(len(frame), dtype=bool)
    if scope.dataset is not None:
        m &= (frame["dataset"] == scope.dataset).to_numpy()
    years = frame["year"].to_numpy() if year is None else np.full(len(frame), year)
    m &= years >= scope.years.start
    if scope.years.end is not None:
        m &= years <= scope.years.end
    if scope.node_id is not None and node_col is not None:
        leaves = set(meta.taxonomy.leaf_ids(scope.node_id))
        m &= frame[node_col].isin(leaves).to_numpy()
    if scope.sources:
        lobs, funding = expand_sources(meta, scope.sources)
        sm = np.zeros(len(frame), dtype=bool)
        if "lob" in frame and lobs:
            sm |= frame["lob"].isin(lobs).to_numpy()
        if "source_id" in frame and funding:
            sm |= frame["source_id"].isin(funding).to_numpy()
        m &= sm
    if scope.geography:
        states = {g for g in scope.geography if g in meta.geography.states}
        counties = set(scope.geography) - states
        gm = np.zeros(len(frame), dtype=bool)
        if states and "state" in frame:
            gm |= frame["state"].isin(states).to_numpy()
        if counties and "county" in frame:
            gm |= frame["county"].isin(counties).to_numpy()
        m &= gm
    return m


def resolve(meta: Metadata, frame: pd.DataFrame, items: list[tuple[int, Assumption]], *,
            node_col: str | None, year: int | None = None, extra_mask: np.ndarray | None = None) -> Resolution:
    """Resolve the assumptions (all for ONE driver) over the rows of `frame`."""
    n = len(frame)
    winner = np.full(n, -1)
    best = np.full(n, -1)
    tie = np.zeros(n, dtype=bool)
    stacks: list[tuple[int, np.ndarray]] = []
    res = Resolution(winner, stacks)
    masks = {}
    for idx, a in items:
        mk = match(meta, frame, a.scope, node_col=node_col, year=year)
        if extra_mask is not None:
            mk &= extra_mask
        masks[idx] = mk
        res.matched[a.id] = int(mk.sum())
        if a.mode == "stack":
            stacks.append((idx, mk))
            continue
        sp = specificity(meta, a.scope)
        better = mk & (sp > best)
        same = mk & (sp == best)
        winner[better] = idx
        tie[better] = False
        tie[same] = True
        best[better] = sp
    by_idx = dict(items)
    for idx, a in items:
        if a.mode == "stack":
            res.effective[a.id] = res.matched[a.id]
            continue
        won = masks[idx] & (winner == idx) & ~tie
        res.effective[a.id] = int(won.sum())
        lost = masks[idx] & (winner != idx)
        if lost.any():
            others = sorted({by_idx[w].label or by_idx[w].driver for w in np.unique(winner[lost]) if w >= 0})
            res.issues.append(Issue("warning", "superseded",
                                    f"Superseded on {int(lost.sum()):,} cells by more specific "
                                    f"assumption(s): {', '.join(others)}.", a.id))
    if tie.any():
        tied = [a for idx, a in items if a.mode == "replace" and (masks[idx] & tie).any()]
        names = ", ".join(f"“{a.label or a.driver}”" for a in tied)
        for a in tied:
            res.issues.append(Issue(
                "error", "conflict",
                f"Conflicts with {names}: equally specific assumptions on the same driver overlap on "
                f"{int(tie.sum()):,} cells. Narrow a scope or year range, disable one, or set one to Stack.",
                a.id))
        winner[tie] = -1
    return res
