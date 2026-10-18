# Architecture

## 1. Principles

1. **One engine.** Every chart, table, KPI and export reads one `ModelResult` produced by
   `core/model/engine.py`. No view does financial arithmetic beyond formatting and filtering.
2. **Pure core.** `profit_pools/core/**` never imports Streamlit. It is testable with pytest alone.
3. **Configuration over code.** Taxonomy, LOBs, categories, geography, metrics and drivers are YAML.
   Hierarchy depth, pool count and LOB count are never hardcoded.
4. **Drivers in, results out.** Assumptions target *drivers* (enrollment, PMPMs, margins, growth
   rates). Derived values (revenue, gain, flows) are never assumption targets. That is what prevents
   double application.
5. **Honest provenance.** Every value can be traced to observed, estimated, allocated, derived, projected,
   assumed or unavailable inputs (DATA_CONTRACTS.md §9).

## 2. Module map

```
app.py                                   Streamlit entry (Phase 3)
profit_pools/
  core/                                  ── pure Python ──────────────────────────
    config/          *.yaml + loader      taxonomy, datasets, funding sources, lobs, categories, geo, periods,
                                          metrics, drivers, baseline forecast, methodology text
    taxonomy.py                           Taxonomy tree, datasets, funding sources, LOBs, categories, geography, periods → Metadata
    data/
      contracts.py                        fictional table schemas + validate_bundle()
      synthetic.py                        deterministic generator (seeded)
      adapters.py                         DataAdapter protocol · SyntheticAdapter · SnowflakeAdapter (stub)
    model/
      cube.py                             facts → driver cube (history) + baseline forecast projection   ✅ Phase 2
      engine.py                           staged pipeline → ModelResult                                   ✅ Phase 2
      views.py                            ModelResult → roll-ups / series / totals (filter + sum only)    ✅ Phase 2
      scenario.py                         Scenario / Assumption dataclasses, schema v1, JSON (de)serialization, validation
      resolve.py                          hierarchical scope matching, precedence, conflict detection
      ledger.py                           per-assumption lineage + sequential attribution bridge
      growth.py                           growth decomposition (CAGR helper lives in views.py)
    export/           (Phase 7)           csv, scenario_json, html_snapshot, pptx_summary
  design/             (Phase 3)           tokens.py, theme.css, plotly_template.py, components.py
  charts/             (Phase 3+)          marimekko.py, sankey.py, trends.py, waterfall.py — figure builders only
  shell/              (Phase 3)           header, global bar, scenario chip, inspector, export menu
  modules/            (Phase 3)           registry.py + profit_pools/ (workspace sections A–E)
tests/
```

**Dependency direction:** `modules → shell/charts/design → core.model → core.data → core.taxonomy → core.config`.
Nothing in `core` imports upward.

## 3. Data access

`DataAdapter` returns contract-shaped DataFrames (`pool_metrics`, `value_flows`, `coverage_population`)
covering both datasets, with optional year and state push-down filters. `SyntheticAdapter` generates them
in memory. `SnowflakeAdapter` is an unconnected stub with documented SQL, and it raises
`SnowflakeAdapterNotConfigured`. The adapter is selected by
`PP_DATA_ADAPTER` (default `synthetic`). Adapters never interpret data. Roll-ups, coverage and scenarios
belong to the model.

## 4. Datasets

The global toggle chooses **MARKET** (total market) or **ENTERPRISE** (the company's book, label configurable).
Both datasets flow through the same cube, engine and views. A dataset is a dimension, not a separate
model. ENTERPRISE excludes Manufacturer, Health Services and PBM Services (`datasets.yaml → excluded_nodes`),
and it has no funding sources. Market payer PMPMs are benchmarks derived from the enterprise's experience.
In the engine they are independent inputs, so a scenario changes either dataset only through assumptions
scoped to it (or to both).

## 5. Driver cube and baseline forecast (`model/cube.py`)

The engine's only input is a **driver cube** at the finest grain:

| Frame | Grain | Drivers |
|---|---|---|
| `payer` | dataset × year × county × payer leaf | `members`, `premium_pmpm`, `opex_pmpm` (derived residual) |
| `spend` | … × source (payer leaf or funding source) × category | `allowed_pmpm` (per member or person of the source), `inst_per_member` (where instances exist) |
| `population` | dataset × year × county × funding source | `population` (uninsured, traditional Medicare & public) |
| `supplemental` | dataset × year × state × funding source × hospital leaf | `amount` |
| `recipient_margin` | dataset × year × state × recipient leaf | `margin` (NaN = unavailable) |
| `top_down` | dataset × year × leaf (national) | `revenue`, `margin` |

* **History:** drivers come straight from the facts, so the engine reproduces the facts exactly (tested).
* **Forecast** (2026–2031):
  * Base-year drivers compound forward: `level_y = level_{y−1} × (1 + rate_y)`.
  * Rates come from `baseline_forecast.yaml` by hierarchical lookup (LOB → LOB group → default; category → target
    ancestors → layer → default; leaf → ancestors). Lookup is configuration-driven, not hardcoded.
  * Allowed PMPM grows by `(1 + utilization_trend)(1 + unit_cost_trend)`. Instances per member grow by
    `utilization_trend`.
  * Margins hold at base-year values.
* `project(cube, meta, rates)` accepts any rate function `(driver, rows, year) → array`. The scenario engine
  wraps the baseline rates to apply growth assumptions, so the projection code never changes.

## 6. Engine pipeline (`model/engine.py`)

Stages run in a fixed order. Each driver belongs to exactly one stage (`drivers.yaml → stage`). Phase 2 runs
them with no assumptions. Phase 4 inserts assumption resolution between stages.

| # | Stage | Consumes | Produces |
|---|---|---|---|
| 1 | Growth | baseline rates ± growth assumptions | forecast-year driver levels |
| 2 | Volume | `enrollment`, `enrollment_shift` (incl. to/from uninsured), `funding_population` | members, populations |
| 3 | Payer unit economics | `premium_pmpm`, `opex_pmpm`, `opex_volume_scaling`, then `payer_margin` (last) | payer PMPMs |
| 4 | Spending | `allowed_pmpm` / `funding_pmpm`; `utilization`, `unit_price` (instance categories only) | allowed PMPM, instances per member |
| 5 | Payer financials | — | revenue = premium × mm; opex = opex PMPM × mm |
| 6 | Flows | `supplemental_payments` | payer / funding flows = allowed PMPM × members or population × 12; supplemental amounts; payer claims = Σ payer flows; payer gain |
| 7 | Recipient financials | `provider_margin` | revenue = Σ inflows; gain = revenue × margin |
| 8 | Top-down | `top_down_revenue`, `top_down_margin` | revenue, gain |
| 9 | Assemble | — | `ModelResult.pools`, `ModelResult.flows` |

How changes propagate (always through drivers):

* **Enrollment +x%:**
  * members ↑, so premium revenue, claims and residual opex rise (PMPMs constant), and payer gain rises.
  * Payer flows rise, so recipient revenue and gain rise at constant margin.
* **Enrollment shift Medicaid → Uninsured:**
  * Payer members fall and the uninsured population rises.
  * Medicaid flows fall and uninsured flows rise at their own PMPMs, so the net change in recipient
    revenue is shown.
* **Allowed PMPM +x% on Hospitals:** payer claims ↑, so payer gain ↓ (premium unchanged). Hospital revenue
  and gain ↑ at constant margin.
* **Utilization vs unit price** (instance categories only): both change allowed and the same downstream
  values. They differ only in instances and price per instance.
* **Supplemental payments +x%:** hospital revenue and gain only.
* **Provider margin +1 pp:** gain of scoped recipients only. **Payer margin −1 pp:** opex PMPM is solved so
  the margin hits target. They never cross-apply.
* **Growth +1 pp:** compounds into every later year, and therefore into the CAGRs.

### 6.1 Assumption model

```python
Assumption(
  id, label, driver,                 # driver ∈ drivers.yaml
  method,                            # pct | pp | set | delta
  value,                             # 0.05 = +5% (pct) or +5 pp (pp)
  mode,                              # "adjust" (incremental) | "override"
  scope=Scope(
    dataset=None | "MARKET" | "ENTERPRISE",   # None = both
    node_id,                         # ANY taxonomy node: layer, pool or leaf
    funding_sources=None | [ids],    # spending/funding drivers: restrict to uninsured / public / supplemental
    lobs=None | [LOB or LOB-group ids],
    payer_node_id=None,              # spending drivers only: restrict by originating payer node
    geography=None | [state / county ids],
    years=YearRange(start, end|None),  # end None = "onward"
  ),
  enabled=True, notes="")
Scenario(id, name, description, base_year, schema_version, assumptions: list[Assumption])
```

Year semantics: a **level** assumption applies to the years in its range only. A **growth** assumption
changes annual rates within its range, and the effect compounds into later years. The UI states which
of the two the user has chosen.

### 6.2 Hierarchical scope resolution (deterministic)

An assumption *matches* a driver cell when the cell's leaf descends from `scope.node_id` (or is it), its LOB
is in `scope.lobs` (LOB groups expand to their LOBs), its geography is inside `scope.geography` (a state matches
its counties), and its year is in range.

**Specificity** of a scope is the tuple `(node depth, LOB / funding-source specificity, geo specificity,
dataset specificity)`, compared lexicographically:

* LOB specificity: all = 0, group = 1, LOB = 2.
* Geo specificity: national = 0, state = 1, county = 2.
* Dataset specificity: both = 0, one dataset = 1.

Hierarchy depth comes from the taxonomy, so nothing is hardcoded.

For each cell and driver, with M = the matching enabled assumptions in list order:

1. **No overrides in M:** apply every adjustment in list order. `pct` compounds multiplicatively and
   `pp`/`delta` add. If M mixes multiplicative and additive methods, the cell is flagged *order-sensitive*.
2. **Overrides in M:**
   * The most specific override wins. A tie goes to the later one and is reported as a **conflict**.
   * The override value replaces the baseline driver level (`set`), or applies to the baseline (`pct`/`pp`).
   * Adjustments **less specific** than the winning override are **superseded** on that cell and reported.
   * Adjustments **equally or more specific** apply on top, in list order.

Worked example (allowed spend, Psychiatric Hospital cells):

| Assumptions | Psych result | Other hospitals | Other providers |
|---|---|---|---|
| Provider +5% (adjust), Hospital +8% (adjust), Psych +12% (adjust) | ×1.05×1.08×1.12 = **+27.0%** | +13.4% | +5% |
| Same, but Psych +12% is **override** | **+12.0%** (Provider and Hospital superseded on Psych cells) | +13.4% | +5% |

**Validation** (blocking errors vs warnings):

* Errors block apply:
  * The driver isn't eligible for the node's economics, e.g. `provider_margin` on a payer or `utilization`
    on a category without instance data, such as any pharmacy category.
  * The scope matches no data.
  * The result is invalid: negative enrollment or PMPM, margin ≥ 100%.
  * An `enrollment_shift` would push a source segment below zero.
* Warnings: conflicts, superseded adjustments, order-sensitive cells, scopes that partially fall on
  unavailable data.

**Preview** (`engine.preview(scenario, assumption)`) returns:

* matched leaves (descendants listed),
* the LOBs, geographies and years matched,
* the number of cells,
* the baseline driver total,
* the Δ revenue and Δ gain by layer.

It runs the pipeline with the assumption and without it.

### 6.3 No double counting

* Assumptions only change drivers. Each driver is consumed by exactly one stage.
* Recipient revenue is *only* Σ inflows. Every inflow has a named source: a payer segment, uninsured,
  traditional Medicare & public, or supplemental. There is no plug that could silently absorb a change.
  A spending assumption changes the inflow once, and the recipient sees it through the flow, never through
  a second adjustment.
* Payer margin and recipient margin are distinct drivers with disjoint eligible economics.
* Funding sources are not profit pools. They add recipient revenue, but no payer revenue or gain.
* Layer revenues are **gross**: payer claims are provider revenue. "Total market revenue" across layers is
  labeled *gross sector revenue (includes inter-sector payments)*. Operating gain *is* additive across
  layers, because each dollar of gain belongs to one pool.
* Ownership (Phase 4b): intra-organization flows are eliminated in the consolidated view. Consolidated gain
  = payer gain + owned-asset gain, and the elimination nets out of revenue and expense only.

### 6.4 Lineage and the driver bridge

* `ledger.py` records, for each assumption: the cells touched, driver before and after, and Δ for every
  derived metric. The "why did this change" inspector reads these entries.
* **Bridge:**
  * Run the pipeline cumulatively, enabling assumptions one at a time in list order.
  * Step *k* = result(1..k) − result(1..k−1).
  * The steps sum exactly to scenario − baseline. The bridge is order-dependent by design, and the
    order is shown.

### 6.5 Growth and CAGR

* Forecast growth rates are **inputs**.
* Displayed CAGRs are **computed** from resulting values: `(V_end / V_start)^(1/n) − 1`.
* Gain CAGR is "n/m" when either endpoint is ≤ 0 or unavailable.
* No calculation feeds a computed CAGR back into a driver, so there is no circularity.

## 7. `ModelResult` and views

```python
ModelResult(
  pools,   # dataset, year, is_forecast, geo_level, state, county, node_id, layer_id, lob,
           # members, revenue, claims, opex, gain, margin_known, source_type
  flows,   # dataset, year, is_forecast, state, county, source_id, source_kind, flow_kind,
           # lob, category, target_id, amount, instances
  base_year, scenario_id)
```

`pools` grain:
* Payer: county.
* Recipients: county, plus state-level rows for supplemental payments.
* Top-down: national.

`gain` is NaN where the margin is unavailable.

`views.py` only filters, groups and sums. It recomputes non-additive measures (margin over revenue *with*
margin data, margin coverage, PMPMs, MLR):

* `rollup(result, meta, dataset, year, parent_id, geo)`: children of any node, plus `unavailable` (with
  reasons) and coverage notes.
* `node_series(...)`: per-year trend for a node.
* `totals(...)`: headline KPIs. Revenue is gross sector revenue.
* `cagr(start, end, n)`: realized CAGR, NaN ("n/m") at a non-positive endpoint.

Geography semantics:

| Geography | What it includes |
|---|---|
| national | everything |
| state | excludes national-only pools, which are reported as unavailable |
| county | payer rows and county-grain recipient rows; excludes state-level supplemental payments (noted) and top-down pools; recipient gain uses the state pool margin |

## 8. Performance and state

* `st.cache_data` caches the adapter frames for the session lifetime.
* The engine runs are cached by `(scenario.content_hash, filters)`. Baseline and scenario each compute once.
* Measured: a baseline run over about 108k flow rows and 16k pool rows (both datasets, 2019–2031) takes about
  0.5 seconds. A test enforces < 3 s.
* Session state keys are namespaced `pp.*`. The **draft** scenario (being edited) is distinct from the
  **applied** scenario, which feeds the engine.

## 9. Streamlit compatibility

| Concern | Decision |
|---|---|
| Version | Local target Streamlit ≥ 1.37 (developed on 1.66). |
| Chart clicks | Marimekko is Plotly bars, so `on_select="rerun"` works, plus a synchronized selector that is always present. Sankey: Streamlit returns no Sankey events, so selection is via synchronized controls. No fake double-click. |
| Dialogs | `st.dialog` for the assumption builder, `st.popover` for compact menus. |
| Custom components | None. This keeps the warehouse runtime of Streamlit in Snowflake viable. |
| Styling | CSS injected via `st.markdown(unsafe_allow_html=True)`, with stable `st-key-*` class hooks. Inter via Google Fonts, falling back to a system font stack (Snowflake or offline). |
| Streamlit in Snowflake | Container runtime preferred (newer Streamlit, pip packages). Warehouse runtime: verify the pinned Streamlit version supports `st.dialog` and `on_select`, otherwise fall back to an expander editor. python-pptx must exist in the Snowflake Anaconda channel or the PPTX export is hidden. |
| Exports | HTML via `plotly.io.to_html(include_plotlyjs="inline")`. PPTX via python-pptx native charts and shapes, with no Kaleido or Chrome dependency. |
