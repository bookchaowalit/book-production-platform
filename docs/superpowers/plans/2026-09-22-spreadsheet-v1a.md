# Spreadsheet V1A / Workbook Builder Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** เพิ่ม Workbook Builder ที่แปลง typed tables และ template parameters เป็น XLSX ที่แก้ไขต่อได้ มี Summary/Detail, สูตรที่คำนวณซ้ำได้ และตรวจ formula injection ก่อน export

**Architecture:** รวม `WorkbookIR`, typed cells, formula specs, lineage, evaluator, XLSX writer, reopen QA, idempotent jobs, API, CLI และ benchmark ไว้ใน repo กลาง `book-production-platform` ที่ `src/book_production_platform/spreadsheet_ir` และ `src/book_production_platform/spreadsheet_production`. เส้นทางมาตรฐานคือ typed input → normalized workbook graph → allowlisted formula evaluator → XLSX writer → XML/reopen QA; ไม่ใช้ LLM และไม่แปลงข้อความของผู้ใช้เป็นสูตรโดยอัตโนมัติ

**Tech Stack:** Python 3.12, Pydantic v2, XlsxWriter, FastAPI, pytest, Ruff, mypy strict, `zipfile`/XML สำหรับ reopen validation

**Spec:** `docs/superpowers/specs/2026-09-22-logic-first-production-platform-design.md` และ Section 7 ของ `docs/superpowers/plans/2026-09-22-production-platform-next-products.md`

**ตำแหน่งส่งมอบ (2026-09-24):** รวม Spreadsheet V1A เข้า repo กลาง `book-production-platform` ที่ใช้กับ Graphics และ 3D แล้ว โดย Solo Empire root เก็บ catalog และ `test:book-production-platform` adapter เท่านั้น; ย้าย source ออกจาก `packages/spreadsheet-production-ir` และ `services/spreadsheet-production-api` แล้ว ส่วนข้อมูลเดิมที่ยังอยู่ใน ignored `services/spreadsheet-production-api/.runtime/` ไม่ถูกย้ายหรือแก้ไข

## Global Constraints

- Input เป็น typed tables + template parameters; output เป็น editable XLSX ที่มี `Summary` และ `Detail`
- V1A ใช้ explicit column mapping, totals, formatting, formula validation และ export; ไม่มี LLM ในเส้นทางมาตรฐาน
- กำหนด `WorkbookIR` ให้มี sheets, typed cells, formula allowlist, named ranges และ source lineage
- ข้อความที่ขึ้นต้นด้วย `=`, `+`, `-`, `@` ต้องถูกเขียนเป็น text เสมอ และห้ามกลายเป็น formula จากการเดาชนิดข้อมูล
- สูตรที่อนุญาตใน V1A มีเพียง `SUM`, `COUNT`, `IF`; ต้องตรวจ broken reference และ cycle ก่อน export
- ต้องคำนวณผลลัพธ์ด้วย local deterministic engine ก่อนเขียนสูตรลง XLSX; การเขียนสูตรอย่างเดียวไม่ถือว่าคำนวณสำเร็จ
- ต้องทดสอบ null, date/locale, decimal, mixed types, formula injection, broken references และ reopen
- fixture benchmark ต้องมี 20 synthetic workbooks, totals/formulas ตรง golden และ `model_calls=0`
- ห้ามสร้าง repository ใหม่หลายชุด และห้ามแตะไฟล์งานที่มีการแก้ไขอยู่ก่อนแล้วนอกขอบเขต Spreadsheet

## Review Focus

- ค่า text เช่น `=SUM(A1:A2)` ต้องคงเป็น text literal ทั้งใน IR และ XLSX — test ใน Task 1 และ Task 2
- สูตรที่อ้าง sheet/cell ผิด, อ้าง external workbook หรือมีวงจรต้องถูก reject ก่อนสร้างไฟล์ — test ใน Task 1
- Decimal, null, date และ locale ต้องมีชนิดที่ชัดเจนและ totals ไม่เปลี่ยนจากการแปลงเป็น float โดยไม่ตั้งใจ — test ใน Task 1 และ Task 2
- ไฟล์ XLSX ที่เขียนแล้วต้องเปิดเป็น ZIP/XML ได้ มี Summary/Detail และมี cached formula values ที่ตรงกับ evaluator — test ใน Task 2
- การรันซ้ำด้วย idempotency key ต้องไม่เขียน workbook ซ้ำ และ input ใหม่ด้วย key เดิมต้อง conflict — test ใน Task 3

### Task 1: WorkbookIR, typed table normalization และ formula engine

**Files:**
- Create: `packages/spreadsheet-production-ir/pyproject.toml`
- Create: `packages/spreadsheet-production-ir/spreadsheet_ir/__init__.py`
- Create: `packages/spreadsheet-production-ir/spreadsheet_ir/models.py`
- Create: `packages/spreadsheet-production-ir/spreadsheet_ir/validation.py`
- Create: `packages/spreadsheet-production-ir/tests/conftest.py`
- Create: `packages/spreadsheet-production-ir/tests/test_models.py`
- Create: `packages/spreadsheet-production-ir/tests/test_validation.py`
- Create: `services/spreadsheet-production-api/pyproject.toml`
- Create: `services/spreadsheet-production-api/spreadsheet_production/__init__.py`
- Create: `services/spreadsheet-production-api/spreadsheet_production/formulas.py`
- Create: `services/spreadsheet-production-api/tests/conftest.py`
- Create: `services/spreadsheet-production-api/tests/test_formulas.py`

**Interfaces:**
- Produces `ColumnSpec`, `TableRequest`, `WorkbookRequest`, `CellIR`, `SheetIR`, `FormulaSpec`, `WorkbookIR`, `ValidationIssue`, `validate_request()` and `evaluate_workbook()` for Tasks 2–3

- [x] **Step 1: Write the failing tests** — assert explicit columns reject unknown/missing keys, decimals and ISO dates retain their types, formula-like text stays text, allowlist rejects unsupported functions and external references, broken references are actionable, and cycles are detected.
- [x] **Step 2: Run the focused tests and verify they fail**

  Run: `python -m pytest packages/spreadsheet-production-ir/tests services/spreadsheet-production-api/tests/test_formulas.py -q`

  Expected: FAIL because the IR models and evaluator do not exist.
- [x] **Step 3: Implement the minimal typed graph and evaluator** — use Pydantic v2 literals, `Decimal`, `date`, explicit column maps, a parser for `SUM`, `COUNT`, `IF`, dependency extraction, named ranges, and DFS cycle detection; never evaluate arbitrary Python or Excel expressions.
- [x] **Step 4: Run the focused tests and verify they pass**

  Run: `python -m pytest packages/spreadsheet-production-ir/tests services/spreadsheet-production-api/tests/test_formulas.py -q`

  Expected: PASS.
- [x] **Step 5: Commit**

  Run: `git add packages/spreadsheet-production-ir services/spreadsheet-production-api/pyproject.toml services/spreadsheet-production-api/spreadsheet_production/__init__.py services/spreadsheet-production-api/spreadsheet_production/formulas.py services/spreadsheet-production-api/tests/conftest.py services/spreadsheet-production-api/tests/test_formulas.py; git commit -m "feat: add workbook ir and formula engine"`

### Task 2: Summary/Detail compiler, XLSX writer and reopen QA

**Files:**
- Create: `services/spreadsheet-production-api/spreadsheet_production/recipe.py`
- Create: `services/spreadsheet-production-api/spreadsheet_production/render.py`
- Create: `services/spreadsheet-production-api/spreadsheet_production/qa.py`
- Create: `services/spreadsheet-production-api/tests/test_recipe.py`
- Create: `services/spreadsheet-production-api/tests/test_render.py`
- Create: `services/spreadsheet-production-api/tests/test_qa.py`

**Interfaces:**
- Consumes Task 1 models and evaluator
- Produces `build_workbook_ir(request) -> WorkbookIR`, `render_workbook(ir, output_path) -> list[ArtifactRef]`, and `qa_workbook(ir, output_path) -> tuple[str, ...]`

- [x] **Step 1: Write the failing tests** — cover Summary and Detail sheets, explicit totals, number/date/text formats, formula-like text written as strings, named ranges, cached formula values, invalid references, and reopening the generated ZIP/XML workbook.
- [x] **Step 2: Run the focused tests and verify they fail**

  Run: `python -m pytest services/spreadsheet-production-api/tests/test_recipe.py services/spreadsheet-production-api/tests/test_render.py services/spreadsheet-production-api/tests/test_qa.py -q`

  Expected: FAIL because the compiler, renderer and QA do not exist.
- [x] **Step 3: Implement the deterministic workbook path** — generate a `Detail` sheet from typed rows, a `Summary` sheet from allowlisted formulas, define named ranges, write values with XlsxWriter using `write_string` for untrusted text, write formula cached values from the evaluator, and validate ZIP/XML sheet names and formulas after closing.
- [x] **Step 4: Run the focused tests and verify they pass**

  Run: `python -m pytest services/spreadsheet-production-api/tests/test_recipe.py services/spreadsheet-production-api/tests/test_render.py services/spreadsheet-production-api/tests/test_qa.py -q`

  Expected: PASS.
- [x] **Step 5: Commit**

  Run: `git add services/spreadsheet-production-api/spreadsheet_production/recipe.py services/spreadsheet-production-api/spreadsheet_production/render.py services/spreadsheet-production-api/spreadsheet_production/qa.py services/spreadsheet-production-api/tests/test_recipe.py services/spreadsheet-production-api/tests/test_render.py services/spreadsheet-production-api/tests/test_qa.py; git commit -m "feat: export validated workbooks with reopen qa"`

### Task 3: Replay-safe workbook jobs, API and CLI

**Files:**
- Create: `services/spreadsheet-production-api/spreadsheet_production/jobs.py`
- Create: `services/spreadsheet-production-api/spreadsheet_production/main.py`
- Create: `services/spreadsheet-production-api/spreadsheet_production/cli.py`
- Create: `services/spreadsheet-production-api/spreadsheet_production/telemetry.py`
- Create: `services/spreadsheet-production-api/tests/test_jobs.py`
- Create: `services/spreadsheet-production-api/tests/test_api.py`
- Create: `services/spreadsheet-production-api/tests/test_cli.py`
- Create: `services/spreadsheet-production-api/tests/test_telemetry.py`
- Create: `services/spreadsheet-production-api/examples/workbook.json`

**Interfaces:**
- Consumes Task 1–2 interfaces
- Produces `run_workbook_job(request, output_dir, idempotency_key) -> JobResult`, `POST /v1/spreadsheets/workbooks`, CLI command `python -m spreadsheet_production.cli build`, and telemetry with `model_calls`, `replayed`, `bytes_written`

- [x] **Step 1: Write the failing tests** — assert a job writes XLSX plus manifest, replay returns the same artifact without extra bytes, changed input conflicts, API has bounded errors, and CLI prints a JSON result.
- [x] **Step 2: Run the focused tests and verify they fail**

  Run: `python -m pytest services/spreadsheet-production-api/tests/test_jobs.py services/spreadsheet-production-api/tests/test_api.py services/spreadsheet-production-api/tests/test_cli.py services/spreadsheet-production-api/tests/test_telemetry.py -q`

  Expected: FAIL because the job/API/CLI modules do not exist.
- [x] **Step 3: Implement the local job boundary** — hash canonical typed input plus recipe version, persist a manifest keyed by idempotency key, reuse verified artifacts on replay, and expose `model_calls=0` without writing prompts or private payloads.
- [x] **Step 4: Run the focused tests and verify they pass**

  Run: `python -m pytest services/spreadsheet-production-api/tests/test_jobs.py services/spreadsheet-production-api/tests/test_api.py services/spreadsheet-production-api/tests/test_cli.py services/spreadsheet-production-api/tests/test_telemetry.py -q`

  Expected: PASS.
- [x] **Step 5: Commit**

  Run: `git add services/spreadsheet-production-api/spreadsheet_production/jobs.py services/spreadsheet-production-api/spreadsheet_production/main.py services/spreadsheet-production-api/spreadsheet_production/cli.py services/spreadsheet-production-api/spreadsheet_production/telemetry.py services/spreadsheet-production-api/tests/test_jobs.py services/spreadsheet-production-api/tests/test_api.py services/spreadsheet-production-api/tests/test_cli.py services/spreadsheet-production-api/tests/test_telemetry.py services/spreadsheet-production-api/examples/workbook.json; git commit -m "feat: add replay-safe workbook jobs"`

### Task 4: 20-workbook benchmark, documentation and product boundary

**Files:**
- Create: `services/spreadsheet-production-api/spreadsheet_production/scripts/__init__.py`
- Create: `services/spreadsheet-production-api/spreadsheet_production/scripts/benchmark.py`
- Create: `services/spreadsheet-production-api/tests/fixtures/workbooks_20.json`
- Create: `services/spreadsheet-production-api/tests/test_benchmark.py`
- Create: `services/spreadsheet-production-api/tests/golden/README.md`
- Create: `services/spreadsheet-production-api/README.md`
- Modify: `services/README.md`
- Modify: `docs/superpowers/plans/2026-09-22-spreadsheet-v1a.md`

**Interfaces:**
- Consumes the public job/compiler/QA interfaces from Tasks 1–3
- Produces `run_benchmark(fixture_path, output_dir) -> dict[str, Any]` with totals, formula checks, reopen checks and `model_calls=0`

- [x] **Step 1: Write the failing benchmark test** — create 20 Thai/English synthetic workbooks with nulls, dates, decimals, mixed text and formula-like literals; assert golden totals, formula values, Summary/Detail presence, reopen QA and zero model calls.
- [x] **Step 2: Run the benchmark test and verify it fails**

  Run: `python -m pytest services/spreadsheet-production-api/tests/test_benchmark.py -q`

  Expected: FAIL until the fixture and benchmark runner are wired.
- [x] **Step 3: Add fixtures, README and golden review notes** — document formula allowlist, formula injection policy, local build command, benchmark command and future AI scope (semantic mapping/anomaly explanation only).
- [x] **Step 4: Run full product verification**

  Run: `python -m pytest packages/spreadsheet-production-ir/tests services/spreadsheet-production-api/tests -q`; `ruff check services/spreadsheet-production-api packages/spreadsheet-production-ir`; `mypy --strict services/spreadsheet-production-api/spreadsheet_production packages/spreadsheet-production-ir/spreadsheet_ir`; `python -m spreadsheet_production.scripts.benchmark --fixture tests/fixtures/workbooks_20.json --output-dir .runtime/spreadsheet-v1a-benchmark`

  Expected: PASS; benchmark reports 20 accepted workbooks, formula/reopen mismatches 0 and `model_calls=0`.
- [x] **Step 5: Commit**

  Run: `git add services/spreadsheet-production-api/spreadsheet_production/scripts services/spreadsheet-production-api/tests/fixtures/workbooks_20.json services/spreadsheet-production-api/tests/test_benchmark.py services/spreadsheet-production-api/tests/golden/README.md services/spreadsheet-production-api/README.md services/README.md docs/superpowers/plans/2026-09-22-spreadsheet-v1a.md; git commit -m "test: benchmark spreadsheet workbook builder"`

## Verification and delivery

Run the shared product suite through `task test:book-production-platform`, plus repository `task typecheck`, `task test`, and `task docs:check`. Keep unrelated working-tree changes untouched. No deployment target exists for this platform, so record `deployment_skipped_no_target` rather than inventing one.

## Consolidation verification — 2026-09-24

- [x] ใช้ repo กลางที่มีอยู่ โดยไม่สร้าง repo หรือ agent runtime แยกตาม product
- [x] เปิดคำสั่ง `book-production spreadsheet build` และ `book-production spreadsheet benchmark` ผ่าน platform CLI
- [x] รันชุดทดสอบรวม, Ruff และ strict mypy หลังย้าย
- [x] คง benchmark สังเคราะห์ 20 ไฟล์, formula allowlist, การตรวจ formula injection, reopen QA และ `model_calls=0`
- [x] ลงทะเบียน ownership ของ SpreadsheetIR และ Workbook Builder ใน root runtime-boundary registry

**หลักฐาน:** `book-production-platform` commit `ce6a87c`; 107 tests passed, 1 skipped เพราะ host ไม่อนุญาต symlink, Ruff และ strict mypy ผ่าน; benchmark ได้ 20 accepted, 0 rejected, 0 formula mismatches, 0 reopen failures และ 0 model calls.
