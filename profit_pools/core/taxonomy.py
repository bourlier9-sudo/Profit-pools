"""Profit pool taxonomy and related dimension metadata.

The taxonomy is an arbitrary-depth tree built from configuration. Only leaf
nodes carry rows in the fact tables; every other level is an aggregation.
Nothing in this module (or its callers) may assume a fixed number of levels.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterator, Mapping

from profit_pools.core.config import load_config

ECONOMICS_TYPES = ("payer_pnl", "margin_on_revenue", "top_down")
COVERAGE_LEVELS = ("bottom_up", "allocated", "top_down")
GEO_LEVELS = ("national", "state", "county")  # coarse → fine

_INHERITED = ("economics", "coverage", "geo_level", "receives_flows")


class TaxonomyError(ValueError):
    """Raised when taxonomy configuration is structurally invalid."""


@dataclass(frozen=True)
class Node:
    id: str
    label: str
    short_label: str
    layer_id: str
    parent_id: str | None
    depth: int  # layer = 0
    economics: str
    coverage: str
    geo_level: str
    receives_flows: bool
    lob: str | None = None
    description: str | None = None
    children: tuple[str, ...] = field(default_factory=tuple)

    @property
    def is_leaf(self) -> bool:
        return not self.children

    @property
    def is_layer(self) -> bool:
        return self.parent_id is None

    @property
    def is_top_down(self) -> bool:
        return self.economics == "top_down"


class Taxonomy:
    """Immutable tree of profit-pool nodes."""

    def __init__(self, spec: Mapping[str, Any]):
        self._nodes: dict[str, Node] = {}
        self._layer_ids: list[str] = []
        for layer in spec.get("layers", []):
            self._add(layer, parent=None, layer_id=layer["id"], depth=0, inherited={})
            self._layer_ids.append(layer["id"])
        self._validate()

    # -- construction -------------------------------------------------
    def _add(self, raw: Mapping[str, Any], parent: str | None, layer_id: str,
             depth: int, inherited: Mapping[str, Any]) -> None:
        node_id = raw["id"]
        if node_id in self._nodes:
            raise TaxonomyError(f"Duplicate taxonomy id: {node_id}")
        attrs = {k: raw.get(k, inherited.get(k)) for k in _INHERITED}
        missing = [k for k, v in attrs.items() if v is None]
        if missing:
            raise TaxonomyError(f"{node_id}: missing attributes {missing}")
        kids = raw.get("children") or []
        self._nodes[node_id] = Node(
            id=node_id,
            label=raw["label"],
            short_label=raw.get("short_label", raw["label"]),
            layer_id=layer_id,
            parent_id=parent,
            depth=depth,
            economics=attrs["economics"],
            coverage=attrs["coverage"],
            geo_level=attrs["geo_level"],
            receives_flows=bool(attrs["receives_flows"]),
            lob=raw.get("lob"),
            description=raw.get("description"),
            children=tuple(k["id"] for k in kids),
        )
        for kid in kids:
            self._add(kid, parent=node_id, layer_id=layer_id, depth=depth + 1, inherited=attrs)

    def _validate(self) -> None:
        for n in self._nodes.values():
            if n.economics not in ECONOMICS_TYPES:
                raise TaxonomyError(f"{n.id}: unknown economics {n.economics!r}")
            if n.coverage not in COVERAGE_LEVELS:
                raise TaxonomyError(f"{n.id}: unknown coverage {n.coverage!r}")
            if n.geo_level not in GEO_LEVELS:
                raise TaxonomyError(f"{n.id}: unknown geo_level {n.geo_level!r}")
            if n.is_leaf and n.economics == "payer_pnl" and not n.lob:
                raise TaxonomyError(f"{n.id}: payer leaves must declare a lob")
            if n.economics == "top_down" and n.receives_flows:
                raise TaxonomyError(f"{n.id}: top-down nodes cannot receive flows")
        lobs = [n.lob for n in self.leaves() if n.lob]
        if len(lobs) != len(set(lobs)):
            raise TaxonomyError("Each LOB may map to only one payer leaf")

    # -- queries ------------------------------------------------------
    def __contains__(self, node_id: object) -> bool:
        return node_id in self._nodes

    def __getitem__(self, node_id: str) -> Node:
        try:
            return self._nodes[node_id]
        except KeyError:
            raise KeyError(f"Unknown taxonomy node: {node_id}") from None

    def __iter__(self) -> Iterator[Node]:
        return iter(self._nodes.values())

    @property
    def layer_ids(self) -> list[str]:
        return list(self._layer_ids)

    def layers(self) -> list[Node]:
        return [self._nodes[i] for i in self._layer_ids]

    def children(self, node_id: str) -> list[Node]:
        return [self._nodes[c] for c in self[node_id].children]

    def ancestors(self, node_id: str) -> list[Node]:
        """Ancestors from the layer down to (excluding) the node."""
        path: list[Node] = []
        cur = self[node_id].parent_id
        while cur is not None:
            path.append(self._nodes[cur])
            cur = self._nodes[cur].parent_id
        return path[::-1]

    def path(self, node_id: str) -> list[Node]:
        """Layer → … → node (inclusive). Used for breadcrumbs."""
        return [*self.ancestors(node_id), self[node_id]]

    def descendants(self, node_id: str, include_self: bool = False) -> list[Node]:
        out: list[Node] = [self[node_id]] if include_self else []
        stack = list(reversed(self[node_id].children))
        while stack:
            n = self._nodes[stack.pop()]
            out.append(n)
            stack.extend(reversed(n.children))
        return out

    def leaves(self, node_id: str | None = None) -> list[Node]:
        """Leaves under a node (the node itself if it is a leaf); all leaves if None."""
        if node_id is None:
            return [n for n in self._nodes.values() if n.is_leaf]
        return [n for n in self.descendants(node_id, include_self=True) if n.is_leaf]

    def leaf_ids(self, node_id: str | None = None) -> list[str]:
        return [n.id for n in self.leaves(node_id)]

    def is_ancestor(self, ancestor_id: str, node_id: str) -> bool:
        return any(a.id == ancestor_id for a in self.ancestors(node_id))

    def leaf_for_lob(self, lob: str) -> Node:
        for n in self.leaves():
            if n.lob == lob:
                return n
        raise KeyError(f"No payer leaf for LOB {lob}")

    def max_depth(self) -> int:
        return max(n.depth for n in self._nodes.values())



    def level_ids(self, node_id: str, max_levels: int) -> list[str | None]:
        """L1..L{max_levels} ids below the layer for a node (None-padded)."""
        below = [n.id for n in self.path(node_id)[1:]]
        return below + [None] * (max_levels - len(below))


@dataclass(frozen=True)
class Lob:
    id: str
    label: str
    group: str


@dataclass(frozen=True)
class LobCatalog:
    groups: dict[str, str]  # id → label
    lobs: dict[str, Lob]

    def in_group(self, group_id: str) -> list[str]:
        return [l.id for l in self.lobs.values() if l.group == group_id]

    def group_of(self, lob_id: str) -> str | None:
        lob = self.lobs.get(lob_id)
        return lob.group if lob else None


@dataclass(frozen=True)
class SpendingCategory:
    id: str
    label: str
    target: str
    has_instances: bool


@dataclass(frozen=True)
class FundingSource:
    id: str
    label: str
    short_label: str
    basis: str  # "population_pmpm" | "lump_sum"
    group: str | None
    geo_level: str
    targets_under: tuple[str, ...]
    description: str


@dataclass(frozen=True)
class Dataset:
    id: str
    label: str
    short_label: str
    excluded_nodes: tuple[str, ...]
    payer_source_type: str
    funding_sources: tuple[str, ...]


@dataclass(frozen=True)
class State:
    id: str
    label: str
    price_index: float
    medicaid_expansion_year: int | None
    counties: tuple[tuple[str, str, float], ...]  # (id, label, weight)


@dataclass(frozen=True)
class Geography:
    national_id: str
    national_label: str
    states: dict[str, State]
    state_weights: dict[str, float]

    def county_label(self, county_id: str) -> str:
        for s in self.states.values():
            for cid, label, _ in s.counties:
                if cid == county_id:
                    return label
        raise KeyError(county_id)

    def state_of_county(self, county_id: str) -> str:
        for s in self.states.values():
            if any(cid == county_id for cid, _, _ in s.counties):
                return s.id
        raise KeyError(county_id)


@dataclass(frozen=True)
class Periods:
    history_start: int
    history_end: int
    base_year: int
    forecast_end: int

    @property
    def history_years(self) -> list[int]:
        return list(range(self.history_start, self.history_end + 1))

    @property
    def forecast_years(self) -> list[int]:
        return list(range(self.base_year + 1, self.forecast_end + 1))

    @property
    def all_years(self) -> list[int]:
        return list(range(self.history_start, self.forecast_end + 1))


@dataclass(frozen=True)
class Metadata:
    """Everything the model and UI need to interpret the fact tables."""

    taxonomy: Taxonomy
    max_levels: int
    lobs: LobCatalog
    categories: dict[str, SpendingCategory]
    instance_unit: dict[str, str]
    funding_sources: dict[str, FundingSource]
    funding_groups: dict[str, str]  # group id → label
    datasets: dict[str, Dataset]
    default_dataset: str
    geography: Geography
    periods: Periods
    # The standard taxonomy. Dataset exclusions, supplemental targets and forecast-rate
    # lookups are resolved against it, so a user regrouping can never change the numbers.
    base_taxonomy: Taxonomy | None = None
    grouping_name: str = "Standard"

    @property
    def standard(self) -> Taxonomy:
        return self.base_taxonomy or self.taxonomy

    def categories_for_target(self, leaf_id: str) -> list[SpendingCategory]:
        return [c for c in self.categories.values() if c.target == leaf_id]

    def _excluded_leaves(self, dataset_id: str) -> frozenset[str]:
        cache = self.__dict__.setdefault("_excl_cache", {})
        if dataset_id not in cache:
            base = self.standard
            cache[dataset_id] = frozenset(l.id for n in self.datasets[dataset_id].excluded_nodes
                                          for l in base.leaves(n))
        return cache[dataset_id]

    def in_dataset(self, node_id: str, dataset_id: str) -> bool:
        """True when at least one leaf under the node belongs to the dataset (exclusions are leaf-level)."""
        excl = self._excluded_leaves(dataset_id)
        return any(l.id not in excl for l in self.taxonomy.leaves(node_id))

    def leaves_in_dataset(self, dataset_id: str, node_id: str | None = None) -> list[Node]:
        excl = self._excluded_leaves(dataset_id)
        return [n for n in self.taxonomy.leaves(node_id) if n.id not in excl]

    def supplemental_targets(self, source_id: str) -> list[str]:
        fs = self.funding_sources[source_id]
        out: list[str] = []
        for anc in fs.targets_under:
            out += [n.id for n in self.standard.leaves(anc) if n.receives_flows]
        return out


def build_lobs(spec: Mapping[str, Any]) -> LobCatalog:
    groups = {g["id"]: g["label"] for g in spec["groups"]}
    lobs = {l["id"]: Lob(l["id"], l["label"], l["group"]) for l in spec["lobs"]}
    for l in lobs.values():
        if l.group not in groups:
            raise TaxonomyError(f"LOB {l.id} references unknown group {l.group}")
    return LobCatalog(groups, lobs)


def build_categories(spec: Mapping[str, Any]) -> dict[str, SpendingCategory]:
    return {
        c["id"]: SpendingCategory(c["id"], c["label"], c["target"], bool(c.get("instances", False)))
        for c in spec["claims_categories"]
    }


def build_geography(spec: Mapping[str, Any]) -> Geography:
    states = {
        s["id"]: State(
            id=s["id"], label=s["label"], price_index=float(s["price_index"]),
            medicaid_expansion_year=s.get("medicaid_expansion_year"),
            counties=tuple((c["id"], c["label"], float(c["weight"])) for c in s["counties"]),
        )
        for s in spec["states"]
    }
    return Geography(spec["national"]["id"], spec["national"]["label"], states,
                     {k: float(v) for k, v in spec["state_weights"].items()})


def build_funding_groups(spec: Mapping[str, Any]) -> dict[str, str]:
    return dict(spec.get("groups") or {})


def build_funding_sources(spec: Mapping[str, Any]) -> dict[str, FundingSource]:
    return {
        f["id"]: FundingSource(
            id=f["id"], label=f["label"], short_label=f.get("short_label", f["label"]),
            basis=f["basis"], geo_level=f["geo_level"], group=f.get("group"),
            targets_under=tuple(f.get("targets_under") or ()), description=f.get("description", ""),
        )
        for f in spec["funding_sources"]
    }


def build_datasets(spec: Mapping[str, Any]) -> tuple[dict[str, Dataset], str]:
    out = {
        k: Dataset(id=k, label=v["label"], short_label=v.get("short_label", v["label"]),
                   excluded_nodes=tuple(v.get("excluded_nodes") or ()),
                   payer_source_type=v["payer_source_type"],
                   funding_sources=tuple(v.get("funding_sources") or ()))
        for k, v in spec["datasets"].items()
    }
    return out, spec["default"]


def build_metadata(
    taxonomy_spec: Mapping[str, Any] | None = None,
    lob_spec: Mapping[str, Any] | None = None,
    category_spec: Mapping[str, Any] | None = None,
    geography_spec: Mapping[str, Any] | None = None,
    period_spec: Mapping[str, Any] | None = None,
    funding_spec: Mapping[str, Any] | None = None,
    dataset_spec: Mapping[str, Any] | None = None,
) -> Metadata:
    """Assemble and cross-validate metadata. Any spec may be overridden (tests, future data)."""
    tax_spec = taxonomy_spec or load_config("taxonomy")
    taxonomy = Taxonomy(tax_spec)
    max_levels = int(tax_spec.get("max_levels_below_layer", taxonomy.max_depth()))
    if taxonomy.max_depth() > max_levels:
        raise TaxonomyError(f"Taxonomy is {taxonomy.max_depth()} levels deep; contract allows {max_levels}")
    lobs = build_lobs(lob_spec or load_config("lobs"))
    cat_spec = category_spec or load_config("spending_categories")
    categories = build_categories(cat_spec)
    f_spec = funding_spec or load_config("funding_sources")
    funding = build_funding_sources(f_spec)
    funding_groups = build_funding_groups(f_spec)
    datasets, default_ds = build_datasets(dataset_spec or load_config("datasets"))
    geography = build_geography(geography_spec or load_config("geography"))
    p = period_spec or load_config("periods")
    periods = Periods(p["history_start"], p["history_end"], p["base_year"], p["forecast_end"])

    for leaf in taxonomy.leaves():
        if leaf.lob and leaf.lob not in lobs.lobs:
            raise TaxonomyError(f"{leaf.id}: LOB {leaf.lob} not in LOB catalog")
    targets = [c.target for c in categories.values()]
    if len(targets) != len(set(targets)):
        raise TaxonomyError("Each recipient leaf may be the target of only one claims category")
    for c in categories.values():
        if c.target not in taxonomy:
            raise TaxonomyError(f"Category {c.id}: unknown target {c.target}")
        tgt = taxonomy[c.target]
        if not tgt.is_leaf or not tgt.receives_flows:
            raise TaxonomyError(f"Category {c.id}: target {c.target} must be a flow-receiving leaf")
    for leaf in taxonomy.leaves():
        if leaf.receives_flows and leaf.id not in targets:
            raise TaxonomyError(f"Recipient leaf {leaf.id} has no claims category")
    for fs in funding.values():
        if fs.basis not in ("population_pmpm", "lump_sum"):
            raise TaxonomyError(f"Funding source {fs.id}: unknown basis {fs.basis}")
        if fs.group is not None and fs.group not in funding_groups:
            raise TaxonomyError(f"Funding source {fs.id}: unknown group {fs.group}")
        for anc in fs.targets_under:
            if anc not in taxonomy:
                raise TaxonomyError(f"Funding source {fs.id}: unknown target node {anc}")
    if default_ds not in datasets:
        raise TaxonomyError(f"Default dataset {default_ds} not defined")
    for ds in datasets.values():
        for n in ds.excluded_nodes:
            if n not in taxonomy:
                raise TaxonomyError(f"Dataset {ds.id}: unknown excluded node {n}")
        for f in ds.funding_sources:
            if f not in funding:
                raise TaxonomyError(f"Dataset {ds.id}: unknown funding source {f}")
    return Metadata(taxonomy, max_levels, lobs, categories, dict(cat_spec["instance_unit"]),
                    funding, funding_groups, datasets, default_ds, geography, periods)


_DEFAULT: Metadata | None = None


def default_metadata() -> Metadata:
    global _DEFAULT
    if _DEFAULT is None:
        _DEFAULT = build_metadata()
    return _DEFAULT
