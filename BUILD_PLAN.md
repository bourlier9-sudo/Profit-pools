# Build Plan

Status legend: ✅ done · 🔄 in progress · ⏳ planned

| Phase | Scope | Status |
|---|---|---|
| 0 | Project docs, scaffold, requirements, pytest | ✅ |
| 1 | Configuration, taxonomy, synthetic data, contracts, adapters, reconciliation tests | ✅ |
| 2 | Baseline driver cube, forecast projection, engine skeleton (no assumptions), aggregation, `ModelResult` | ⏳ |
| 3 | App shell, design system, KPI strip, **Marimekko and drilldown**, trends and detail (Section B) | ⏳ |
| 4 | Scenario engine: assumptions, hierarchical resolution, validation, ledger, bridge. Builder dialog. Compare mode | ⏳ |
| 4b | Ownership and value capture: integrated org, owned asset routing, consolidation | ⏳ |
| 5 | Sankey with per-dimension granularity, roll-ups, threshold; flow inspector; table view | ⏳ |
| 6 | Growth and CAGR section, baseline vs scenario, driver waterfall | ⏳ |
| 7 | Exports: CSV, scenario JSON import/export, HTML snapshot, PPTX | ⏳ |
| 8 | Visual polish at 1440px, responsiveness, performance, end-to-end consistency tests | ⏳ |

---

## Phase 0: docs and scaffold ✅

**Deliverables:** CLAUDE.md, PRODUCT_SPEC.md, ARCHITECTURE.md, DESIGN_SYSTEM.md, DATA_CONTRACTS.md,
BUILD_PLAN.md, `requirements.txt`, `pyproject.toml` (pytest config), package layout.

**Acceptance:** the documents reflect the agreed economics, which are the constant recipient margin,
derived payer residual opex, allowed = claims = receipts, optional volume decomposition, and hierarchical
assumptions.

## Phase 1: configuration and synthetic data ✅

**Deliverables:**
* `core/config/*.yaml`: taxonomy (arbitrary depth, inherited attributes), LOBs, spending categories with
  service units and reliability, fictional geography, periods, metric catalog, driver catalog.
* `core/taxonomy.py`: `Taxonomy`, `Metadata` and cross-validation.
* `core/data/contracts.py`: fictional schemas and `validate_bundle` (12 rule families).
* `core/data/synthetic.py`: deterministic generator, 2019–2025, 6 states, 21 counties, 9 LOBs, 14 claims
  categories.
* `core/data/adapters.py`: `DataAdapter` protocol, `SyntheticAdapter`, unconnected `SnowflakeAdapter`, and a factory.
* Tests: 43 passing (taxonomy, reconciliation, edge cases, validator corruption tests, adapters,
  documentation sync).

**Acceptance (met):**
* The dataset passes every contract validation, including payer PMPM identities, claims ↔ flows,
  recipient revenue ↔ inflows, and gain = revenue × margin.
* County → state → national sums agree.
* The built-in edge cases are present and tested: negative margins, missing margins, absent LOB, partial
  attribution, unreliable service counts, top-down pools.
* Adding a taxonomy breakdown or a deeper level is configuration-only (tested).

**Snapshot (2025, national, fictional):**

| Layer | Revenue | Gain | Margin |
|---|---|---|---|
| Payer | $537B | $5.9B | 1.1% |
| Provider | $693B | $37.9B (97% margin coverage) | 5.5% |
| Pharmacy | $199B | $7.3B | 3.7% |
| Manufacturer | $218B | $45.1B | 20.7% |
| Health Services | $45B | $4.7B | 10.3% |

## Phase 2: baseline model and engine skeleton ⏳

**Deliverables:**
* `core/config/baseline_forecast.yaml` (default growth rates by LOB, category and pool).
* `model/baseline.py`: driver cube from facts, with forecast projection to 2031.
* `model/engine.py`: stages 1–9 with no assumptions applied, producing `ModelResult`.
* `model/views.py`: roll-up to any taxonomy node, geography filter, and coverage reporting.

**Acceptance:**
* For history years, engine output equals the facts exactly.
* Forecast identities hold.
* Roll-ups reconcile at every hierarchy level and geography.
* Coverage is reported for missing margins.
* Top-down pools are excluded from state and county views, with an explanation.

## Phase 3: shell, design system, Marimekko ⏳

**Acceptance:**
* `streamlit run app.py` renders the shell, the global bar, the KPI strip and the hero Marimekko.
* Drilldown and breadcrumbs work through `on_select` and the synchronized selector.
* Tooltips show provenance and missing-margin hatching.
* Section B trends follow the selection.
* Screenshots are reviewed at 1440px.

## Phase 4: scenario engine and builder ⏳

**Acceptance:**
* Hierarchical scopes resolve per ARCHITECTURE.md §5.2.
* Conflicts and supersessions are reported.
* Propagation tests cover enrollment, allowed PMPM, utilization/price, payer and provider margin,
  unattributed revenue and top-down drivers.
* No double counting.
* The bridge sums to the total Δ.
* The builder dialog supports full CRUD, reorder, enable/disable, preview, draft vs applied, and reset.

## Phase 5: Sankey ⏳

**Acceptance:**
* Granularity is independent for LOB, recipient and category.
* Roll-ups and the "Other" threshold preserve totals (tested).
* The inspector shows the attribution method and Δ.
* The table view matches the chart.

## Phase 6: growth ⏳

**Acceptance:**
* Growth assumptions change forecast years everywhere.
* Realized CAGRs are recomputed from results, with "n/m" handling.
* The waterfall reconciles.

## Phase 7: exports ⏳

**Acceptance:**
* Every export control produces a valid file (tested by parsing it back).
* JSON round-trips, and bad versions or structures are rejected with messages.
* The HTML is self-contained.
* The PPTX opens and contains the native-shape Marimekko.

## Phase 8: polish ⏳

**Acceptance:**
* Playwright screenshots at 1440 and 1024 are reviewed.
* End-to-end tests prove that one scenario changes the Marimekko, Sankey, KPI and growth views
  consistently.
* A cold start renders in under 3 seconds.

---

## Decision log

| Date | Decision |
|---|---|
| 2026-10-18 | Recipient economics use a constant operating margin. No variable/fixed-cost model (user directive). |
| 2026-10-18 | Payer residual opex is derived. Its PMPM is held constant in scenarios (user directive). |
| 2026-10-18 | Allowed spend is used as both payer claims and provider receipts, with the caveat documented (user directive). |
| 2026-10-18 | Service-count decomposition only where counts are reliable. Units are never mixed (user directive). |
| 2026-10-18 | Fictional state and county names, to avoid implying real data. |
| 2026-10-18 | Facts are stored at their finest grain only, with no pre-aggregated rows, to prevent double counting. |
| 2026-10-18 | No ADMIN_EXPENSE column. Residual opex is derived by the model. |
| 2026-10-18 | PPTX via python-pptx native charts and shapes. No Kaleido or Chrome dependency. |
