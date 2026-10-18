# Design System — Profit Pools Explorer

Restrained, dense, financial-grade. The Marimekko is the hero. Everything else supports it.
Tokens live in `profit_pools/design/tokens.py` (Phase 3). That one module emits both the CSS variables
and the Plotly template, so the UI and the charts can't drift apart.

## 1. Principles

1. **Content over chrome.** Hairline separators, not cards. Shadows only on overlays (dialog, popover).
2. **One accent.** Teal marks interactivity and selection. Everything else is neutral ink.
3. **Numbers first.** Tabular figures in tables and axes. Units are always visible. Precision matches the
   data (no $1,234,567,890 when the source supports $1.2B).
4. **Honest states.** Missing, estimated and assumed values have a visual treatment, never a silent zero.
5. **No dead controls.** A control that cannot work in the current context is hidden or disabled with a
   reason in its tooltip.

## 2. Layout — 1440 × 900 reference frame

| Region | Spec (Figma-equivalent) |
|---|---|
| Frame | 1440 wide. Auto-layout vertical. Background `--bg` |
| Left nav | 216 fixed. Module list and footer (data source badge). Collapses to an icon rail (56) under 1200 |
| Main column | Fluid. Horizontal padding 32. Max content width 1600 |
| Global bar | Height 48, sticky top. Hairline bottom border. Items: Year · Geography · View (Baseline / Scenario / Compare) · Scope · Scenario chip · Export |
| Section rhythm | 40 between sections (A–E), 16 between a section header and its content |
| Grid | 12 columns, 24 gutter. Marimekko spans 12. Supporting charts are 6 + 6 or 8 + 4 |
| Inspector | Right column, 4/12 (about 360 at 1440), shown only when a selection exists. Never floats over the chart |
| Section nav | Anchor links in the global bar overflow ("Overview · Trends · Flows · Growth · Detail") |

**Breakpoints:**
* ≥ 1280: full layout.
* 1024–1279: nav collapses to the rail and the inspector stacks under the chart.
* < 1024: single column, and the Marimekko keeps a minimum height of 420.

## 3. Color tokens

### Neutrals and accent
| Token | Hex | Use |
|---|---|---|
| `--bg` | `#FAFBFB` | App background |
| `--surface` | `#FFFFFF` | Chart plots, tables, dialogs |
| `--ink-1` | `#14202B` | Primary text, KPI values |
| `--ink-2` | `#4A5866` | Secondary text, labels |
| `--ink-3` | `#7D8A96` | Muted: axis ticks, captions |
| `--line` | `#E3E8EC` | Hairlines, gridlines |
| `--line-strong` | `#C9D1D8` | Axis baselines, input borders |
| `--accent` | `#0F766E` | Selection, active nav, primary button, scenario indicator |
| `--accent-weak` | `#E6F2F1` | Selected row wash, active chip background |
| `--focus` | `#2563EB` | Keyboard focus ring (2px) |

### Status (reserved; always paired with an icon and label)
| Token | Hex | Meaning |
|---|---|---|
| `--good` | `#15803D` | Validation passed, favorable delta text |
| `--warn` | `#B45309` | Conflict, superseded, order-sensitive |
| `--bad` | `#B42318` | Blocking validation error, unfavorable delta text |

### Operating margin — diverging scale (Marimekko tiles, margin cells)
* Red `#B42318` (≤ −10%) → `#E8A39C` → neutral `#ECEFF1` (0%) → `#8CC9C1` → teal `#0F766E` (≥ +20%).
* The scale is asymmetric and **anchored at 0%**: losses are red, gains teal, with gray at zero.
* Endpoints are clamped, and the legend shows the clamp ("≤ −10%", "≥ 20%").
* **Missing margin:** fill `#F3F4F6` with a 45° hatch in `#C9D1D8` and a legend entry "Margin unavailable".
* **Top-down estimate:** normal fill, plus a dotted 1px tile outline and an "Estimate" badge in the tooltip.

### Layer identity — categorical (Sankey nodes, trend lines, layer legends)
Fixed order, never cycled, never re-assigned by rank. Validated with the dataviz CVD checker on white:
worst adjacent CVD ΔE 11.4, normal-vision ΔE 23.1.

| Slot | Layer | Hex |
|---|---|---|
| 1 | Payer | `#1F6FB2` |
| 2 | Provider | `#E07A2F` |
| 3 | Pharmacy | `#159A8C` |
| 4 | Manufacturer | `#8A63D2` |
| 5 | Health Services | `#C9A227` (sub-3:1 contrast, so it always appears with a visible label) |
| — | Unattributed / Other | `#B8C0C8` |

Sankey links use the source node's hue at 35% opacity, or 60% on hover or selection.

### Baseline vs scenario
* Baseline: `--ink-3`, a solid 1.5px line (or an outlined bar).
* Scenario: `--accent`, a solid 2px line.
* Forecast years: a light `--line` band behind the forecast region, labeled "Forecast". Lines are never dashed.

## 4. Typography

The family is **Inter** (Google Fonts), falling back to `system-ui, -apple-system, "Segoe UI", sans-serif`.

| Style | Size / line height | Weight | Use |
|---|---|---|---|
| Display | 28 / 34 | 600 | Page title "Profit Pools Explorer" |
| H2 | 18 / 24 | 600 | Section titles |
| H3 | 14 / 20 | 600 | Chart titles, inspector headings |
| Body | 14 / 20 | 400 | Default |
| Small | 12 / 16 | 400 | Captions, axis titles, footnotes |
| Micro | 11 / 14 | 500, +0.04em, uppercase | KPI labels, table headers |
| KPI | 24 / 28 | 600 | Metric values |

Numbers in tables, axes and the inspector use `font-variant-numeric: tabular-nums`.

## 5. Spacing, radius, elevation

* **Spacing scale (4-pt):** 2, 4, 8, 12, 16, 24, 32, 40, 56.
* **Radius:** 4 for inputs, chips and buttons. 6 for dialogs. 0 for charts and tables.
* **Elevation:** none on the page. Overlays use `0 8px 24px rgba(20,32,43,.12)`.

## 6. Number formatting

| Quantity | Format | Example |
|---|---|---|
| ≥ $1B | `$X.XB` (≥ $100B: `$XXXB`) | $42.7B, $693B |
| ≥ $1M | `$X.XM` | $812.4M |
| PMPM | `$X,XXX` no decimals | $1,184 |
| Margin | `X.X%` | 4.2%, −1.8% |
| Δ margin | `±X.X pp` | +0.6 pp |
| Δ value | `±$X.XB (±X.X%)` | +$3.1B (+2.4%) |
| Enrollment | `X.XM` / `XXXK` | 13.5M |
| CAGR | `X.X%`; `n/m` when undefined | 5.8%, n/m |
| Services | `X.XM visits` (unit always named) | 41.2M visits |

Negative numbers use a true minus (−), not a hyphen. Unavailable values render as an em dash (—) with a tooltip giving the reason.

## 7. Components (`design/components.py`, Phase 3)

| Component | Anatomy | Variants / states |
|---|---|---|
| `page_header` | Title (Display) and an optional subtitle (Small, `--ink-2`) | — |
| `global_bar` | Inline compact selects and segmented control, 32 high | sticky |
| `scenario_chip` | Dot, "Scenario A · 4 changes", caret. Opens the builder dialog | `baseline` (gray dot), `active` (teal dot and teal text), `draft` (amber dot "unapplied edits") |
| `kpi_strip` | 4–6 metrics in one row, hairline dividers. Each has a Micro label, a KPI value and a Δ line (Small) | `delta-good` / `delta-bad` / `n/a` with reason |
| `section_header` | H2, a one-line explanation, and right-aligned section controls | anchor id |
| `breadcrumb` | `All pools › Payer › Commercial`. Every segment except the last is a button | — |
| `provenance_badge` | 11px pill: Observed / Allocated / Derived / Estimated / Assumed / Unavailable | neutral fill; only Assumed is teal |
| `inspector` | H3 title, provenance badges, key-value grid (label `--ink-2`, value right-aligned tabular), "Why it changed" ledger list | empty state: "Select a pool or flow" |
| `assumption_row` | Index, driver label, scope summary, value, timing, enable toggle, kebab menu (duplicate / delete / move) | collapsed / expanded (scope, method, mode, years, notes, preview) / invalid (red left rule and message) / warning (amber) |
| `data_table` | `st.dataframe` with column config: formatted numbers, right-aligned, no index | — |
| `note` | Small `--ink-2` text with an ⓘ glyph. Used for coverage and caveats | — |

## 8. Chart specifications

**Shared Plotly template:**
* Background `--surface`. Font Inter 12 in `--ink-2`.
* Gridlines `--line` at 1px on the y-axis only. No zero-line emphasis except on diverging axes.
* Margins t 8, r 8, b 32, l 48.
* Hover label: white background, `--line-strong` border, `--ink-1` text at 12px.
* Modebar hidden. A legend is always present for ≥ 2 series, placed top-left horizontally.

### Marimekko (hero)
* Height 520 at 1440 (minimum 420).
* X = cumulative revenue share (0–100%). Column width = revenue share of the parent. Within a column,
  segment height = share of the column. Tile area ∝ revenue.
* Built as a Plotly `bar` trace with explicit `x` (centers) and `width`. That makes it a true variable-width
  chart that still supports `on_select`.
* Gaps between tiles are 2px in `--surface`.
* Labels inside a tile only when it is at least 64px wide and 28px tall: the short label, plus revenue
  when there's room. Labels are white on dark fills and `--ink-1` on light fills.
* Column headers above the plot show the label and the column's revenue share.
* **Tooltip:** name and path; revenue and share of parent; operating gain; margin; enrollment and PMPMs
  where available; provenance; and Δ vs baseline when a scenario is applied.
* **Selection:** the selected tile gets a 2px `--ink-1` outline and the others drop to 55% opacity. A
  "Drill into" button appears when the tile has children. The breadcrumb navigates back up.
* **Compare mode:** a toggle between Scenario and Δ vs Baseline. In Δ mode, colors encode the margin change
  in pp (diverging), and the widths still show scenario revenue.

### Sankey
* Height 560. Nodes are 14 wide with 10 padding.
* Node color = layer identity. Link color = source hue at 35%.
* Columns depend on the selected layers, for example LOB group → LOB → spending category → recipient
  pool → recipient leaf.
* Flows below the threshold fold into "Other (n flows)" per column and are reconciled: the totals are
  unchanged.
* There is always a table view, in a toggle next to the chart.

### Trends
* Line charts with 2px lines and 8px markers at the selected year only.
* Revenue and gain are on **separate charts**, never on two axes.
* A forecast band covers the forecast years.

### Growth bridge (waterfall)
* Baseline end value → one bar per assumption (in list order) → scenario end value.
* Increases teal, decreases red, totals `--ink-3`.

## 9. Interaction rules

* Selection state is shared. Selecting a pool in the Marimekko, the selector, a table row or the Sankey
  filter updates the inspector and every dependent section.
* Filters never re-color entities. Colors follow the entity.
* Disabled controls carry a tooltip explaining why ("Service counts unavailable for Imaging; use allowed
  PMPM").
* Every chart has a short footnote naming its data coverage, e.g. "Manufacturer: top-down estimate. No LOB
  or state detail."
