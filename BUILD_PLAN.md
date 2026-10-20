# Build Plan

Status legend: ✅ done · 🔄 in progress · ⏳ planned

| Phase | Scope | Status |
|---|---|---|
| 0 | Project docs, scaffold, requirements, pytest | ✅ |
| 1 | Configuration, taxonomy, synthetic data, contracts, adapters, reconciliation tests (revised: datasets, explicit revenue components, instances, L1–L4) | ✅ |
| 2 | Baseline driver cube, forecast projection, engine (no assumptions), roll-ups, geography, coverage, `ModelResult` | ✅ |
| 3 | App shell, design system, KPI strip, **Marimekko and drilldown**, trends and detail (Section B); allowed / paid / consumer spend | ✅ |
| 4 | Scenario engine: assumptions, hierarchical resolution, validation, ledger, bridge. Builder dialog. Compare mode | ✅ |
| 4b | Ownership and value capture: integrated org, owned asset routing, consolidation | ✅ |
| 5 | Sankey with per-dimension granularity, roll-ups, threshold; flow inspector; table view | ✅ |
| 6 | Growth and CAGR section, indexed growth, revenue-growth drivers, YoY heatmap. Guided assumption builder, Impact-only view, pool groupings | ✅ |
| 7 | Exports: CSV, scenario JSON import/export, HTML snapshot, PPTX | ✅ |
| 8 | Visual polish at 1440px, responsiveness, performance, end-to-end consistency tests | ✅ |
| 9 | Gain bridge rethink, Sankey levels + enterprise dollar, where to play, scenario library and comparison, data dictionary, guided tour, cleaner PPTX | ✅ |

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
* The Export menu arrived in Phase 7.
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

## Phase 5: Sankey ✅

**Deliverables:**
* `model/flows_view.py`: configurable Sankey views, including:
  * source group and/or detail columns;
  * an optional recipient-layer column;
  * recipient depth 0–4 with per-node expansion;
  * source and recipient filters, and an "Other" threshold;
  * the consumer split, and a recipient-gain metric;
  * node and link inspector facts, and compare deltas.
* `charts/sankey.py`: identity colours; Δ-coloured links in Compare; red loss links for the gain metric;
  selection highlight.
* `modules/profit_pools/flows_section.py`: Section C with controls, the Sankey or table, reconciliation notes,
  a selectable flow table, a node selector and the inspector (source/destination, shares, allowed, paid and
  consumer, attributed gain and margin, enrollment and PMPM, instances, pool economics, method, roll-up
  contents, Δ vs baseline).
* Config fix: the "Aged, Blind & Disabled" label was split by an unquoted comma in YAML. A guard test now
  catches stray keys.
* Tests: 127 view tests across a grid of configurations, plus 10 UI tests. **343 tests total.**

**Acceptance (met):**
* Every column sums to the same total under every configuration, and links equal node values.
* Allowed equals recipient revenue.
* Paid + consumer = allowed.
* Folding, expansion and filters reconcile.
* Gain matches recipient gain.
* The table selection drives the inspector, verified in Chromium.
* Compare colouring follows the scenario (Commercial and MA up, Medicaid down in the example).

## Phase 6: growth, guided assumptions, impact view, pool groupings ✅

**Deliverables:**
* **Standard taxonomy refresh** (public industry framework used for structure only; all figures fictional):
  Health Services & Technology (HST) = Software & platforms (payer platforms, provider software), Data &
  analytics, Tech-enabled services (RCM, care management, enablement), Behavioral health services.
  Pharmacy = Pharmacy Services & Dispensing; Manufacturer = Life Sciences Manufacturers.
* **Guided assumption builder** (`shell/assumption_wizard.py`): Where (pool at any level, or a funder) →
  What (Enrollment / Financials / Growth; only levers valid for that pool) → How much & when (value, years,
  optional linear **phase-in**). Levers are worded as trends with an explicit payer and payee ("Reimbursement
  rate trend — price paid per service", paid by …), and each shows a plain-English sentence of who pays whom
  plus the driver's `explains` line, so cascades are never hidden. Live preview: Δ gain by layer per year.
  Replaces the old driver form; the Excel-style expert table was dropped (user decision).
* **Impact-only view** (View → Impact only): greys every pool the scenario does not touch and shows only the
  change. "Impact of" runs all assumptions or **one assumption on its own** against the baseline. Tile size
  = pool size or size of change (`views.impact_layout`); measure = Δ gain or Δ revenue. KPIs, trends (Δ by
  year), the Sankey (link width = |Δ|, teal up / red down) and tables all show deltas.
* **Growth & forecasting section** (`core/model/growth.py`, `growth_section.py`): indexed growth (start
  year = 100) with baseline overlay; CAGR table (history, chosen period, baseline, gain CAGR, Δ margin);
  **revenue-growth bridge** splitting the change into plan enrollment, premium rate, members covered,
  utilization, price per service, spend per member, uninsured & government FFS, supplemental, top-down
  growth and mix; YoY heatmap on the shared diverging scale.
* **Pool groupings** (`core/grouping.py`, `shell/grouping_editor.py`): a separate dialog to rename pools,
  move them under another group and add groups, with native controls (no drag-and-drop, user decision).
  Draft vs apply, reset to standard, JSON import/export. Invalid moves (into a data-holding pool, into its
  own subtree, payer segments out of Payer) are never offered and are refused by the core. Values never
  change under a grouping; only roll-ups and labels do. Every cached result is keyed by the grouping.
* Fixed: Sankey depth labels were a fixed list (broke under deeper groupings); the margin legend's neutral
  tick now sits at the true 0% position.
* Tests: wizard (1,576 pool × lever combinations validate and round-trip), growth bridge reconciliation at
  every grain, impact layout identities, grouping editor and grouped workspace (AppTest). **446 tests total.**

**Acceptance (met):**
* Every lever the builder offers validates and matches data; pharmacy never offers volume levers; the
  enterprise never offers market-only funders.
* The growth bridge sums exactly to the change in revenue (residual 0) for every node, dataset and geography
  tested; a price assumption lands in "Price per service" only.
* Impact tiles reconcile to the roll-up delta; size-by-change shares sum to 1.
* Screenshots reviewed at 1440 and 1024 (builder, impact view, groupings dialog, growth section).

## Phase 4b: ownership and value capture ✅

**Deliverables:**
* `config/ownership.yaml`: a fictional integrated organization (the enterprise plan) with four owned assets,
  each inside one recipient leaf (primary care, behavioral health clinicians, home health, mail-order
  pharmacy) in named states, with a routing share, a margin offset to the market margin, and a third-party
  revenue ratio.
* `core/model/ownership.py`: the ownership driver frame (asset × state × year, built in `project()`), the
  engine stage `value_capture()` (stage 10) and `consolidate()`. `ModelResult.ownership` carries the result.
* Two scenario drivers, consumed only by stage 10: **routing_share** ("Share of our members' care delivered
  by owned assets") and **owned_margin**. Enterprise only, state grain, pools containing an owned asset only.
  The guided builder offers them on those pools.
* `views.ownership_view()` and the **Value capture** section (Enterprise dataset): consolidated revenue,
  gain and margin; the consolidation bridge (plan revenue + owned revenue − eliminated intra-organization
  claims); an owned-asset table with owned vs market margin; Δ vs baseline in Compare.
* Tests: definitions, enterprise spend = flows, consolidation identities at national and state grain,
  county = unavailable, routing changes only ownership (pools identical), owned margin and provider margin
  never cross-apply, spending assumptions flow through to owned assets, validation codes.

**Acceptance (met):**
* Consolidated gain = plan gain + owned gain; consolidated revenue − expense = gain (elimination nets out).
* Routing never changes spending, pool revenue or pool gain.

## Phase 7: exports ✅

**Deliverables:**
* `profit_pools/export/` (outside `core`, because it uses the chart builders): `ExportContext` mirrors the
  current filters; `csv_export` (rows behind the view; ZIP of pool, flow and ownership results at state ×
  leaf grain for every year, baseline / scenario / Δ, README and scenario JSON); `html_snapshot`
  (self-contained, Plotly inlined once, labelled "not a simulator"); `pptx_summary` (python-pptx native
  KPI boxes, Marimekko as one shape per tile with the app's own tile colours, native line and bar charts,
  flow, scenario and value-capture tables).
* Export menu in the global bar. Files are generated only when clicked (callable download data). Scenario
  JSON is disabled with a reason when no scenario is shown; PPTX is hidden if python-pptx is missing.
* `charts/marimekko.tile_fill()` is shared by the chart and the PPTX so colours never diverge.

**Acceptance (met):**
* Every export parses back to the workspace's numbers (CSV = roll-up; ZIP totals = engine totals; scenario
  JSON round-trips).
* The HTML loads nothing from the network. The PPTX opens, has one native shape per Marimekko tile, and
  native charts. Slides were rendered and reviewed (LibreOffice → PDF).

## Phase 8: polish ✅

**Deliverables:**
* Performance: memoized node × year aggregate per result (`views._agg`), vectorized `_finish`, cached
  ancestor maps, vectorized forecast-rate lookup. Measured in AppTest: cold start 2.9 s (was 6.4 s
  profiled), warm rerun 0.5 s, Compare 0.9 s; first application of a 5-assumption scenario 3.5 s (the
  sequential attribution runs the engine once per assumption, then is cached).
* `tests/test_consistency.py`: one scenario agrees across KPIs, Marimekko, roll-up, CSV, Sankey, trends,
  growth bridge, attribution (sums to the KPI Δ), impact tiles and pool groupings; the memo never serves
  stale sums for a new result.
* Layout checked at 1440 and 1024: Value capture KPIs and asset table simplified so nothing truncates.
* **501 tests total.**

**Acceptance (met):**
* Screenshots reviewed at 1440 and 1024.
* One scenario changes the Marimekko, Sankey, KPIs, growth and exports consistently (tested).
* Cold start under 3 seconds.

## Phase 9: analyst experience ✅

**Deliverables:**
* **Growth bridge rebuilt** (`growth.bridge`): overall = operating gain by economic driver (additive, no
  double counting); "Bridge for" picks one pool with economics-specific detail (plans: enrollment, premium,
  medical and admin cost; providers & pharmacy: people, utilization, price, spend per person, supplemental,
  margin; top-down: growth by sub-pool + margin). Uninsured / FFS are no longer lines: every inflow decomposes
  by people × services × price via the new `flows.people` column. Revenue available for single pools.
* **Sankey**: Expand pools tree (level by level, fold removes descendants) and Expand / Fold buttons in the
  inspector; Arrange nodes (order + pins, explicit positions); **Enterprise dollar** mode
  (`enterprise_flow.py`) — premiums → outside providers / owned assets / admin / plan gain; owned assets +
  members' cost sharing + other payers → costs / gain; all gain → total enterprise operating gain; loss-makers
  via a Losses node; "Operating gain only" focus. Owned assets now only operate in states with enterprise plans.
* **Where to play** bubble chart (growth vs margin, size = gain, averages, baseline→scenario arrows).
* **Scenario library** (session, seeded with three fictional examples, applied scenarios saved automatically,
  JSON download/upload) and **Compare scenarios** section (up to three vs baseline).
* **Data dictionary** page (glossary.yaml + config + contracts; coverage matrix; ZIP download).
* **Guided tour** (spotlight, 14 steps, auto-start once, replay from the sidebar, scroll script).
* **PPTX**: optional user template (in memory, fitted titles, sample slides dropped, any slide size);
  generated headlines; executive summary; where profit moves (two Marimekkos); where to play (native bubble);
  "Other" folding and fit-aware labels; notes slide. HTML snapshot uses the same headlines.
* Status badges use Streamlit badges and Material icons instead of emoji.
* **619 tests total.**

**Acceptance (met):**
* Bridges reconcile exactly for every node, dataset and geography, gain and revenue; overall = Σ layers.
* Enterprise flow balances at every node and nets to the Value capture consolidated gain.
* Every export and the template path parse back; screenshots reviewed at 1440 and 1024 (tour, Sankey modes,
  where to play, comparison, dictionary, slides rendered through LibreOffice).

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
| 2026-10-20 | **Decided (user):** assumptions are worded as trends with an explicit payer (e.g. "reimbursement rate trend paid by plans"), never as "increase pool revenue", so cascading effects are implied. |
| 2026-10-20 | **Decided (user):** no Excel-style expert table. Pool groupings use simple native controls instead of drag-and-drop (MVP, low processing cost). |
| 2026-10-20 | **Decided (user):** the standard taxonomy follows the public industry profit-pool framework (sector → sub-pools); all values stay fictional. |
| 2026-10-20 | Ownership: routing moves enterprise members' care to owned assets inside the same pool, so spending and pool revenue never change; only who keeps the gain does. Owned margin = market margin + an asset offset, adjustable only by its own driver. |
| 2026-10-20 | Exports live outside `core` (they use chart builders) and are generated on click. |
| 2026-10-20 | **Decided (user):** the growth waterfall is overall first, then per pool, because detail differs by pool (HST vs payer). Overall uses operating gain (additive). |
| 2026-10-20 | **Decided (user):** guided tour with a scroll script; scenario library and side-by-side comparison; where-to-play chart. think-cell export tabled. |
| 2026-10-20 | Insourced gain reaches the Sankey through a separate "Enterprise dollar" flow (one currency: enterprise dollars), not as gain nodes on the spending Sankey. |
| 2026-10-20 | Impact-only "one assumption on its own" runs that assumption alone against the baseline (standalone effect). The sequential bridge remains the attribution that sums to the full scenario. |
