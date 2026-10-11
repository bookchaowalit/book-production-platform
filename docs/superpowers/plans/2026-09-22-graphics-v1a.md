# Graphics V1A / Template Batch Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** เพิ่ม product boundary สำหรับ Template Batch ที่แปลง CSV/JSON และ brand tokens เป็น editable SVG กับ PNG preview แบบ deterministic โดยไม่เรียก LLM สำหรับ layout

**Architecture:** Product code อยู่ใน owner repository [`book-production-platform`](../../../README.md) ภายใต้ `src/book_production_platform/graphics_ir` และ `src/book_production_platform/graphics_production`. Solo Empire เก็บเฉพาะ task adapter และ ownership registry. เส้นทางหลักคือ input ที่ validate แล้ว → GraphicIR → local renderer → artifact manifest; จำกัด asset เป็น local references และไม่ fetch network. ใช้ platform/agent infrastructure ชุดเดียวกับ domain อื่น.

**Tech Stack:** Python 3.12, Pydantic v2, FastAPI, Pillow สำหรับ font validation, headless Edge/Chrome สำหรับ SVG rasterization และ Thai shaping, pytest, Ruff, mypy strict

**Spec:** `docs/superpowers/specs/2026-09-22-logic-first-production-platform-design.md` และ Section 6 ของ `docs/superpowers/plans/2026-09-22-production-platform-next-products.md`

**Current ownership:** รายการไฟล์ในขั้นตอนด้านล่างบันทึกประวัติ prototype ที่ root checkout; implementation ปัจจุบันรวมอยู่ใน `book-production-platform` ตาม runtime ownership registry.

## Global Constraints

- Input เป็น CSV/JSON + brand tokens + local asset refs; output เป็น editable SVG และ preview PNG
- V1A ใช้ fixed-template composition, text wrapping, bounded auto-fit, aspect-ratio variants, batch export และ deterministic QA
- ไม่มี image generation หรือ LLM ในเส้นทาง layout มาตรฐาน; benchmark ทุกงานต้องรายงาน `model_calls=0`
- รองรับ template ชุดแรก `quote_card`, `product_card`, `announcement` และขนาดที่ recipe ระบุชัด
- ข้อความไทยยาว, asset/font หาย, unsafe SVG/external refs และ invalid dimensions ต้องได้ผลลัพธ์ที่ตรวจสอบได้; ห้าม fetch network จาก input
- ต้องมี fixture batch 30 outputs ตรวจข้อความครบ dimension ถูก และ visual QA golden set
- ใช้ platform pattern ร่วมกันโดยไม่ทำซ้ำ planner, model gateway หรือ agent runtime; domain schemas, tools, tests และ build อยู่ใน owner platform repository ส่วน Solo Empire มี orchestration adapter/catalog
- ห้ามแตะไฟล์ที่มีการแก้ไขอยู่ก่อนแล้วนอกขอบเขต Graphics และห้าม stage ไฟล์เหล่านั้น

## Review Focus

- ข้อความไทยยาวต้อง wrap/autofit จนครบหรือคืน validation error ที่ระบุสาเหตุ — test ใน Task 2
- path ของ asset และ font ที่เป็น URL, absolute path นอก root หรือ SVG ที่มี external reference ต้องถูก reject — test ใน Task 1 และ Task 2
- dimensions ติดลบ/ศูนย์หรือ aspect ratio ที่ไม่อยู่ใน recipe ต้อง reject ก่อนเขียน artifact — test ใน Task 1
- output ที่เล่นซ้ำด้วย idempotency key ต้องไม่สร้างผลลัพธ์ที่แตกต่าง และต้องแยก fresh/replay counters — test ใน Task 3
- PNG preview ที่สร้างจาก SVG ต้องมีขนาดตรงกับ canvas และ batch 30 งานต้องคง `model_calls=0` — test ใน Task 3 และ Task 4

### Task 1: GraphicIR, input validation และ safe asset policy

**Files:**
- Create: `packages/graphics-production-ir/pyproject.toml`
- Create: `packages/graphics-production-ir/graphics_ir/__init__.py`
- Create: `packages/graphics-production-ir/graphics_ir/models.py`
- Create: `packages/graphics-production-ir/graphics_ir/validation.py`
- Create: `packages/graphics-production-ir/tests/test_models.py`
- Create: `packages/graphics-production-ir/tests/test_validation.py`
- Create: `services/graphics-production-api/pyproject.toml`
- Create: `services/graphics-production-api/graphics_production/__init__.py`
- Create: `services/graphics-production-api/graphics_production/assets.py`
- Create: `services/graphics-production-api/tests/conftest.py`
- Create: `services/graphics-production-api/tests/test_assets.py`

**Interfaces:**
- Produces `GraphicRequest`, `GraphicIR`, `CanvasSpec`, `BrandTokens`, `TextNode`, `ShapeNode`, `ImageNode`, `TemplateId`, `validate_request()` และ `validate_local_reference()` ที่ tasks ถัดไปใช้

- [x] **Step 1: Write the failing tests** — assert `GraphicRequest` accepts only the three template IDs and recipe sizes, rejects zero/negative dimensions, retains Thai text, and rejects `https://`, `data:`, traversal and out-of-root asset/font references.
- [x] **Step 2: Run the focused tests and verify they fail**

  Run: `python -m pytest packages/graphics-production-ir/tests services/graphics-production-api/tests/test_assets.py -q`

  Expected: FAIL because the package and validation functions do not exist.
- [x] **Step 3: Implement the minimal models and validators** — use Pydantic v2 literals/enums, `schema_version="graphic-request.v1"`, explicit node unions, bounded text policies, and `Path.resolve()` containment checks; never access the network.
- [x] **Step 4: Run the focused tests and verify they pass**

  Run: `python -m pytest packages/graphics-production-ir/tests services/graphics-production-api/tests/test_assets.py -q`

  Expected: PASS.
- [x] **Step 5: Commit**

  Run: `git add packages/graphics-production-ir services/graphics-production-api/pyproject.toml services/graphics-production-api/graphics_production/__init__.py services/graphics-production-api/graphics_production/assets.py services/graphics-production-api/tests/conftest.py services/graphics-production-api/tests/test_assets.py; git commit -m "feat: add graphics ir and safe input contract"`

### Task 2: Deterministic templates and SVG/PNG renderers

**Files:**
- Create: `services/graphics-production-api/graphics_production/layout.py`
- Create: `services/graphics-production-api/graphics_production/templates.py`
- Create: `services/graphics-production-api/graphics_production/render.py`
- Create: `services/graphics-production-api/graphics_production/qa.py`
- Create: `services/graphics-production-api/tests/test_templates.py`
- Create: `services/graphics-production-api/tests/test_render.py`
- Create: `services/graphics-production-api/tests/test_qa.py`

**Interfaces:**
- Consumes `GraphicRequest` and `GraphicIR` from Task 1
- Produces `compile_template(request) -> GraphicIR`, `render_svg(ir) -> str`, `render_png(ir) -> bytes`, and `run_graphic_qa(svg, png, ir) -> tuple[str, ...]`

- [x] **Step 1: Write the failing tests** — cover all three templates, three recipe sizes, long Thai wrapping, bounded font reduction, XML escaping, asset image nodes, SVG dimensions, PNG dimensions and no external refs.
- [x] **Step 2: Run the focused tests and verify they fail**

  Run: `python -m pytest services/graphics-production-api/tests/test_templates.py services/graphics-production-api/tests/test_render.py services/graphics-production-api/tests/test_qa.py -q`

  Expected: FAIL because compiler and renderers do not exist.
- [x] **Step 3: Implement deterministic layout and rendering** — use fixed template recipes (square 1080×1080, portrait 1080×1350, story 1080×1920), character-width estimation with a minimum font floor, explicit `overflow_policy`, XML escaping, local image embedding, and a local headless browser for Thai-shaped raster previews without model calls.
- [x] **Step 4: Run the focused tests and verify they pass**

  Run: `python -m pytest services/graphics-production-api/tests/test_templates.py services/graphics-production-api/tests/test_render.py services/graphics-production-api/tests/test_qa.py -q`

  Expected: PASS.
- [x] **Step 5: Commit**

  Run: `git add services/graphics-production-api/graphics_production/layout.py services/graphics-production-api/graphics_production/templates.py services/graphics-production-api/graphics_production/render.py services/graphics-production-api/graphics_production/qa.py services/graphics-production-api/tests/test_templates.py services/graphics-production-api/tests/test_render.py services/graphics-production-api/tests/test_qa.py; git commit -m "feat: render graphics templates deterministically"`

### Task 3: Batch job, API/CLI and replay-safe artifact contract

**Files:**
- Create: `services/graphics-production-api/graphics_production/jobs.py`
- Create: `services/graphics-production-api/graphics_production/main.py`
- Create: `services/graphics-production-api/graphics_production/cli.py`
- Create: `services/graphics-production-api/graphics_production/telemetry.py`
- Create: `services/graphics-production-api/graphics_production/scripts/benchmark.py`
- Create: `services/graphics-production-api/examples/batch.json`
- Create: `services/graphics-production-api/tests/test_jobs.py`
- Create: `services/graphics-production-api/tests/test_api.py`
- Create: `services/graphics-production-api/tests/test_cli.py`
- Create: `services/graphics-production-api/tests/test_telemetry.py`

**Interfaces:**
- Consumes Task 1–2 interfaces
- Produces `run_batch(items, output_dir, idempotency_key) -> BatchResult`, `POST /v1/graphics/batches`, CLI command `python -m graphics_production.cli batch`, and telemetry fields `model_calls`, `fresh_count`, `replay_count`, `bytes_written`

- [x] **Step 1: Write the failing tests** — assert batch writes SVG/PNG/manifest, repeats return the same manifest without duplicate writes, malformed items are rejected per item, API returns a bounded error, and CLI prints JSON summary.
- [x] **Step 2: Run the focused tests and verify they fail**

  Run: `python -m pytest services/graphics-production-api/tests/test_jobs.py services/graphics-production-api/tests/test_api.py services/graphics-production-api/tests/test_cli.py services/graphics-production-api/tests/test_telemetry.py -q`

  Expected: FAIL because job, API and CLI modules do not exist.
- [x] **Step 3: Implement the job boundary** — hash canonical input plus recipe version, store a local manifest keyed by idempotency key, reuse existing artifacts on replay, record `model_calls=0`, and expose only local filesystem writes.
- [x] **Step 4: Run the focused tests and verify they pass**

  Run: `python -m pytest services/graphics-production-api/tests/test_jobs.py services/graphics-production-api/tests/test_api.py services/graphics-production-api/tests/test_cli.py services/graphics-production-api/tests/test_telemetry.py -q`

  Expected: PASS.
- [x] **Step 5: Commit**

  Run: `git add services/graphics-production-api/graphics_production/jobs.py services/graphics-production-api/graphics_production/main.py services/graphics-production-api/graphics_production/cli.py services/graphics-production-api/graphics_production/telemetry.py services/graphics-production-api/graphics_production/scripts/benchmark.py services/graphics-production-api/examples/batch.json services/graphics-production-api/tests/test_jobs.py services/graphics-production-api/tests/test_api.py services/graphics-production-api/tests/test_cli.py services/graphics-production-api/tests/test_telemetry.py; git commit -m "feat: add replay-safe graphics batch jobs"`

### Task 4: 30-output benchmark, golden QA and product documentation

**Files:**
- Create: `services/graphics-production-api/tests/fixtures/batch_30.json`
- Create: `services/graphics-production-api/tests/test_benchmark.py`
- Create: `services/graphics-production-api/tests/golden/README.md`
- Modify: `README.md`
- Modify: `docs/superpowers/plans/2026-09-22-graphics-v1a.md`

**Interfaces:**
- Consumes the public job/QA interfaces from Tasks 1–3
- Produces a repeatable benchmark JSON summary and a documented golden-set review procedure

- [x] **Step 1: Write the failing benchmark test** — create 30 synthetic Thai/English items across the three templates and assert all accepted outputs contain expected text, have recipe dimensions, produce PNG previews, and report `model_calls=0`.
- [x] **Step 2: Run the benchmark test and verify it fails**

  Run: `python -m pytest services/graphics-production-api/tests/test_benchmark.py -q`

  Expected: FAIL until the fixture and benchmark runner are wired.
- [x] **Step 3: Add fixtures, golden-set notes and README usage** — record schema/template/renderer versions, commands for local batch execution and manual preview inspection, and explicitly state that AI additions (brief extraction, segmentation, new assets) are separate capabilities.
- [x] **Step 4: Run the full product verification**

  Run: `python -m pytest services/graphics-production-api/tests packages/graphics-production-ir/tests -q`; `ruff check services/graphics-production-api packages/graphics-production-ir`; `mypy --strict services/graphics-production-api/graphics_production packages/graphics-production-ir/graphics_ir`; `python -m graphics_production.scripts.benchmark --fixture services/graphics-production-api/tests/fixtures/batch_30.json --output-dir artifacts/graphics-v1a-benchmark`

  Expected: PASS; benchmark summary reports 30 accepted outputs, zero rejected items, zero model calls, and no unsafe SVG references.
- [x] **Step 5: Commit**

  Run: `git add services/graphics-production-api/tests/fixtures/batch_30.json services/graphics-production-api/tests/test_benchmark.py services/graphics-production-api/tests/golden/README.md services/graphics-production-api/README.md docs/superpowers/plans/2026-09-22-graphics-v1a.md; git commit -m "test: benchmark graphics template batch"`

## Verification and delivery

Graphics passed the shared-repository gate: 82 tests passed, 1 host-limited symlink test skipped, focused Ruff and strict mypy passed for 15 Graphics source files. The 30-output synthetic benchmark had 30 accepted, 0 rejected, 0 model calls, and 0 unsafe SVG issues. Visual review confirmed Thai shaping and template positions. The implementation is committed on `main` as `6ee48e4` in `book-production-platform`. No deployment target exists for this platform.
