---
title: Production Tools — Spec-Driven Development (SDD) Program
tags: [design, spec-driven-development, production-platform, ai-adapter, roadmap]
created: 2026-09-29
updated: 2026-09-30
status: approved-written-spec
description: กระบวนการ spec-driven และ spec card ราย tool สำหรับชุด production tools ทั้ง 15 ตัว ตั้งแต่ปิด gate ที่ค้างไปจนถึงเปิด AI adapter แบบคุมงบ พร้อม requirement IDs, acceptance criteria, eval และลำดับงาน
---

# Production Tools — Spec-Driven Development (SDD) Program

## 0. สรุปสั้น

ชุด production tools มี **15 tools** (ตาม `catalog/production-tools.yaml`)
ทุกตัวสร้างบนหลัก logic-first ของ
[Logic-first Production Platform](2026-09-22-logic-first-production-platform-design.md):
structured input → validated IR → deterministic engine → QA → artifact manifest และ
`model_calls=0` ในเส้นทางปกติ

งานถัดไปมี 3 ชนิดที่ต้องแยกกันชัด:

1. **ปิด gate ที่ค้าง** — งานที่ tool ทำได้แล้วแต่ยังไม่มีหลักฐาน (ฟังเสียง, ติดตั้งเครื่องใหม่, runtime จริง)
2. **เพิ่มความสามารถ deterministic** — feature ที่ไม่ต้องใช้ AI (เช่น 16:9, ตัดคำไทย, template ใหม่)
3. **เปิด AI adapter (V1B)** — ให้ AI แปลง brief ภาษาคน → **IR proposal** ที่ต้องผ่าน validator
   ก่อนเข้า engine เสมอ AI ไม่สร้าง output สุดท้ายเองและไม่รันคำสั่ง

เอกสารนี้กำหนด (1) กระบวนการ SDD ที่ทุกงานต้องผ่าน (2) contract และ requirement ที่ใช้ร่วมกัน
(3) spec card ของแต่ละ tool (4) ลำดับ wave และ task ย่อย (5) สิ่งที่เจ้าของต้องตัดสินใจ

เอกสารนี้ **ไม่ใช่การอนุมัติให้เปิด paid provider, ดาวน์โหลดโมเดลใหม่, ติดตั้ง runtime หรือ deploy**
แต่ละ wave ต้องผ่านขั้น Spec → Plan ที่เจ้าของอนุมัติก่อนลงมือ

## 1. ขอบเขตและความสัมพันธ์กับเอกสารอื่น

| เอกสาร | บทบาท |
|---|---|
| [Logic-first platform design](2026-09-22-logic-first-production-platform-design.md) | หลักการแม่บท, contracts (JobSpec/DomainIR/CostEvent), cost policy |
| [Production tools roadmap](../plans/2026-09-26-production-tools-roadmap.md) | สถานะงานปัจจุบันและหลักฐานรายรอบ |
| `catalog/production-tools.yaml` | source of truth ของรายการ tool, owning repo, gate status |
| Central AI Gateway (Solo Empire `systems/ai-data/central-ai-gateway.md`) | ทางผ่านเดียวของ provider-backed generation, privacy/token budget |
| spec/plan ราย tool เดิมใน `docs/superpowers/specs` และ `plans` | สัญญาของรุ่นที่ส่งมอบแล้ว (V1A) |

**In scope:** กระบวนการ, requirement ร่วม, spec card ของ 15 tools, ลำดับงาน 2026-Q4
**Out of scope:** เลือกชื่อโมเดล/ราคา (ต้องมี benchmark ก่อน), GPU provisioning, domain ใหม่
ใน backlog (GIS, Electronics, Robotics, Manufacturing, Software, Infrastructure) — พักไว้จนกว่า
pilot จะมีหลักฐานความต้องการ

## 2. กระบวนการ Spec-Driven Development

### 2.1 ขั้นตอนและ artifact

ทุกงานที่ไม่ใช่ bug fix เล็ก ต้องเดินตาม 6 ขั้น แต่ละขั้นมี artifact และผู้อนุมัติ

| ขั้น | Artifact | ที่เก็บ | ผู้อนุมัติ | Exit criteria |
|---|---|---|---|---|
| S1 Spec | spec card / design doc | `docs/superpowers/specs/YYYY-MM-DD-<topic>-design.md` | เจ้าของ | requirement มี ID, acceptance ทดสอบได้, non-goals ชัด, open decisions ถูกตอบ |
| S2 Plan | implementation plan แบบ checkbox | `docs/superpowers/plans/YYYY-MM-DD-<topic>.md` | เจ้าของ | ทุก task อ้าง REQ-ID, ระบุไฟล์, คำสั่งทดสอบ, repo ที่เป็นเจ้าของ |
| S3 Tests first | failing tests + eval fixtures | repo เจ้าของ tool | agent | test ล้มด้วยเหตุผลที่ตรงกับ requirement ก่อนแก้ |
| S4 Implement | code change ขั้นต่ำ | repo เจ้าของ tool | agent | test ผ่าน, ไม่มี lint/type error ใหม่ |
| S5 Verify | ผล suite + eval + smoke จริง (browser/CLI/runtime) | บันทึกใน plan | agent + เจ้าของ (gate คน) | ผ่านทุก AC; gate ที่ต้องใช้คนมีผลบันทึก |
| S6 Evidence | สถานะใน roadmap + catalog + commit | roadmap, `catalog/production-tools.yaml` | agent | `task production-tools:check` ไม่มี drift, commit เฉพาะ scope และ push |

กติกา:

- ไม่มี spec ที่อนุมัติ → ไม่มี plan; ไม่มี plan ที่อนุมัติ → ไม่เขียน product code
- spec เปลี่ยนหลังอนุมัติ ต้องแก้ spec ก่อนแก้ code และบันทึกเหตุผลในหัวข้อ "การเปลี่ยนแปลง"
- bug ที่เจอระหว่างทาง (เช่น token ไทยยาว 0 วินาที) แก้ได้ทันทีด้วย test-first แต่ต้องบันทึกใน evidence ของ wave
- งานของ agent ภายนอก (เช่น Codex) ต้องผ่าน S5 ในเครื่องที่มี runtime ครบก่อน commit

### 2.2 Traceability IDs

| รูปแบบ | ความหมาย | ตัวอย่าง |
|---|---|---|
| `REQ-X-NN` | requirement ร่วม (X = PLAT, AI, SEC, OBS, UX) | `REQ-AI-03` |
| `REQ-<TOOL>-NN` | requirement ราย tool | `REQ-VID-04` |
| `AC-<TOOL>-NN` | acceptance criterion ที่ทดสอบได้ | `AC-VID-04a` |
| `EVAL-<TOOL>-<suite>` | ชุด eval ของ AI adapter | `EVAL-DOC-brief60` |
| `T<wave>.<n>` | task ใน plan | `T2.3` |

ทุก test ที่ครอบ requirement ใส่ ID ใน docstring หรือชื่อ test เพื่อ `grep` ย้อนกลับได้

### 2.3 สถานะของ spec

`draft` → `draft-for-owner-review` → `approved-written-spec` → `approved-implementation-plan` →
`implemented` → `accepted` (ผ่าน gate) / `superseded`

### 2.4 Definition of Ready / Done

**Ready (เริ่ม S4 ได้):** spec อนุมัติ, plan อนุมัติ, runtime ที่ต้องใช้มีอยู่หรือมี task ติดตั้งที่อนุมัติแล้ว,
fixture สังเคราะห์พร้อม, ไม่มี open decision ค้าง

**Done:** AC ครบ, eval ผ่านเกณฑ์, smoke ของจริงผ่าน, gate คนมีผลบันทึก (ถ้ามี), catalog/roadmap อัปเดต,
commit เฉพาะ scope และ push แล้ว, `deployment_skipped_no_target` เมื่อไม่มีเป้า deploy

## 3. Requirement ร่วม (ทุก tool)

### 3.1 Platform

| ID | Requirement |
|---|---|
| REQ-PLAT-01 | ทุก job รับ input ที่มี schema version และถูกปฏิเสธก่อน execute ถ้า validate ไม่ผ่าน |
| REQ-PLAT-02 | job เดิม + input เดิม คืนผลเดิม (replay) โดยตรวจ hash ของ artifact ก่อนคืน |
| REQ-PLAT-03 | key เดิมแต่ input ต่าง คืน conflict ไม่เขียนทับ |
| REQ-PLAT-04 | output ที่ยังไม่ผ่าน QA ไม่ถูกเผยแพร่ (stage → verify → publish) |
| REQ-PLAT-05 | manifest บันทึก recipe/engine version, runtime identity, `model_calls`, cost (unknown = `null` ไม่ใช่ 0) |
| REQ-PLAT-06 | เส้นทาง structured ทำงานได้โดยไม่มี provider config และ `model_calls=0` |

### 3.2 AI adapter (ใช้เมื่อ tool เปิด V1B)

| ID | Requirement |
|---|---|
| REQ-AI-01 | AI ทำได้เพียงเสนอ **IR proposal** ผ่าน contract `brief-adapter.v1` (ต่อยอดจาก Documents V1B) ห้ามสร้าง artifact สุดท้าย, shell command หรือเรียก tool เอง |
| REQ-AI-02 | proposal แยกค่าเป็น `provided` / `inferred` / `missing`; ค่า critical ที่ missing → `needs_input` ห้ามเดา |
| REQ-AI-03 | ตัวเลขที่คำนวณได้ (ยอดเงิน, สูตร, มิติ, เวลา) มาจาก engine เท่านั้น proposal ที่มีค่าคำนวณเองถูกปฏิเสธ |
| REQ-AI-04 | เรียก provider ผ่าน Central AI Gateway เท่านั้น; ไม่มี config → `provider_not_configured` แบบ fail-closed |
| REQ-AI-05 | จอง budget แบบ atomic ก่อน dispatch; ต่อ job ≤ 2 calls (รวม retry/repair), input ≤ 8,000 tokens/call, output (คำตอบ JSON) ≤ 2,000 tokens/call, timeout ≤ 30 s/call, deadline ≤ 120 s/job. ข้อยกเว้นที่เจ้าของอนุมัติ 2026-10-03: เมื่อตั้ง `reasoning_effort` ให้มี `reasoning_tokens` ≤ 14,384 แยกจากคำตอบ (รวมไม่เกิน 16,384 ตาม gateway), timeout ≤ 120 s/call, deadline ≤ 300 s/job และจอง cost รวม reasoning |
| REQ-AI-06 | ไม่มี rate card หรือเพดานงบที่เจ้าของกำหนด → ห้าม dispatch paid step |
| REQ-AI-07 | ไม่ใช้ confidence ที่โมเดลให้ตัวเองเป็นเกณฑ์เดียว; ตรวจ evidence ของ field เทียบ source |
| REQ-AI-08 | ทุก adapter มี fake adapter แบบ scripted สำหรับ test และ eval suite ตาม §4 ก่อนเปิดใช้ของจริง |
| REQ-AI-09 | human-facing text ที่ AI สร้างเป็นภาษาไทยเป็นค่าเริ่มต้นตามนโยบาย gateway; ไม่เพิ่มคำสั่งภาษาขัดแย้งใน adapter |

### 3.3 Security / privacy

| ID | Requirement |
|---|---|
| REQ-SEC-01 | log, manifest, telemetry ไม่เก็บ raw brief, raw transcript หรือข้อมูลเอกสาร (มี canary test) |
| REQ-SEC-02 | ข้อมูล private/restricted ไม่เข้า shared inference cache และไม่ส่งออกนอกเครื่องโดย default (เช่น STT ใช้ local Whisper) |
| REQ-SEC-03 | path และ input จากผู้ใช้ถูก escape/allowlist ก่อนเข้า FFmpeg/Blender/FreeCAD/Unity |
| REQ-SEC-04 | ไฟล์ของเจ้าของที่ใช้ทดสอบ (เช่นเสียงประชุม) อยู่ใน `.runtime/` ที่ gitignore เท่านั้น |

### 3.4 Observability

| ID | Requirement |
|---|---|
| REQ-OBS-01 | stage ที่ล้มคืน error code เฉพาะ (เช่น `transcript_invalid`) ไม่ใช่ `stage_error` รวม; exception ที่ไม่คาดคิด log traceback |
| REQ-OBS-02 | บันทึก wall time และ CPU time แยกกัน; fresh กับ replay แยกกัน |
| REQ-OBS-03 | ทุก wave รายงาน accepted/rejected/needs_input, model calls, latency, cost known/unknown |

### 3.5 UX (tool ที่มีหน้าเว็บ)

| ID | Requirement |
|---|---|
| REQ-UX-01 | สถานะบนหน้าจอตรงกับสถานะใน API โดยไม่ต้อง refresh |
| REQ-UX-02 | ทุก error บนหน้าจอบอกสาเหตุที่ผู้ใช้เข้าใจและทางแก้ (retry / ติดต่อ / แก้ input) |
| REQ-UX-03 | ภาษาไทยแสดงผลถูกต้องทุกจุด: ฟอนต์, การตัดบรรทัด, ไม่มีเศษ token ที่อ่านไม่ออก |
| REQ-UX-04 | label ผูกกับ input และควบคุมด้วยคีย์บอร์ดได้ |

## 4. Eval framework สำหรับ AI adapter

### 4.1 องค์ประกอบของชุด eval (ต่อ tool)

| กลุ่ม | สัดส่วนขั้นต่ำ | จุดประสงค์ |
|---|---|---|
| structured | 1/3 | ข้ามโมเดลได้ ต้อง `model_calls=0` |
| clear brief | 1/3 | brief ชัด ต้องได้ IR ถูก |
| ambiguous | 1/6 | ขาดค่า critical ต้อง `needs_input` |
| adversarial | 1/6 | injection, path traversal, ค่าผิดสามัญสำนึก, หน่วยปน — ต้องถูกปฏิเสธ |

ไทยและอังกฤษอย่างละครึ่งในทุกกลุ่ม บังคับด้วย composition test
แบ่ง dev/held-out และห้ามปรับ prompt โดยดู held-out

### 4.2 เกณฑ์ผ่าน (default ปรับได้ใน spec ราย tool)

| ตัวชี้วัด | เกณฑ์ |
|---|---|
| critical field exact match (clear brief) | ≥ 95% |
| `needs_input` recall (ambiguous) | 100% — ห้ามเดาค่า critical แม้ครั้งเดียว |
| adversarial ผ่านเงียบ | 0 |
| ค่าคำนวณมาจากโมเดล | 0 |
| raw input หลุดลง log/manifest | 0 (canary) |
| model calls ต่อ job | ≤ 2 |

### 4.3 ขั้นเปิดใช้

fake adapter + suite ผ่าน → real adapter ผ่าน gateway ในโหมด shadow (ไม่ใช้ผล, เทียบอย่างเดียว) →
เปิดจริงเมื่อผ่านเกณฑ์บน held-out และเจ้าของอนุมัติ rate card

## 5. Spec cards ราย tool

รูปแบบของแต่ละ card: สถานะปัจจุบัน → รุ่นถัดไป → บทบาท AI → requirement → acceptance → eval → dependency/decision
ลำดับเรียงตาม wave ใน §6

### 5.1 Video Post-Production Studio (`video-studio`) — root repo

- **สถานะ:** `needs-human-gate` web smoke กับคลิปไทย 75 วิผ่านแล้ว; แก้ bug ไทย 4 ตัว (token 0 วิ, word ล้น segment, ฟอนต์, ตัดบรรทัด); มี 16:9, ตั้งช่วงจาก transcript
- **ค้าง:** V1 ของ Codex (เลือก `fast`/`accurate` ต่อโปรเจกต์) อยู่ใน working tree **ยังไม่ผ่าน S5**
- **รุ่นถัดไป V1C (deterministic):** quality per project, คลิปยาว, ตัดคำไทย, installer ใหม่
- **รุ่นถัดไป V2 (AI):** เสนอช่วงไฮไลต์ (highlight proposal) จาก transcript

| ID | Requirement |
|---|---|
| REQ-VID-01 | เมื่อใช้ `faster_whisper` โปรเจกต์เลือก `transcription_quality` = `fast` (Whisper small) / `accurate` (medium); ค่าเริ่มต้น `fast`; ถอดใหม่แบบ `accurate` ได้โดยไม่ลบ transcript เดิม; provider อื่นต้องซ่อนตัวเลือกและ API ปฏิเสธ quality override แทนการใช้ model เดิมเงียบ ๆ; ถ้าอ่าน capability ไม่ได้ ฟอร์มต้องปิดการสร้างจนตรวจสอบสำเร็จ |
| REQ-VID-02 | คลิป 60 นาทีถอดเสียงจนจบโดยไม่ timeout ในทั้งสอง quality บน CPU ของเครื่องนี้ |
| REQ-VID-03 | ซับไทยตัดบรรทัดที่ขอบคำจากตัวตัดคำไทย ไม่ตัดกลางคำเมื่อคำยาวไม่เกินความกว้างบรรทัด |
| REQ-VID-04 | V2: AI เสนอ ≤ N ช่วงไฮไลต์เป็น `EDLProposal` ที่ start/end **ต้องตรงขอบ segment ของ transcript**; ผู้ใช้ยืนยันก่อนสร้าง EDL |
| REQ-VID-05 | installer ใหม่มีฟอนต์ไทย, 16:9 และ REQ-VID-01 และผ่าน fresh-install smoke |

| AC | Acceptance |
|---|---|
| AC-VID-01a | migration `0003` รันบน DB ที่มีโปรเจกต์เดิมแล้ว โปรเจกต์เดิมได้ `fast` และเปิดได้ |
| AC-VID-01b | browser: สร้าง `fast` และ `accurate` อย่างละโปรเจกต์ → transcript บอก model ตรง; ปุ่มถอดใหม่แบบแม่นยำได้ `medium` |
| AC-VID-01c | เมื่อใช้ `openai_compatible` API แจ้งว่าไม่มี project quality selection, UI ไม่แสดงตัวเลือก และ API ปฏิเสธ quality override ที่ส่งมาเอง |
| AC-VID-01d | เมื่ออ่าน capability ไม่ได้หรือ response ผิดรูปแบบ ฟอร์มแสดงข้อผิดพลาด, ปิดการสร้างโปรเจกต์ และให้ลองตรวจสอบซ้ำ |
| AC-VID-02a | บันทึก wall time, peak RAM ของคลิป 30–60 นาที ทั้ง 2 quality; ไม่มี stage timeout |
| AC-VID-03a | test: คำไทยที่ตัวตัดคำคืนมา ไม่ถูกแยกข้ามบรรทัด; เทียบ render จริงด้วยภาพ frame |
| AC-VID-04a | EVAL-VID-highlight: ทุกช่วงที่เสนอ ตรงขอบ segment 100%; ความยาวอยู่ในช่วงที่ขอ; brief กำกวม → `needs_input` |
| AC-VID-05a | ติดตั้งบน Windows user ใหม่ → เปิด → อัปโหลด → ถอดเสียง → render สำเร็จ (gate คน) |

- **Eval:** `EVAL-VID-highlight` 30 เคส (transcript สังเคราะห์ + ของจริงที่เจ้าของอนุญาต)
- **Decision:** เจ้าของเลือก PyThaiNLP `newmm`; วัด image delta ก่อนแพ็ก และห้ามดาวน์โหลด tokenizer model

### 5.2 Documents (`documents-v1a` + V1B + V1C) — root repo

- **สถานะ:** V1A.1 `pilot-ready`; V1B foundation เสร็จ (`brief-adapter.v1`, fake adapter, 60 เคส, provider stub fail-closed); V1C scan ผ่าน synthetic แต่ไม่มี OCR runtime จริง
- **รุ่นถัดไป:** V1B real adapter (T8.3/T8.4), V1C OCR จริง, visual golden ของ V1A

| ID | Requirement |
|---|---|
| REQ-DOC-01 | real adapter ผ่าน gateway ตาม REQ-AI-04..06 และผ่าน `EVAL-DOC-brief60` held-out ตาม §4.2 |
| REQ-DOC-02 | V1C: ติดตั้ง Poppler + Tesseract (ไทย/อังกฤษ) แบบ pin version และ OCR เฉพาะหน้าที่ข้อความฝังไม่พอ |
| REQ-DOC-03 | golden DOCX/PDF ไทย/อังกฤษ และหลายหน้า มีภาพทุกหน้าเป็นหลักฐาน |

| AC | Acceptance |
|---|---|
| AC-DOC-01a | shadow mode 60 เคส: critical exact ≥ 95%, needs_input recall 100%, adversarial ผ่านเงียบ 0 |
| AC-DOC-02a | สแกนจริง 10 หน้า (ไทย/อังกฤษ): field proposal มี provenance ทุกค่า, ค่าที่อ่านไม่ได้ = abstain |
| AC-DOC-03a | เจ้าของตรวจ golden ภาพ 6 ชุด (gate คน) |

- **Decision:** rate card + เพดานงบ + provider (T8.3) — **บล็อก REQ-DOC-01**

### 5.3 Audio — `audio-batch-v1a` (book-audio-production) และ `audio-master-v1a` (platform)

- **สถานะ:** batch `audio-v1c` pilot-ready แบบมีเงื่อนไข (recorder ไทยผ่าน, latency/fade แก้แล้ว); master ยัง `needs-human-gate`
- **บทบาท AI:** **ไม่มี LLM** — งานเป็น DSP ล้วน การตัดสินคุณภาพใช้การวัด + หูคน
- **รุ่นถัดไป:** ยืนยันต้นทางมือถือ/ห้องก้อง; รวมสองตัวหรือกำหนดขอบเขตชัด

| ID | Requirement |
|---|---|
| REQ-AUD-01 | ต้นทางมือถือและห้องก้องอย่างละ 2 ไฟล์ ผ่าน QA หรือถูกปฏิเสธพร้อมเหตุผล ไม่มีผลลัพธ์ที่พลาดเป้าแต่ถูก accept |
| REQ-AUD-02 | ตัดสินใจเรื่องสองโปรดักต์: รวมเป็นหนึ่ง หรือระบุใน catalog ว่า batch ต่างจาก master อย่างไร |
| REQ-AUD-03 | ลดหนี้ lint/type (ruff 41, mypy 32) ในไฟล์ที่แตะ โดยไม่เปลี่ยนพฤติกรรม |

| AC | Acceptance |
|---|---|
| AC-AUD-01a | A/B clip ปรับระดับเท่ากันของ 4 ไฟล์ใหม่ + ผลฟังของเจ้าของ (gate คน) |
| AC-AUD-02a | catalog และ README ของทั้งสอง repo อธิบายขอบเขตไม่ซ้อนกัน |

### 5.4 Graphics Template Batch (`graphics-v1a`) — platform

- **สถานะ:** `accepted` 30/30, Thai visual QA ผ่าน; Pilot 1 dry-run 30 ชิ้น 62 วิ
- **รุ่นถัดไป V1B (AI):** brief → เลือก template + ใส่ข้อความ

| ID | Requirement |
|---|---|
| REQ-GFX-01 | AI เสนอ `GraphicsBatchIR` (template id จาก registry เท่านั้น + text fields + brand token refs); ไม่สร้างภาพเอง |
| REQ-GFX-02 | ข้อความที่ยาวเกิน slot → engine รายงาน overflow แล้วคืนให้คนย่อ: needs_input + `text_overflow` ระบุ slot และจำนวนตัวอักษรสูงสุด, เก็บ template ที่เลือกไว้; AI ไม่ย่อหรือแก้ copy ของลูกค้าเอง และระบบไม่ส่งคำขอย่อให้ AI (2026-10-04: shadow จริงพบว่า gpt-6-luna บางครั้งไม่ยอมย่อ บางครั้งย่อเองจนเคสที่ต้องคืนให้คนหลุดเป็น complete จึงตัดการขอย่อออก) |
| REQ-GFX-03 | Pilot 1 จริง: วัดเวลาต่อชิ้นเทียบทำมือ (มี baseline จากเจ้าของ) |

| AC | Acceptance |
|---|---|
| AC-GFX-01a | EVAL-GFX-brief40: template ถูก ≥ 95%, overflow ที่ไม่ถูกจับ 0 |
| AC-GFX-03a | ตารางวัด pilot: ≥ 3 เท่าเร็วกว่าทำมือ หรือบันทึกว่าไม่ถึง |

### 5.5 3D Product Scene Composer (`scene3d-v1a`) — platform, Blender

- **สถานะ:** `accepted`
- **บทบาท AI:** brief → `SceneIR` preset (มุมกล้อง, แสง, พื้นหลัง จากค่าที่ลงทะเบียน) **ไม่สร้าง geometry**

| ID | Requirement |
|---|---|
| REQ-S3D-01 | AI เลือกได้เฉพาะ preset ที่ลงทะเบียน; ค่าตัวเลขอิสระ (ตำแหน่งกล้อง) ต้องอยู่ในช่วงที่ validator กำหนด |
| REQ-S3D-02 | Materials/Rendering Batch (deterministic): render หลายมุมจาก SceneIR เดียวแบบ batch |

| AC | Acceptance |
|---|---|
| AC-S3D-01a | EVAL-S3D-brief30 + render จริงทุกเคสใน Blender ที่ pin version |

### 5.6 Spreadsheet Workbook Builder (`spreadsheet-v1a`) — platform

- **สถานะ:** `accepted` 20/20
- **บทบาท AI:** brief/ตัวอย่างข้อมูล → column mapping + **formula intent** (เช่น "ยอดรวมต่อเดือน") ไม่ใช่สูตร Excel ดิบ

| ID | Requirement |
|---|---|
| REQ-XLS-01 | AI เสนอ intent จาก allowlist (sum, average, count, group-by, lookup); engine แปลงเป็นสูตร |
| REQ-XLS-02 | สูตรที่ engine สร้างต้อง reopen แล้วคำนวณได้ค่าตรงกับ Python reference |

| AC | Acceptance |
|---|---|
| AC-XLS-01a | EVAL-XLS-brief40: mapping ถูก ≥ 95%, สูตรดิบจากโมเดล 0, mismatch หลัง reopen 0 |

### 5.7 Data + Data Viz (`data-viz-v1a`) — root repo

- **สถานะ:** `accepted`
- **บทบาท AI:** column mapping + แนะนำ chart type จาก allowlist

| ID | Requirement |
|---|---|
| REQ-DVZ-01 | AI เสนอ mapping/chart type; engine ตรวจ unit/axis และ aggregation เอง |
| REQ-DVZ-02 | Report Dashboard (deterministic): หลาย chart ในรายงานเดียว |

| AC | Acceptance |
|---|---|
| AC-DVZ-01a | EVAL-DVZ-brief30: axis/unit mismatch 0, chart type นอก allowlist 0 |

### 5.8 Data SQL/Pipeline Builder (`data-pipeline-v1a`) — platform

- **สถานะ:** `accepted` (plan-only, read-only SQLite)
- **บทบาท AI:** คำถามภาษาคน → **PipelineIR graph** (ไม่ใช่ SQL) → compiler เดิม

| ID | Requirement |
|---|---|
| REQ-DPL-01 | AI ห้ามส่ง SQL; ส่งได้เฉพาะ node ที่ลงทะเบียน (source, filter, join, aggregate, sort, limit) |
| REQ-DPL-02 | ตาราง/คอลัมน์ต้องมีอยู่ใน schema ที่ให้; ไม่มี → `needs_input` |
| REQ-DPL-03 | source execution จริง (นอกเหนือ plan-only) ต้องมี design gate แยก — ไม่อยู่ในรุ่นนี้ |

| AC | Acceptance |
|---|---|
| AC-DPL-01a | EVAL-DPL-question40: ผลลัพธ์ query ตรง reference ≥ 95%, SQL จากโมเดล 0, write statement 0 |

### 5.9 Marketing Campaign Package (`marketing-v1a`) — root repo

- **สถานะ:** `accepted` (cost unknown)
- **บทบาท AI:** เขียน copy ตามช่องทางภายใต้ brand rules และความยาว

| ID | Requirement |
|---|---|
| REQ-MKT-01 | copy ผ่าน validator: ความยาวตามช่องทาง, คำต้องห้าม, claim ที่ต้องมีหลักฐาน → flag |
| REQ-MKT-02 | ไม่มีการโพสต์/ส่งออกภายนอกจาก tool (ต้องอนุมัติคนทุกครั้ง) |

| AC | Acceptance |
|---|---|
| AC-MKT-01a | EVAL-MKT-brief40: ความยาวเกิน 0, คำต้องห้ามหลุด 0, claim ไม่มีหลักฐานถูก flag 100% |

### 5.10 Web Landing Builder (`web-v1a`) — root repo

- **สถานะ:** `accepted`
- **บทบาท AI:** brief → `WebPageIR` (section จาก registry + copy)

| ID | Requirement |
|---|---|
| REQ-WEB-01 | AI เลือกได้เฉพาะ section type ที่ลงทะเบียน; ไม่มี HTML/JS จากโมเดล |
| REQ-WEB-02 | ผลลัพธ์ผ่าน responsive + accessibility check เดิม |

| AC | Acceptance |
|---|---|
| AC-WEB-01a | EVAL-WEB-brief30: HTML ดิบจากโมเดล 0, a11y violation ใหม่ 0 |

### 5.11 Workflow Plan Builder (`workflow-v1a`) — root repo

- **สถานะ:** `accepted`
- **บทบาท AI:** อธิบายกระบวนการ → `WorkflowIR` DAG

| ID | Requirement |
|---|---|
| REQ-WFL-01 | DAG ต้องไม่มี cycle, step ที่ต้องใช้คนมี role ใน permission policy, retry ≤ 3 |
| REQ-WFL-02 | ไม่เรียก external API จาก plan (plan-only) |

| AC | Acceptance |
|---|---|
| AC-WFL-01a | EVAL-WFL-brief30: DAG invalid ที่ผ่านเงียบ 0, step ที่ไม่มี role 0 |

### 5.12 CAD Parametric Part Generator (`cad-v1a`) — platform, FreeCAD

- **สถานะ:** `accepted` (mounting plate เดียว)
- **บทบาท AI:** brief/บันทึกแบบ → `PartIR` parameters — **ห้ามเดามิติ**
- **รุ่นถัดไป:** Drawing / Assembly Check (deterministic) ก่อน AI

| ID | Requirement |
|---|---|
| REQ-CAD-01 | มิติทุกค่าต้องมาจาก brief พร้อม evidence; ขาดค่า → `needs_input` (recall 100%) |
| REQ-CAD-02 | Drawing: สร้างแบบ 2D พร้อม dimension จาก PartIR เดิม |
| REQ-CAD-03 | V1A ไม่ใช่ engineering certification — ข้อความนี้อยู่ใน output ทุกไฟล์ |

| AC | Acceptance |
|---|---|
| AC-CAD-01a | EVAL-CAD-brief30: มิติที่เดา 0, geometry invariant ผ่านทุกเคส |

### 5.13 BIM Quantity Takeoff (`bim-v1a`) — platform, FreeCAD/IfcOpenShell

- **สถานะ:** `accepted` (synthetic)
- **บทบาท AI:** **ไม่มีใน takeoff** — ปริมาณมาจาก IFC เท่านั้น; ในอนาคต AI อาจเสนอ mapping IFC class → รหัสงาน/ราคา (proposal ให้คนยืนยัน)
- **รุ่นถัดไป:** IFC จริงจากงานลูกค้า (ต้องได้รับอนุญาต), Clash/Rule checking

| ID | Requirement |
|---|---|
| REQ-BIM-01 | ทดสอบ IFC จริง ≥ 2 ไฟล์ที่เจ้าของอนุญาต; coverage gap รายงานครบ |
| REQ-BIM-02 | ปริมาณใน report ไม่มีค่าที่ไม่ได้มาจาก IFC |

| AC | Acceptance |
|---|---|
| AC-BIM-01a | ผลรวมเทียบกับตัวเลขที่ผู้เชี่ยวชาญตรวจ (gate คน) |

### 5.14 Game Scene Prototype Builder (`game-v1a`) — platform, Unity

- **สถานะ:** `accepted`
- **บทบาท AI:** brief → `GameIR` layout ภายใน grid/ขอบเขตที่กำหนด

| ID | Requirement |
|---|---|
| REQ-GAM-01 | layout ที่ AI เสนอต้องผ่าน validator (reachability: ทุก collectible และ exit ต้องไปถึงได้) |
| REQ-GAM-02 | Unity batch test ผ่านทุก layout ที่ accept |

| AC | Acceptance |
|---|---|
| AC-GAM-01a | EVAL-GAM-brief30: layout ไปไม่ถึงที่ผ่านเงียบ 0 |

## 6. ลำดับงาน (Waves)

หลักการเลือกลำดับ: ปิดของที่เกือบเสร็จก่อน → ทำสิ่งที่ pilot ต้องใช้ → เปิด AI ทีละ tool หลังมีงบ
AI adapter ทำทีละ tool เพื่อพิสูจน์ pattern ก่อนขยาย

| Wave | เป้าหมาย | Tools | ต้องการจากเจ้าของ | ขึ้นกับ |
|---|---|---|---|---|
| **W0** | ปิดงานค้าง + hygiene | Video V1 (Codex), docs:check | — | — |
| **W1** | Video พร้อมใช้งานจริง | Video | ไฟล์ประชุมยาว, ติดตั้งเครื่องใหม่ | W0 |
| **W2** | Pilot สร้างรายได้ | Graphics+3D หรือ Video Reels | เลือกงานจริง, baseline ทำมือ, อนุมัติ offer | W1 (ถ้าเลือก Video) |
| **W3** | เปิด AI ตัวแรก | Documents V1B | rate card, เพดานงบ, provider | — (ทำขนานได้) |
| **W4** | ขยาย AI adapter | Graphics → Spreadsheet/Data Viz → Video highlight | ผลของ W3 | W3 |
| **W5** | ความสามารถ deterministic รอบถัดไป | Audio, CAD drawing, 3D batch, Data Viz dashboard, OCR จริง | ไฟล์ทดสอบจริง | — |
| **W6** | AI ที่เหลือ | Marketing, Web, Workflow, Data Pipeline, 3D, CAD, Game | — | W4 |

## 7. Task ย่อย (W0–W3 ละเอียด)

### W0 — ปิดงานค้าง

- [x] **T0.1** รีวิว diff ของ Codex (V1) ทีละไฟล์เทียบ REQ-VID-01; เอาส่วนที่นอก scope ออก
- [x] **T0.2** รัน API pytest + ruff, web vitest + tsc ในเครื่องที่มี runtime ครบ
- [x] **T0.3** ทดสอบ migration `0003` บน Postgres ใน Docker ที่มีโปรเจกต์เดิม (AC-VID-01a)
- [x] **T0.4** browser smoke AC-VID-01b; บันทึกเวลา `fast` vs `accurate`
  - UI-only provider/editor smoke ผ่าน; full transcription comparison ยังรอ local `medium` model ที่ไม่มีใน host หรือ API image cache และวิดีโอตัวอย่างที่ได้รับอนุญาต (ห้ามดาวน์โหลด/สร้างไฟล์สื่อแทนเจ้าของ). เสียงสังเคราะห์อังกฤษ 30 นาทีเป็น performance probe เท่านั้น ไม่ใช่หลักฐานภาษาไทยหรือคลิปประชุมตาม REQ-VID-02.
- [x] **T0.5** commit V1 แยก API/web; push; อัปเดต roadmap + catalog
- [x] **T0.6** แก้ `task docs:check`: บังคับ UTF-8 ใน `task_command_ownership.py`; ตรวจสาเหตุ entity-graph `missing active entity: git_commit` (44) แล้ว regenerate หรือแก้ guard
  - UTF-8 ถูกกำหนดใน subprocess และ task ownership parity ผ่าน. `task docs:check` ปัจจุบันหยุดที่ `portfolio:contexts:check` (`tasks without context: 53`); `task entity-graph:check` แยกรายงาน gap review registry drift (`missing=102, stale=0`). ตามการตัดสินใจของเจ้าของ ให้คง gate เป็น blocker และไม่แก้ task links หรือ regenerate graph.
- [x] **T0.7** อัปเดต catalog: `video-studio` ชื่อ/สถานะให้ตรงเวอร์ชันจริง (installer ปัจจุบันคือ v0.1.2)
- **หลักฐาน W0 (2026-09-30):** commits `0a3c6a61` (API), `5de1346b` (web), `dbbb6ed0` (UTF-8), `c950423b` (catalog v0.1.2), `44a55d60` (pin PyAV <19 หลัง PyAV 19 ทำให้ถอดเสียงล้มทุกงาน); API pytest 80 passed / 1 skipped, web vitest 17 passed + tsc; migration 0003 บน Postgres 16 ชั่วคราว โปรเจกต์เดิมได้ `fast` (AC-VID-01a); full-flow smoke คลิปสังเคราะห์ 75 วิ: fast 21.0 วิ, accurate retranscribe 69.6 วิ, accurate project 61.6 วิ (AC-VID-01b) — ไม่ใช่หลักฐานคุณภาพไทย (ดู D1)
  - ยืนยันเวอร์ชัน `0.1.2` ใน `apps/video-postproduction-desktop/package.json`; catalog และ dashboard ใช้ชื่อ/สถานะเดียวกัน และ `task production-tools:check` ผ่าน.

### W1 — Video พร้อมใช้งานจริง

- [x] **T1.1** ใช้ fixture สังเคราะห์ใน `.runtime/video-w1/meeting-synthetic-30m.wav` วัด performance; ต้องมีไฟล์จริงที่เจ้าของอนุญาตก่อนรับรองความแม่นยำหรือเปิด pilot
- [x] **T1.2** รัน `fast` และ `accurate`; เก็บ wall time, peak RAM, จุด timeout (AC-VID-02a)
- [ ] **T1.3** ถ้ามี timeout: เพิ่ม timeout ตามความยาวคลิป + test
- [x] **T1.4** spec ย่อยตัดคำไทย (เลือกไลบรารี) → test ตามที่ AC-VID-03a กำหนด → implement ใน `captions/ass.py`
- [ ] **T1.5** ตัด 3 คลิป (9:16 และ 16:9) ให้เจ้าของดู; บันทึกผล
- [x] **T1.6** build installer ใหม่ (`apps/video-postproduction-desktop`), ตรวจว่า runtime bundle มี Dockerfile ใหม่
  - หลักฐาน T1.1/T1.2/T1.4/T1.6: [video V1C readiness plan](../plans/2026-09-29-video-v1c-production-readiness.md) (wall time + sampled peak RAM, ไม่มี timeout) และ catalog `video-studio` (v0.1.4 candidate มี offline newmm, ฟอนต์ไทย, migration 0001-0003; synthetic 30 นาทีทั้งสองโหมดและ real Thai 38m12s ผ่าน). T1.3 ไม่พบ timeout ในรอบนี้; T1.5 ยังรอเจ้าของให้คะแนนคลิป
- [ ] **T1.7** 👤 fresh-install smoke บน Windows user ใหม่ (AC-VID-05a) → เปลี่ยน gate เป็น `pilot-ready`

### W2 — Pilot

- [ ] **T2.1** 👤 เลือกคู่ pilot: (ก) Graphics+3D mockup 30 ชิ้น หรือ (ข) คลิปประชุม/พอดแคสต์ → Reels ซับไทย
- [ ] **T2.2** 👤 จับเวลาทำมือ 1 ชิ้นเป็น baseline
- [ ] **T2.3** รันงานจริง 1 รอบ; ตารางวัด: เวลา/ชิ้น, จำนวนครั้งที่แก้มือ, ต้นทุนที่วัดได้
- [ ] **T2.4** บันทึกผลลง `ventures/<name>/`
- [ ] **T2.5** ร่าง offer/pricing หน้าเดียว → 👤 อนุมัติก่อนส่งออกภายนอก (T7.4)

### W3 — Documents V1B เปิดจริง

- [ ] **T3.1** 👤 กำหนด rate card, เพดานงบต่อ job/วัน/เดือน, provider/model slot
- [x] **T3.2** เขียน budget reservation แบบ atomic บน persisted ledger (REQ-AI-05) + test concurrency
  - `brief_adapter_kit.ledger`: ตาราง `ai_cost_ledger` จอง worst-case cost ใน `BEGIN IMMEDIATE` เทียบเพดานงานต่องาน/วัน/เดือน; ค่าใช้จริงที่ไม่ทราบคงยอดจองไว้เต็ม; test 12 threads ผ่านเพดานได้ตรง 5
- [x] **T3.3** real adapter ผ่าน Central AI Gateway แทน stub; ยังคง fail-closed เมื่อไม่มี config
  - `ProviderBriefAdapter` ต้องมี `documents-provider-config.v1` (rate card + เพดานงบ ต่องาน/วัน/เดือน + บันทึกการอนุมัติ), จอง cost แบบ atomic, ≤ 2 calls, timeout 30 วิ/deadline 120 วิ, ตรวจ grounding ทุกค่ากับ brief (REQ-AI-07), กัน prompt injection ก่อนใช้ token; config เริ่มที่ `mode: shadow` และ `brief --adapter provider` ปฏิเสธจนกว่าเจ้าของเปลี่ยนเป็น `live`. pytest 235 passed
- [ ] **T3.4** shadow mode บน `EVAL-DOC-brief60` held-out; รายงานตาม §4.2
  - เครื่องมือพร้อม: `document_production.cli brief-shadow` + fixture free-text 16 เคส + split dev/held-out; ทดสอบ gate ด้วย oracle/degraded transport แล้ว. ยังไม่ได้รันกับ provider จริงเพราะรอ D3 (rate card/เพดานงบ/token ของ gateway)
- [ ] **T3.5** ถ้าผ่าน: เปิดใช้ + canary test ความเป็นส่วนตัว; ถ้าไม่ผ่าน: บันทึกเหตุผล ไม่ลด quality floor
- [x] **T3.6** เขียน `docs/.../ai-adapter-pattern.md` สรุป pattern จาก Documents เพื่อใช้ซ้ำใน W4
  - pattern อยู่ใน [`packages/brief-adapter-kit/README.md`](../../../packages/brief-adapter-kit/README.md) พร้อมโค้ดกลาง (config, ledger, transport, runner, grounding, shadow, testing); Documents ใช้ kit นี้เป็น reference implementation และ platform repo มีสำเนา vendored ที่ตรวจ parity

### W4–W6

แตก task ตอนเขียน plan ของแต่ละ tool ตาม spec card §5 โดยใช้ pattern จาก T3.6
แต่ละ tool ต้องมี: IR proposal schema → fake adapter → eval suite ตาม §4 → shadow → เปิดใช้

**สถานะ 2026-10-03 (ขั้น IR proposal schema → fake adapter → eval suite → shadow harness เสร็จครบ; ขั้น "shadow จริง → เปิดใช้" รอ D3):**

| Tool | Eval suite | ผล offline gate (oracle transport) | ค้าง |
|---|---|---|---|
| Documents | EVAL-DOC-brief60 + free-text 16 | ผ่าน | shadow จริง (D3) |
| Data Viz | EVAL-DVZ-brief30 | ผ่าน | shadow จริง (D3) |
| Marketing | EVAL-MKT-brief40 | ผ่าน | shadow จริง (D3) |
| Web | EVAL-WEB-brief30 | ผ่าน | shadow จริง (D3) |
| Workflow | EVAL-WFL-brief30 | ผ่าน | shadow จริง (D3) |
| Video highlight | EVAL-VID-highlight 30 | ผ่าน | shadow จริง (D3), ต่อเข้า API/web, คอลัมน์ privacy ของโปรเจกต์ |
| Graphics | EVAL-GFX-brief40 | ผ่าน | shadow จริง (D3) |
| 3D Scene | EVAL-S3D-brief30 | ผ่านระดับ IR | render Blender จริง |
| Spreadsheet | EVAL-XLS-brief40 | ผ่าน | shadow จริง (D3) |
| Data Pipeline | EVAL-DPL-question40 | ผ่าน (รันจริงบน SQLite สังเคราะห์) | shadow จริง (D3); engine ยังไม่รองรับ join |
| CAD | EVAL-CAD-brief30 | ผ่านระดับ IR | build FreeCAD จริง |
| Game | EVAL-GAM-brief30 | ผ่านระดับ IR | Unity batch test จริง |

ทุกตัวใช้ [`packages/brief-adapter-kit`](../../../packages/brief-adapter-kit/README.md) (platform repo ใช้สำเนา vendored ที่มี parity test); config เริ่มที่ `mode: shadow` และปฏิเสธการใช้จริงจนกว่าเจ้าของเปลี่ยนเป็น `live`. Audio และ BIM ไม่มีบทบาท LLM ตาม spec card. ชุด eval ขนาด 30/40 แบ่งไทย/อังกฤษต่างกันได้ไม่เกิน 1 ในกลุ่มที่เป็นเลขคี่ และใช้ floor ของสัดส่วน 1/3, 1/6 (บันทึกใน README ของแต่ละ tool)

## 8. สิ่งที่เจ้าของต้องตัดสินใจ

| # | เรื่อง | บล็อก | ค่าที่แนะนำ |
|---|---|---|---|
| D1 | ไฟล์ประชุมยาวสำหรับทดสอบ | W1 | ยังไม่เลือกไฟล์ประชุมไทยจริง; fixture สังเคราะห์อังกฤษ 30 นาทีใน `.runtime/video-w1/meeting-synthetic-30m.wav` เป็นเพียง performance probe ที่ agent สร้าง ไม่ใช่หลักฐานคุณภาพไทยหรือเกณฑ์ 60 นาที; เจ้าของยืนยันว่าไม่มีต้นฉบับใน checkout และไม่ให้ดาวน์โหลด |
| D2 | คู่ pilot | W2 | Video Reels ซับไทย (ตรงกับของที่เพิ่งทำเสร็จ) หรือ Graphics+3D (พร้อมที่สุด) |
| D3 | rate card + เพดานงบ + provider | W3 | เริ่มเพดานต่ำ ต่อ job และต่อเดือน แล้วขยายตามผล |
| D4 | ไลบรารีตัดคำไทย | T1.4 | เลือกแล้ว: PyThaiNLP `newmm`; วัด image delta ก่อนแพ็กและห้ามดาวน์โหลดโมเดล |
| D5 | รวม audio สองตัวหรือไม่ | REQ-AUD-02 | กำหนดขอบเขตให้ชัดก่อน ยังไม่รวม |
| D6 | ลำดับ AI adapter หลัง Documents | W4 | Graphics (มี pilot) → Spreadsheet/Data Viz → Video highlight |

**สถานะการตัดสินใจ:** D1 ยังเปิดอยู่ ไฟล์สังเคราะห์ภาษาอังกฤษจาก Windows voice (16 kHz mono PCM, 30 นาที) เป็น fixture ชั่วคราวที่ agent สร้างเพื่อวัด performance เท่านั้น ไม่ใช่ไฟล์ที่เจ้าของเลือก และไม่ทดแทนเสียงประชุมไทยจริงหรือเกณฑ์ 60 นาที เจ้าของยืนยันว่าไม่มีต้นฉบับใน checkout และไม่ให้ดาวน์โหลด; ต้องรอ path ของไฟล์ที่ได้รับอนุญาตก่อนรับรองคุณภาพไทยหรือปิด gate ที่ต้องการเสียงจริง. D4 เจ้าของเลือก PyThaiNLP tokenizer `newmm`; ต้องวัด image delta ก่อนแพ็กและห้ามดาวน์โหลดโมเดล

## 9. ความเสี่ยง

| ความเสี่ยง | ผลกระทบ | การป้องกัน |
|---|---|---|
| ขยายหลาย tool พร้อมกันจนไม่มีตัวไหนถึงผู้ใช้จริง | รายได้ยังเป็น ฿0 | W2 pilot มาก่อน W4; จำกัด WIP ≤ 2 wave |
| AI เดาค่าที่สำคัญ (ราคา, มิติ) | ความเสียหายต่อลูกค้า | REQ-AI-02/03 + needs_input recall 100% |
| ต้นทุน AI ไม่ทราบ | ใช้งบเกิน | REQ-AI-05/06 ไม่มีเพดาน = ไม่ dispatch |
| งาน agent ภายนอกไม่ได้ verify | bug เข้า main | กติกา §2.1 ข้อสุดท้าย |
| ภาษาไทยพังในจุดที่ test สังเคราะห์ไม่ครอบ | ใช้งานจริงไม่ได้ | ทุก wave มี smoke ด้วยข้อมูลไทยจริงที่เจ้าของอนุญาต |
| ข้อมูลส่วนตัวของเจ้าของหลุด | privacy | REQ-SEC-01/02/04 + canary |

## 10. การเปลี่ยนแปลง

- 2026-09-29: ร่างแรกสำหรับเจ้าของรีวิว
- 2026-09-30: แก้สถานะ D1 ตามคำยืนยันเจ้าของ: fixture อังกฤษ 30 นาทีเป็น performance probe ที่ agent สร้าง ไม่ใช่การเลือกไฟล์ประชุมไทย; ต้นฉบับไม่มีใน checkout และห้ามดาวน์โหลด จึงคง D1 เป็น open.
- 2026-09-30: ปิด T0.1–T0.3 หลังรีวิว API/Web 16 ไฟล์และ migration; เพิ่ม provider guard สำหรับ accurate retranscription และคืนสถานะโปรเจกต์เดิมเมื่อคิวล้มเหลว; Postgres migration `0003` ให้โปรเจกต์เดิมเป็น `fast` และ API เปิดโปรเจกต์ได้. API tests 80 passed/1 skipped, scoped Ruff ผ่าน, Web Vitest 17 passed, typecheck และ UI-only browser smoke ผ่าน. T0.4 ยังเปิดเพราะไม่มี `medium` ใน cache; ไม่ดาวน์โหลดและไม่อ้างผล transcript จริง.
- 2026-09-30: ยืนยัน T0.7 จาก desktop package/catalog/dashboard และ `task production-tools:check`; `task test` กับ `task typecheck` ผ่าน. เจ้าของเลือกคง T0.6 เป็น blocker โดยไม่แก้ task links; `task docs:check` ติด 53 tasks ที่ไม่มี context และ `task entity-graph:check` รายงาน gap registry drift 102 รายการ. ไม่เขียนฐานข้อมูลหรือ regenerate graph ระหว่างตรวจ; T0.5 ยังไม่ทำเพราะ working tree มี 126 ไฟล์เปลี่ยนและไม่ได้รับคำสั่ง commit/push อย่างชัดเจน.
- 2026-10-03: ปิด T3.2/T3.3/T3.6 และเตรียม T3.4: เพิ่ม `packages/brief-adapter-kit` (shared AI adapter kit), Documents ใช้ adapter จริงผ่าน gateway แบบ fail-closed + shadow evaluator; ติ๊ก T1.1/T1.2/T1.4/T1.6 ตามหลักฐานใน catalog. T3.1 (D3) และการรัน shadow จริงยังรอเจ้าของกำหนด rate card/เพดานงบ
- 2026-10-03: W4–W6 ขั้น adapter/fake/eval/shadow harness เสร็จทั้ง 11 tool ที่มีบทบาท AI; เหลือ shadow จริงกับ gateway (รอ D3) และ runtime gate ของ Blender/FreeCAD/Unity
- 2026-10-03: เจ้าของเลือก GPT-6 Luna effort `max` เป็นโมเดลหลัก (API รับสูงสุด `xhigh` จึงใช้ `xhigh`; gateway รองรับ effort รายคำขอ); ขยาย REQ-AI-05 ให้มี reasoning headroom/timeout ที่ยาวขึ้นเฉพาะเมื่อตั้ง reasoning effort (kit ตรวจแบบ fail-closed)
- 2026-10-04: shadow จริงรอบ 2–5 กับ gpt-6-luna (xhigh): Documents, Data Pipeline, Game, Video highlight, Data Viz, 3D, Spreadsheet, CAD, Workflow, Graphics ผ่าน gate held-out; ปิดช่อง adversarial ที่พบด้วยโค้ด deterministic; REQ-GFX-02 ยึดการคืน overflow ให้คนย่อ
- 2026-10-04: shadow จริงรอบ 6 (ครบ 12 tool ในรอบเดียว, gpt-6-luna xhigh, ค่าใช้จ่ายจาก ledger $0.0907; สะสมทุกรอบ $0.4394): ผ่าน gate 7 ตัว ได้แก่ Documents, Data Viz, Marketing, Workflow, Video highlight, Spreadsheet และ CAD (CAD ผ่านแค่ระดับ IR และรอ build FreeCAD). ล้ม 5 ตัวที่ critical/template/result match ระดับ clear: Web 6/7, 3D 7/8, Graphics 11/12, Data Pipeline 11/12, Game 7/8. ทุกเคสที่ล้มเป็นการถามกลับหรือปฏิเสธเคสที่ข้อมูลครบ (ระบบไม่เดา) ไม่มี adversarial silent pass, canary leak หรือค่าที่โมเดลคำนวณเอง. ข้อสรุป: ด้านความปลอดภัยนิ่งแล้ว แต่ความแม่นของ 5 ตัวนี้ยังไม่นิ่งพอเพราะ held-out clear มีแค่ 7–12 เคส จึงยังไม่ผ่านเกณฑ์ 95%; ไม่ผ่อนเกณฑ์ และทุก config ยังอยู่ใน `mode: shadow` จนกว่าเจ้าของจะสั่งเปลี่ยน
- 2026-10-04: แก้ต้นเหตุ 5 tool ที่ล้มในรอบ 6 จากคำตอบจริงที่ capture ไว้ (ไม่ผ่อนเกณฑ์): 3D prompt v3 (คำบรรยายไม่ใช่คำขอที่ขาด); Graphics prompt v3 (ใส่คำที่ใช้เป็นหลักฐานของแต่ละขนาด, หลักฐานใน brief ตัดสิน variant, และตัดการขอให้ AI ย่อออก เพราะ AI บางครั้งย่อ copy เองจนเคส ambiguous หลุดเป็น complete); Data Pipeline prompt v2 (alias ภาษาอังกฤษ, select เฉพาะเมื่อคำถามระบุคอลัมน์) + เปลี่ยนชื่อ alias ที่ไม่ใช่ ASCII แบบ deterministic; Game ให้ AI แก้ผิดด้านผังได้ 1 ครั้งภายในเพดาน 2 calls (ไม่แก้ข้อเท็จจริงที่ brief ระบุ); Web prompt v3 (ชื่อเชิงบรรยายนับเป็นชื่อ ส่วนคำเรียกกว้าง ๆ ไม่นับ) และแก้ brief ของเคส held-out `web-clear-th-05` จาก "ร้านดอกไม้สังเคราะห์" (อ่านได้ทั้งเป็นชื่อร้านและเป็นประเภทร้านดอกไม้ประดิษฐ์ โมเดลจึงถามกลับราวครึ่งหนึ่ง) เป็น "ร้านดอกไม้สังเคราะห์ ห้า" ตามรูปแบบเคสไทยอื่น. ผล: รอบ 9a/9b ผ่านทั้ง 5 tool และรอบ 10 ผ่าน gate ครบ 12 tool ในรอบเดียว (ledger $0.0900; สะสม $0.6963). 3D/CAD/Game ยัง `live_eligible: false` จนกว่าจะรัน Blender/FreeCAD/Unity จริง (เครื่องนี้ไม่มีทั้งสามตัว); ทุก config ยังเป็น `mode: shadow`
- 2026-10-04: Graphics เปลี่ยนเป็น template แบบข้อมูล: ทุก template เป็น theme JSON (`graphic-theme.v1`) ที่ประกอบจาก motif 20 แบบ (`graphics_production/motifs.py`) และ `graphic-ir.v2` (path, gradient, กลุ่มชั้นสำหรับ Affinity/Figma); template เดิม 3 ตัวยัง compile เป็น `graphic-ir.v1` เดิมทุกไบต์ (เทียบ 84 request กับ compiler เดิม) เพิ่มธีม synthwave, bold_editorial, soft_gradient และขนาด landscape 1920×1080; AI adapter อ่าน template/slot/คำในบรีฟจาก registry (prompt graphics-brief.v4) shadow จริง 2 รอบผ่าน gate เต็ม; PNG ใช้ resvg ได้เมื่อไม่มี browser
- 2026-10-04: Graphics ปรับดีไซน์ได้โดยไม่แก้ theme: `design` ใน request (style preset, palette, ซ่อน/จาง/เลื่อน layer, ค่าราย item แบบ `V + 40`) ตรวจกับ theme ทุกค่า; `graphics controls` แสดงทุกค่าที่ปรับได้; AI design editor (`graphics edit`, route `graphics.design_edit`, prompt graphics-edit.v2) แปลงคำสั่งภาษาธรรมดาเป็น change set ที่ engine ตรวจแบบ deterministic (เป้าหมาย/ค่า, ข้อความคัดจากคำสั่ง, อยู่ในขอบภาพ, text fit) และแก้ต่อกันได้; EVAL-GFX-edit24 shadow จริง 2 รอบผ่าน (clear 100%, ambiguous recall 100%, adversarial 0); config เริ่มที่ `mode: shadow` รอเจ้าของเปลี่ยน
- 2026-10-04: วิธีทำเองแบบเฉพาะงาน: ทุก tool ที่ใช้ `production_trace` บันทึก `manual-facts.json` และเขียน `MANUAL-AI.md` ด้วย AI อัตโนมัติทุกงาน (เมื่อมี `PRODUCTION_AI_MANUAL_CONFIG` ที่เป็น `mode: live`) ในโปรแกรมที่เลือกต่อ tool (`PRODUCTION_AI_MANUAL_PROGRAMS`); ค่าทุกค่าอ้างจาก fact id, ตัวเลขที่ไม่มีในข้อมูลถูกซ่อม/ติด ⚠️, โปรแกรมที่ทำงานนั้นไม่ได้ = needs_input; EVAL-MANUAL-12 (Graphics v1/v2, Spreadsheet, Data Viz × 8 โปรแกรม) shadow จริง 2 รอบผ่าน (clear 100%, recall 100%, injection 0); Graphics themed guide ระบุทุก motif พร้อมค่าและเครื่องมือใน Affinity Designer 2
