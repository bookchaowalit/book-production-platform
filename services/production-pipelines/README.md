# Production Pipelines (T6.3)

Local-first deterministic orchestrator that wires the four production tools
into the monthly report chain:

```text
Data pipeline (clean) -> Data Viz (chart) -> Spreadsheet (workbook) -> Documents (report DOCX/PDF)
```

The chain is thin orchestration: every stage is invoked exactly as its owning
service documents it, via subprocess with explicit `PYTHONPATH`; no stage code
is imported or copied, and no code ownership moves. Work passes between tools
only through **versioned artifact contracts** validated at each boundary.

## Contracts

Owned by this package (published JSON Schemas in `schemas/`):

- `pipeline-report-config.v1` — the monthly report job configuration
  (`--config` input). Structural schema plus semantic checks (chart columns
  must exist in the dataset; `y_column` is required and numeric for `sum`/`avg`;
  every declared column must appear in the CSV header row).
- `pipeline-report.v1` — the published pipeline manifest linking each stage's
  tool, command, request, job manifest, and artifact identities
  (name, relative path, media type, sha256, byte size). All paths are relative
  to the run directory; absolute local paths, drive letters, and backslashes
  are rejected.

Consumer-side boundary validators (the upstream services own these contracts;
copies here validate only what this orchestrator emits or consumes, mirroring
the T6.2 precedent in `services/video-postproduction`):

- `dataset-request.v1` — requests for the Data and Data Viz stages
  (`services/data-production-api`).
- `workbook-request.v1` — request for the Spreadsheet stage
  (`book-production-platform`, nested repository).
- `report-document-request.v1` — kept byte-identical with
  `services/document-production-api/schemas/report-document-request.v1.schema.json`
  by a dedicated alignment test.

## Stage ownership and invocation

| Stage | Owner | Invocation |
| --- | --- | --- |
| `data_pipeline` | `services/data-production-api` | `python -m data_production.cli build` |
| `data_viz` | `services/data-production-api` | `python -m data_production.cli build` (chart over the stage-1 `cleaned.csv`) |
| `spreadsheet` | `book-production-platform` (nested repo) | `python -m book_production_platform.cli spreadsheet build` |
| `documents` | `services/document-production-api` | `python -m document_production.cli report` (template `report@1`) |

## Runner

```bash
PYTHONPATH=services/production-pipelines python -m production_pipelines monthly_report \
  --config <job.json> \
  --output <run-directory>
```

Exit codes: `0` published, `2` config or input error, `3` stage failure,
`4` boundary contract violation, `5` output directory already exists.
Failures print one stable JSON error object (with the failing `stage` id and
`error` code) to stderr.

Determinism and safety:

- Offline, no model calls (`model_calls` is pinned to `0` in the manifest).
- Each stage runs with a bounded timeout (configurable
  `stage_timeout_seconds`, 30–900) using typed argv, `shell=False`.
- Every stage artifact is hash-verified against the stage manifest before the
  next stage starts.
- The run builds in a hidden staging directory and publishes atomically by
  renaming it into place only after the validated `pipeline-report.v1`
  manifest is written. A failed run publishes nothing and removes its staging
  directory; an existing output directory is never overwritten.

## Run layout

```text
<run-directory>/
  pipeline-manifest.json
  stages/
    01-data-pipeline/request.json + job-<hash>/{cleaned.csv, report.json, manifest.json}
    02-data-viz/request.json      + job-<hash>/{cleaned.csv, chart.svg, report.json, manifest.json}
    03-spreadsheet/request.json   + job-<hash>/{workbook.xlsx, manifest.json}
    04-document/request.json      + jobs/<uuid>/{document.docx, document.pdf, manifest.json}
```

## Local verification

```bash
cd services/production-pipelines
python -m pytest tests -q
python -m ruff check .
MYPYPATH="../../packages/document-production-ir" python -m mypy production_pipelines
```

The end-to-end test (`tests/test_e2e_monthly_report.py`) runs all four real
stage CLIs on a small synthetic dataset; it skips automatically when stage
dependencies are not installed.

## Known boundary

Embedding binary chart images inside the DOCX/PDF would require an image
field on the shared `document_ir.DocumentIR` contract
(`packages/document-production-ir`), which is outside this change. Template
`report@1` therefore references each chart and the workbook by bare file
name, media type, and full sha256, and renders the aggregation table and
totals block; the totals arithmetic is re-verified by the documents QA gate
(`subtotal` equals the table column sum, `grand_total` equals
`subtotal + tax_total`).
