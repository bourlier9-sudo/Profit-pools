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
5. **Honest provenance.** Every value can be traced to observed, allocated, derived, estimated, assumed
   or unavailable inputs.

## 2. Module map

```
app.py                                   Streamlit entry (Phase 3)
profit_pools/
  core/                                  ── pure Python ──────────────────────────
    config/          *.yaml + loader      metadata (taxonomy, lobs, categories, geo, periods, metrics, drivers)
    taxonomy.py                           Taxonomy tree, LobCatalog, SpendingCategory, Geography, Periods, Metadata
    data/
      contracts.py                        fictional table schemas + validate_bundle()
      synthetic.py                        deterministic generator (seeded)
      adapters.py                         DataAdapter protocol · SyntheticAdapter · SnowflakeAdapter (stub)
    model/            (Phase 2+)
      baseline.py                         facts → baseline driver cube (history + projected forecast)
      scenario.py                         Scenario / Assumption dataclasses, schema v1, JSON (de)serialization, validation
      resolve.py                          hierarchical scope matching, precedence, conflict detection
      engine.py                           staged pipeline → ModelResult
      ledger.py                           per-assumption lineage + sequential attribution bridge
      growth.py                           realized CAGR, growth decomposition
      views.py                            ModelResult → tidy frames for Marimekko / Sankey / trends (no new math)
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

`DataAdapter` returns contract-shaped DataFrames (`pool_metrics`, `value_flows`) with optional year and state
push-down filters. `SyntheticAdapter` generates them in memory. `SnowflakeAdapter` is an unconnected stub
with documented SQL, and it raises `SnowflakeAdapterNotConfigured`. The adapter is selected by
`PP_DATA_ADAPTER` (default `synthetic`). Adapters never interpret data. Roll-ups, coverage and scenarios
belong to the model.

## 4. Baseline driver cube (Phase 2)

The engine works on a **driver cube** at the finest available grain, for history and forecast years:

| Economics | Cell grain | Drivers (stored) | Derived (computed) |
|---|---|---|---|
| payer_pnl | year × county × payer leaf | `members`, `premium_pmpm`, `opex_pmpm` | revenue, claims, opex, gain, margin, MLR |
| claims flows | year × county × payer leaf × category | `allowed_pmpm`; where reliable, `util_per_1000` and `price_per_service` with `allowed_pmpm = util/1000 × price / 12` | flow $, services |
| opex flows | year × county × payer leaf × opex category | `opex_share` (share of payer residual opex) | flow $ |
| recipients | year × state × recipient leaf | `unattributed_revenue`, `margin` | revenue, gain, residual opex |
| top_down | year × national × leaf | `revenue`, `margin` | gain |

**Forecast years** (2026–2031) are projected from base-year (2025) drivers using baseline annual growth
rates (`growth_drivers` in `drivers.yaml`, with defaults in a baseline-forecast config added in Phase 2).
For example, `members_y = members_{y−1} × (1 + enrollment_growth_y)`. Margins hold at base-year values.
History years are the observed facts with no growth applied.

## 5. Scenario pipeline (Phase 4)

Stages run in a fixed order. Each driver belongs to exactly one stage (see `drivers.yaml → stage`).

| # | Stage | Consumes | Produces |
|---|---|---|---|
| 1 | Growth | baseline growth rates ± growth assumptions (forecast years only) | projected driver levels |
| 2 | Volume | `enrollment` (pct / set / delta), `enrollment_shift` (sum-preserving) | members |
| 3 | Payer unit economics | `premium_pmpm`, `opex_pmpm`, then `payer_margin` (solved via opex PMPM, last) | payer PMPMs |
| 4 | Spending | `allowed_pmpm`; or `utilization` and `unit_price` where service counts are reliable | allowed PMPM, services |
| 5 | Payer financials | — | revenue = premium × mm; claims = Σ allowed; opex = opex_pmpm × mm; gain |
| 6 | Flows | claims flows, opex flows = opex × share, `unattributed_revenue` | inflows by recipient |
| 7 | Recipient financials | `provider_margin` | revenue = Σ inflows; gain = revenue × margin |
| 8 | Top-down | `top_down_revenue`, `top_down_margin` | revenue, gain |
| 9 | Aggregate | filters, taxonomy | roll-ups, coverage, totals, `ModelResult` |

How changes propagate (all through derived values, never by touching outputs directly):

* **Enrollment +x%** → members ↑ → premium revenue, claims and residual opex ↑ (PMPMs constant) →
  payer gain ↑ proportionally → claims flows ↑ → recipient revenue ↑ → recipient gain ↑ at constant
  margin.
* **Allowed PMPM +x%** on Hospitals → claims flows to hospital leaves ↑ → payer claims ↑ → payer gain ↓
  (premium unchanged) → hospital revenue and gain ↑ (constant margin).
* **Utilization vs unit price** (reliable cells only) → both change allowed and therefore the same
  downstream values. They differ only in services and price-per-service outputs. There is no cost-response
  difference.
* **Provider margin +1 pp** → gain of scoped recipients only. Payers untouched.
* **Payer margin −1 pp** → opex PMPM solved so the margin hits target. Revenue and claims unchanged.
  Recipients untouched.
* **Growth +1 pp enrollment growth 2027+** → compounding member path → every downstream value in later
  years, and therefore CAGRs.

### 5.1 Assumption model

```python
Assumption(
  id, label, driver,                 # driver ∈ drivers.yaml
  method,                            # pct | pp | set | delta
  value,                             # 0.05 = +5% (pct) or +5 pp (pp)
  mode,                              # "adjust" (incremental) | "override"
  scope=Scope(
    node_id,                         # ANY taxonomy node: layer, pool or leaf
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

### 5.2 Hierarchical scope resolution (deterministic)

An assumption *matches* a driver cell when the cell's leaf descends from `scope.node_id` (or is it), its LOB
is in `scope.lobs` (LOB groups expand to their LOBs), its geography is inside `scope.geography` (a state matches
its counties), and its year is in range.

**Specificity** of a scope is the tuple `(node depth, LOB specificity, geo specificity)`, compared
lexicographically:

* LOB specificity: all = 0, group = 1, LOB = 2.
* Geo specificity: national = 0, state = 1, county = 2.

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
    where counts are unreliable.
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

### 5.3 No double counting

* Assumptions only change drivers. Each driver is consumed by exactly one stage.
* Recipient revenue is *only* Σ inflows. A spending assumption changes the inflow once, and the recipient
  sees it through the flow, never through a second adjustment.
* Payer margin and recipient margin are distinct drivers with disjoint eligible economics.
* Layer revenues are **gross**: payer claims are provider revenue. "Total market revenue" across layers is
  labeled *gross sector revenue (includes inter-sector payments)*. Operating gain *is* additive across
  layers, because each dollar of gain belongs to one pool.
* Ownership (Phase 4b): intra-organization flows are eliminated in the consolidated view. Consolidated gain
  = payer gain + owned-asset gain, and the elimination nets out of revenue and expense only.

### 5.4 Lineage and the driver bridge

* `ledger.py` records, for each assumption: the cells touched, driver before and after, and Δ for every
  derived metric. The "why did this change" inspector reads these entries.
* **Bridge:**
  * Run the pipeline cumulatively, enabling assumptions one at a time in list order.
  * Step *k* = result(1..k) − result(1..k−1).
  * The steps sum exactly to scenario − baseline. The bridge is order-dependent by design, and the
    order is shown.

### 5.5 Growth and CAGR

* Forecast growth rates are **inputs**.
* Displayed CAGRs are **computed** from resulting values: `(V_end / V_start)^(1/n) − 1`.
* Gain CAGR is "n/m" when either endpoint is ≤ 0 or unavailable.
* No calculation feeds a computed CAGR back into a driver, so there is no circularity.

## 6. `ModelResult`

```python
ModelResult(
  scenario_id, filters,
  pools: DataFrame,      # year × geo × leaf (× LOB for payers): revenue, gain, margin, enrollment, claims, opex, PMPMs, provenance flags
  flows: DataFrame,      # year × geo × source × target × LOB × category × kind: amount, services, method, provenance
  ledger: list[LedgerEntry],
  validation: list[Issue],
  coverage: DataFrame,   # per node × metric: share of revenue with data, provenance mix
)
```

`views.py` turns this into Marimekko tiles, Sankey nodes and links at the chosen granularity, trend series
and tables. Roll-ups keep reconciliation: an aggregated node's value equals Σ of its parts, and tooltips
list the parts.

## 7. Performance and state

* `st.cache_data` caches the adapter frames for the session lifetime.
* The engine runs are cached by `(scenario.content_hash, filters)`. Baseline and scenario each compute once.
* Vectorized pandas on about 20k flow rows × 13 years runs well under a second.
* Session state keys are namespaced `pp.*`. The **draft** scenario (being edited) is distinct from the
  **applied** scenario, which feeds the engine.

## 8. Streamlit compatibility

| Concern | Decision |
|---|---|
| Version | Local target Streamlit ≥ 1.37 (developed on 1.66). |
| Chart clicks | Marimekko is Plotly bars, so `on_select="rerun"` works, plus a synchronized selector that is always present. Sankey: Streamlit returns no Sankey events, so selection is via synchronized controls. No fake double-click. |
| Dialogs | `st.dialog` for the assumption builder, `st.popover` for compact menus. |
| Custom components | None. This keeps the warehouse runtime of Streamlit in Snowflake viable. |
| Styling | CSS injected via `st.markdown(unsafe_allow_html=True)`, with stable `st-key-*` class hooks. Inter via Google Fonts, falling back to a system font stack (Snowflake or offline). |
| Streamlit in Snowflake | Container runtime preferred (newer Streamlit, pip packages). Warehouse runtime: verify the pinned Streamlit version supports `st.dialog` and `on_select`, otherwise fall back to an expander editor. python-pptx must exist in the Snowflake Anaconda channel or the PPTX export is hidden. |
| Exports | HTML via `plotly.io.to_html(include_plotlyjs="inline")`. PPTX via python-pptx native charts and shapes, with no Kaleido or Chrome dependency. |
