# Documents V1B Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:executing-plans` to implement this plan task-by-task. The parent task already selected inline execution on `main`.

**Goal:** เพิ่มเส้นทาง `free-text brief → bounded extraction → validated DocumentRequest → Documents V1A renderer` โดยไม่ให้โมเดลคำนวณยอดหรือสร้าง executable action

**Architecture:** รับ brief ผ่านสัญญา `document-brief.v1` แล้วลอง deterministic labeled parser ก่อน หากยังขาด critical fields จึงเรียก optional extraction adapter ที่ถูกจำกัดด้วย token/call budget; output ของ adapter จะถูกตรวจ provenance และแปลงเป็น `DocumentRequest` ก่อนส่งเข้า ledger, recipe, render และ QA เดิม

**Tech Stack:** Python 3.11+, Pydantic, SQLite, pytest, FastAPI และ CLI เดิม; ไม่มี provider dependency ใหม่และไม่มี paid call ใน default path

**Spec:** `docs/superpowers/specs/2026-09-22-logic-first-production-platform-design.md`

## Global Constraints

- Structured V1A requests remain model-free with `model_calls=0`.
- V1B accepts at most 8,000 approximate input tokens per extraction call, 2,000 output tokens per call, 2 calls per job, 30 seconds per call, and 120 seconds per job.
- Missing or invalid critical fields return `needs_input` with actionable field names; never invent quantity, price, currency, date, or customer.
- Totals remain Decimal calculations in `recipe.py`; extraction output never supplies derived totals.
- Telemetry and manifests store extraction metadata and hashes, never the raw brief text or provider response.
- Paid providers are disabled by default; this cycle adds a provider-neutral adapter protocol and a deterministic local parser only.
- Do not change Video, browser-extension, customer-demo, or unrelated working-tree files.

## Review Focus

- Thai and English labeled briefs with decimal commas and date variants must produce typed fields or `needs_input`.
- A model adapter returning unknown fields, missing provenance, or derived totals must be rejected before rendering.
- Reusing an idempotency key with a changed brief must conflict even when both briefs extract the same structured fields.
- Concurrent budget reservations must never exceed the two-call job cap.
- The API and CLI must not expose the raw brief in a response, manifest, or cost event.

### Task 1: Extraction contract and deterministic parser

**Files:**
- Create: `services/document-production-api/document_production/extraction.py`
- Test: `services/document-production-api/tests/test_extraction.py`

**Interfaces:**
- Produces `BriefRequest`, `ExtractionResult`, `ExtractionAdapter`, `RuleBasedExtractor`, and `validate_extraction_result`.
- `RuleBasedExtractor.extract(brief: str, *, locale: str) -> ExtractionResult` parses labeled lines such as `title:`, `customer:`, `date:`, `currency:`, and `item: description | quantity | unit_price | tax_rate`.

- [ ] Write failing tests for complete Thai/English briefs, missing critical fields, malformed item values, and token cap rejection.
- [ ] Run `python -X utf8 -m pytest services/document-production-api/tests/test_extraction.py -q` and observe the expected missing-module failures.
- [ ] Implement typed extraction models, required-field calculation, approximate token limits, decimal/date parsing, provenance lists, and the deterministic parser.
- [ ] Run the focused extraction tests and then the product suite.
- [ ] Commit `feat: add bounded document brief extraction contract`.

### Task 2: Budget and routing gate

**Files:**
- Create: `services/document-production-api/document_production/budget.py`
- Create: `services/document-production-api/document_production/routing.py`
- Test: `services/document-production-api/tests/test_budget.py`
- Test: `services/document-production-api/tests/test_routing.py`

**Interfaces:**
- `BudgetLedger(root).reserve(job_id, calls, input_tokens, output_tokens)` atomically enforces the V1B caps in `ai_budget.sqlite3`.
- `route_brief(brief_request, *, adapter=None, budget=None) -> RouteResult` runs the deterministic parser first, optionally calls an adapter, validates the result, and returns `accepted`, `needs_input`, or `rejected`.

- [ ] Write failing tests for atomic two-call caps, concurrent reservations, parser-first routing, missing-input abstention, and rejection of untrusted model fields.
- [ ] Run the focused tests and observe missing-module failures.
- [ ] Implement SQLite reservation, adapter protocol validation, critical-field gate, and conversion to a `DocumentRequest` without trusting model totals.
- [ ] Run focused tests and the product suite.
- [ ] Commit `feat: add document extraction budget and routing gate`.

### Task 3: Job, API, and CLI integration

**Files:**
- Modify: `services/document-production-api/document_production/jobs.py`
- Modify: `services/document-production-api/document_production/main.py`
- Modify: `services/document-production-api/document_production/cli.py`
- Modify: `services/document-production-api/README.md`
- Test: `services/document-production-api/tests/test_jobs.py`
- Test: `services/document-production-api/tests/test_api.py`
- Test: `services/document-production-api/tests/test_cli.py`

**Interfaces:**
- Add `run_document_brief_job(request, output_root, idempotency_key, *, adapter=None) -> JobResult`.
- Add `POST /v1/documents/briefs` and CLI command `document-production brief`.
- Preserve existing `run_document_job` behavior and route completed briefs through its V1A ledger/render/QA path.

- [ ] Write failing tests for accepted brief rendering, `needs_input` 422/exit 4, changed-brief idempotency conflict, metadata-only response, and zero model calls on parser success.
- [ ] Run the focused tests and observe the expected failures.
- [ ] Implement optional job request hash payload, extraction metadata, brief route, CLI command, and documented status mapping.
- [ ] Run the full product suite, Ruff, and mypy strict.
- [ ] Commit `feat: expose documents v1b brief jobs`.

### Task 4: Acceptance fixtures and final verification

**Files:**
- Modify: `services/document-production-api/tests/test_acceptance.py`
- Modify: `services/document-production-api/document_production/scripts/benchmark.py`
- Modify: `services/document-production-api/README.md`

- [ ] Add synthetic brief classes for clear, missing, invalid, and adversarial inputs without customer data.
- [ ] Add a `brief` benchmark mode reporting accepted/needs_input/rejected, model calls, abstentions, and budget failures without storing raw briefs.
- [ ] Run the product suite, Ruff, mypy strict, fresh structured benchmark, and brief benchmark.
- [ ] Record any root check blocked by the host environment, update the V1B ledger, and commit `test: benchmark documents v1b brief routing`.
