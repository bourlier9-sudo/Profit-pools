# Data Contracts

> **Everything here is fictional.** The schemas, states, counties, pools, companies and numbers are invented
> for a standalone prototype. They are not representations of any real corporate table, company or market.

Executable source of truth: `profit_pools/core/data/contracts.py` (column specs + validators).
`tests/test_docs_contracts.py` fails if a contract column is missing from this document.

---

## 1. Conventions

| Topic | Rule |
|---|---|
| Datasets | `DATASET ∈ {MARKET, ENTERPRISE}` (`datasets.yaml`). MARKET is the total market. ENTERPRISE is the company's own book. The label is configurable, and this fictional build ships "Enterprise (fictional)". |
| Currency | Nominal USD. No inflation adjustment. |
| Annual values | `REVENUE`, `CLAIMS_EXPENSE`, `OPERATING_GAIN`, `ANNUAL_FLOW_AMOUNT` are **USD per calendar year**, stored in dollars. |
| Enrollment / population | **Average persons** in the year = member-months ÷ 12. |
| PMPM | **Monthly** dollars per average member: `annual = PMPM × members × 12`. Never summed; recomputed after aggregation. |
| Ratios | Margins are fractions (0.042 = 4.2%), recomputed from sums and never averaged. |
| Service volume | One unit only: **service instances** per year (`spending_categories.yaml → instance_unit`). Present only for categories flagged `instances: true`. **Never for pharmacy.** |
| Years | History 2019–2025. Base year 2025. Forecast 2026–2031 is **produced by the model**, never stored as facts. |
| Geography | `GEO_LEVEL ∈ {national, state, county}`. National rows use `STATE = 'US'` and `COUNTY = null`. Each row is stored at its **finest grain only**, with no pre-aggregated rows. |
| Hierarchy | `LAYER_ID`, then `L1_ID`…`L4_ID`, mirroring the source data shape. `NODE_ID` = the leaf (deepest non-null level). Payer and Manufacturer use L1–L2, Health Services L1–L3, Provider and Pharmacy up to L4 (ragged). |
| Nulls | Null means **unavailable**, never zero. A cell that does not exist (e.g. Expansion Adults in a non-expansion state, or no enterprise presence in a state) has **no rows**. |

---

## 2. Economic definitions (agreed model)

### 2.1 Datasets and how the market is built

| | MARKET (total market) | ENTERPRISE (company book) |
|---|---|---|
| Payer enrollment | Market estimate by county × LOB | Observed |
| Payer allowed PMPM | **PMPM benchmark**: enterprise state-level PMPM × market factor | Observed claims |
| Payer revenue, gain | Market enrollment × benchmark premium PMPM; gain = revenue × estimated market margin | Observed |
| Service instances | Enterprise instances per member × market members | Observed |
| Recipient revenue | Payer claims + uninsured + traditional Medicare & public + supplemental payments | Enterprise payer claims only |
| Recipient margin | Pool operating margin | **Same pool margin** as the market (a property of the pool) |
| Top-down pools | Manufacturer, Health Services, PBM Services | **Excluded** |

Enterprise recipient gain is therefore *the part of the pool's profit associated with enterprise
members*. It is not the enterprise's own profit.

### 2.2 Allowed, paid and consumer spend

Every claims-based flow carries both amounts:

```
allowed  (ANNUAL_FLOW_AMOUNT)  = everything the recipient receives for the service
paid     (PAID_AMOUNT)         = the part paid by the payer or public program
consumer = allowed − paid      = member cost sharing (deductibles, copays, coinsurance) and self-pay
```

| Source | Paid | Consumer |
|---|---|---|
| Payer segments | payer-paid claims | member cost sharing (varies by LOB and category) |
| Uninsured & self-pay | 0 | all of it (self-pay, valued at allowed-equivalent) |
| Traditional Medicare & public | program-paid | beneficiary cost sharing |
| Supplemental & state payments | all of it | 0 |

* **Recipient revenue is allowed** (paid + consumer).
* **Payer claims expense is paid.**
* The *spend basis* control (Allowed / Payer-paid / Consumer) shows any flow-based measure on one basis.
  * Provider and pharmacy revenue and gain scale to that part at the pool's margin.
  * Payer and top-down pools have no such split, and they are reported as unavailable under the paid or
    consumer basis.

### 2.3 Payers (`economics: payer_pnl`)

```
REVENUE            = REVENUE_PMPM × ENROLLMENT × 12
ALLOWED_SPEND      = Σ allowed over claims categories = ALLOWED_PMPM × ENROLLMENT × 12
CLAIMS_EXPENSE     = Σ paid over claims categories    = PAID_PMPM × ENROLLMENT × 12
OPERATING_GAIN     (observed for enterprise; estimated for market)
residual_opex      = REVENUE − CLAIMS_EXPENSE − OPERATING_GAIN          DERIVED (not stored)
```

* No administrative expense is reported. **Residual opex is a derived residual** against *paid* claims.
* In scenarios, residual opex **PMPM is held constant**, so it scales with enrollment. A user may enter an
  `opex_volume_scaling` assumption (the share of opex that varies with enrollment). The data offers no
  estimate of this.
* Changes in allowed PMPM change paid claims (at the cell's paid ratio) and therefore payer gain. A
  member-cost-share assumption moves dollars between paid and consumer without changing recipient revenue.
  Premium is unchanged unless assumed.

### 2.4 Flow recipients: provider and dispensing pharmacy (`economics: margin_on_revenue`)

Revenue is the sum of **explicit, separately estimated components**. There is **no balancing plug**.

```
REVENUE = Σ payer_claims inflows        payer segment members × allowed PMPM_c
        + Σ funding_pmpm inflows        uninsured / traditional Medicare & public population × PMPM_c
        + Σ supplemental inflows        state/supplemental payments (hospital leaves only)
OPERATING_GAIN = REVENUE × OPERATING_MARGIN           (margin constant unless assumed)
residual_opex  = REVENUE − OPERATING_GAIN              DERIVED
```

There is no variable/fixed cost model and no operating leverage. Payer and recipient margin assumptions are
separate drivers.

### 2.5 Non-pool funding sources (`funding_sources.yaml`, MARKET only)

| Id | Label | Basis | Grain | Targets |
|---|---|---|---|---|
| `FS_UNINSURED` | Uninsured & self-pay | population × PMPM | county | all claims categories |
| `FS_TRAD_MCR` | Traditional Medicare & other public programs | population × PMPM | county | all claims categories |
| `FS_SUPPLEMENTAL` | Supplemental & state payments | lump sum | state | hospital leaves only |

These are **not** payer profit pools. They never appear as Marimekko tiles. They appear as Sankey sources
and as components of recipient revenue.

### 2.6 Top-down pools (`economics: top_down`, MARKET only)

Manufacturer, Health Services, and Pharmacy Benefit Services (`RX_PBM`, under the Pharmacy layer) carry
national revenue and margin **estimates only**. They have no LOB, geography or flow detail. PBM Services
appears in the Pharmacy column of the Marimekko and **never in value flows**.

### 2.7 Service instances (optional volume decomposition)

```
allowed (claims-based flow) = INSTANCE_COUNT × average allowed per instance
utilization                 = INSTANCE_COUNT / members × 1,000
```

Instances exist only for categories with `instances: true`, and only on `payer_claims` flows. They are
never present for pharmacy, because pharmacy claim counts mix administrative and drug claims. Utilization
and unit-price assumptions are offered only there. Utilization changes are **user estimates**: the data
provides instance counts, not a volume-scaling estimate. Margins stay constant under both.

| Category | Target leaf | Instances |
|---|---|---|
| IP_MEDSURG, IP_MATERNITY | Med/Surg IP, Maternity & Newborn | yes |
| OP_ED, OP_SURG, OP_OTHER | ED, OP Surgery, Other OP | yes |
| IP_PSYCH, IP_REHAB | Psychiatric, Rehabilitation Hospitals | yes |
| PROF_PRIMARY, PROF_SPEC, PROF_BH | Primary, Specialty, BH Clinicians | yes |
| SNF | Skilled Nursing | yes |
| HOME_HEALTH | Home Health | **no** (edge case) |
| ASC | Ambulatory Surgery Centers | yes |
| IMAGING | Imaging Centers | **no** (edge case) |
| RX_* (7 categories) | Dispensing pharmacy leaves | **never** |

---

## 3. `FACT_POOL_METRICS` (fictional)

Grain: `DATASET × YEAR × STATE × COUNTY × NODE_ID × LOB` at the leaf's own `geo_level`.

| Column | Type | Null | Unit | Definition |
|---|---|---|---|---|
| `DATASET` | str | no | - | MARKET or ENTERPRISE (datasets.yaml). |
| `YEAR` | int | no | calendar year | Fiscal = calendar year. |
| `GEO_LEVEL` | str | no | - | national / state / county — grain of this row. |
| `STATE` | str | no | - | State id, or 'US' for national rows. |
| `COUNTY` | str | yes | - | County id; null for state and national rows. |
| `LAYER_ID` | str | no | - | Top-level taxonomy layer. |
| `L1_ID` | str | no | - | Level-1 node below the layer. |
| `L2_ID` | str | yes | - | Level-2 node; null if the leaf is shallower. |
| `L3_ID` | str | yes | - | Level-3 node; null if the leaf is shallower. |
| `L4_ID` | str | yes | - | Level-4 node; null if the leaf is shallower. |
| `NODE_ID` | str | no | - | Leaf node = deepest non-null level. Must match the taxonomy path. |
| `LOB` | str | yes | - | Payer LOB for payer rows; null otherwise. |
| `ENROLLMENT` | float | yes | avg members | Average members in year (member-months / 12). Payer only. |
| `REVENUE` | float | no | USD/yr | Annual revenue. |
| `CLAIMS_EXPENSE` | float | yes | USD/yr | Payer-paid claims (allowed − member cost share). Payer only. |
| `ALLOWED_SPEND` | float | yes | USD/yr | Allowed spend incl. member cost share. Payer only. |
| `OPERATING_GAIN` | float | yes | USD/yr | Annual operating gain; null when margin unavailable. |
| `OPERATING_MARGIN` | float | yes | ratio | Operating gain / revenue; null when unavailable. |
| `REVENUE_PMPM` | float | yes | USD PMPM | REVENUE / (ENROLLMENT × 12). Payer only. |
| `ALLOWED_PMPM` | float | yes | USD PMPM | ALLOWED_SPEND / (ENROLLMENT × 12). Payer only. |
| `PAID_PMPM` | float | yes | USD PMPM | CLAIMS_EXPENSE / (ENROLLMENT × 12). Payer only. |
| `SOURCE_TYPE` | str | no | - | observed / allocated / estimated. |
| `COVERAGE_LEVEL` | str | no | - | bottom_up / allocated / top_down. |

There is deliberately no `ADMIN_EXPENSE` column. Residual opex is derived (§2.3).

| Layer / node | Row grain | Datasets | `SOURCE_TYPE` |
|---|---|---|---|
| Payer leaves | county × LOB | MARKET (estimated), ENTERPRISE (observed) | per dataset |
| Provider and dispensing pharmacy leaves | state | both | allocated |
| PBM Services, Manufacturer, Health Services leaves | national | MARKET only | estimated |

## 4. `FACT_VALUE_FLOWS` (fictional)

| Column | Type | Null | Unit | Definition |
|---|---|---|---|---|
| `DATASET` | str | no | - | MARKET or ENTERPRISE (datasets.yaml). |
| `YEAR` | int | no | calendar year | Fiscal = calendar year. |
| `GEO_LEVEL` | str | no | - | national / state / county — grain of this row. |
| `STATE` | str | no | - | State id, or 'US' for national rows. |
| `COUNTY` | str | yes | - | County id; null for state and national rows. |
| `SOURCE_NODE_ID` | str | no | - | Payer leaf (payer_claims) or funding source id (funding_pmpm, supplemental). |
| `TARGET_NODE_ID` | str | no | - | Recipient leaf (receives_flows = true). |
| `PAYER_LOB` | str | yes | - | Payer LOB for payer_claims flows; null for funding-source flows. |
| `SPENDING_CATEGORY` | str | yes | - | Claims category; null for supplemental flows. |
| `FLOW_KIND` | str | no | - | payer_claims / funding_pmpm / supplemental. |
| `ANNUAL_FLOW_AMOUNT` | float | no | USD/yr | Annual allowed amount = recipient receipts (payer/program-paid + consumer). |
| `PAID_AMOUNT` | float | no | USD/yr | Portion paid by the payer or program. Consumer spend = ANNUAL_FLOW_AMOUNT − PAID_AMOUNT. |
| `INSTANCE_COUNT` | float | yes | instances/yr | Service instances; null unless the category carries instances. |
| `ALLOCATION_METHOD` | str | no | - | observed_claims / pmpm_benchmark / population_pmpm_estimate / supplemental_estimate. |
| `SOURCE_TYPE` | str | no | - | observed / estimated. |

| `FLOW_KIND` | From → To | Grain | `ALLOCATION_METHOD` | Datasets |
|---|---|---|---|---|
| `payer_claims` | payer leaf → recipient leaf via category | county | `observed_claims` (ENTERPRISE) or `pmpm_benchmark` (MARKET) | both |
| `funding_pmpm` | FS_UNINSURED, FS_TRAD_MCR → recipient leaf via category | county | `population_pmpm_estimate` | MARKET |
| `supplemental` | FS_SUPPLEMENTAL → hospital leaf | state | `supplemental_estimate` | MARKET |

## 5. `FACT_COVERAGE_POPULATION` (fictional)

Population for funding sources with basis `population_pmpm` (the uninsured, traditional Medicare and other
public programs). Payer-pool enrollment stays in `FACT_POOL_METRICS.ENROLLMENT`.

| Column | Type | Null | Unit | Definition |
|---|---|---|---|---|
| `DATASET` | str | no | - | MARKET or ENTERPRISE (datasets.yaml). |
| `YEAR` | int | no | calendar year | Fiscal = calendar year. |
| `GEO_LEVEL` | str | no | - | national / state / county — grain of this row. |
| `STATE` | str | no | - | State id, or 'US' for national rows. |
| `COUNTY` | str | yes | - | County id; null for state and national rows. |
| `FUNDING_SOURCE_ID` | str | no | - | Funding source with basis population_pmpm. |
| `POPULATION` | float | no | avg persons | Average covered (or uninsured) persons in year. |
| `SOURCE_TYPE` | str | no | - | estimated. |

---

## 6. Validations (`validate_bundle`, tested in `tests/`)

1. Schema: all columns present, non-nullable complete, numeric types. Known `DATASET` values.
2. Unique grain. `NODE_ID` is a known leaf, and `LAYER_ID` and `L1`–`L4` equal the taxonomy path. `GEO_LEVEL` and `COVERAGE_LEVEL` match the taxonomy.
3. **Dataset exclusions:** no rows or flows for nodes excluded from a dataset, and no funding flows in a dataset without that source.
4. Payer identities:
   * `REVENUE = REVENUE_PMPM × ENROLLMENT × 12`
   * `CLAIMS_EXPENSE = PAID_PMPM × ENROLLMENT × 12`
   * `ALLOWED_SPEND = ALLOWED_PMPM × ENROLLMENT × 12`
   * paid ≤ allowed
   * LOB matches the leaf, and `SOURCE_TYPE` matches the dataset.
5. **Enterprise ⊆ market:** enterprise enrollment ≤ market enrollment in every year × county × LOB.
6. `OPERATING_GAIN = REVENUE × OPERATING_MARGIN`, with both null together. Enterprise recipient margins equal the market pool margins.
7. **Claims reconciliation:** payer `CLAIMS_EXPENSE` = Σ `PAID_AMOUNT` and `ALLOWED_SPEND` = Σ `ANNUAL_FLOW_AMOUNT` of `payer_claims` flows, for every dataset × year × county × payer leaf. `0 ≤ PAID_AMOUNT ≤ ANNUAL_FLOW_AMOUNT`. Supplemental payments are fully paid.
8. **Recipient reconciliation:** recipient `REVENUE` = Σ inflows of every kind for every dataset × year × state × leaf.
9. Funding flows originate at a funding source, have a null LOB, and have a `FLOW_KIND` consistent with the source's basis. `funding_pmpm` flows have a population row.
10. Supplemental payments go only to leaves under `targets_under`, and carry no category or instances.
11. Each category maps to exactly its target. Instances appear only on categories with instance data (never pharmacy) and are > 0.
12. Population rows are positive, at the source's grain, for population-based sources only.

Tolerance: relative 1e-9. The identities are exact by construction.

## 7. Built-in edge cases

| Case | Where |
|---|---|
| Negative payer margins | Medicaid ABD (structural), several MA and Medicaid cells in 2024–25 |
| Negative recipient margins | Psychiatric Hospitals (all years), Value-Based Care Enablement (top-down), SNF and Primary Care in some years |
| Missing margin (state) | Rehabilitation Hospitals in Westmoor and Lakeland, all years |
| Missing margin (years) | Imaging Centers 2019–20; Other BH Services (top-down) 2019–20 |
| LOB absent | Expansion Adults: none in Southridge; Central Plains from 2021 |
| No enterprise presence | Westmoor (all LOBs); enterprise SNP in Southridge and Central Plains (market PMPMs fall back to the national benchmark, re-priced to the state) |
| No instances | Home Health, Imaging; all pharmacy |
| Coverage shifts | Uninsured population rises during the 2023–24 Medicaid unwinding |
| Cost sharing | Highest for ACA and generic retail Rx; near zero for Medicaid; 100% for the uninsured; 0% for supplemental |
| Top-down only | Manufacturer, Health Services, PBM Services |

---

## 8. Metadata / configuration (`profit_pools/core/config/`)

| File | Purpose |
|---|---|
| `taxonomy.yaml` | Pool tree (L1–L4 below the layer, ragged). Inherited `economics`, `coverage`, `geo_level`, `receives_flows`. |
| `datasets.yaml` | MARKET / ENTERPRISE: labels, excluded nodes, payer source type, funding sources. |
| `funding_sources.yaml` | Uninsured, traditional Medicare & public, supplemental: basis, grain, eligible targets. |
| `lobs.yaml` | LOB groups → LOBs. |
| `spending_categories.yaml` | Category → recipient leaf (one each), instance availability, instance-unit definition. |
| `geography.yaml` | Fictional states and counties, weights, price index, Medicaid expansion year. |
| `periods.yaml` | History, base year, forecast horizon. |
| `metrics.yaml` | Metric catalog: unit, additivity, provenance. |
| `drivers.yaml` | Scenario driver catalog: scope kind, required data, methods, stage. |
| `baseline_forecast.yaml` | Baseline annual rates with hierarchical lookup. |
| `methodology.yaml` | Text for "How is provider revenue estimated?" and other methodology panels, by dataset. |

## 9. Provenance vocabulary (UI labels)

| Tag | Meaning | Examples |
|---|---|---|
| **Observed** | Enterprise fact-table input | enterprise enrollment, revenue, claims, gain |
| **Estimated** | Market estimate or top-down | market enrollment, benchmark PMPMs, uninsured population, supplemental payments, manufacturer revenue |
| **Allocated** | Spending assigned to a recipient by a mapping | recipient revenue |
| **Derived** | Arithmetic consequence of other values | residual opex, recipient gain, PMPMs, MLR |
| **Projected** | Forecast-year value from baseline growth rates | every 2026–2031 value |
| **Assumed** | Changed by an active scenario assumption | any value touched by the ledger |
| **Unavailable** | Not supported by the data | rehab margin in Westmoor, top-down pools by state, instances for pharmacy |

## 10. Planned additions

* **Ownership (Phase 4b):** an owned-asset carve-out of a recipient leaf, with a `routing_share` driver that
  moves enterprise allowed spend from external providers to the owned asset. Intra-organization flows are
  eliminated in the consolidated view. This will add `ownership.yaml`.
