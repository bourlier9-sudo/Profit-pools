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
* A global bar holds Year, Geography, View (Baseline / Scenario / Compare), Scope, the Scenario chip and Export.
* A contextual inspector on the right shows details for the current selection.
* A consistent export menu.

## 4. Unified workspace (one scrolling page)

| Section | Content |
|---|---|
| **A. Executive overview** | KPI strip (revenue, operating gain, margin, enrollment, Δ vs prior year or baseline); hero **Marimekko** with drilldown, breadcrumbs, tooltips and coverage notes |
| **B. Pool trends and detail** | Revenue and gain trends, margin trends, composition change, selected-pool detail, summary table, key drivers of change |
| **C. Value flows** | Configurable Sankey, flow inspector, tabular alternative |
| **D. Growth and forecasting** | Baseline vs scenario projections, realized CAGRs, driver bridge |
| **E. Supporting detail and exports** | Full tables, coverage and provenance, downloads |

There is no separate page for assumptions. The assumption builder opens from the scenario chip anywhere.

## 5. Economic model (agreed rules)

1. **Payers:** gain = revenue − claims − residual opex.
   * Residual opex is derived from observed revenue, claims and gain.
   * In scenarios, residual opex PMPM is held constant, so it scales with enrollment.
   * A claims-PMPM change moves payer gain one-for-one, and premium does not move unless separately assumed.
2. **Providers, pharmacy and other flow recipients:** revenue = attributed payer allowed spend plus an
   unattributed residual. Gain = revenue × operating margin.
   * The margin is constant unless explicitly assumed.
   * There is no variable/fixed-cost or operating-leverage model.
   * Remaining expense is a derived residual.
3. **Allowed spend** is treated as both the payer's modeled claims outflow and the provider's receipt. That
   allowed amounts include member cost sharing is a documented caveat.
4. **Service volume** (optional): allowed = services × average allowed per service, only where counts are
   reliable. Utilization vs price assumptions are offered only there, and neither changes margin.
5. **Top-down pools** (Manufacturer, some Health Services): national revenue and margin estimates only. They
   have no LOB, geography or flow attribution.
6. **Payer margin and provider margin assumptions are separate**, and they never cross-apply.

## 6. Scenarios

* A scenario has an id, name, description, base year, schema version and an ordered list of assumptions.
  Each assumption has a driver, method (pct / pp / set / delta), mode (adjust / override), value, scope
  (taxonomy node at **any level**, LOBs or LOB groups, geography, years), enabled flag and notes.
* Overlapping assumptions resolve deterministically by specificity, override vs adjust, then list order.
  Conflicts, superseded adjustments and order-sensitive cells are shown to the user (ARCHITECTURE.md §5.2).
* Only drivers valid for the selected node's economics and data coverage are offered.
* Invalid inputs produce validation messages and block apply. Results never silently go inconsistent.
* **Builder UX** (opened as a dialog from the chip "Scenario A · 4 changes"):
  * Create, rename, add, edit, duplicate, enable or disable, delete and reorder assumptions.
  * Each assumption shows a preview of the affected descendants and totals.
  * Reset, compare with baseline, and import or export JSON.
  * Draft edits stay distinct from the applied scenario.

## 7. Value flows

* Users choose the visible layers or columns.
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
| CSV: selected analysis | Rows behind the current view |
| CSV: scenario outputs | Pool and flow results, baseline vs scenario |
| JSON: scenario | Configuration only, schema-versioned. Import validates version and structure |
| HTML snapshot | Self-contained charts and KPIs, labeled as a snapshot (not a simulator) |
| PPTX summary | Title, KPIs, Marimekko (native shapes), key charts, scenario assumptions. Optional module |

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
