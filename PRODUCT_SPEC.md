# Product Spec — Profit Pools Explorer

> A personal, independently developed prototype on **entirely fictional data**. It contains no corporate
> code, data, credentials or proprietary calculation rules.

## 1. Purpose

The product helps executives and analysts understand how revenue, operating gain, margins, enrollment and
spending interact across a fictional healthcare economy. It answers three connected questions from **one
analytical model**:

1. Where are revenue and operating gain generated across sectors?
2. How does spending flow between payers, providers, pharmacy and other participants?
3. How would changes in enrollment, spending, margins, growth or ownership alter those economics?

## 2. Users

* **Senior executives:** need the market picture in seconds, plus credible "what if" answers.
* **Analysts:** need to build multi-assumption scenarios, inspect lineage and export results.

## 3. Platform shell

* A persistent, restrained left navigation lists the modules from a registry. Only **Profit Pools Explorer**
  is registered. Future modules (Enrollment Intelligence, Quality Intelligence, Healthcare Services
  Analytics) are added by registering them, with no nonfunctional placeholders.
* A global bar holds **Dataset** (Total market / Enterprise), Year, Geography, View (Baseline / Scenario /
  Compare / Impact only), Scope, **Pool groupings**, the Scenario chip and Export.
  * **Impact only** greys pools the scenario does not touch and shows just the change, for all assumptions
    or one assumption run on its own; tiles sized by pool or by size of change.
  * **Pool groupings** (separate from scenarios): rename pools, move them between groups, add groups; reset
    to the standard taxonomy; import/export JSON. Numbers never change, only the hierarchy.
  * The Enterprise label is configurable and ships as "Enterprise (fictional)".
  * The Enterprise view hides Manufacturer, Health Services and PBM Services.
* A contextual inspector on the right shows details for the current selection.
* A consistent export menu.

## 4. Unified workspace (one scrolling page)

| Section | Content |
|---|---|
| **A. Executive overview** | KPI strip (revenue, operating gain, margin, enrollment, Δ vs prior year or baseline); hero **Marimekko** with drilldown, breadcrumbs, tooltips and coverage notes |
| **B. Pool trends and detail** | Revenue and gain trends, margin trends, composition change, selected-pool detail, summary table, key drivers of change |
| **C. Value flows** | Configurable Sankey, flow inspector, tabular alternative |
| **D. Growth and forecasting** | Indexed growth, CAGR table (history / period / baseline), revenue-growth drivers waterfall, YoY heatmap |
| **Value capture** (Enterprise) | Consolidated revenue, gain and margin; consolidation bridge; owned assets (routed share, owned vs market margin) |
| **E. Supporting detail and exports** | Full tables, coverage and provenance, downloads |

There is no separate page for assumptions. The assumption builder opens from the scenario chip anywhere.

## 5. Economic model (agreed rules)

1. **Two datasets, one model.**
   * **Total market** is the primary view. Market payer cells are market enrollment × PMPM benchmarks derived
     from the enterprise's experience.
   * **Enterprise** is the company's observed book. It excludes Manufacturer, Health Services and PBM Services.
   * Both run through the same engine.
2. **Payers:** gain = revenue − claims − residual opex.
   * Residual opex is derived. Its PMPM is held constant in scenarios, so it scales with enrollment.
   * A user may enter an opex volume-scaling assumption, but no default estimate is offered.
   * Claims-PMPM changes move payer gain, and premium does not move unless separately assumed.
3. **Provider and dispensing-pharmacy revenue** is the sum of explicit estimated components, with no plug:
   * payer claims (members × allowed PMPM by category),
   * uninsured & self-pay (population × PMPM),
   * government-managed insurance — Medicare FFS and Medicaid FFS (population × PMPM),
   * supplemental & state payments (hospitals only, annual by state).

   Gain = revenue × operating margin. The margin is constant unless explicitly assumed. Remaining expense is a
   derived residual. In the Enterprise view, recipient revenue is the enterprise's own claims spend with the
   pool, valued at the pool's market margin.
4. **Allowed vs paid vs consumer spend.**
   * Provider and pharmacy revenue = **allowed** (everything received).
   * Payer claims expense = **paid**.
   * **Consumer spend** = allowed − paid (member cost sharing and self-pay; the uninsured are all self-pay).
   * A *spend basis* control (Allowed / Payer-paid / Consumer) applies to every flow-based view. Payer and
     top-down pools have no split and are reported as unavailable on the paid and consumer bases.
5. **Service volume** is a single **instance** count on provider categories where available, never pharmacy.
   Allowed = instances × average allowed per instance. Utilization changes are user-entered assumptions,
   and neither utilization nor price changes margin.
6. **Top-down pools** (Manufacturer, Health Services, PBM Services under Pharmacy) are national revenue and margin
   estimates only. They appear in the Marimekko and never in flows.
7. **Payer margin and provider margin assumptions are separate**, and they never cross-apply.
8. **Hierarchy depth below each layer:** Payer and Manufacturer L1–L2, Health Services L1–L3, Provider and
   Pharmacy up to L4.
9. **Ownership:** the enterprise owns care-delivery assets inside provider and pharmacy pools. Routing a
   share of its members' care to them never changes spending or pool revenue; it changes who keeps the
   gain. Consolidated gain = plan gain + owned-asset gain (intra-organization claims eliminated).

## 6. Scenarios

* A scenario has an id, name, description, base year, schema version and an ordered list of assumptions.
  Each assumption has a driver, method (pct / pp / set / delta), mode (adjust / override), value, scope, an
  enabled flag and notes.
  * The scope covers dataset (market / enterprise / both), a taxonomy node at **any level**, LOBs or LOB
    groups, funding sources, geography and years.
* **Overlapping assumptions conflict by default.**
  * The more specific scope replaces broader ones on shared cells, flagged as superseded.
  * Equally specific overlaps block Apply until resolved.
  * An assumption explicitly set to **Stack** compounds instead (ARCHITECTURE.md §6.2).
* Only drivers valid for the selected node's economics and data coverage are offered.
* Invalid inputs produce validation messages and block apply. Results never silently go inconsistent.
* **Guided assumptions:** Where (pool or funder) → What (Enrollment / Financials / Growth) → How much & when
  (with optional phase-in). Levers are worded as trends with an explicit payer, e.g. "Reimbursement rate
  trend — price paid per service, paid by Commercial plans", never "increase hospital revenue", and each
  shows who pays whom so cascading effects are visible.
* **Builder UX** (opened as a dialog from the chip "Scenario A · 4 changes"):
  * Create, rename, add, edit, duplicate, enable or disable, delete and reorder assumptions.
  * Each assumption shows a preview of the affected descendants and totals.
  * Reset, compare with baseline, and import or export JSON.
  * A **"How is provider revenue estimated?"** panel shows text from `methodology.yaml` for the active dataset.
    Payer-economics and top-down notes sit alongside it.
  * Draft edits stay distinct from the applied scenario.

## 7. Value flows

* Sources are payer segments plus, in the market view, the uninsured, government-managed FFS (Medicare FFS, Medicaid FFS), and
  supplemental payments. Top-down pools, including PBM Services, never appear in flows.

* Users choose the visible columns: source groups and/or detail, an optional recipient layer, and recipient
  depth with per-pool expansion. Consumer spend can be shown as its own source.
* Granularity is set **independently per dimension**: payer LOB (all / group / individual LOB), provider
  category (layer / pool / leaf), and spending category.
* Users can filter LOBs and recipients, set a minimum-flow threshold with an "Other" roll-up, and choose a
  metric and unit.
* There is a baseline / scenario / Δ view and a table alternative.
* Roll-ups reconcile exactly. Tooltips and the inspector list the underlying parts.
* Inspector content: source and destination, annual value, share of parent totals, enrollment and PMPM or
  utilization where applicable, related revenue, gain and margin, the allocation method, and Δ vs baseline.

## 8. Growth

* Baseline projections cover 2026–2031 from base year 2025.
* Users can make growth assumptions by layer, pool, LOB and geography.
* Realized CAGRs are computed from results for revenue, gain and enrollment. Gain CAGR is "n/m" when an
  endpoint is ≤ 0.
* There is a baseline vs scenario chart and a driver bridge (waterfall).

## 9. Exports

| Export | Contents |
|---|---|
| CSV: selected analysis | Rows behind the current view (Δ vs baseline when comparing) |
| ZIP: scenario outputs | pools.csv, flows.csv, ownership.csv (state × leaf, all years, baseline / scenario / Δ), README, scenario.json |
| JSON: scenario | Configuration only, schema-versioned. Import validates version and structure |
| HTML snapshot | Self-contained charts and KPIs, labeled as a snapshot (not a simulator) |
| PPTX summary | Title, KPIs, Marimekko (native shapes), trend and growth-driver charts (native), largest flows, scenario assumptions, value capture. Hidden if python-pptx is missing |

All exports come from the Export menu in the global bar and are generated on click.

## 10. Data coverage communication

Every value is classed Observed / Allocated / Derived / Estimated / Assumed / Unavailable. Missing data is
never zero. Top-down pools stay visible but never imply detail they lack.

## 11. Acceptance criteria

1. The user opens the app and immediately understands the profit distribution.
2. A legible Marimekko with meaningful colors and hover information.
3. Drill into a pool and back, and see related trends.
4. Inspect spending flows.
5. Create a scenario with several interacting, hierarchical assumptions.
6. Apply it, and the Marimekko, Sankey, KPIs and growth all change together.
7. Inspect exactly why a value changed (lineage).
8. Compare baseline and scenario.
9. Export results, and save and reload assumptions.
10. Understand when data is missing or estimated.

No dead buttons, fake interactivity, misleading precision, or inconsistent calculations.

## 12. Out of scope (prototype)

Real data or connections, authentication, multi-user persistence, real state names, and an offline scenario
simulator in the HTML export.
