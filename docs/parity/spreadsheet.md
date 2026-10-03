# Spreadsheet V1A — feature parity with Microsoft Excel and Google Sheets

Scope: `spreadsheet_production` + `spreadsheet_ir` (tool id `spreadsheet-v1a`).
The engine is a deterministic workbook builder: typed tables become a
`Summary` sheet plus one data sheet per table, with allowlisted formulas that
the engine evaluates itself before XlsxWriter writes the cached values. A
reopen QA reads the XLSX XML back. It is not an interactive spreadsheet, so
"missing" usually means "not in the IR or the formula language yet".

Status: **have** = implemented and tested; **partial** = a subset works;
**missing** = not implemented. Priority: P1 next, P2 later, P3 only on demand,
— = out of scope for a deterministic batch engine.

## Capability matrix

| Capability | Excel / Google Sheets | Engine status | Priority | Notes |
|---|---|---|---|---|
| Multiple sheets, fixed order and names | yes | have | — | `Summary`, `Detail`, then one sheet per extra table |
| Typed cells (text, integer, decimal, date, boolean, blank) | yes | have | — | text that looks like a formula/number stays text |
| Formulas with cached values | yes | have | — | V1A: `SUM`, `COUNT`, `IF`; V1B intent language adds `AVERAGE`, `SUMIF`, `COUNTIF`, `INDEX/MATCH` |
| Named ranges | Name Manager / Named ranges | have | — | one per numeric column; **fixed in this change**: columns past `Z` (`AA`, `AB` …) now get a valid absolute reference |
| Freeze top row | Freeze Panes | have | — | every sheet |
| Column widths | Column Width | have | — | 18 on data sheets; 24 / 18 on Summary |
| Header and title styling | Bold, fill, borders | **have (fixed)** | — | the header/title cells used to be overwritten with the plain text style; they now keep bold + `#D9EAF7` fill + border and the 16 pt bold title |
| Number formats per column | Format Cells ▸ Number ▸ Custom | **have (new)** | — | allowlisted codes: `0`, `0.00`, `#,##0`, `#,##0.00`, `0%`, `0.00%`, `"฿"#,##0.00`, `"$"#,##0.00`, `"€"#,##0.00`, `"£"#,##0.00`, `"¥"#,##0`; dates `yyyy-mm-dd`, `dd/mm/yyyy`, `mm/dd/yyyy`, `d mmm yyyy`, `mmm yyyy`. Integer formula results now display as `0` instead of `0.00` |
| Dropdown (list) data validation | Data Validation ▸ List / Data validation ▸ Dropdown | **have (new)** | — | text columns; ≤ 50 values, no commas/quotes/formula prefixes, ≤ 255 chars; existing rows must already be in the list; reopen QA checks every range |
| Conditional formatting (cell value rules) | Highlight Cells Rules / Conditional formatting | **have (new)** | — | numeric columns; `>`, `>=`, `<`, `<=`, `=`, `<>`, `between`; hex fill colour; reopen QA checks every range |
| Text / boolean formula results in reopen QA | — | **have (fixed)** | — | QA used to treat every cached value as a number, so `IF(...,"high","low")` failed QA |
| Job trace, history, manual Excel steps | — | **have (new)** | — | `trace.json`, `history.jsonl`, `ir.json`, `MANUAL-STEPS.md` (see README) |
| More functions (`MIN`, `MAX`, `ROUND`, `AVERAGEIF`, `SUMIFS`, `COUNTIFS`, date functions) | yes | missing | P1 | each needs evaluator + cached value + QA; `SUMIFS`/`COUNTIFS` are the highest-value additions for the V1B intents |
| Excel Tables (ListObject) with AutoFilter | Format as Table / Filter views | missing | P1 | XlsxWriter `add_table`; would make data sheets sortable/filterable by hand |
| Pivot-style summary sheet | PivotTable / Pivot table | partial | P1 | the V1B `group-by` intent writes `SUMIF`/`COUNTIF` rows (a static pivot); real PivotTable caches are not written |
| Charts | Insert ▸ Charts | missing | P2 | XlsxWriter supports charts; needs a chart IR + QA of the chart XML |
| Arithmetic operators in formulas (`+ - * /`) | yes | missing | P2 | the evaluator only accepts whole-function calls |
| Cross-sheet formulas into data sheets from intents | yes | have | — | Analysis formulas reference `Detail` ranges |
| Data validation types other than list (number range, date) | yes | missing | P2 | |
| Conditional formatting: colour scales, data bars, formula rules | yes | missing | P2 | |
| Merged cells, row heights, fonts per cell, alignment | yes | missing | P3 | |
| Comments / notes | yes | missing | P3 | |
| Sheet protection / locked cells | Protect Sheet | missing | P3 | |
| Hyperlinks, external links, macros, `INDIRECT`, `WEBSERVICE` | yes | — | — | deliberately forbidden (security and determinism) |
| Google Sheets-native output | Sheets file | — | — | XLSX imports into Google Sheets; the guide lists the Sheets menu equivalents |
| AI brief → workbook proposal (V1B) | — | have | — | allowlist unchanged: the new parity features are request-level fields the model cannot propose (`normalize_proposal` rejects unknown fields) |

## Implemented in this change

1. **Number formats** (`ColumnSpec.number_format`, allowlisted per type) →
   `SheetIR.column_formats` → XlsxWriter `num_format`; the manual tells the
   person to type the identical custom format code.
2. **Dropdown validation** (`ColumnSpec.allowed_values`) →
   `SheetIR.data_validations` → `data_validation(list)`, checked on reopen.
3. **Conditional formatting** (`WorkbookRequest.conditional_formats`) →
   `SheetIR.conditional_formats` → `conditional_format(cell)`, checked on reopen.
4. Fixes found while writing the Excel guide: header/title styles were
   overwritten, named ranges broke past column `Z`, integer formula results
   showed two decimals, and text/boolean formula results failed reopen QA.

Requests that do not use the new fields keep the same idempotency hash (the
new keys are dropped from the hash while unset), so earlier jobs still replay
under their idempotency key.

## Roadmap (prioritised)

1. P1 — `SUMIFS` / `COUNTIFS` / `MIN` / `MAX` / `ROUND` in the intent language, with V1B intents and eval cases.
2. P1 — Excel Table + AutoFilter on data sheets.
3. P1 — real PivotTable output (or keep the static `SUMIF` pivot and document it as such).
4. P2 — charts (column/line/pie) from a chart IR, with chart XML reopen QA.
5. P2 — arithmetic operators in the evaluator.
6. P2 — numeric/date validation types; colour-scale conditional formats.
7. P3 — cell-level styling, comments, protection.
