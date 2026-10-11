# Documents V1A Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a deterministic Documents V1A vertical slice that accepts a structured request, produces a versioned document IR, renders DOCX and PDF artifacts locally, and records QA, provenance, idempotency, and cost evidence with zero model calls.

**Architecture:** Create one independently testable `document-production` product boundary under `services/` with a small reusable IR package under `packages/`. The service executes a pinned recipe: validate input → calculate derived values → render → inspect artifacts → write an immutable manifest. There is no LLM or hosted provider dependency in V1A; later extraction/OCR adapters can enter through the same typed contracts without changing the deterministic path.

**Tech Stack:** Python 3.11+, Pydantic v2, JSON Schema, `python-docx` for DOCX, ReportLab for PDF, FastAPI for the bounded local API, pytest, and standard-library SHA-256 hashing. Use the repository's existing service layout and dependency pinning style.

**Spec:** `docs/superpowers/specs/2026-09-22-logic-first-production-platform-design.md`

## Global Constraints

- V1A accepts only structured text/numeric input and fixed templates; scanned uploads, free-text extraction, OCR, and hosted model calls are out of scope.
- The deterministic path must work with zero provider configuration and must report `model_calls=0` in its evidence.
- Inputs above 100 line items or outputs above 10 pages fail with an actionable validation error; content must never be silently truncated.
- Required fields, currency rounding, and totals must be validated before rendering and after rendering where applicable.
- Every artifact and derived value carries schema/version, producer/version, source lineage, and content hash metadata.
- Model output and arbitrary shell commands are not accepted by this service; only registered typed operations are executable.
- Duplicate requests with the same idempotency key and input hash return the existing result; conflicting reuse of a key fails safely.
- Private or restricted payloads are not placed in shared caches or telemetry; telemetry contains metadata and usage only.
- Do not modify `services/video-postproduction-api/`, `packages/video-postproduction-edl/`, unrelated user changes, or production deployment configuration.
- Do not add paid services, credentials, model weights, GPU provisioning, or external messages.

## Review Focus

- Currency values with more than two fractional digits must follow the declared rounding mode and produce the same total in IR, DOCX, and PDF.
- A missing required field, empty line item, zero/negative quantity, unsupported currency, or malformed date must be rejected before any artifact is written.
- A line-item count of 101 and a document render exceeding the page cap must stop with an actionable error rather than truncating output.
- Replaying an idempotency key with changed input or template version must fail without overwriting the original artifacts.
- A renderer crash, partial artifact, or missing output directory must leave no accepted manifest and must not report a zero cost or a successful job.

---

### Task 1: Establish the product boundary and typed document IR

**Files:**
- Create: `packages/document-production-ir/README.md`
- Create: `packages/document-production-ir/document_ir/__init__.py`
- Create: `packages/document-production-ir/document_ir/models.py`
- Create: `packages/document-production-ir/document_ir/validation.py`
- Create: `packages/document-production-ir/document_ir/schema.json`
- Create: `packages/document-production-ir/tests/test_models.py`
- Create: `packages/document-production-ir/tests/test_validation.py`
- Create: `packages/document-production-ir/pyproject.toml`

**Interfaces:**
- Produces `DocumentRequest`, `DocumentIR`, `DocumentSection`, `DocumentTable`, `DocumentRow`, `ArtifactRef`, `ValidationIssue`, and `validate_request()` for the service tasks.
- `DocumentRequest` fields: `schema_version: str`, `template_id: str`, `template_version: str`, `document_id: str`, `locale: str`, `currency: str`, `title: str`, `customer_name: str`, `issue_date: date`, `line_items: list[LineItemInput]`, `notes: str | None`.
- `LineItemInput` fields: `description: str`, `quantity: Decimal`, `unit_price: Decimal`, `tax_rate: Decimal`.
- `DocumentIR` fields: `schema_version`, `document_id`, `template_ref`, `locale`, `currency`, `sections`, `derived_totals`, `source_refs`, `assumptions`.
- `validate_request()` returns a stable list of issue codes and never mutates the request.

- [ ] **Step 1: Write the failing model and validation tests**

```python
def test_request_round_trips_decimal_and_date():
    request = DocumentRequest.model_validate(SAMPLE_REQUEST)
    assert request.line_items[0].quantity == Decimal("2")
    assert request.issue_date.isoformat() == "2026-09-22"

def test_validation_rejects_empty_description_and_zero_quantity():
    issues = validate_request(SAMPLE_REQUEST | {"line_items": [{"description": "", "quantity": "0", "unit_price": "10.00", "tax_rate": "7"}]})
    assert {issue.code for issue in issues} == {"line_item_description_required", "quantity_must_be_positive"}

def test_validation_rejects_101_line_items_without_truncating():
    issues = validate_request(SAMPLE_REQUEST | {"line_items": SAMPLE_REQUEST["line_items"] * 101})
    assert any(issue.code == "line_item_limit_exceeded" for issue in issues)
```

- [ ] **Step 2: Run the focused tests and verify they fail because the package does not exist**

Run: `python -m pytest packages/document-production-ir/tests/test_models.py packages/document-production-ir/tests/test_validation.py -q`

Expected: FAIL with an import error for `document_ir`.

- [ ] **Step 3: Implement the Pydantic models, decimal-safe field types, request limits, stable issue codes, and JSON Schema export**

Use `Decimal` for money and rates, quantize only in the calculation layer, reject unsupported currencies (`THB`, `USD`, `EUR` are the initial allowlist), and preserve source field paths in `ValidationIssue.path`.

- [ ] **Step 4: Run the focused tests and schema check**

Run: `python -m pytest packages/document-production-ir/tests -q`

Expected: all model and validation tests pass, including the 101-item boundary and no input mutation.

- [ ] **Step 5: Commit the package**

```bash
git add packages/document-production-ir
git commit -m "feat: add deterministic document IR contract"
```

### Task 2: Add deterministic recipe calculation and document renderers

**Files:**
- Create: `services/document-production-api/README.md`
- Create: `services/document-production-api/pyproject.toml`
- Create: `services/document-production-api/document_production/__init__.py`
- Create: `services/document-production-api/document_production/recipe.py`
- Create: `services/document-production-api/document_production/renderers.py`
- Create: `services/document-production-api/document_production/templates/invoice-v1.json`
- Create: `services/document-production-api/tests/test_recipe.py`
- Create: `services/document-production-api/tests/test_renderers.py`

**Interfaces:**
- Consumes `DocumentRequest` from Task 1.
- Produces `build_document_ir(request) -> DocumentIR` and `render_document(ir, output_dir) -> list[ArtifactRef]`.
- `build_document_ir()` must compute `subtotal`, `tax_total`, and `grand_total` with `Decimal`, round each monetary line to two places using `ROUND_HALF_UP`, and put calculation method/version in `assumptions`.
- `render_document()` writes `document.docx` and `document.pdf` atomically through temporary files, returns hashes and byte sizes, and raises a typed `RenderError` on failure.

- [ ] **Step 1: Write failing calculation and renderer tests**

```python
def test_recipe_calculates_totals_without_model_calls():
    ir = build_document_ir(sample_request())
    assert ir.derived_totals == {"subtotal": "20.00", "tax_total": "1.40", "grand_total": "21.40"}
    assert ir.assumptions["calculation"] == "decimal-half-up-v1"

def test_renderer_writes_openable_docx_and_pdf(tmp_path):
    artifacts = render_document(build_document_ir(sample_request()), tmp_path)
    assert {artifact.media_type for artifact in artifacts} == {"application/vnd.openxmlformats-officedocument.wordprocessingml.document", "application/pdf"}
    assert all((tmp_path / artifact.filename).stat().st_size > 0 for artifact in artifacts)

def test_renderer_rejects_missing_template(tmp_path):
    with pytest.raises(RenderError, match="template"):
        render_document(sample_ir(template_ref="missing@1"), tmp_path)
```

- [ ] **Step 2: Run the focused tests and verify the missing recipe/renderer failures**

Run: `python -m pytest services/document-production-api/tests/test_recipe.py services/document-production-api/tests/test_renderers.py -q`

Expected: FAIL with import errors for `document_production.recipe` and `document_production.renderers`.

- [ ] **Step 3: Implement the pinned `invoice-v1` recipe and local renderers**

Render from `DocumentIR`, never from raw model-like input. The DOCX renderer must include title, customer, issue date, line-item table, notes, and totals. The PDF renderer must use the same normalized IR values and include a page count in its artifact metadata. No renderer may invoke a network service or model.

- [ ] **Step 4: Run the focused tests and inspect the golden output metadata**

Run: `python -m pytest services/document-production-api/tests/test_recipe.py services/document-production-api/tests/test_renderers.py -q`

Expected: all tests pass; both artifacts are non-empty and hashes are stable for the same IR and library versions.

- [ ] **Step 5: Commit the recipe and renderers**

```bash
git add services/document-production-api packages/document-production-ir
git commit -m "feat: render deterministic document artifacts"
```

### Task 3: Add artifact QA, provenance, cost events, and idempotent job execution

**Files:**
- Create: `services/document-production-api/document_production/jobs.py`
- Create: `services/document-production-api/document_production/qa.py`
- Create: `services/document-production-api/document_production/telemetry.py`
- Create: `services/document-production-api/tests/test_jobs.py`
- Create: `services/document-production-api/tests/test_qa.py`
- Create: `services/document-production-api/tests/test_telemetry.py`

**Interfaces:**
- Consumes `build_document_ir()` and `render_document()` from Task 2.
- Produces `run_document_job(request, output_root, idempotency_key) -> JobResult`.
- `JobResult` contains `job_id`, `status`, `model_calls`, `artifacts`, `qa`, `cost_event`, and `manifest_path`.
- `cost_event` must distinguish `estimated`, `actual`, and `unknown` amounts and record `model_calls=0`, CPU duration, bytes written, and `rate_card_version="local-v1"`.
- `run_document_job()` writes a manifest only after both artifacts and QA pass; a conflicting idempotency key raises `IdempotencyConflict` without modifying the original directory.

- [ ] **Step 1: Write failing job, QA, and telemetry tests**

```python
def test_job_reports_zero_model_calls_and_writes_manifest(tmp_path):
    result = run_document_job(sample_request(), tmp_path, "idem-001")
    assert result.status == "accepted"
    assert result.model_calls == 0
    assert json.loads(result.manifest_path.read_text())["qa"]["status"] == "passed"

def test_same_idempotency_key_returns_same_result(tmp_path):
    first = run_document_job(sample_request(), tmp_path, "idem-001")
    second = run_document_job(sample_request(), tmp_path, "idem-001")
    assert second.job_id == first.job_id
    assert second.manifest_path == first.manifest_path

def test_changed_input_with_same_key_does_not_overwrite(tmp_path):
    run_document_job(sample_request(), tmp_path, "idem-001")
    with pytest.raises(IdempotencyConflict):
        run_document_job(sample_request(title="changed"), tmp_path, "idem-001")

def test_failed_renderer_has_no_accepted_manifest(tmp_path, monkeypatch):
    monkeypatch.setattr("document_production.jobs.render_document", fail_renderer)
    result = run_document_job(sample_request(), tmp_path, "idem-002")
    assert result.status == "failed"
    assert result.manifest_path is None
```

- [ ] **Step 2: Run the focused tests and verify missing job/QA/telemetry failures**

Run: `python -m pytest services/document-production-api/tests/test_jobs.py services/document-production-api/tests/test_qa.py services/document-production-api/tests/test_telemetry.py -q`

Expected: FAIL with import errors for the job modules.

- [ ] **Step 3: Implement content-hash provenance, golden-field QA, atomic manifest publication, idempotency, and local cost accounting**

QA must compare required fields and totals against the IR, validate artifact existence and hashes, reject page counts over 10, and return issue codes. A failed or cancelled run records a failure event but never a passing manifest. Keep raw customer values out of cost telemetry; hashes and field names are sufficient.

- [ ] **Step 4: Run focused tests plus a replay test with a fresh output root**

Run: `python -m pytest services/document-production-api/tests/test_jobs.py services/document-production-api/tests/test_qa.py services/document-production-api/tests/test_telemetry.py -q`

Expected: all tests pass, including no-overwrite behavior and `model_calls == 0`.

- [ ] **Step 5: Commit job execution and QA**

```bash
git add services/document-production-api
git commit -m "feat: add idempotent document jobs and QA evidence"
```

### Task 4: Expose a bounded local API and CLI smoke path

**Files:**
- Create: `services/document-production-api/document_production/main.py`
- Create: `services/document-production-api/document_production/cli.py`
- Create: `services/document-production-api/tests/test_api.py`
- Create: `services/document-production-api/tests/test_cli.py`
- Modify: `Taskfile.yml` (add a narrow `documents:test` owner task only if the existing task registry permits it)
- Modify: `services/document-production-api/README.md`

**Interfaces:**
- `POST /v1/documents/jobs` accepts a structured `DocumentRequest` plus `idempotency_key` and returns `JobResult` metadata; it does not accept arbitrary commands or a provider URL.
- `GET /health` returns service name, contract version, and `model_calls_supported: false` for V1A.
- CLI command: `python -m document_production.cli render --input examples/invoice.json --output <dir> --idempotency-key <key>`.

- [ ] **Step 1: Write failing API and CLI tests**

```python
def test_health_declares_zero_model_v1a(client):
    response = client.get("/health")
    assert response.json()["model_calls_supported"] is False

def test_post_job_returns_artifact_manifest(client):
    response = client.post("/v1/documents/jobs", json={"idempotency_key": "api-001", **sample_request()})
    assert response.status_code == 201
    assert response.json()["model_calls"] == 0

def test_cli_rejects_unknown_template(tmp_path):
    result = run_cli("render", "--input", "examples/unknown.json", "--output", str(tmp_path))
    assert result.returncode != 0
    assert "template" in result.stderr
```

- [ ] **Step 2: Run the focused tests and verify missing API/CLI failures**

Run: `python -m pytest services/document-production-api/tests/test_api.py services/document-production-api/tests/test_cli.py -q`

Expected: FAIL with import errors for `document_production.main` and `document_production.cli`.

- [ ] **Step 3: Implement the API, CLI, bounded request limits, and README usage examples**

Use local filesystem storage by default under `.runtime/` and never expose absolute private paths in API responses. Return structured 4xx errors for validation and idempotency conflicts; return 500 only for an unexpected internal failure while preserving safe error classes in logs.

- [ ] **Step 4: Run API/CLI tests and a manual local smoke command**

Run: `python -m pytest services/document-production-api/tests/test_api.py services/document-production-api/tests/test_cli.py -q`

Expected: all tests pass. Then run `python -m document_production.cli render --input services/document-production-api/examples/invoice.json --output .runtime/documents-smoke --idempotency-key smoke-001` and verify the CLI prints the job ID, both artifact names, QA status, and `model_calls=0`.

- [ ] **Step 5: Commit the bounded interface**

```bash
git add services/document-production-api Taskfile.yml
git commit -m "feat: expose documents v1a local job interface"
```

### Task 5: Add held-out fixtures, cost benchmark, and operator documentation

**Files:**
- Create: `services/document-production-api/examples/invoice.json`
- Create: `services/document-production-api/tests/fixtures/structured_cases.json`
- Create: `services/document-production-api/tests/test_acceptance.py`
- Create: `services/document-production-api/scripts/benchmark.py`
- Modify: `services/document-production-api/README.md`
- Modify: `docs/superpowers/specs/2026-09-22-logic-first-production-platform-design.md` (record measured V1A results only after the benchmark runs)

**Interfaces:**
- `scripts/benchmark.py` accepts `--fixture`, `--output`, and optional `--json` and reports accepted count, failed count, p50/p95 latency, bytes written, CPU time, model calls, and cost classification.
- Acceptance fixtures contain synthetic Thai and English structured cases and invalid boundary cases; no customer data.

- [ ] **Step 1: Write failing acceptance tests for the 20 structured and boundary cases**

```python
def test_structured_acceptance_cases_have_zero_model_calls():
    results = [run_document_job(case, tmp_path, f"case-{case['document_id']}") for case in structured_cases()]
    assert all(result.status == "accepted" for result in results)
    assert all(result.model_calls == 0 for result in results)

def test_boundary_cases_reject_without_artifacts():
    for case in invalid_cases():
        result = run_document_job(case, tmp_path, f"invalid-{case['document_id']}")
        assert result.status == "failed"
        assert result.manifest_path is None
```

- [ ] **Step 2: Run the acceptance tests before fixtures/benchmark exist and verify the expected missing-fixture failure**

Run: `python -m pytest services/document-production-api/tests/test_acceptance.py -q`

Expected: FAIL because the fixture loader and benchmark contract do not exist.

- [ ] **Step 3: Add synthetic fixtures, benchmark output, and operator runbook**

Keep held-out cases separate from cases used to adjust templates. Report cost as `unknown` for local runs if no approved machine rate card exists; never print `0` as though compute were free. Document how to add a future extraction/OCR capability without changing V1A and how to disable it by configuration.

- [ ] **Step 4: Run the full product test suite and benchmark**

Run: `python -m pytest services/document-production-api/tests packages/document-production-ir/tests -q`

Expected: all tests pass. Then run `python services/document-production-api/scripts/benchmark.py --fixture services/document-production-api/tests/fixtures/structured_cases.json --output .runtime/documents-benchmark --json` and retain only aggregate evidence in the spec; do not commit generated artifacts.

- [ ] **Step 5: Commit acceptance and documentation**

```bash
git add services/document-production-api docs/superpowers/specs/2026-09-22-logic-first-production-platform-design.md
git commit -m "test: benchmark documents v1a deterministic path"
```

## Completion Gate

Before calling V1A complete, run the product test suite, the narrow type check/lint available for the new service, `git diff --check`, and the repository's `task typecheck`. Run `task test` and `task docs:check` if the WSL Bash prerequisite is available; if it is unavailable, report the exact environment failure and do not claim those checks passed. Do not commit generated `.runtime/` outputs.

The implementation is ready for later V1B planning only when the acceptance suite demonstrates zero model calls for all structured cases, deterministic replay, no accepted manifest on failure, and measured artifact/QA evidence. V1A does not prove OCR, free-text extraction, or readiness for arbitrary document types.
