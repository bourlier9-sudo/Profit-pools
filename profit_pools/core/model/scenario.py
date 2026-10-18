"""Scenario data model: an ordered list of assumptions on drivers.

Pure data + (de)serialization. Economic meaning lives in `scenario_engine.py`;
resolution rules in `resolve.py`. Scenario JSON holds configuration only —
never data or credentials.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import asdict, dataclass, field, replace
from typing import Any

SCHEMA_VERSION = 1
METHODS = ("pct", "pp", "set", "delta")
MODES = ("replace", "stack")


class ScenarioFormatError(ValueError):
    """Raised when scenario JSON is structurally invalid or from an unsupported schema version."""

    def __init__(self, problems: list[str]):
        super().__init__("; ".join(problems))
        self.problems = problems


@dataclass(frozen=True)
class YearRange:
    start: int
    end: int | None = None   # None = "onward"

    def contains(self, year: int) -> bool:
        return year >= self.start and (self.end is None or year <= self.end)

    def label(self) -> str:
        if self.end is None:
            return f"{self.start} onward"
        return str(self.start) if self.end == self.start else f"{self.start}–{self.end}"


@dataclass(frozen=True)
class Scope:
    years: YearRange
    dataset: str | None = None                  # None = both datasets
    node_id: str | None = None                  # any taxonomy node; None = every eligible node
    sources: tuple[str, ...] = ()               # LOB, LOB-group, funding-source or funding-group ids
    geography: tuple[str, ...] = ()             # state and/or county ids; empty = national


@dataclass(frozen=True)
class Assumption:
    driver: str
    method: str
    value: float
    scope: Scope
    mode: str = "replace"        # replace: conflicts with overlapping assumptions (default); stack: compounds
    enabled: bool = True
    target: str | None = None    # enrollment_shift destination (payer leaf or funding source)
    label: str = ""
    notes: str = ""
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])


@dataclass(frozen=True)
class Scenario:
    name: str
    assumptions: tuple[Assumption, ...] = ()
    description: str = ""
    base_year: int = 2025
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    schema_version: int = SCHEMA_VERSION

    @property
    def enabled(self) -> list[Assumption]:
        return [a for a in self.assumptions if a.enabled]

    # --- editing helpers (immutable) ---------------------------------------
    def with_assumptions(self, items) -> "Scenario":
        return replace(self, assumptions=tuple(items))

    def add(self, a: Assumption) -> "Scenario":
        return self.with_assumptions([*self.assumptions, a])

    def update(self, a: Assumption) -> "Scenario":
        return self.with_assumptions([a if x.id == a.id else x for x in self.assumptions])

    def remove(self, aid: str) -> "Scenario":
        return self.with_assumptions([x for x in self.assumptions if x.id != aid])

    def duplicate(self, aid: str) -> "Scenario":
        out = []
        for x in self.assumptions:
            out.append(x)
            if x.id == aid:
                out.append(replace(x, id=uuid.uuid4().hex[:8], label=(x.label + " (copy)").strip()))
        return self.with_assumptions(out)

    def move(self, aid: str, step: int) -> "Scenario":
        items = list(self.assumptions)
        i = next(i for i, x in enumerate(items) if x.id == aid)
        j = max(0, min(len(items) - 1, i + step))
        items.insert(j, items.pop(i))
        return self.with_assumptions(items)

    def toggle(self, aid: str, enabled: bool) -> "Scenario":
        return self.with_assumptions([replace(x, enabled=enabled) if x.id == aid else x for x in self.assumptions])

    # --- serialization -------------------------------------------------------
    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["assumptions"] = [_assumption_dict(a) for a in self.assumptions]
        return d

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2, sort_keys=False)

    def content_hash(self) -> str:
        """Stable hash of the economic content (enabled assumptions in order), used for caching."""
        payload = [_assumption_dict(a) | {"id": None, "label": None, "notes": None} for a in self.enabled]
        return hashlib.sha1(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()[:16]

    @staticmethod
    def from_dict(d: dict[str, Any]) -> "Scenario":
        problems: list[str] = []
        if not isinstance(d, dict):
            raise ScenarioFormatError(["Scenario must be a JSON object"])
        ver = d.get("schema_version")
        if ver != SCHEMA_VERSION:
            raise ScenarioFormatError([f"Unsupported schema_version {ver!r}; expected {SCHEMA_VERSION}"])
        for k in ("name", "assumptions"):
            if k not in d:
                problems.append(f"Missing field '{k}'")
        if problems:
            raise ScenarioFormatError(problems)
        if not isinstance(d["assumptions"], list):
            raise ScenarioFormatError(["'assumptions' must be a list"])
        items = []
        for i, a in enumerate(d["assumptions"]):
            try:
                items.append(_assumption_from(a))
            except (KeyError, TypeError, ValueError) as e:
                problems.append(f"Assumption {i + 1}: {e}")
        if problems:
            raise ScenarioFormatError(problems)
        ids = [a.id for a in items]
        if len(ids) != len(set(ids)):
            raise ScenarioFormatError(["Duplicate assumption ids"])
        return Scenario(name=str(d["name"]), assumptions=tuple(items), description=str(d.get("description", "")),
                        base_year=int(d.get("base_year", 2025)), id=str(d.get("id") or uuid.uuid4().hex[:8]))

    @staticmethod
    def from_json(text: str) -> "Scenario":
        try:
            return Scenario.from_dict(json.loads(text))
        except json.JSONDecodeError as e:
            raise ScenarioFormatError([f"Not valid JSON: {e.msg} (line {e.lineno})"]) from None


def _assumption_dict(a: Assumption) -> dict[str, Any]:
    d = asdict(a)
    d["scope"]["sources"] = list(a.scope.sources)
    d["scope"]["geography"] = list(a.scope.geography)
    return d


def _assumption_from(a: dict[str, Any]) -> Assumption:
    if not isinstance(a, dict):
        raise TypeError("must be an object")
    sc = a["scope"]
    yrs = sc["years"]
    method, mode = a["method"], a.get("mode", "replace")
    if method not in METHODS:
        raise ValueError(f"unknown method {method!r}")
    if mode not in MODES:
        raise ValueError(f"unknown mode {mode!r}")
    value = float(a["value"])
    if value != value or value in (float("inf"), float("-inf")):
        raise ValueError("value must be a finite number")
    end = yrs.get("end")
    scope = Scope(years=YearRange(int(yrs["start"]), None if end is None else int(end)),
                  dataset=sc.get("dataset"), node_id=sc.get("node_id"),
                  sources=tuple(sc.get("sources") or ()), geography=tuple(sc.get("geography") or ()))
    return Assumption(driver=str(a["driver"]), method=method, value=value, scope=scope, mode=mode,
                      enabled=bool(a.get("enabled", True)), target=a.get("target"),
                      label=str(a.get("label", "")), notes=str(a.get("notes", "")),
                      id=str(a.get("id") or uuid.uuid4().hex[:8]))
