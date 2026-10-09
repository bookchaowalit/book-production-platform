# file-intake

Shared, bounded file intake for the production tools. Standard library only.

| Module | What it does |
|---|---|
| `detect.py` | `detect(path)` says what a file is from its leading bytes and ZIP directory, checked against the extension. A mismatch, a legacy or encrypted Office file, an empty or oversized file, or an unknown type is reported as an `issue`, never trusted. |
| `tables.py` | `read_table(path, sheet=None)` reads CSV/TSV (UTF-8 or Thai Windows `cp874`, delimiter sniffed) or XLSX (first sheet or a named sheet) into `header` + `rows` of strings. |
| `routes.py` | `routes_for(kind)` lists the production tools that take that kind of file and how. |
| `__main__.py` | `python -m file_intake inspect <file> [--sheet NAME]` prints a `file-intake-report.v1` JSON: kind, issue, routes, and a table preview. |

## Safety limits (XLSX/CSV)

- Table files ≤ 20 MiB; workbook ZIP expands to ≤ 100 MiB; encrypted entries refused.
- XML with a `DOCTYPE` or `ENTITY` declaration is refused (no entity expansion).
- ≤ 100,000 rows, ≤ 200 columns, ≤ 32,767 characters per cell.
- Formulas are never evaluated: a formula cell gives Excel's cached value, or an
  empty cell plus the note `formula_without_cached_value`.
- Numbers are shown the way Excel displays them (15 significant digits), so a
  stored `99.98999999999999` reads as `99.99`. Date-formatted numbers become ISO
  dates. Column types are decided by each tool's own validator, not here.
- Legacy `.xls`/`.doc` files are not read; save them as `.xlsx`/`.docx`.

## Where it is used

- Data Viz (`services/data-production-api`): `build --input file.xlsx|csv` and
  `propose --input file.xlsx --brief "..."`.
- Spreadsheet (book-production-platform): `spreadsheet from-file` (no AI) and
  `spreadsheet brief --input file.xlsx --brief "..."`.

book-production-platform carries a vendored copy under
`src/book_production_platform/file_intake/`; keep both copies identical (the
test `tests/test_vendored_packages_parity.py` checks it).

## Tests

```bash
PYTHONPATH=packages/file-intake python -m pytest packages/file-intake/tests -q
```
