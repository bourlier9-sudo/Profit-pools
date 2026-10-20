"""Data-access interface.

The model layer only talks to a :class:`DataAdapter`. Two implementations:

* :class:`SyntheticAdapter` — local, deterministic, fictional data (default).
* :class:`SnowflakeAdapter` — an UNCONNECTED stub that documents the intended
  query shape. It holds no credentials and opens no connections.

Adapters return contract-shaped frames for the requested years/states.
Interpretation (geography roll-ups, coverage, scenarios) belongs to the
model layer, not to adapters.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Iterable, Protocol

import pandas as pd

from profit_pools.core.data.contracts import (
    COVERAGE_POPULATION_COLUMNS,
    POOL_METRICS_COLUMNS,
    VALUE_FLOWS_COLUMNS,
    Issue,
    validate_bundle,
)
from profit_pools.core.data.synthetic import DEFAULT_SEED, SyntheticBundle, generate
from profit_pools.core.taxonomy import Metadata, default_metadata


@dataclass(frozen=True)
class AdapterInfo:
    name: str
    synthetic: bool
    description: str


class DataAdapter(Protocol):
    info: AdapterInfo
    metadata: Metadata

    def pool_metrics(self, years: Iterable[int] | None = None,
                     states: Iterable[str] | None = None) -> pd.DataFrame: ...

    def value_flows(self, years: Iterable[int] | None = None,
                    states: Iterable[str] | None = None) -> pd.DataFrame: ...

    def coverage_population(self, years: Iterable[int] | None = None,
                            states: Iterable[str] | None = None) -> pd.DataFrame: ...

    def validate(self) -> list[Issue]: ...


def _filter(df: pd.DataFrame, years: Iterable[int] | None, states: Iterable[str] | None) -> pd.DataFrame:
    mask = pd.Series(True, index=df.index)
    if years is not None:
        mask &= df["YEAR"].isin(list(years))
    if states is not None:
        # National (top-down) rows are excluded by a state filter; the model
        # layer reports them as "unavailable at this geography".
        mask &= df["STATE"].isin(list(states))
    return df.loc[mask].copy()


class SyntheticAdapter:
    """In-memory fictional dataset. Generation is deterministic for a seed."""

    def __init__(self, seed: int = DEFAULT_SEED, metadata: Metadata | None = None):
        self.metadata = metadata or default_metadata()
        self.seed = seed
        self.info = AdapterInfo(
            name="Synthetic",
            synthetic=True,
            description=f"Fictional data generated locally (seed {seed}). Not real market data.",
        )
        self._bundle: SyntheticBundle | None = None

    @property
    def bundle(self) -> SyntheticBundle:
        if self._bundle is None:
            self._bundle = generate(self.seed, self.metadata)
        return self._bundle

    def pool_metrics(self, years=None, states=None) -> pd.DataFrame:
        return _filter(self.bundle.pool_metrics, years, states)

    def value_flows(self, years=None, states=None) -> pd.DataFrame:
        return _filter(self.bundle.value_flows, years, states)

    def coverage_population(self, years=None, states=None) -> pd.DataFrame:
        return _filter(self.bundle.coverage_population, years, states)

    def validate(self) -> list[Issue]:
        b = self.bundle
        return validate_bundle(b.pool_metrics, b.value_flows, b.coverage_population, self.metadata)


class SnowflakeAdapterNotConfigured(RuntimeError):
    pass


class SnowflakeAdapter:
    """Placeholder for a future, separately authorized environment.

    Intentionally unconnected: no driver import, no credentials, no network.
    The SQL below documents the intended contract-shaped reads (bind
    parameters, no string interpolation). Implementing this class later must
    not change any model or UI code.
    """

    POOL_METRICS_SQL = (
        f"SELECT {', '.join(c.name for c in POOL_METRICS_COLUMNS)} "
        "FROM FACT_POOL_METRICS WHERE YEAR BETWEEN :y0 AND :y1"
    )
    VALUE_FLOWS_SQL = (
        f"SELECT {', '.join(c.name for c in VALUE_FLOWS_COLUMNS)} "
        "FROM FACT_VALUE_FLOWS WHERE YEAR BETWEEN :y0 AND :y1"
    )
    COVERAGE_POPULATION_SQL = (
        f"SELECT {', '.join(c.name for c in COVERAGE_POPULATION_COLUMNS)} "
        "FROM FACT_COVERAGE_POPULATION WHERE YEAR BETWEEN :y0 AND :y1"
    )

    def __init__(self, metadata: Metadata | None = None):
        self.metadata = metadata or default_metadata()
        self.info = AdapterInfo(
            name="Snowflake (not connected)",
            synthetic=False,
            description="Stub only. Not available in this prototype.",
        )

    def _unavailable(self):
        raise SnowflakeAdapterNotConfigured(
            "The Snowflake adapter is a documented stub and is not connected in this prototype."
        )

    def pool_metrics(self, years=None, states=None) -> pd.DataFrame:
        self._unavailable()

    def value_flows(self, years=None, states=None) -> pd.DataFrame:
        self._unavailable()

    def coverage_population(self, years=None, states=None) -> pd.DataFrame:
        self._unavailable()

    def validate(self) -> list[Issue]:
        self._unavailable()


ADAPTERS = {"synthetic": SyntheticAdapter, "snowflake": SnowflakeAdapter}


def get_adapter(name: str | None = None) -> DataAdapter:
    """Factory. ``PP_DATA_ADAPTER`` env var selects the adapter; default synthetic."""
    key = (name or os.environ.get("PP_DATA_ADAPTER") or "synthetic").lower()
    try:
        return ADAPTERS[key]()
    except KeyError:
        raise ValueError(f"Unknown data adapter {key!r}; expected one of {sorted(ADAPTERS)}") from None
