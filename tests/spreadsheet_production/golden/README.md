# Spreadsheet V1A golden review set

`workbooks_20.json` is synthetic and covers Thai and English labels, null
text, ISO dates, Decimal amounts, integer counts, mixed text, and literals
beginning with `=`, `+`, `-`, and `@`. The benchmark verifies each workbook's
calculated amount total, cached formula values, Summary/Detail sheet names, and
reopenability as an XLSX ZIP/XML document.

For a manual review, open a generated `job-*/workbook.xlsx` in a spreadsheet
application and check that Summary formulas recalculate, Detail text remains
literal, dates display as dates, and two-decimal formatting is preserved.
