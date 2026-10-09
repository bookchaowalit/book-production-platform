# Data + Data Viz V1A — feature parity with real programs

Compared with Microsoft Excel 365 (Power Query + charts), Power BI Desktop, and
Tableau. Status: **have** = the engine does it deterministically today;
**partial** = a narrower version exists; **missing** = not built. Priority:
P1 = next, P2 = later, P3 = out of scope for a deterministic local engine.

Last reviewed: 2026-10-03 (recipe `csv-cleaner-chart-builder-v4`).

## Capability table

| Capability | Excel / Power BI / Tableau | Our status | Priority |
|---|---|---|---|
| Import CSV with delimiter (`,` `;` tab) | Data ▸ From Text/CSV; Get Data; Connect ▸ Text file | have | — |
| File encoding choice (TIS-620, UTF-16 …) | File Origin | partial (UTF-8 text only) | P2 |
| Typed columns (text, whole, decimal, date, true/false) | Data Type; Change Type | have | — |
| Locale-aware parsing (`1.234,5`, Thai/Buddhist-era dates) | Using Locale… | missing | P1 |
| Trim, replace null, filter equals, sort | Format ▸ Trim; Replace Values; filter; sort | have | — |
| Other filters (>, <, between, contains, in list) | Number/Text Filters | missing | P1 |
| Remove duplicates | Remove Rows ▸ Remove Duplicates | missing | P1 |
| Calculated columns / formulas | Add Column; DAX; calculated fields | missing | P2 |
| Split / merge columns, change case | Split Column; Merge Columns | missing | P2 |
| Append / join several files | Append / Merge Queries; relationships | missing | P2 |
| Pivot / unpivot | Pivot Column; Unpivot | missing | P2 |
| Aggregations sum / count / avg | PivotTable Values; DAX | have | — |
| Min / max / median / distinct count | Summarize Values By | missing | P1 |
| Group by two fields (series breakdown) | PivotTable Columns; Legend field | missing | P1 |
| Column (vertical bar) and line charts | Clustered Column; Line with Markers | have | — |
| Horizontal bar chart | Clustered Bar | **have (v4)** | — |
| Pie chart with % per slice | 2-D Pie; data labels Percentage | **have (v4)**: ≤ 12 slices, sum/count only, no negatives | — |
| Stacked / clustered multi-series bars, area, combo | Stacked Column; Area; Combo | missing (needs group-by) | P1 |
| Scatter / bubble, histogram, box plot | Scatter; Histogram; Box & Whisker | missing | P2 |
| Maps | Filled Map; Tableau map | missing | P3 |
| Sort by value / category, top-N, "Other" bucket | Sort; Top 10 filter; grouping | **have (v4)** | — |
| Number format: decimals, thousands separator | Format Cells ▸ Number | **have (v4)** (half away from zero, as Excel) | — |
| Currency / percent / date axis formats, Thai digits | Format Code; Format Axis | partial (unit suffix in axis title only) | P2 |
| Data labels | Add Chart Element ▸ Data Labels | **have (v4)** | — |
| Axis titles, unit, five fixed ticks | Axis Titles; Bounds / Units | have | — |
| Manual axis bounds, log scale | Format Axis ▸ Bounds; Logarithmic | missing | P3 |
| Custom colours / themes, legend position | Format Data Series; Themes | partial (fixed palette; legend only on pie) | P2 |
| Export SVG | Save as Picture ▸ SVG | have | — |
| Export PNG / PDF | Save as Picture ▸ PNG; Export ▸ PDF | missing | P2 |
| Workbook with native chart | .xlsx | partial (spreadsheet service builds .xlsx tables; no native chart) | P2 |
| Tooltips, slicers, dashboards, drill-down | Slicers; Power BI report; Tableau dashboard | missing | P3 |
| Scheduled refresh, live connectors | Power BI Service refresh; Tableau extracts | missing | P3 |
| Per-cell source lineage | — (not in these programs) | have (report.json) | — |
| Replay-safe idempotent jobs with artifact hashes | — | have | — |
| Job history, step trace, manual Excel guide | — | have (`trace.json`, `MANUAL-STEPS.md`, `history.jsonl`) | — |

## Implemented in this round (v4)

1. **More chart types with checks** — `horizontal_bar` (value ticks on the x
   axis, first category on top) and `pie` (slices from 12 o'clock clockwise,
   legend `category (pct%)`, `report.json ▸ chart.percentages`). The engine
   rejects pies with negative values, a zero total, `avg`, or more than 12
   slices (`pie_too_many_slices`; use `top_n` + `group_rest_as`). QA checks
   slice count, legend text, and that a pie has no axes. The AI adapter still
   may only propose `bar` or `line`.
2. **Sort / top-N / "Other"** — `sort` (`none`, `value_desc`, `value_asc`,
   `category_asc`, `category_desc`; ties keep first appearance; nulls last),
   `top_n` (1–50), and `group_rest_as` (fold the rest into one category; `avg`
   is recomputed from the folded rows, not averaged averages). Line charts
   reject value sorts. `ChartIR.folded_categories` records what was folded.
3. **Number formats and data labels** — `value_decimals` (0–6, half away from
   zero like Excel), `thousands_separator`, `data_labels`. Tick labels and data
   labels use the same formatter; QA re-derives every label; `report.json ▸
   chart.display.labels` lists them; the manual gives the matching Excel format
   code (for example `#,##0.00`).

A request with the v4 fields at their defaults hashes and renders exactly as
v3 did (checked against the 20 benchmark fixtures), so earlier jobs still replay.

## Roadmap (not done)

- **P1:** group by a second field → stacked / clustered bars and multi-line;
  min / max / median / distinct count; comparison and list filters; remove
  duplicates; locale-aware number and date parsing.
- **P2:** calculated columns; split / merge columns; append / join; pivot /
  unpivot; scatter and histogram; currency / percent / date formats and Thai
  digits; custom palettes; PNG / PDF export; native Excel chart in the
  spreadsheet stage.
- **P3 (out of scope locally):** interactive dashboards, slicers, drill-down,
  maps, scheduled refresh, live connectors.

Known gaps in the manual guide: Excel's CSV export differs in bytes (BOM,
CRLF, `TRUE`/`FALSE`, trailing zeros), so the guide asks for a value-by-value
comparison; Excel's pie shows percentages on slices while the engine writes
them in the legend; marker and line widths are the closest Excel sizes.
