# CLAUDE.md — engineering and design guidance

Profit Pools Explorer: a Streamlit + Plotly healthcare profit-pool analytics prototype on **fictional data only**.
Read PRODUCT_SPEC.md, ARCHITECTURE.md, DATA_CONTRACTS.md and DESIGN_SYSTEM.md before changing behavior.
Track progress in BUILD_PLAN.md and update it at the end of every phase.

## Hard rules

* **Fictional data only.** Never access, request or reconstruct corporate code, data, credentials or
  proprietary rules. State and county names are invented on purpose. Keep them that way.
* **One engine.** Charts, KPIs, tables and exports consume `ModelResult` from `core/model/engine.py`. Never
  add a calculation in a chart or UI module that could disagree with the engine.
* **`profit_pools/core` never imports Streamlit.** Keep it pure and pytest-able.
* **Never hardcode the taxonomy.** No fixed depth, pool list, LOB list or layer list in code. Use
  `Metadata` / `Taxonomy`, which are driven by YAML in `core/config/`.
* **Agreed economics** (do not change without the user's approval):
  * Recipients: gain = revenue × margin. The margin is constant unless an explicit `provider_margin`
    assumption changes it. There is no cost-structure or operating-leverage model.
  * Payers: gain = revenue − claims − residual opex. Residual opex is derived. Its PMPM is held constant
    in scenarios, so it scales with enrollment.
  * Allowed spend = payer claims outflow = provider receipt (cost-sharing caveat documented).
  * Volume decomposition only where service counts are reliable. Units are never mixed across categories.
  * Top-down pools have no LOB, geography or flow detail.
  * Payer and provider margin assumptions never cross-apply.
* **Assumptions target drivers, never derived outputs.** Each driver is consumed by exactly one pipeline stage.
* **Null = unavailable, not zero.** Propagate it. Show coverage. Never fill with 0 in aggregation without
  also reporting coverage.
* **No dead controls.** If a control cannot produce a valid result (an export, a selection, a driver), hide
  it or disable it with a reason.

## Code conventions

* Python ≥ 3.11, type hints, dataclasses for value objects, pandas vectorization over loops in the model.
* Contract column names are UPPER_SNAKE (they mirror warehouse tables). Model and internal frames use
  lower_snake.
* Money is stored in dollars as float64. Format only at the presentation edge (`design/format.py`).
* Session-state keys are namespaced `pp.<area>.<name>`. Keep draft vs applied scenario separate.
* Cache adapter reads with `st.cache_data`. Cache engine runs by scenario content hash and filters.
* Comments explain *why* or state economic definitions. Keep them sparse.

## Testing

* `python -m pytest`. It must pass before every commit.
* Every economic rule gets a reconciliation test (identities hold at every grain).
* Every new assumption type gets propagation tests across payer, flows, recipients and growth, plus a
  no-double-count test.
* Corruption tests prove the validators catch bad data.
* `tests/test_docs_contracts.py` keeps DATA_CONTRACTS.md in sync with `contracts.py`.

## Running

```bash
pip install -r requirements.txt
python -m pytest
python -m profit_pools.core.data.synthetic     # dataset summary + validation
streamlit run app.py                           # from Phase 3
```

## Design

Follow DESIGN_SYSTEM.md. Tokens come only from `design/tokens.py`. Use hairlines, not cards. Use one teal
accent. Margin colors use the diverging red–gray–teal scale anchored at 0%. Missing data gets a hatch.
Never use a dual-axis chart. Check every visual change at 1440px with a screenshot before calling it done.

## Streamlit compatibility

No custom components. Target Streamlit ≥ 1.37 (`st.dialog`, `on_select`). Keep a documented fallback for
the Streamlit-in-Snowflake warehouse runtime (ARCHITECTURE.md §8).
