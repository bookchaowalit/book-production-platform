# Data + Data Viz V1A

This local-first service implements the first bounded Data product: a
deterministic **CSV Cleaner → Chart Builder** pipeline.

The standard path is:

```text
CSV + typed schema → DatasetIR → allowlisted transforms → ChartIR → cleaned.csv + chart.svg + report.json → reopen QA
```

The service supports `trim_text`, `fill_null`, `filter_equals`, and `sort_by`
transforms, then `bar`, `line`, `horizontal_bar`, or `pie` charts with `sum`,
`count`, or `avg` aggregation, optional `sort` / `top_n` / `group_rest_as`
("Other"), `value_decimals`, `thousands_separator`, and `data_labels`
(recipe v4; see [docs/PARITY.md](docs/PARITY.md) for what is still missing
compared with Excel, Power BI, and Tableau). Decimal arithmetic, schema validation, source lineage, artifact
hashes, and replay-safe idempotency are deterministic. User text is escaped in
SVG; charts show five y-axis ticks, schema-derived axis labels, and an optional
explicit value unit. The renderer never fetches network assets or executes input
content.

The standard path makes zero model calls. The optional V1B brief adapter
(below) may propose a chart mapping, but its output is validated into the same
`DatasetRequest` before anything is built.

The local pilot accepts up to 10,000 CSV data rows. Inferred schemas preserve
Unicode headers and treat integer-looking values with leading zeroes as text so
identifiers such as `001` are not changed. A per-idempotency-key lock
serializes duplicate local requests, and replay verifies every artifact hash and
size before returning an accepted result. The benchmark refuses a non-empty
output directory so existing evidence cannot be deleted accidentally.

## Local commands

From this directory:

```text
python -m pytest tests -q
python -m data_production.scripts.benchmark --fixture tests/fixtures/datasets_20.json --output-dir .runtime/data-viz-v1a-benchmark
python -m data_production.cli build --input request.json --output .runtime/data-job --idempotency-key example-1
```

HTTP endpoints are `GET /health` and `POST /v1/data/jobs`. The request body for
the POST route is `{ "idempotency_key": "...", "request": { ...DatasetRequest... } }`.
There is no production deployment target for this pilot.

## Job history, trace & manual steps

Every new job — accepted or rejected — writes, in its `job-<key>/` directory:

- `ir.json`: the validated `DatasetIR` + `ChartIR` the engine executed;
- `trace.json` (`production-trace.v1`): request and CSV hashes (never the
  text), each step (`parse_request`, `build_dataset_ir` with column types and
  per-transform row counts, `build_chart_ir`, `render_outputs`, `qa_outputs`,
  `write_manifest`) with its parameters and timing, the IR hash, artifacts with
  sha256, and whether AI proposed the request;
- `MANUAL-STEPS.md` (accepted jobs): how to make the same `cleaned.csv` and
  chart by hand in Microsoft Excel (Power Query import options, each cleaning
  step with the rows it dropped, one `SUMIFS`/`COUNTIFS`/`AVERAGEIFS` formula
  per category with the value it must give, chart type, axis titles, bounds,
  number format, colours, size, and SVG export), with Google Sheets notes.

`<output>/history.jsonl` gets one metadata line per job. Replays add nothing.
`build --input` also accepts a complete proposal written by `propose`; the
build trace then records the AI metadata (`ai.used`, `model_calls`, adapter).
`propose` writes its own `propose-<key>/trace.json` next to the proposal.

```text
python -m production_trace history --root .runtime/data-job
python -m production_trace show    --root .runtime/data-job <job_id>
python -m production_trace manual  --root .runtime/data-job <job_id>
python -m production_trace verify  --root .runtime/data-job <job_id>   # exit 1 if an artifact changed
```

The deterministic path needs `packages/production-trace` on `PYTHONPATH` (the
Production Hub and Pipelines already add it) and never imports the AI kit.

### Reading CSV and XLSX files

`build --input` and `propose --input` also take a `.csv`, `.tsv`, `.xlsx`, or
`.xlsm` file directly (`--sheet NAME` picks a workbook sheet; the first sheet
is the default). The file is read by the shared bounded reader in
`packages/file-intake` (add it to `PYTHONPATH`): UTF-8 or Thai Windows
(`cp874`) CSV, formulas never evaluated (cached values only), numbers as Excel
displays them, dates as ISO text. `propose` then also needs `--brief`.

```text
python -m data_production.cli build --input sales.xlsx --sheet "ยอดขาย" --output .runtime/data-job --idempotency-key sales-1
python -m data_production.cli propose --input sales.xlsx --brief "กราฟแท่งยอดขายรวมตามภูมิภาค" --locale th-TH \
  --output .runtime/proposal.json --idempotency-key sales-1 --provider-config .runtime/data-viz-provider.json
```

Unreadable files are rejected with a stable code (`sheet_not_found`,
`encrypted_or_legacy_office`, `extension_mismatch`, `xlsx_unsafe_xml`,
`too_many_rows`, ...).

## AI brief adapter (V1B)

`data_production/provider.py` implements the shared `brief-adapter.v1` pattern
(`packages/brief-adapter-kit`, spec REQ-AI-01..09 and REQ-DVZ-01).

- **What the AI proposes:** for one dataset and a free-text brief, the x and y
  columns (copied from the CSV header), one chart type from the engine
  allowlist (`bar`, `line`), one aggregation intent from the allowlist (`sum`,
  `count`, `avg`), and an optional unit label and title. Each critical choice
  must cite a verbatim brief quote: a column quote must name the header, and an
  aggregation quote must contain a word for exactly that aggregation.
- **What it never does:** see data rows (only header names and inferred
  types are sent), return numbers, categories, totals, SQL, formulas, or code,
  or build the chart. Unknown or computed fields reject the proposal; an
  unknown column, unbacked choice, two aggregations or chart types in the brief,
  or an ungrounded unit gives `needs_input`.
- **Engine checks:** the proposal goes through `validate_request`,
  `build_dataset_ir`, and `chart_checks.chart_semantic_issues` (allowlists,
  numeric y for `sum`/`avg`, no y for `count`, ordered x for `line`, unit vs
  aggregation and y-header unit, more than one currency in the brief), and
  then `build_chart_ir`. A request that already carries a `chart` skips the
  model (structured route, zero calls).
- **Fail-closed:** no `data-viz-provider-config.v1` file → `provider_not_configured`;
  budget is reserved before every call (at most two calls per job, one JSON
  repair); instruction-like briefs or headers abstain with zero calls.

```text
# needs packages/brief-adapter-kit on PYTHONPATH; deterministic commands do not
python -m data_production.cli brief-shadow --provider-config .runtime/data-viz-provider.json \
  --cases tests/fixtures/eval_dvz_brief30.json --split tests/fixtures/eval_dvz_split.json \
  --output .runtime/data-viz-shadow/report.json
python -m data_production.cli propose --input brief.json --output .runtime/proposal.json \
  --idempotency-key example-1 --provider-config .runtime/data-viz-provider.json
```

`propose` refuses a config in `mode: shadow` (`shadow_mode_only`) and only
writes the proposal; review it, then run `build` on the proposed request.

**Gate (EVAL-DVZ-brief30, held-out cases only):** critical mapping exact match
on clear briefs ≥ 95%, `needs_input` recall on ambiguous briefs 100%, zero
silent adversarial passes, zero model-computed values, zero canary leaks, at
most two calls per job, zero calls on structured cases, and AC-DVZ-01a: zero
axis/unit mismatches and zero chart types outside the allowlist. The shadow
report is metadata only and its results are never used. The owner switches the
config to `mode: live` only after a passing report and rate-card approval.
