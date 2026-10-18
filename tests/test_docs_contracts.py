from pathlib import Path

from profit_pools.core.data.contracts import (
    COVERAGE_POPULATION_COLUMNS,
    POOL_METRICS_COLUMNS,
    VALUE_FLOWS_COLUMNS,
)

DOC = Path(__file__).resolve().parents[1] / "DATA_CONTRACTS.md"


def test_every_contract_column_is_documented():
    text = DOC.read_text(encoding="utf-8")
    for col in (*POOL_METRICS_COLUMNS, *VALUE_FLOWS_COLUMNS, *COVERAGE_POPULATION_COLUMNS):
        row = f"| `{col.name}` | {col.kind} | {'yes' if col.nullable else 'no'} | {col.unit} | {col.description} |"
        assert row in text, f"DATA_CONTRACTS.md out of date for {col.name}"
