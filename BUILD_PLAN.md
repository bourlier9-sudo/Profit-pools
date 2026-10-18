# Build Plan

Status legend: ✅ done · 🔄 in progress · ⏳ planned

| Phase | Scope | Status |
|---|---|---|
| 0 | Project docs, scaffold, requirements, pytest | ✅ |
| 1 | Configuration, taxonomy, synthetic data, contracts, adapters, reconciliation tests (revised: datasets, explicit revenue components, instances, L1–L4) | ✅ |
| 2 | Baseline driver cube, forecast projection, engine (no assumptions), roll-ups, geography, coverage, `ModelResult` | ✅ |
| 3 | App shell, design system, KPI strip, **Marimekko and drilldown**, trends and detail (Section B); allowed / paid / consumer spend | ✅ |
| 4 | Scenario engine: assumptions, hierarchical resolution, validation, ledger, bridge. Builder dialog. Compare mode | ✅ |
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

## Phase 1: configuration and synthetic data ✅ (revised 2026-10-18)

**Deliverables:**
* `core/config/*.yaml`:
  * taxonomy: L1–L4 below the layer, ragged, with PBM Services under Pharmacy
  * datasets: MARKET / ENTERPRISE
  * funding sources: uninsured, government-managed FFS (Medicare FFS, Medicaid FFS), supplemental
  * LOBs, and spending categories (one per recipient leaf, instance availability)
  * fictional geography, periods
  * metric and driver catalogs, baseline forecast rates
  * methodology text
* `core/taxonomy.py`: `Taxonomy` and cross-validated `Metadata`, covering datasets, funding sources and the
  L1–L4 limit.
* `core/data/contracts.py`: three fictional tables (`FACT_POOL_METRICS`, `FACT_VALUE_FLOWS`,
  `FACT_COVERAGE_POPULATION`) and `validate_bundle`.
* `core/data/synthetic.py`: both datasets, 2019–2025. Market payer PMPMs are enterprise benchmarks × a market
  factor. Recipient revenue is built from explicit components with no plug.
* `core/data/adapters.py`: synthetic adapter and an unconnected Snowflake stub.

**Acceptance (met):**
* Zero contract violations across both datasets.
* Enterprise ⊆ market.
* Recipient revenue = Σ named inflows.
* Instances never appear on pharmacy.
* Edge cases are tested.

## Phase 2: baseline model and engine ✅

**Deliverables:**
* `model/cube.py`: driver cube from facts, a baseline forecast to 2031 with hierarchical rate lookup, and a
  pluggable rate function (the hook for scenario growth assumptions).
* `model/engine.py`: the staged pipeline producing `ModelResult` (pools + flows, both datasets, 2019–2031).
* `model/views.py`: roll-up to any node, national/state/county semantics, margin coverage, unavailability
  reasons, trends, totals and realized CAGR.

**Acceptance (met; 83 tests):**
* History reproduces the facts exactly: pools, payer detail, flows and instances.
* Forecast identities hold. Recipient margins are constant.
* Growth compounds from the base year. Allowed PMPM grows by (1+u)(1+p).
* Roll-ups reconcile at every node in both datasets. States sum to national, and counties to state.
* The county view excludes state-level supplemental payments (noted).
* Top-down pools are reported as unavailable below national.
* Margins are computed on revenue with margin data, with coverage reported.
* A baseline run takes about 0.5 seconds.

**Snapshot (national, fictional; revenue $B / gain $B / margin):**

| Layer | Market 2025 | Market 2031 | Enterprise 2025 |
|---|---|---|---|
| Payer | 542 / 4.2 / 0.8% | 829 / 19.2 / 2.3% | 98 / 1.1 / 1.1% |
| Provider | 508 / 33.2 / 6.6% | 713 / 47.1 / 6.6% | 66 / 4.3 / 6.6% |
| Pharmacy | 145 / 6.7 / 4.6% | 228 / 10.8 / 4.8% | 21 / 0.9 / 4.4% |
| Manufacturer | 236 / 46.9 / 19.9% | 322 / 65.6 / 20.3% | — |
| Health Services | 28 / 2.6 / 9.2% | 39 / 3.7 / 9.4% | — |

## Phase 3: shell, design system, Marimekko ✅

**Model addition (user request): allowed vs paid vs consumer spend**
* `FACT_VALUE_FLOWS.PAID_AMOUNT`. Payer `CLAIMS_EXPENSE` is paid, alongside `ALLOWED_SPEND`, `ALLOWED_PMPM` and `PAID_PMPM`.
* The uninsured are all self-pay. Supplemental payments are fully program-paid.
* The engine carries paid and consumer amounts per flow, plus flow-level gain attribution.
* Views take `basis ∈ {allowed, paid, consumer}`.
* The `member_cost_share` driver is catalogued for Phase 4.

**Deliverables:**
* `app.py` and `modules/registry.py`: platform shell with the module registry; only working modules are listed.
* `design/`: tokens (single source), CSS and Plotly template, number formatting, HTML components.
* `charts/marimekko.py`: true variable-width Marimekko as one bar trace with native click selection,
  diverging margin fill, hatching for missing margins, and "est." labels for top-down pools.
* `charts/trends.py`: trend lines with a forecast band and a composition area chart.
* `shell/`: namespaced session state, cached data and engine, sidebar, and a sticky wrapping global bar
  (dataset, year, geography, scope, scenario status).
* `modules/profit_pools/page.py`:
  * **A.** KPIs, toolbar (breadcrumb, segment by, spend basis), Marimekko, legend and coverage notes,
    synchronized selector, drill actions.
  * **B.** Trends (revenue, gain or margin), composition, what-drove-the-change table, and an inspector
    (provenance badges, metrics, revenue by funding source with consumer share, methodology panels).
  * **E.** Summary table.
* `tests/test_app.py`: 42 AppTest smoke tests across every control combination, drill level and selection
  flow.

**Acceptance (met):**
* Opens to the all-pools Marimekko with KPIs.
* Click a tile, then the outline, synchronized selector, inspector and drill buttons all update.
* Drill into a column, then return via the breadcrumb (previous scope stays selected).
* Segment by sub-pools, geography or funding source. Spend basis allowed, paid or consumer.
* The dataset toggle hides market-only pools.
* Missing margins are hatched with coverage notes. Unavailable pools are listed with reasons.
* Reviewed in Chromium screenshots at 1440px and 1024px (controls wrap rather than clip).

**Known limits (by design, until later phases):**
* The scenario chip is a static "Baseline · no scenario" indicator (Phase 4).
* There is no Export menu yet (Phase 7), so nothing is shown rather than a dead control.
* Driver attribution covers baseline year-over-year change only.

## Phase 4: scenario engine and builder ✅

**Model update (user):** government-managed insurance, with Medicare FFS and Medicaid FFS as separate
funding sources (group "Government-managed (FFS)"), replaces the single "traditional Medicare & public" source.

**Deliverables:**
* `model/scenario.py`: Assumption, Scope, YearRange and Scenario (immutable), editing helpers, schema-v1 JSON
  round-trip with structural and version validation, content hash.
* `model/resolve.py`: scope matching (any taxonomy level, LOB or funding sources and groups, state or county,
  dataset, years), specificity, replace/stack resolution, conflict and supersession detection.
* `model/scenario_engine.py`: driver catalog validation (eligibility, data requirements, units, years,
  datasets), stage-ordered application.
  * Levels, totals distributed per dataset-year, enrollment shifts (incl. to the uninsured), growth-rate
    overrides via the pluggable rate function.
  * Payer margin solved via opex, opex volume scaling, member cost share.
  * Result sanity checks, ledger, sequential **bridge**, single-assumption **preview**.
* `engine.py`: opex volume scaling and payer-margin targets (stage 3, last).
* UI:
  * The scenario chip opens a **builder dialog**:
    * rows with enable, reorder, edit, duplicate and delete;
    * per-row status and issues;
    * an add/edit form with drivers grouped by type and the pool picker filtered by eligibility;
    * sources, geography, years, method, value with units, Replace/Stack, shift target, preview;
    * methodology panels;
    * Apply, Discard, Reset, Export JSON, Import JSON, and the illustrative example.
  * **View** control (Baseline / Scenario / Compare) and scenario banner.
  * KPIs Δ vs baseline. Marimekko tooltips Δ and **color by Δ margin**. Trends with faint baseline lines.
  * **"Why the scenario differs"** bridge table, and inspector **"Why it changed"**.
  * Summary-table Δ columns.
  * Phone-width CSS: KPIs wrap two per row, tighter padding.
* Tests: 40 scenario-engine tests (propagation, no double counting, resolution, validation, growth/CAGR,
  bridge, JSON) plus 13 scenario UI tests. **205 tests total.**

**Acceptance (met):**
* One engine. Every view in Scenario and Compare reads the same cached scenario run.
* Enrollment, spending, margin, cost-share, shift and growth assumptions each propagate exactly once (tested).
* Conflicts block apply. Supersessions are flagged.
* The bridge sums to the total change.
* The builder flow was verified in Chromium: load example → apply → compare.

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
| 2026-10-18 | **Revised (user):** the market is primary. Market payer PMPMs are benchmarks from enterprise data. Recipient revenue = explicit components (payer claims, uninsured, government-managed FFS (Medicare FFS, Medicaid FFS), supplemental), with no unattributed plug. |
| 2026-10-18 | **Revised (user):** a dataset toggle covers market vs the company book. The company view excludes Manufacturer, Health Services and PBM Services. The company label is configurable, and the fictional build uses "Enterprise (fictional)". |
| 2026-10-18 | **Revised (user):** volume is a single "instances" count, provider only, never pharmacy (admin vs drug claims). Utilization and opex volume scaling are user-entered estimates only. |
| 2026-10-18 | **Revised (user):** PBM Services moves under Pharmacy (top-down, Marimekko only, no flows). Hierarchy depth: Payer and Manufacturer L1–L2, Health Services L1–L3, Provider and Pharmacy ≤ L4. |
| 2026-10-18 | Enterprise recipient gain uses the pool's market margin: profit associated with enterprise members, not enterprise profit. |
| 2026-10-18 | Market and enterprise drivers are independent in the engine. Benchmarks are applied when the data is built, not re-linked during scenarios. |
| 2026-10-18 | **Revised (user):** allowed vs paid. Payer claims = paid. Recipient revenue = allowed. Consumer spend = allowed − paid, viewable through a spend-basis control. |
| 2026-10-18 | **Decided (user):** overlapping assumptions **conflict by default**. The most specific replaces broader ones; equal specificity blocks; `stack` is explicit. |
| 2026-10-18 | **Confirmed (user):** enterprise recipient gain = enterprise spend × the pool's market margin. |
| 2026-10-18 | **Revised (user):** government-managed insurance is split into Medicare FFS and Medicaid FFS funding sources. |
| 2026-10-18 | PPTX via python-pptx native charts and shapes. No Kaleido or Chrome dependency. |
