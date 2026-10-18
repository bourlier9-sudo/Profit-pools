# Data Contracts

> **Everything here is fictional.** The schemas, states, counties, pools and numbers are invented for a
> standalone prototype. They are not representations of any real corporate table or market.

Executable source of truth: `profit_pools/core/data/contracts.py` (column specs + validators).
`tests/test_docs_contracts.py` fails if a contract column is missing from this document.

---

## 1. Conventions

| Topic | Rule |
|---|---|
| Currency | Nominal USD. No inflation adjustment. |
| Annual values | `REVENUE`, `CLAIMS_EXPENSE`, `OPERATING_GAIN`, `ANNUAL_FLOW_AMOUNT` are **USD per calendar year**. Stored in dollars, displayed as $B / $M. |
| Enrollment | **Average members** in the year = member-months ÷ 12. |
| PMPM | **Monthly** dollars per average member: `annual = PMPM × ENROLLMENT × 12`. PMPMs are never summed; they are recomputed from additive components after aggregation. |
| Ratios | `OPERATING_MARGIN = OPERATING_GAIN / REVENUE`, stored as a fraction (0.042 = 4.2%). Never averaged; recomputed from sums. |
| Service counts | Annual counts in a **category-specific unit** (admissions, visits, inpatient days, …). Never summed across categories or units. |
| Years | History 2019–2025 (synthetic "observed"). Base year 2025. Forecast 2026–2031 is **produced by the model**, never stored as facts. |
| Geography | `GEO_LEVEL ∈ {national, state, county}`. National rows use `STATE = 'US'`, `COUNTY = null`. State rows have `COUNTY = null`. Each row is stored only at its **finest available grain**. There are no pre-aggregated rows, so summing never double counts. |
| Nulls | Null means **unavailable**, never zero. A LOB that does not exist in a state (e.g. Expansion Adults in a non-expansion state) has **no rows**. |

---

## 2. Economic definitions (agreed model)

### 2.1 Payers (`economics: payer_pnl`)

```
REVENUE            = REVENUE_PMPM × ENROLLMENT × 12                    observed
CLAIMS_EXPENSE     = Σ allowed spend over claims categories             observed
                   = SPEND_PMPM × ENROLLMENT × 12
OPERATING_GAIN                                                          observed
residual_opex      = REVENUE − CLAIMS_EXPENSE − OPERATING_GAIN          DERIVED (not stored)
residual_opex_pmpm = residual_opex / (ENROLLMENT × 12)                  DERIVED
```

* No administrative expense is reported. **Residual operating expense is a derived residual**, not a
  modeled cost structure.
* In scenarios, residual opex **PMPM is held at its baseline value** unless an assumption changes it.
  Total residual opex therefore scales with enrollment.
* A change in allowed-spend PMPM changes claims expense and therefore payer gain. Premium is unchanged
  unless a separate assumption changes it.

**Allowed-vs-paid caveat.** The prototype treats allowed spend as both the payer's modeled claims outflow
and the provider's receipt. Real allowed amounts include member cost sharing, so they exceed payer-paid
claims. Under this simplification, derived residual opex absorbs the cost-sharing gap. The UI states
this wherever claims or residual opex are shown.

### 2.2 Flow recipients: providers, pharmacy, Payer Services (`economics: margin_on_revenue`)

```
attributed_revenue   = Σ inflows from modeled payer LOBs                ALLOCATED (from observed claims)
unattributed_revenue = residual inflow from EXT_UNATTRIBUTED            ESTIMATED
REVENUE              = attributed_revenue + unattributed_revenue
OPERATING_MARGIN                                                        observed (may be null)
OPERATING_GAIN       = REVENUE × OPERATING_MARGIN                       DERIVED
residual_opex        = REVENUE − OPERATING_GAIN                         DERIVED (not stored)
```

* **Constant margin.** There is no variable/fixed cost model and no operating leverage. When a recipient's
  revenue changes for any reason (enrollment, utilization, price, routing), gain changes at the
  unchanged margin. Only an explicit `provider_margin` assumption changes the margin.
* Payer and recipient margin assumptions are separate drivers and never cross-apply.

### 2.3 Top-down pools (`economics: top_down`)

Manufacturer, Behavioral Health Services and Pharmacy Benefit Services carry national revenue and margin
**estimates only**. They have no LOB attribution, no state or county detail, and no flows. The UI never
shows LOB, geography or service detail for them. It says "top-down estimate".

### 2.4 Service volume decomposition (optional, per cell)

Where `SERVICE_COUNT` is present:

```
ANNUAL_FLOW_AMOUNT (allowed) = SERVICE_COUNT × avg_allowed_per_service
utilization_per_1000         = SERVICE_COUNT / ENROLLMENT × 1000
```

Counts exist only where `spending_categories.yaml → reliable_lob_groups` lists the payer's LOB group.
Only those cells offer *utilization* and *unit price* assumptions. Everywhere else only *allowed PMPM*
assumptions apply. Under both decompositions the recipient margin stays constant.

| Category | Target leaf | Unit | Reliable LOB groups |
|---|---|---|---|
| IP_ACUTE | General Acute Hospitals | admission | COMM, MCR, MCD |
| OP_HOSP | General Acute Hospitals | visit | COMM, MCR, MCD |
| IP_PSYCH | Psychiatric Hospitals | inpatient_day | COMM, MCR |
| IP_REHAB | Rehabilitation Hospitals | admission | — |
| PROF_PRIMARY | Primary Care | visit | COMM, MCR, MCD |
| PROF_SPEC | Specialty Care | visit | COMM, MCR, MCD |
| PROF_BH | Behavioral Health Clinicians | visit | COMM, MCR |
| SNF | Skilled Nursing Facilities | facility_day | MCR, MCD |
| HOME_HEALTH | Home Health | episode | — |
| ASC | Ambulatory Surgery Centers | procedure | COMM, MCR |
| IMAGING | Imaging Centers | procedure | — |
| RX_RETAIL | Retail Pharmacy | script_30d | COMM, MCR, MCD |
| RX_SPECIALTY | Specialty Pharmacy | script_30d | COMM, MCR, MCD |
| RX_MAIL | Mail-Order Pharmacy | script_30d | COMM, MCR |
| PAYER_SERVICES (opex) | Payer Services & Technology | — | — |

---

## 3. `FACT_POOL_METRICS` (fictional)

Grain: `YEAR × STATE × COUNTY × SUBPOOL_ID × LOB` at the leaf's own `geo_level`.

| Column | Type | Null | Unit | Definition |
|---|---|---|---|---|
| `YEAR` | int | no | calendar year | Fiscal = calendar year. |
| `GEO_LEVEL` | str | no | - | national / state / county — grain of this row. |
| `STATE` | str | no | - | State id, or 'US' for national rows. |
| `COUNTY` | str | yes | - | County id; null for state and national rows. |
| `LAYER_ID` | str | no | - | Top-level taxonomy layer. |
| `POOL_ID` | str | no | - | Level-1 ancestor of the leaf (informational). |
| `SUBPOOL_ID` | str | no | - | Leaf taxonomy node. The hierarchy is resolved from the taxonomy, not from these columns. |
| `LOB` | str | yes | - | Payer LOB for payer rows; null for recipient and top-down rows. |
| `ENROLLMENT` | float | yes | avg members | Average members in year (member-months / 12). Payer only. |
| `REVENUE` | float | no | USD/yr | Annual revenue. |
| `CLAIMS_EXPENSE` | float | yes | USD/yr | Payer claims expense = allowed spend (see caveats). Payer only. |
| `OPERATING_GAIN` | float | yes | USD/yr | Annual operating gain; null when margin unavailable. |
| `OPERATING_MARGIN` | float | yes | ratio | Operating gain / revenue; null when unavailable. |
| `REVENUE_PMPM` | float | yes | USD PMPM | REVENUE / (ENROLLMENT × 12). Payer only. |
| `SPEND_PMPM` | float | yes | USD PMPM | CLAIMS_EXPENSE / (ENROLLMENT × 12). Payer only. |
| `SOURCE_TYPE` | str | no | - | observed / allocated / estimated. |
| `COVERAGE_LEVEL` | str | no | - | bottom_up / allocated / top_down. |

`ADMIN_EXPENSE` is deliberately **absent** because no reliable administrative expense exists. Residual opex is
derived by the model (§2).

| Layer | Row grain | `SOURCE_TYPE` | Populated metrics |
|---|---|---|---|
| Payer | county × LOB | observed | all payer fields |
| Provider, Pharmacy | state | allocated | `REVENUE`, `OPERATING_MARGIN`, `OPERATING_GAIN` |
| Payer Services | state | allocated | same as provider |
| Manufacturer, BH Services, PBM Services | national | estimated | `REVENUE`, `OPERATING_MARGIN`, `OPERATING_GAIN` |

## 4. `FACT_VALUE_FLOWS` (fictional)

| Column | Type | Null | Unit | Definition |
|---|---|---|---|---|
| `YEAR` | int | no | calendar year | Fiscal = calendar year. |
| `GEO_LEVEL` | str | no | - | county for payer-originated flows; state for residual flows. |
| `STATE` | str | no | - | State id. |
| `COUNTY` | str | yes | - | Member county; null for state-level residual flows. |
| `SOURCE_NODE_ID` | str | no | - | Payer leaf, or 'EXT_UNATTRIBUTED' for residual flows. |
| `TARGET_NODE_ID` | str | no | - | Recipient leaf (receives_flows = true). |
| `PAYER_LOB` | str | no | - | Payer LOB, or 'UNMODELED' for residual flows. |
| `SPENDING_CATEGORY` | str | yes | - | Category id; null for residual flows. |
| `FLOW_KIND` | str | no | - | claims / opex / residual. |
| `ANNUAL_FLOW_AMOUNT` | float | no | USD/yr | Annual amount (allowed $ for claims flows). |
| `SERVICE_COUNT` | float | yes | services/yr | Category-specific unit; null unless reliable. |
| `SERVICE_UNIT` | str | yes | - | Unit id from spending_categories.yaml; null when SERVICE_COUNT is null. |
| `ALLOCATION_METHOD` | str | no | - | direct_claims_mapping / estimated_opex_share / unattributed_residual. |
| `SOURCE_TYPE` | str | no | - | observed / estimated. |

| `FLOW_KIND` | From → To | Method | Provenance |
|---|---|---|---|
| `claims` | payer leaf (county) → recipient leaf via category | `direct_claims_mapping` | observed allowed spend |
| `opex` | payer leaf (county) → Payer Services | `estimated_opex_share` (~20% of derived residual opex) | estimated |
| `residual` | `EXT_UNATTRIBUTED` (state) → recipient leaf | `unattributed_residual` | estimated |

---

## 5. Validations (enforced by `validate_bundle`, tested in `tests/`)

1. Schema: all columns present, non-nullable columns complete, numeric types.
2. Unique grain. `SUBPOOL_ID` is a known **leaf**, and `LAYER_ID`, `GEO_LEVEL` and `COVERAGE_LEVEL` match the taxonomy.
3. Geography sentinels are consistent with `GEO_LEVEL`.
4. Payer identities: `REVENUE = REVENUE_PMPM × ENROLLMENT × 12` and `CLAIMS_EXPENSE = SPEND_PMPM × ENROLLMENT × 12`. Enrollment > 0 and LOB matches the leaf.
5. Payer-only fields are null on every other row.
6. `OPERATING_GAIN = REVENUE × OPERATING_MARGIN`. The two are null together.
7. **Claims reconciliation:** payer `CLAIMS_EXPENSE` equals Σ claims flows for every year × county × payer leaf.
8. **Opex bound:** opex flows ≤ derived payer residual opex.
9. **Recipient reconciliation:** recipient `REVENUE` equals Σ inflows of every kind, for every year × state × leaf.
10. No flows to or from top-down nodes. Each category maps to exactly its configured target.
11. Service counts appear only where reliable, carry the category's unit, and are > 0.
12. Residual flows originate only at `EXT_UNATTRIBUTED` with `PAYER_LOB = 'UNMODELED'`.

Tolerance: relative 1e-9. The identities are exact by construction; the tolerance only absorbs floating-point error.

## 6. Built-in edge cases

| Case | Where |
|---|---|
| Negative payer margins | Medicaid ABD (structurally), several MA/Medicaid cells in 2024–25 |
| Negative provider margins | Psychiatric Hospitals (all years); SNF and Primary Care in some years |
| Missing margin (state) | Rehabilitation Hospitals in Westmoor and Lakeland, all years |
| Missing margin (years) | Imaging Centers 2019–20; Behavioral Health Services (top-down) 2019–20 |
| LOB absent | Expansion Adults: none in Southridge; Central Plains from 2021 |
| Partial attribution | Payer Services: estimated opex-share inflows + unattributed residual |
| No service counts | IP_REHAB, HOME_HEALTH, IMAGING (all LOBs); IP_PSYCH, PROF_BH (Medicaid); SNF (Commercial); ASC, RX_MAIL (Medicaid) |
| Top-down only | Manufacturer layer, BH Services, PBM Services |

---

## 7. Metadata / configuration (`profit_pools/core/config/`)

| File | Purpose |
|---|---|
| `taxonomy.yaml` | Arbitrary-depth pool tree. Inherited `economics`, `coverage`, `geo_level`, `receives_flows`. Payer leaves name a `lob`. |
| `lobs.yaml` | LOB groups → LOBs, plus the `UNMODELED` pseudo-LOB for residual flows. |
| `spending_categories.yaml` | Category → target leaf, service unit and definition, reliable LOB groups. |
| `geography.yaml` | Fictional states and counties, weights, price index, Medicaid expansion year. |
| `periods.yaml` | History, base year, forecast horizon. |
| `metrics.yaml` | Metric catalog: unit, additivity, provenance by economics type. |
| `drivers.yaml` | Scenario driver catalog: eligible economics, required data, methods, pipeline stage. |

## 8. Provenance vocabulary (UI labels)

| Tag | Meaning | Example |
|---|---|---|
| **Observed** | Fact-table input | payer revenue, enrollment, allowed spend, provider margin |
| **Allocated** | Observed amount assigned to a recipient by a mapping rule | provider attributed revenue |
| **Derived** | Arithmetic consequence of other values | residual opex, provider gain, PMPMs, MLR |
| **Estimated** | Top-down or residual estimate | manufacturer revenue, unattributed residual, opex-share flows |
| **Assumed** | Changed by an active scenario assumption | any value touched by the ledger |
| **Unavailable** | Not supported by the data | rehab margin in Westmoor, county detail for providers |

## 9. Planned additions (not yet built)

* **Ownership (Phase 4b):** a fictional integrated organization owns a share of payer enrollment by LOB × state,
  and an owned asset that is a carve-out of a recipient leaf. A `routing_share` driver moves the org's
  allowed spend from external providers to the owned asset. Intra-organization flows are eliminated in the
  consolidated view. A config file `ownership.yaml` will be added with this phase.
