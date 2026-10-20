import pytest

from profit_pools.core.data.adapters import (
    SnowflakeAdapter,
    SnowflakeAdapterNotConfigured,
    SyntheticAdapter,
    get_adapter,
)


@pytest.fixture(scope="module")
def adapter():
    return SyntheticAdapter()


def test_synthetic_adapter_is_labeled_synthetic(adapter):
    assert adapter.info.synthetic
    assert "Fictional" in adapter.info.description


def test_synthetic_adapter_validates_clean(adapter):
    assert adapter.validate() == []


def test_year_and_state_filters(adapter):
    m = adapter.pool_metrics(years=[2024], states=["NVL"])
    assert set(m["YEAR"]) == {2024} and set(m["STATE"]) == {"NVL"}
    # National-only top-down rows are excluded by a state filter.
    assert not (m["LAYER_ID"] == "MANUFACTURER").any()
    f = adapter.value_flows(years=[2024], states=["NVL"])
    assert set(f["STATE"]) == {"NVL"}


def test_adapter_returns_copies(adapter):
    m = adapter.pool_metrics()
    m["REVENUE"] = 0
    assert adapter.pool_metrics()["REVENUE"].sum() > 0


def test_snowflake_stub_is_unconnected():
    sf = SnowflakeAdapter()
    assert not sf.info.synthetic
    with pytest.raises(SnowflakeAdapterNotConfigured):
        sf.pool_metrics()
    with pytest.raises(SnowflakeAdapterNotConfigured):
        sf.value_flows()


def test_factory(monkeypatch):
    assert isinstance(get_adapter(), SyntheticAdapter)
    monkeypatch.setenv("PP_DATA_ADAPTER", "snowflake")
    assert isinstance(get_adapter(), SnowflakeAdapter)
    with pytest.raises(ValueError):
        get_adapter("oracle")


def test_coverage_population_filter(adapter):
    p = adapter.coverage_population(years=[2025], states=["SRG"])
    assert set(p["YEAR"]) == {2025} and set(p["STATE"]) == {"SRG"}
    assert set(p["FUNDING_SOURCE_ID"]) == {"FS_UNINSURED", "FS_MCR_FFS", "FS_MCD_FFS"}
