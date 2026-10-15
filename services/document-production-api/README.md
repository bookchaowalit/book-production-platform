# Documents V1A

This local product accepts structured invoice input and produces DOCX and PDF
artifacts through a pinned deterministic recipe. It performs validation,
Decimal-safe calculations, local rendering, and later QA/manifest publication.
V1A does not call an LLM, OCR provider, hosted API, or shell command.

The service depends on the sibling `packages/document-production-ir` contract.

Run the local CLI from the repository root after exposing the two local packages:

```powershell
$env:PYTHONPATH = "services/document-production-api;packages/document-production-ir;packages/brief-adapter-kit;packages/production-trace"
python -m document_production.cli render `
  --input services/document-production-api/examples/invoice.json `
  --output .runtime/document-production-smoke `
  --idempotency-key smoke-001
```

The command prints artifact metadata, QA status, and `model_calls: 0`. The HTTP
surface exposes `GET /health` and `POST /v1/documents/jobs`; set
`DOCUMENT_PRODUCTION_OUTPUT_ROOT` to choose the local output root.

PDFs embed one TrueType font for Thai and Latin text. Set
`DOCUMENT_PRODUCTION_FONT` to a `.ttf` path; otherwise the renderer tries
Leelawadee UI (`C:/Windows/Fonts/LeelawUI.ttf`), TLWG Garuda
(`/usr/share/fonts/truetype/tlwg/Garuda.ttf`, Debian/Ubuntu package
`fonts-tlwg-garuda-ttf`), Sarabun, then TLWG Loma, and falls back to Helvetica,
which rejects Thai text. Every font must cover printable ASCII (Latin letters,
digits, punctuation) as well as Thai, because PDF text QA reads dates and
totals back: a Thai-only face such as `NotoSansThai-Regular.ttf` is skipped as
a built-in fallback, and a configured font that is missing or lacks those
glyphs fails the render instead of producing an unreadable PDF.

The local smoke path requires no provider configuration. A fresh benchmark must
use an empty output directory; a replay benchmark reuses its SQLite ledger and
counts only idempotency hits:

```powershell
$env:PYTHONPATH = "services/document-production-api;packages/document-production-ir;packages/brief-adapter-kit;packages/production-trace"
python -m document_production.scripts.benchmark `
  --fixture services/document-production-api/tests/fixtures/structured_cases.json `
  --output .runtime/documents-benchmark-fresh `
  --mode fresh
python -m document_production.scripts.benchmark `
  --fixture services/document-production-api/tests/fixtures/structured_cases.json `
  --output .runtime/documents-benchmark-fresh `
  --mode replay
```

The API returns `201` for an accepted or replayed job, `202` with
`Retry-After: 1` while an equivalent job is running, `409` for an idempotency
key bound to different input, `422` for request validation, and `500` for an
engine or QA failure. The CLI returns `0` for accepted output, `2` for input
or validation errors, `3` while a job is pending, `4` when a brief needs
more required fields, and `5` for an idempotency conflict (the CLI counterpart
of the API `409`).

`document-production smoke` renders a bundled structured sample end to end
through the deterministic path; it needs no input file and no provider
configuration, and always reports `model_calls: 0`:

```powershell
$env:PYTHONPATH = "services/document-production-api;packages/document-production-ir;packages/brief-adapter-kit;packages/production-trace"
python -m document_production.cli smoke --output .runtime/document-production-smoke
```

The smoke command prints a JSON summary (`status`, `job_id`, `model_calls`,
`qa_status`, `artifacts`, `replayed`) and exits `0` when the sample is accepted;
re-running it against the same output directory replays the stored manifest.

V1B accepts `POST /v1/documents/briefs` and the CLI command
`document-production brief`. The brief uses labeled lines (`title:`,
`customer:`, `date:`, `currency:`, and `item: description | quantity |
unit_price | tax_rate`). The deterministic parser runs first with zero model
calls. A future gateway adapter can fill missing fields only within the
8,000-input-token, 2,000-output-token, and two-call job budget; totals remain
calculated by the Decimal recipe. A `needs_input` result returns the missing
field names and does not publish a manifest.

## Job history, trace & manual steps

Every render job (all templates; success **and** failure) writes, through the
shared [production-trace](../../packages/production-trace/README.md) package:

- `jobs/<job_id>/trace.json` — inputs (hashes only), every engine step, the
  validated IR (`ir.json`), artifacts with sha256, QA, and whether AI proposed
  the data;
- `jobs/<job_id>/MANUAL-STEPS.md` — how to build the same document by hand in
  Microsoft Word (margins, footer with page number, styles, every table row,
  totals copied from the engine), then export PDF;
- `history.jsonl` in the output root — one line per job.

```bash
export PYTHONPATH="services/document-production-api:packages/document-production-ir:packages/production-trace"
python -m production_trace history --root .runtime/document-out
python -m production_trace manual  --root .runtime/document-out <job_id>
python -m production_trace verify  --root .runtime/document-out <job_id>
```

Feature comparison with Word and invoicing software: [docs/PARITY.md](docs/PARITY.md).

## Brief adapter interface (T8.1/T8.2)

The adapter boundary is versioned as `brief-adapter.v1`
(`BRIEF_ADAPTER_VERSION` in `document_production/extraction.py`). Input is the
raw brief text plus the document template id/version and a locale hint;
output is an `ExtractionResult` carrying name/value fields with provenance —
never totals, never money. `validate_extraction_result` rejects results with
the wrong `adapter_version`, unknown fields, missing provenance, more than two
model calls, or over-budget tokens, and forbids `subtotal`, `tax_total`,
`grand_total`, or `derived_totals` in adapter fields: only `recipe.py`
(`build_document_ir`) computes money.

Three implementations exist:

- `RuleBasedExtractor` (default, zero model calls) — parses explicit labeled
  briefs. It abstains with `needs_input` on missing or invalid fields, and it
  hardens extraction against adversarial content: path-like strings, HTML
  tags, and SQL statement fragments in text fields become `field_invalid`;
  currency must be exactly one three-letter code; line-item numbers must be
  non-negative and bounded (quantity/unit price ≤ 1e12, tax rate ≤ 100).
- `ScriptedBriefAdapter` (`tests/fake_adapter.py`) — the deterministic fake
  adapter for the 60-case suite (`tests/fixtures/adapter_suite_cases.json` +
  `structured_cases.json`: structured 20, clear 20, ambiguous 10, adversarial
  10; half Thai, half English). It maps an exact brief text to a scripted
  outcome and conservatively abstains on anything unscripted.
- `ProviderBriefAdapter` (`document_production/provider.py`) — the real
  adapter over the Central AI Gateway, built on the shared
  [brief adapter kit](../../packages/brief-adapter-kit/README.md). It is
  fail-closed: construction requires a validated
  `documents-provider-config.v1` (gateway URL, the *name* of the token
  environment variable, rate card, per-job/day/month USD caps, owner approval)
  and a persistent budget ledger root; otherwise it raises
  `ProviderNotConfigured`. A missing token at call time returns
  `provider_not_configured` with zero calls and zero charge.

### Gateway adapter (T3.3 / T8.3)

Each extraction:

1. abstains with `needs_input` and zero calls when the brief contains
   instruction-like content (prompt-injection guard);
2. reserves the call and the worst-case cost (input estimate + 2,000 output
   tokens at the rate card) atomically against the per-job, per-day, and
   per-month caps in `ai_budget.sqlite3`; any cap → `budget_exceeded` before
   dispatch;
3. calls `POST {gateway_url}/api/ai/generate` once (`json_object`, temperature
   0, timeout ≤ 30 s, job deadline ≤ 120 s), with at most one repair call if
   the reply is not the required JSON object (≤ 2 calls per job);
4. settles the measured cost from provider usage; an unknown cost stays fully
   charged;
5. grounds every value against the brief (REQ-AI-07): customer name and line
   item text must appear verbatim, quantities/prices/tax rates must be numbers
   the brief states, the date must be a date the brief states (ISO, D/M/Y,
   English or Thai month names, Buddhist Era), and the currency must be the
   only currency the brief names. Ungrounded or "inferred" critical values
   become `missing`, so the route abstains with `needs_input`. Only `title`
   and `notes` may be inferred. Totals from the model are rejected.

Gateway and provider error bodies are never surfaced; issues carry a stable
code such as `gateway_timeout` or `gateway_rate_limited`. Raw brief text never
appears in telemetry, manifests, the ledger, or error payloads (canary tests).

### Shadow mode and going live (T3.4)

Provider configs start in `mode: "shadow"`. `brief --adapter provider`
refuses a shadow config with `shadow_mode_only`. `brief-shadow` runs the real
adapter over the eval cases, never renders, and writes a metadata-only report
(case IDs, statuses, metrics; no brief text or values):

```bash
export SOLO_EMPIRE_AI_GATEWAY_TOKEN=...   # from the secret store; never in the config file
python -m document_production.cli brief-shadow \
  --provider-config .runtime/documents-provider-config.json \
  --cases tests/fixtures/adapter_suite_cases.json \
  --cases tests/fixtures/adapter_freetext_cases.json \
  --split tests/fixtures/adapter_eval_split.json \
  --budget-root .runtime/document-production-ai-budget \
  --output .runtime/documents-shadow-report.json
```

The gate (spec §4.2, AC-DOC-01a) is decided on held-out cases only: critical
exact match on clear briefs ≥ 95%, `needs_input` recall on ambiguous briefs
100%, adversarial silent passes 0, model-computed totals 0, canary leaks 0,
≤ 2 calls per job. Exit code `0` = passed, `6` = gate failed, `2` =
configuration error. Prompts may be tuned only against the dev IDs in
`adapter_eval_split.json`. After a passing report the owner sets
`mode: "live"`; then:

```bash
python -m document_production.cli brief --adapter provider \
  --provider-config .runtime/documents-provider-config.json \
  --input brief.json --output .runtime/brief-out --idempotency-key brief-001
```

Start from `examples/provider-config.example.json` (placeholders only).

The service records local compute as `unknown` until a rate card is configured;
it does not treat local CPU or replayed artifact bytes as zero-cost generation.
`cpu_seconds` uses the local process CPU clock for diagnostics; under concurrent
in-process jobs it must not be used as a per-job charge without isolated workers
or a separate concurrency-aware measurement.

Documents V1C adds `document-production scan`. It reads a versioned JSON request
with `document_id`, exactly one source path relative to `--input-root`, and an
optional `locale`. The source is either `source_pdf` (original PDF-only field)
or `source_file`, which also accepts DOCX and raster images (`.png`, `.jpg`,
`.jpeg`, `.tif`, `.tiff`, `.webp`). The extension and the file's magic bytes
must agree (`source_type_mismatch` otherwise); any other extension is
`source_type_unsupported`. For PDFs, embedded text is parsed first into field proposals with page excerpts;
conflicting values and missing fields require human input. Only pages with
fewer than 24 embedded text characters are sent to the optional local OCR
adapter. OCR runs only when both Poppler (`pdftoppm`) and Tesseract are available,
with fixed English or Thai language mappings, 50-page/20 MiB input limits, a
10-page OCR budget, per-page timeouts, and no shell. Otherwise those pages are marked
`ocr_unavailable` and the proposal abstains.

DOCX sources are read with python-docx (paragraphs in order, then table rows;
a row whose first cell is a known label becomes `Label: value | ...`, and cells
that already contain `Label: value` lines stay intact). The archive is checked
before parsing: at most 100 MiB of declared uncompressed content
(`docx_uncompressed_limit_exceeded`), no encrypted entries, and OLE files such
as encrypted DOCX or legacy `.doc` are rejected as `docx_encrypted_or_legacy`.
DOCX text fills synthetic pages of at most 25,000 characters split on line
boundaries (at most 50, else `docx_page_limit_exceeded`) with method
`embedded_text`; DOCX never uses OCR.

Image sources are one page with method `ocr`. Dimensions are read from the
header without decoding pixels and must be at most 12,000 px per side and 50
megapixels (`image_too_large`). Images need only Tesseract (no Poppler); without
it the page is marked `ocr_unavailable` with a hint to install `tesseract-ocr`
and `tesseract-ocr-tha`, and the proposal returns `needs_input`.

The output JSON contains field provenance, QA results, a source hash manifest
(`page_count` is the PDF page count, synthetic DOCX page count, or 1 for an
image), and `model_calls: 0`; it does not render or publish a document.

```powershell
$env:PYTHONPATH = "services/document-production-api;packages/document-production-ir;packages/brief-adapter-kit;packages/production-trace"
python -m document_production.cli scan `
  --input scan-request.json `
  --input-root .runtime/scans `
  --output .runtime/scan-proposal.json
```

## Transcript documents (T6.2)

The deterministic `transcript` template renders a verbatim, timestamped
transcript document from a `transcript-document-request.v1` payload (published
JSON Schema at `schemas/transcript-document-request.v1.schema.json`). Each
segment becomes one paragraph `[HH:MM:SS.mmm - HH:MM:SS.mmm] text` with an
optional `speaker:` prefix; the heading is Thai (`บทถอดเสียง`) for `th*`
locales. The recipe publishes an empty `derived_totals` mapping, so the
invoice Totals block and totals arithmetic are skipped while every artifact
integrity QA gate is inherited. Semantic summarization requires AI and stays
deferred to T8 / Phase 4; this path never calls a model.

```powershell
$env:PYTHONPATH = "services/document-production-api;packages/document-production-ir;packages/brief-adapter-kit;packages/production-trace"
python -m document_production.cli transcript `
  --input transcript-document-request.json `
  --output .runtime/transcript-smoke `
  --idempotency-key transcript-001
```

Exit codes match `render`: `0` accepted, `2` validation error, `3` pending,
`5` idempotency conflict.

## Tabular report documents (T6.3)

The deterministic `report` template (`report@1`) renders a versioned
`report-document-request.v1` payload (published JSON Schema at
`schemas/report-document-request.v1.schema.json`) as a tabular monthly-report
document for the production pipeline: one data table whose last column is
numeric, an explicit Totals block, and reference lines (bare file name,
media type, full sha256) for chart and workbook artifacts produced by
upstream pipeline stages. The recipe rejects requests whose
`totals.subtotal` does not equal the Decimal sum of the table's last column,
or whose `grand_total` does not equal `subtotal + tax_total`, so the invoice
totals arithmetic QA gate is inherited unchanged. Section headings are Thai
for `th*` locales. Embedding binary chart images would require an image field
on the shared `document_ir.DocumentIR` contract and stays out of scope here.

```powershell
$env:PYTHONPATH = "services/document-production-api;packages/document-production-ir;packages/brief-adapter-kit;packages/production-trace"
python -m document_production.cli report `
  --input report-document-request.json `
  --output .runtime/report-smoke `
  --idempotency-key report-001
```

Exit codes match `render`: `0` accepted, `2` validation error, `3` pending,
`5` idempotency conflict.
