# Logic-first Production Platform — แผนงานต่อจาก Documents V1A

> **For agentic workers:** ใช้ superpowers:executing-plans เมื่อเริ่ม implementation ทีละ task; ทำใน checkout หลักบน `main` ตามคำสั่งผู้ใช้ และ commit เฉพาะขอบเขตงานนี้

**Goal:** ทำ Documents ให้มีหลักฐานคุณภาพและต้นทุนที่เชื่อถือได้ แล้วขยายเป็น Graphics และ Spreadsheet โดยใช้ AI เฉพาะขั้นตอนที่จำเป็น

**Architecture:** structured input → validated domain IR → deterministic engine → QA → artifact manifest เป็นเส้นทางหลัก โมเดลเป็น optional adapter สำหรับตีความข้อมูล; output ของโมเดลต้องผ่าน validation ก่อน execution แยก domain schemas และแชร์ infrastructure เฉพาะส่วนที่พิสูจน์แล้วว่าใช้ซ้ำได้

**Tech Stack:** ใช้ Python, Pydantic, FastAPI และ local renderers เดิมสำหรับ Documents; engine ของ domain ใหม่เลือกหลังตรวจ runtime compatibility โดยยังไม่เพิ่ม dependency ในรอบวางแผน

**Spec:** [Logic-first platform design](../specs/2026-09-22-logic-first-production-platform-design.md)

**สถานะ:** แผนลำดับส่งมอบและ acceptance gates; สถานะล่าสุดระบุแยกตาม product family การเลือก model/provider และ engine ใหม่ต้องมีผลทดลองก่อน pin version

## 1. Baseline และข้อจำกัด

- Documents V1A อยู่บน `main` แล้วที่ merge commit `04e609e0`: invoice recipe, IR, DOCX/PDF, API/CLI, QA และ local idempotency
- หลักฐานรอบ merge: Documents + tracked Video/EDL tests ผ่าน 64 tests; synthetic benchmark accepted 20/rejected 3, mismatch 0, model calls 0
- ตัวเลขนี้ไม่ยืนยัน layout ภาษาไทย, concurrent execution หรือ production readiness
- `jobs.py` ใช้ JSON index แบบ read-modify-write: atomic file replace ไม่เท่ากับ atomic transaction ของหลาย request
- `jobs.py` วัด `perf_counter()` แล้วส่งเป็น `cpu_seconds`: ต้องแยก wall time และ CPU time ก่อนใช้คิดต้นทุน
- benchmark เรียก key เดิมได้และรวม cost event จาก manifest เก่า: ต้องแยก fresh/replay เพื่อไม่รายงานต้นทุนซ้ำ
- `renderers.py` มี fallback เป็น Helvetica และแทนตัวอักษรที่ไม่รองรับ; ต้องป้องกันเอกสารไทยผ่าน QA ทั้งที่ข้อความสูญหาย
- การทดสอบรวมที่เคยติด Video untracked เป็นงานอีกขอบเขต ต้องตรวจสถานะล่าสุดก่อน execute และไม่แก้ตามรายงานเก่าโดยอัตโนมัติ

## 2. Global constraints

- ทำใน `main`; ตรวจ status ก่อน stage ทุกครั้งและไม่ใช้ `git add .`
- ไม่แก้ Video ในแผนนี้; root เป็น control plane/catalog และ product ต้องมี ownership/runtime boundary ชัดเจน
- structured jobs ต้องทำงานได้เมื่อไม่มี API key และมี `model_calls=0`
- paid steps ปิดเป็นค่าเริ่มต้น; ไม่มี rate card หรือ job/daily caps ต้องไม่ dispatch
- unknown cost เป็น `null/unknown`; local CPU ไม่ใช่ฟรี; ไม่อ้างว่าประหยัดกี่เปอร์เซ็นต์ก่อนมี benchmark เทียบกัน
- model output ไม่ใช่ executable code; ห้ามนำไปสร้าง shell command หรือเรียก tool โดยไม่ผ่าน allowlist
- V1B: input ≤ 8,000 tokens/call, output ≤ 2,000 tokens/call, รวม ≤ 2 calls/job, timeout ≤ 30s/call, deadline ≤ 120s/job
- private/restricted ไม่เข้า shared inference cache และต้องรักษาข้อจำกัด gateway เดิม
- ใช้ synthetic fixtures; telemetry ไม่เก็บ raw brief หรือข้อมูลเอกสาร
- ไม่เปิด paid provider, deploy production, ซื้อ GPU หรือสั่ง external side effects จากแผนนี้

## 3. ลำดับ product ที่ส่งมอบ

| ลำดับ | Product / รุ่น | ผู้ใช้ได้อะไร | Logic หลัก | AI ที่เปิดได้ | Gate ก่อนขยับต่อ |
|---|---|---|---|---|---|
| 1 | Documents V1A.1 | ใบแจ้งหนี้ที่ตรวจเนื้อหาและเรียกซ้ำได้ปลอดภัย | validation, render, ledger, QA | ไม่มี | concurrency/content/measurement ผ่าน |
| 2 | Documents V1B | brief ไทย/อังกฤษ → draft fields → DOCX/PDF | normalize, totals, render | bounded field extraction | critical fields ถูกหรือถามเพิ่ม |
| 3 | Graphics V1A / Template Batch | CSV/JSON → งานกราฟิกหลายชิ้น | template, SVG layout, batch | ไม่มี | ผ่าน 2026-09-24: 30/30 outputs, 0 calls, Thai visual QA |
| 4 | Spreadsheet V1A / Workbook Builder | ตารางข้อมูล → workbook สรุปพร้อมสูตร | typed cells, formulas, formatting | ไม่มี | ผ่าน 2026-09-24: 20/20 workbooks, reopen/formula mismatches 0, 0 calls |
| 5 | Documents V1C / Scan Import | สแกน → field proposals ที่ตรวจย้อนกลับได้ | embedded-text parsing ก่อน | OCR เฉพาะหน้าที่ต้องใช้ | ผ่าน synthetic gate: provenance/abstention, OCR เฉพาะหน้าที่ sparse, 0 model calls; runtime OCR จริงยังไม่ได้ติดตั้ง/ตรวจ |
| 6 | Data + Data Viz V1A | CSV → clean dataset → chart/report | schema, transforms, aggregation | optional column mapping | ผ่าน 2026-09-24: synthetic 20 เคส, 0 aggregate/reopen mismatch, axis/unit QA ผ่าน, 0 model calls |
| 7 | Marketing V1A / Campaign Package | brief + assets → ชุดแคมเปญพร้อมตรวจ | campaign rules, asset assembly | copy/brief เฉพาะส่วน | ผ่าน 2026-09-24: 20 accepted, 20 replay hits, 0 lineage failures/model calls; cost unknown |

ลำดับนี้เป็น dependency/reuse plan ไม่ใช่ข้อสรุป market demand หรือประมาณการรายได้ งานหนึ่งถือว่าจบเมื่อผ่าน gate ไม่ใช้จำนวนวันที่คาดเดาแทนหลักฐาน

## 4. งานแรก: Documents V1A.1

แต่ละ task ให้เพิ่ม regression test ที่แสดงปัญหา → แก้ขั้นต่ำ → ตรวจเฉพาะส่วน → commit แยก ก่อนเริ่มต้องอ่านไฟล์ล่าสุดเพราะมีงานอื่นทำใน checkout ร่วมกัน

### Task 1 — วัดต้นทุนและเวลาให้ถูกชนิด

**Files:** แก้ `services/document-production-api/document_production/jobs.py`, `telemetry.py`, `scripts/benchmark.py`; เพิ่มเคสใน `tests/test_telemetry.py`, `test_jobs.py`, `test_acceptance.py`

- [ ] เพิ่ม versioned cost-event fields `wall_seconds`, `cpu_seconds`, `attempt_id`, `replayed`; CPU ใช้ process CPU clock และบันทึกข้อจำกัดเมื่อมี concurrency
- [ ] แยก original job cost ออกจาก incremental replay cost; replay ไม่คิด bytes ของ artifact เดิมว่าเขียนใหม่
- [ ] benchmark fresh ใช้ directory ว่างและตรวจว่ามีการ render; replay เป็นอีก mode พร้อมแสดง hit count
- [ ] เพิ่ม fake-clock test ที่ wall=5s แต่ CPU=1s ต้องแสดงแยกกัน และ replay ต้องไม่เพิ่ม original cost ซ้ำ
- [ ] ไม่รวมตัวเลข process-wide CPU เป็น per-job cost เมื่อมีงานพร้อมกัน; ใช้ isolated worker หรือรายงาน unavailable พร้อมเหตุผล

**ผ่านเมื่อ:** fresh/replay metrics แยกกันและไม่มีการอ้าง CPU จาก wall clock; schema migration ไม่ทำให้ manifest เก่าอ่านผิดเงียบ ๆ

### Task 2 — Request ซ้ำและ crash recovery

**Files:** แก้ `jobs.py`; เพิ่ม `document_production/job_store.py`; เพิ่ม `tests/test_job_store.py` และ concurrency cases ใน `tests/test_jobs.py`

- [ ] ใช้ SQLite job ledger ภายใน output root ของ product พร้อม UNIQUE ของ scoped idempotency key และ transactional claim
- [ ] สถานะ `pending/running/accepted/failed` แยกจาก artifact publication; ไม่ถือ transaction ค้างตลอด render
- [ ] key เดิม/input เดิมคืน job เดิม; key เดิม/input ต่างคืน conflict; running job คืนสถานะกำลังทำ ไม่ render ซ้ำ
- [ ] pin recipe/schema/engine versions ใน job fingerprint; อ่าน legacy JSON index ผ่าน migration ที่ทดสอบแล้ว
- [ ] ทดสอบสอง process เรียก key เดียวกันและคนละ key; index ไม่สูญหายและมี accepted output ชุดเดียวต่อ key
- [ ] ทดสอบ crash หลังเขียนไฟล์ก่อน commit accepted, artifact สูญหายหลัง accepted และ stale running lease; กู้คืนโดยไม่อ้างไฟล์ที่ไม่มี

**ผ่านเมื่อ:** concurrent duplicate ไม่ผลิต accepted artifacts ซ้ำ และ crash ไม่ทำให้ผลที่ยังไม่ผ่าน QA ถูกเผยแพร่

### Task 3 — ความถูกต้องของ DOCX/PDF

**Files:** แก้ `renderers.py`, `qa.py`; เพิ่มเคสใน `tests/test_renderers.py`, `tests/test_qa.py` และ golden fixtures ใน `tests/fixtures/`

- [ ] escape ข้อความก่อนเข้า ReportLab Paragraph; ทดสอบ `<`, `>`, `&` และข้อความหน้าตาเหมือน markup ให้เป็นข้อความตาม input
- [ ] เมื่อ font ไม่รองรับอักษรต้องคืน error ที่แก้ไขได้; ไม่แทนภาษาไทยด้วย `?` แล้ว accepted
- [ ] ตรวจ actual PDF page count; เทียบข้อความสำคัญกับ source และตรวจ totals ในทั้ง DOCX/PDF
- [ ] ทดสอบ 0/1/100/101 รายการ, ข้อความยาว, ขึ้นหลายหน้า, อักษรไทยพร้อมสระ/วรรณยุกต์, font หาย และ max 10 pages
- [ ] เปิดตรวจ golden outputs ไทย/อังกฤษและภาพทุกหน้าของเคสหลายหน้า; บันทึก evidence แยกจาก unit tests

**ผ่านเมื่อ:** ไม่มีข้อความสำคัญหาย/ซ้อน/ล้นใน golden set และไม่อ้างว่า byte hash QA ยืนยัน layout ได้

### Task 4 — Contract และ local usability

**Files:** แก้ `main.py`, `cli.py`, `README.md`, `tests/test_api.py`, `tests/test_cli.py`

- [ ] map invalid input → 422, idempotency conflict → 409, running job → documented pending response, engine failure → server error
- [ ] CLI แสดง actionable error และ exit code สอดคล้อง API; ไม่แสดง private paths ใน API response
- [ ] เพิ่ม smoke command ที่รัน structured sample แบบไม่ต้องมี provider config
- [ ] ตรวจ runtime registry ก่อนเพิ่ม root Task adapter; product เป็นเจ้าของ test/build commands

**Verification:** `python -X utf8 -m pytest services/document-production-api/tests packages/document-production-ir/tests -q`; รัน Ruff และ mypy strict ของสองส่วนนี้ รวม `task docs:check` สำหรับเอกสาร และรายงาน infrastructure checks ที่รันไม่ได้ตามจริง

## 5. Documents V1B — ขอบเขต implementation ถัดจาก hardening

เพิ่มไฟล์ภายใต้ product เดิม: `extraction.py` สำหรับ typed adapter, `routing.py` สำหรับ recipe rules, `budget.py` สำหรับ reservation และ `tests/test_extraction.py`, `test_routing.py`, `test_budget.py`

- [ ] structured request ข้ามโมเดลทั้งหมด; brief → extraction result ที่แยก provided/inferred/missing fields
- [ ] missing required fields → `needs_input`; ไม่เดาราคา จำนวน สกุลเงิน หรือข้อมูลสำคัญ
- [ ] LLM อ่าน brief เป็น fields เท่านั้น; `recipe.py` คำนวณ totals และ validators เดิมเป็น gate
- [ ] fake adapter สำหรับ tests; real adapter ต้องผ่าน Central AI Gateway และเลือก model slot จาก benchmark
- [ ] atomic budget reservation ใช้ persisted ledger; รวม retry/repair/escalation อยู่ใน cap 2 calls เดียวกัน
- [ ] ไม่ใช้ confidence ที่โมเดลให้ตนเองเป็นเกณฑ์เดียว; validate field evidence และใช้ชุด held-out
- [ ] สร้างชุด 60 เคสตาม spec: structured 20, clear brief 20, ambiguous 10, invalid/adversarial 10; ไทย/อังกฤษอย่างละครึ่ง; development 40/held-out 20 แบบ stratified

**ผ่านเมื่อ:** critical fields ถูกหรือ abstain ทุก acceptance case, clear briefs accepted ≥90%, structured model calls=0 และ budget exhaustion ไม่เรียก provider เพิ่ม

เป้าหมายลด cost/accepted output ≥30% จาก LLM-every-step baseline เป็นเกณฑ์ทดลอง ไม่ใช่ผลที่เกิดแล้ว หากยังไม่ได้เรียก paid baseline ให้ระบุว่า unverified หรือ modeled estimate

## 6. Graphics V1A — product ที่สอง

**Product:** Template Batch; input เป็น CSV/JSON + brand tokens + local asset refs; output เป็น editable SVG และ preview PNG

**Capabilities:** fixed-template composition, text wrapping, bounded auto-fit, aspect-ratio variants, batch export, deterministic QA; ไม่มี image generation ใน V1A

- [x] ตรวจ owner/catalog และใช้ repo กลาง `book-production-platform` ร่วมกับ 3D
- [x] กำหนด `GraphicIR` และเส้นทาง renderer/QA แบบ local
- [x] รองรับ 3 templates และ 3 ขนาดตาม recipe
- [x] ทดสอบข้อความไทย, asset/font, SVG safety, replay และ invalid dimensions โดยไม่ fetch network
- [x] fixture 30 outputs ผ่าน พร้อม visual QA และ `model_calls=0`
- [x] เพิ่ม root task adapter ให้ชี้ไปยัง repo กลาง โดยไม่สร้าง agent infrastructure ซ้ำ

**AI รุ่นถัดไป:** brief extraction, background segmentation หรือสร้าง asset เป็นคนละ capability และคิด usage แยก ไม่เรียกโมเดลสร้างภาพเพื่อวาด layout ที่ template ทำได้

## 7. Spreadsheet V1A — product ที่สาม

**Product:** Workbook Builder; input เป็น typed tables + template parameters; output เป็น editable XLSX พร้อม Summary และ Detail sheets

**Capabilities:** column mapping แบบ explicit, totals, formatting, formula validation และ export; ไม่มี LLM ในเส้นทางมาตรฐาน

- [x] กำหนด `WorkbookIR`: sheets, typed cells, formula allowlist, named ranges และ source lineage
- [x] แยก untrusted text จาก formula; ค่าเริ่ม `=`, `+`, `-`, `@` ที่เป็นข้อความต้องไม่กลายเป็น formula
- [x] รองรับสูตรชุดแรก `SUM`, `COUNT`, `IF` เท่านั้น และตรวจ reference/cycle ก่อน export
- [x] คำนวณและตรวจค่าด้วย deterministic formula evaluator ก่อน export พร้อม cache ค่าไว้ใน XLSX
- [x] ทดสอบ null, date/locale, decimal, mixed types, formula injection, broken references และ workbook เปิดซ้ำ
- [x] 20 synthetic workbooks ผ่าน formula/reopen golden checks และ `model_calls=0`

**สถานะ 2026-09-24:** ย้าย IR และ Workbook Builder เข้า repo กลาง `book-production-platform`; CLI ใช้ `book-production spreadsheet build|benchmark`. Root คง catalog และ test adapter เท่านั้น.

**AI รุ่นถัดไป:** semantic column mapping และคำอธิบาย anomaly; ไม่ให้ LLM คำนวณผลลัพธ์แทน formula engine

## 8. Product backlog หลังพิสูจน์สาม domain

| Family | Product แรกที่แคบพอส่งมอบ | ถัดไป | Gate เฉพาะโดเมน |
|---|---|---|---|
| Data | CSV Cleaner → SQL/Pipeline Builder | Source execution only after a separate design gate | schema, lineage, reproducible transforms |
| Data Viz | Chart Builder | Report Dashboard | aggregation/axis/units ถูก |
| Web | Template Landing Builder | Structured Site Builder | build, responsive, accessibility; local V1A implemented |
| Workflow | Form-to-Document Flow | API Workflow Builder | retries, permissions, idempotency; local V1A implemented |
| Marketing | Campaign Package | Experiment Planner / Ads Operations | budget/authorization และ measurement |
| Audio | Loudness/Trim Batch | Transcribe / Cleanup | clipping, loudness, intelligibility |
| 3D | [Product Scene Composer — design review](../specs/2026-09-23-3d-product-scene-composer-design.md) | Materials / Rendering Batch | geometry, units, render evidence |
| Game | Scene Prototype Builder | Dialogue / Level Tools | executable scene และ gameplay tests |
| CAD | [Parametric Part Generator V1A](2026-09-25-cad-parametric-part-generator-v1a.md) — local pilot passed | Drawing / Assembly Check | dimensions, constraints, geometry validity; V1A is not engineering certification |
| BIM | Quantity Takeoff | Clash/Rule Checking | IFC validity และ quantity evidence |
| GIS | Layer Cleaner / Map Export | Spatial Analysis | CRS, geometry, spatial correctness |
| Electronics | Netlist Validator | SPICE / BOM Builder | ERC, simulation, component evidence |
| Software | Scoped Patch + Tests | AST Refactor | build/tests และ review |
| Infrastructure | Config Plan + Policy Check | Sandbox Provisioning | plan diff, policy, rollback |
| Robotics | Trajectory Simulation | Perception Adapter | simulation constraints; ยังไม่สั่ง robot จริง |
| Manufacturing | Job/Toolpath Validation | CAM Preparation | machine profile และ simulation; ยังไม่สั่งเครื่องจริง |

Video อยู่ในงานแยกเดิม; Audio และผลิตภัณฑ์อื่นเชื่อมผ่าน versioned artifact/adapter contract โดยไม่ย้าย ownership

## 9. Delivery และข้อสรุปสำหรับผู้ลงมือ

- Graphics V1A, Spreadsheet V1A, Documents V1C, Data + Data Viz V1A, Marketing V1A และ Data SQL/Pipeline Builder V1A ผ่าน synthetic gates. Data SQL/Pipeline Builder อยู่ใน [`book-production-platform`](../../..) ตาม [design](../specs/2026-09-24-data-sql-pipeline-builder-design.md) และ [implementation plan](2026-09-24-data-sql-pipeline-builder-v1a.md); รอบนี้ได้ 20/20 expected outcomes, 12 replay hits, 0 query-plan/lineage failures และ 0 model calls. ตัวสร้าง SQL ยังคง plan-only และไม่อ่านหรือรัน query บนฐานข้อมูลจริง. Product family ถัดไปให้เลือกจาก CAD/BIM/Electronics/Robotics/Manufacturing ผ่าน design gate เดียวกันก่อนลงมือ
- เดินหน้าทีละ gate และต่อยอด platform เดิม โดยไม่ทำซ้ำ planner, memory หรือ model gateway ต่อ domain
- platform shared core เป็นผลจาก reuse ที่พิสูจน์ในสอง domain; shared envelope ไม่บังคับให้ใช้ universal domain schema
- commit เฉพาะไฟล์ของงาน; ก่อน push ต้องตรวจ upstream และ outgoing commits ทั้งชุด ถ้ามีงานอื่นปน ห้ามอ้างว่า push เฉพาะ plan ได้
- แผนนี้ไม่มี deployment target: `deployment_skipped_no_target`
- รายงานแต่ละ release: accepted/rejected/needs_input, model calls, fresh/replay latency, resource usage, cost known/unknown และ golden QA evidence

## 10. Marketing V1A pilot evidence — 2026-09-22

Marketing V1A is implemented under `packages/marketing-production-ir` and
`services/marketing-production-api`. The first slice accepts structured
campaign requests, validates channel/asset rules, records source hashes, and
writes review-only package/manifest artifacts. It has no publish, audience
sync, ad API, or spend capability and reports `model_calls=0` on its standard
path. The 20-case synthetic benchmark passed on 2026-09-24 with 20 accepted,
20 replay hits, 0 lineage failures, and 0 model calls. Local compute cost remains
unknown without an approved rate card; there is no deployment target.

## 11. Web V1A pilot evidence — 2026-09-22

Web V1A is implemented under `packages/web-production-ir` and
`services/web-production-api`. It accepts a bounded page spec and emits a
fixed landing template with escaped HTML, local CSS, viewport metadata,
semantic landmarks, responsive layout, and keyboard focus styling. The build
path has no raw HTML/JavaScript, external asset fetch, deployment action, or
model call. A 20-page synthetic benchmark and replay QA are included in the
service suite.

## 12. Workflow V1A pilot evidence — 2026-09-22

Workflow V1A is implemented under `packages/workflow-production-ir` and
`services/workflow-production-api`. It accepts a typed form plus a bounded DAG,
rejects cycles, dangling dependencies, missing values, unauthorized review
roles, and retry caps above three attempts, then emits a topological plan and
escaped Markdown document. The path is plan-only: it has no external API,
notification, publishing, or model call capability. The 20-workflow synthetic
benchmark and replay/tamper checks are part of the service suite; measured
results for this checkout are 20 accepted, 0 rejected, 0 model calls, 20
replay hits, and 0 QA failures.

## 13. 3D Product Scene Composer — local V1A implementation (2026-09-23)

The approved V1A design is available in
[the 3D Product Scene Composer spec](../specs/2026-09-23-3d-product-scene-composer-design.md).
It defines and now implements a local, deterministic Blender Scene Composer
with editable `.blend`, PNG render, replay protection, pilot resource caps and
QA evidence. The final synthetic acceptance run passed 20/20 expected cases
(5 accepted, 5 replayed, 10 rejected), with 0 unexpected cases and 0 model
calls. Five golden renders received visual review. EEVEE is selected from the
local benchmark; monetary cost remains unknown without a rate card.

The product implementation is committed locally on `main` in the standalone
`book-production-platform` repository. Its Git remote is not configured, so
there is no verified external GitHub URL or published release. This is a local
synthetic pilot, not evidence of representative customer-asset coverage or an
upload-ready hostile-file sandbox.

The implementation plan is
[3D Product Scene Composer V1A](2026-09-23-3d-product-scene-composer-v1a.md).

## 14. Documents V1C Scan Import — synthetic gate evidence (2026-09-24)

The local service now accepts a versioned `ScanImportRequest` and produces
traceable `ScanImportResult` proposals from PDF text. It uses the Documents V1B
rule parser before any optional OCR adapter, stores bounded page excerpts with
each proposal, marks duplicate/conflicting labels ambiguous, and abstains when
required fields or OCR evidence are unavailable. It never creates a rendered
invoice from scan proposals. The standard path reports `model_calls=0` and
local compute cost as unknown.

Synthetic checks cover an embedded-text invoice with complete field provenance,
OCR being called only for the sparse page through an injected fake adapter,
unavailable OCR abstention, conflicting and duplicate fields, malformed and
path-escaping PDFs, the 50-page cap, and the ten-page OCR budget. The CLI writes
a proposal/QA manifest and returns a separate exit code when more input is
needed. Tests, Ruff, and strict mypy pass for the Documents service and IR
packages.

The host has no Poppler + Tesseract OCR pair, so OCR image quality, Thai OCR
accuracy, and process execution on a real scanned PDF remain unverified. On
this host, image-only pages are safely marked `needs_input`; install and validate
the optional local runtime before promising OCR completion to users.

## 15. Data + Data Viz V1A axis follow-up — synthetic gate evidence (2026-09-24)

The chart IR now accepts optional axis labels and an explicit value unit. Labels
default deterministically from the typed dataset schema (`Count` for count
charts). SVG output includes five y-axis ticks, axis labels, and the supplied
unit; the JSON report and reopen QA verify the same metadata. This extends the
original CSV Cleaner → Chart Builder without adding model calls.

The 20-case synthetic benchmark passed with 17 accepted and 3 expected rejects,
0 aggregation mismatches, 0 reopen failures, and 0 model calls. Targeted tests
cover positive/negative/zero tick values, escaped labels, unit display, report
metadata, tamper detection, and replay across the chart recipe version update.
Local compute cost remains unknown without an approved rate card; there is no
deployment target.

## 16. Data SQL/Pipeline Builder V1A — synthetic gate evidence (2026-09-25)

The central `book-production-platform` repository now contains the versioned
PipelineIR, read-only parameterized SQLite compiler, content-addressed private
plan artifacts, local API/CLI, and synthetic benchmark. The benchmark passed
20 cases: 12 accepted, 8 expected rejections, 0 unexpected outcomes, 0 query-
plan failures, 0 lineage failures, 12 exact replay hits, 0 model calls, and
32,566 artifact bytes written. The fixture covers every allowlisted operation,
typed filters, union lineage, graph/schema/type rejection, and SQL-shaped filter
values. Values remain in the private `parameters.json` artifact and do not
appear in SQL, reports, manifests, API/CLI responses, or benchmark summaries.

The compiler does not connect to or execute against a source database. Its
query-plan check uses an in-memory SQLite schema stub and `EXPLAIN QUERY PLAN`;
published jobs are plans for a separately authorized consumer. The local API
launch instructions bind only to `127.0.0.1`; no remote or hosted configuration
is documented. Monetary cost remains unknown without a rate card. The nested
product repository has no configured remote and no deployment target exists,
so handoff status is `deployment_skipped_no_target` with push unavailable.

## 17. CAD Parametric Part Generator V1A — local pilot evidence (2026-09-25)

The [approved design](../specs/2026-09-25-cad-parametric-part-generator-design.md)
and [implementation plan](2026-09-25-cad-parametric-part-generator-v1a.md) are
implemented in the existing
[`book-production-platform` checkout](../../..)
on `main` at commit `d0d9ac9ddfea030efeb6f141f0e673a2f2d4db9d`. The pilot accepts a
bounded `PartIR` mounting-plate request and produces an editable FreeCAD
document, STEP, STL, deterministic preview, and integrity-checked QA manifest.
The 20-case acceptance benchmark matched every expected result: five fresh
builds, five replay hits, eight input rejections, two tamper rejections, zero QA
failures, and zero model calls. Five representative native documents were
reopened in FreeCAD GUI; each retained the editable feature history and a valid
single solid, and its preview and viewport were visually reviewed.

This is a local Windows x86-64 pilot pinned to FreeCAD 1.1.3, not a hosted
service or a production-ready engineering workflow. The geometry checks do not
certify load, tolerance, safety, regulatory, or manufacturing suitability.
The nested checkout has no configured remote or upstream and no deployment
target; its result is local-only, with no push or deployment claimed. See the
[CAD pilot evaluation](../reviews/2026-09-25-cad-parametric-part-generator-v1a-evaluation.md)
for exact runtime identity, timings, byte totals, verification scope, and
remaining limits.

## 18. BIM Quantity Takeoff V1A — local synthetic gate evidence (2026-09-26)

The [implementation plan](2026-09-25-bim-quantity-takeoff-v1a.md) is implemented
in the existing
[`book-production-platform` checkout](../../..)
on `main`, continuing from preflight commit `3d1efc9` through `e0ea113`
(`7946f01`, `f14f53f`, `822cf43`, `9b9fa5c`, `2be6213`, `6034f1f`, `9662758`,
`e0ea113`). The pinned worker preflight ran for real through FreeCADCmd
(FreeCAD 1.1.3, Python 3.11, IfcOpenShell 0.8.4, `gui_loaded=false`, IFC2X3 and
IFC4 schemas, malformed SPF rejected, findings 0). The synthetic acceptance gate
passed 20/20 benchmark cases (5 fresh, 5 replay, 8 input rejections, 2 tamper
rejections) with `model_calls_total=0` and `monetary_cost=unknown`, and a CLI
takeoff publish followed by replay produced exactly `takeoff.json`,
`takeoff.csv`, and `qa-manifest.json` under the ignored `.runtime/bim/` area
with integrity-checked artifacts and no local paths in outputs. The full
repository check suite passed after the change: 585 tests passed with 4
pre-existing Windows symlink skips, Ruff clean on all configured targets, and
strict mypy clean (re-run by the parent session, 144.88s). Source IFC fixtures
were hash-verified unmodified.

The nested checkout still has no configured remote or upstream and no
deployment target; its result is local-only, with no push or deployment claimed
(`push_skipped_no_remote`). Remaining limits: the worker bounds takeoff output
at 1 MiB / 4000 records (larger models fail closed with `ifc_input_too_large`),
schema fingerprinting reads only the first 64 KiB of the SPF header, and the
benchmark harness requires the test-side fixture macro path.
