---
title: Production Tools Roadmap — ปิดงานค้าง รวมทางเข้าเดียว ลองงานจริง แล้วค่อยเปิดใช้ AI
aliases: [Production Tools Roadmap 2026-09-26, Tools Before Domains Roadmap]
tags: [plan, roadmap, production-tools, phase-plan]
created: 2026-09-26
updated: 2026-09-26
status: proposed
description: ลำดับงาน 4 เฟสสำหรับ production tools ทั้งหมด — ปิดงานที่ค้าง (BIM, Documents, Audio, Video), รวมทางเข้าเดียวด้วย tool catalog, ลองใช้กับ venture จริงเพื่อหารายได้ แล้วจึงเปิดใช้ AI แบบคุมงบ โดยหยุดเพิ่ม domain ใหม่จนกว่าจะมีหลักฐานความต้องการ
related:
  - 2026-09-25-bim-quantity-takeoff-v1a.md
  - 2026-09-22-production-platform-next-products.md
---

# Production Tools Roadmap — 2026-09-26

> **For agentic workers:** นี่คือ roadmap ระดับพอร์ตโฟลิโอ ไม่ใช่ execution plan ราย task งาน execution ให้ไปที่ plan ลูกที่อ้างถึงในแต่ละ T-item (เช่น BIM ดู `2026-09-25-bim-quantity-takeoff-v1a.md`) และทำงานใน repo เจ้าของตาม boundary ที่ระบุ ขั้นตอนใช้ checkbox (`- [ ]`) เพื่อติดตามความคืบหน้า

## ภาพรวมก่อนวางแผน

จากการสำรวจ repo มีเรื่องที่ควรรู้ก่อน 3 ข้อ:

1. มี tool ทั้งหมด 13 ตัว แต่รายได้ยังเป็น ฿0 (ตาม `dashboard/CURRENT-STATE.md`) และยังไม่มี tool ตัวไหนที่ผู้ใช้จริงเคยใช้
2. มีงานที่ทำค้างไว้: BIM ทำไป 0 จาก 40 checkbox, Documents V1A.1 และ V1B เหลือ 27 checkbox, ส่วน Audio ยังรอให้คนฟังตรวจ
3. backlog ยังมี domain ใหม่รออีกเยอะ เช่น GIS, Electronics, Robotics, Manufacturing

**กลยุทธ์:** หยุดเพิ่ม domain ใหม่ก่อน แล้วทำตามลำดับนี้ — ปิดงานที่ค้าง → รวมทุก tool ให้ใช้งานผ่านทางเข้าเดียว → เอาไปลองกับงานจริงเพื่อหาเงิน → ค่อยเปิดใช้ AI

## Phase 1 — ปิดงานที่ค้างอยู่ (สัปดาห์ที่ 1–2)

### T1. BIM Quantity Takeoff V1A

ทำต่อตาม plan ใน `book-production-platform` (ดู `2026-09-25-bim-quantity-takeoff-v1a.md`)

- [x] T1.0 รัน IfcOpenShell probe ภายใน FreeCADCmd ให้ผลเหมือนเดิมทุกครั้ง โดยต่อจาก preflight commit `3d1efc9`
- [x] T1.1 ทำ BimTakeoffIR แบบ strict และ canonical fingerprint
- [x] T1.2 ตรวจ syntax และ schema ของ IFC2X3/IFC4 แล้วสร้าง IFC จำลองไว้ใช้ทดสอบ
- [x] T1.3 ดึง QTO ออกมา แปลงหน่วยเป็น SI แล้วรายงาน coverage gap
- [x] T1.4 export JSON/CSV พร้อม QA manifest โดยไม่มี path หรือข้อมูลดิบติดออกไป
- [x] T1.5 ทำ job แบบ content-addressed และ replay ที่ตรวจ hash
- [x] T1.6 ทำ CLI `book-production bim takeoff`, benchmark 20 เคส และ README
- [x] T1.7 รัน synthetic acceptance gate แล้วบันทึกหลักฐานลง section ใหม่ของ platform plan

**เสร็จเมื่อ:** ผ่าน 20/20 เคส, `model_calls=0`, replay ได้ hash ตรงกัน และ `scripts/check.py` ผ่าน

### T2. Documents V1A.1 — เสริมความทนทาน

ทำตาม plan §4 ใน `services/document-production-api/`

- [x] T2.1 วัดต้นทุนให้ถูกชนิด: แยก `wall_seconds` กับ `cpu_seconds` และแยกงานใหม่ (fresh) กับงานที่ replay พร้อมเขียน fake-clock test
- [x] T2.2 กันงานซ้ำและกู้คืนหลัง crash: เพิ่ม SQLite job ledger ใน `job_store.py` พร้อม UNIQUE idempotency key, ทดสอบ 2 process พร้อมกัน และทดสอบ crash recovery
- [x] T2.3 ความถูกต้องของ DOCX/PDF: escape markup ใน ReportLab ให้ error ชัดเจนเมื่อ font ไทยไม่รองรับ (แทนการขึ้น `?` เงียบ ๆ) และทดสอบ 0/1/100/101 รายการกับเอกสารหลายหน้า
- [x] T2.4 contract ของ API/CLI: map error เป็น 422/409/pending และทำ smoke command ที่รันได้โดยไม่ต้องตั้งค่า provider

**เสร็จเมื่อ:** request ซ้ำพร้อมกันไม่สร้าง artifact ซ้ำ และภาษาไทยไม่หายเงียบ ๆ

### T3. Audio — ด่านให้คนฟังตรวจ

- [x] T3.1 รัน full suite โดยใช้ FFmpeg จริง ห้ามนับ integration test ที่ถูก skip ว่าผ่าน
- [x] T3.2 สร้างชุดฟังตัวอย่าง 5–10 track (พูด, เพลง, noise) แล้วทำ checklist ให้เจ้าของฟังด้วยหู ดู clipping, loudness และความชัดของเสียงพูด
- [x] T3.3 บันทึกผลฟังลงใน plan แล้วเปลี่ยนสถานะเป็น pilot-ready หรือเปิด fix ticket — **2026-09-27: pilot-ready (มีเงื่อนไข)** เจ้าของฟัง A/B ปรับระดับเท่ากัน 9 จุดที่ limiter บีบหนักสุดของเสียงไทยจริง 3 track: ผลงาน "ดีพอ ๆ กับต้นทาง"; 8 track สังเคราะห์ผ่านเชิงวัตถุ (pure gain, residual ≈ −125 dB)
- [x] T3-fix แก้ loudness normalization สำหรับเสียงจริง (เพิ่มจาก fix ticket — สั่งโดยเจ้าของ): near-miss correction ด้วย volume + alimiter margin ปรับตัว จากต้นทางเดิมเสมอ สูงสุด 6 ครั้ง — เสียงไทยจริง 3 ช่วง accepted 3/3 (recipe `audio-v1b`, nested `2c49af1`)

หมายเหตุ: ข้อนี้เจ้าของต้องลงมือเอง ผู้ช่วยเตรียมไฟล์และ checklist ให้ได้

### T4. ตรวจความทนทานของ Video Studio

- [ ] T4.1 ติดตั้ง installer v0.1.1 บนเครื่องที่ติดตั้งใหม่ หรือบน Windows user คนละคน แล้วรัน smoke: อัปโหลดคลิป → ถอดเสียง → ได้ first cut
- [ ] T4.2 ลองคลิปยาว 30–60 นาทีกับคลิปเสียงภาษาไทย แล้วจดเวลา, RAM และจุดที่ล้ม
- [x] T4.0 (เพิ่ม 2026-09-27 ตามเจ้าของ: ใช้ผ่าน web app ก่อน) smoke web app `http://127.0.0.1:3400` ด้วยคลิปประชุมไทย 75 วิ: upload <1 วิ, ถอดเสียง `faster_whisper small` บน CPU 40 วิ (ตรวจภาษา th 0.97), render first cut 9:16 ~23 วิ ใน 4.5–6 วิ — พบและแก้ 3 bug ที่ทำให้ใช้กับภาษาไทยไม่ได้ (root `f4e671cc`, `202e0c0a`): (1) token ไทยความยาว 0 วิ ทำให้ transcript ถูกปฏิเสธทั้งไฟล์ → รวมเข้าคำข้างเคียง (2) ซับไทยเป็นกล่อง □ เพราะ image ไม่มีฟอนต์ไทย → ติดตั้ง Noto Sans Thai (3) ซับยาวล้นจอเพราะไม่มีการตัดบรรทัด → ตัดที่ช่องว่าง/ขอบคำ/cluster ไทย; UX: พับ word chips ที่เป็นเศษคำไทย; API suite 65 passed / 1 skipped, web vitest 3 passed + typecheck ผ่าน
- [x] T4.0b (2026-09-27) UX รอบ 2 + 16:9: คลิกประโยคเพื่อ seek, ปุ่มเริ่ม/จบตรงนี้ตั้งช่วง first cut, สถานะรีเฟรชเอง, ขอบซับหนาขึ้น (`c0a13930`, `3f01e421`); output 16:9 เต็มเฟรม letterbox + ซับบน canvas ตาม output (`2ef29450`); แก้ word timing ของ Whisper medium ที่ล้นขอบ segment (`b14a983f`); benchmark คลิปไทย 75 วิ บน CPU: small ถอด 26 วิ (โหลด 4 วิ) vs medium 71 วิ (โหลด 8 วิ) — medium แม่นกว่าชัดเจน (เช่น "สมบูรณ์", "scenario", "component" ที่ small ถอดผิด) ค่าเริ่มต้นยังเป็น small รอเจ้าของเลือก

**เสร็จเมื่อ:** มีตัวเลขที่ยืนยันได้ว่าใช้กับงานจริงได้ถึงระดับไหน

## Phase 2 — รวมให้เป็นระบบเดียว (สัปดาห์ที่ 2–3)

### T5. Tool Catalog และทางเข้าเดียว

- [x] T5.1 สร้าง `catalog/production-tools.yaml` เก็บชื่อ, CLI, input schema, output, สถานะ gate, runtime ที่ต้องมี (Blender, Unity, FreeCAD, FFmpeg) และ repo ที่เป็นเจ้าของ
- [x] T5.2 ทำ `book-production doctor` ตรวจว่าเครื่องมี runtime ไหน version อะไร และ tool ไหนรันได้
- [x] T5.3 เพิ่ม check ใน `task docs:check` ว่า catalog ตรงกับ `runtime-boundaries.yaml` และ entry point จริง
- [x] T5.4 ทำหน้า dashboard รวม tools ทั้งหมดที่แสดงสถานะ gate ของแต่ละตัว

### T6. ต่อ tool เข้าเป็น pipeline ข้าม domain

งานที่ทำให้ tool แต่ละตัวมีค่ามากขึ้นเมื่อใช้ร่วมกัน:

- [x] T6.1 3D Scene → PNG → Graphics Template Batch เพื่อทำภาพสินค้าหลายขนาด
- [x] T6.2 Video transcript → Documents ทำสรุปหรือ subtitle และ Video → Audio master
- [x] T6.3 Data pipeline → Data Viz → Spreadsheet → Documents ทำเป็นรายงานรายเดือน

ข้อกำหนด: ส่งต่องานกันผ่าน versioned artifact contract และไม่ย้าย ownership ของ code

## Phase 3 — ลองกับงานจริงเพื่อหาเงิน (สัปดาห์ที่ 3–5)

### T7. เลือก 2 tool ไปใช้กับ venture ที่มีอยู่แล้ว

| Tool | Venture ที่เข้ากัน | สิ่งที่จะส่งมอบ |
| --- | --- | --- |
| Graphics Batch + 3D Scene | `ventures/print-on-demand`, `etsy`, `shopee-affiliate` | ภาพสินค้าและ mockup ครั้งละ 30–100 ชิ้น |
| Video + Audio | `ventures/content-creator` | clip + subtitle + เสียงที่ master แล้ว |
| Documents + Spreadsheet + Data Viz | `freelance-dev`, ลูกค้าใน `clients/` | รายงานหรือใบแจ้งหนี้อัตโนมัติ |

- [x] T7.1 เลือก 2 คู่จากตาราง (**ต้องให้เจ้าของตัดสินใจ**) — เจ้าของเลือกครบทั้ง 3 คู่ (มากกว่าที่แผนตั้งไว้ บันทึกตามการตัดสินใจ)
- [x] T7.2 กำหนดตัววัดของ pilot: เวลาต่อชิ้นเทียบกับทำมือ, จำนวนครั้งที่ต้องแก้ด้วยมือ และต้นทุนจริง — ดู `ventures/production-tools-pilot-plan-2026-09-26.md` (Pilot 3 เริ่มไม่ได้จนกว่า T3.3+T4 ผ่าน)
- [ ] T7.3 ใช้งานจริง 1 รอบ แล้วบันทึกผลลง `ventures/<name>/`
- [ ] T7.4 ทำ offer หรือ pricing หน้าเดียวจากผลที่ได้ (**ต้องให้เจ้าของอนุมัติก่อนส่งออกไปภายนอก**)

## Phase 4 — เปิดใช้ AI แบบคุมงบ (หลัง Phase 3)

### T8. Documents V1B (ใช้ AI เป็นตัวแรก)

- [x] T8.1 ทำ fake adapter และชุดทดสอบ 60 เคส: structured 20, brief ชัดเจน 20, brief กำกวม 10, adversarial 10 โดยแบ่งไทยและอังกฤษอย่างละครึ่ง
- [x] T8.2 ให้ LLM แค่อ่าน brief แล้วแปลงเป็น fields ส่วนยอดรวมให้ `recipe.py` คำนวณ ถ้าขาด field ให้คืน `needs_input`
- [ ] T8.3 ส่ง request ผ่าน Central AI Gateway โดยจองงบแบบ atomic จำกัด ≤ 2 calls/job และ ≤ 8k input tokens
- [ ] T8.4 benchmark เลือก model slot แล้วค่อย pin version

ข้อกำหนด: ต้องมี rate card และเพดานงบก่อน ถ้ายังไม่มีห้ามเปิด paid provider

### T9. ใช้รูปแบบเดียวกันกับ tool อื่น

เช่น brief → GameIR/SceneIR/PartIR หรือ brief → campaign copy — ทำเมื่อ T8 ผ่านแล้วเท่านั้น

## สถานะการปฏิบัติ — อัปเดต 2026-09-26

- **T1 BIM Quantity Takeoff V1A: เสร็จสิ้น** ใน nested repo `book-production-platform` บน `main` ต่อจาก `3d1efc9` ถึง `e0ea113` (8 commits: `7946f01`…`e0ea113`) — preflight จริงผ่าน FreeCADCmd (FreeCAD 1.1.3 / Python 3.11 / IfcOpenShell 0.8.4), benchmark ผ่าน 20/20 เคสด้วย `model_calls_total=0` และ `monetary_cost=unknown`, replay ตรวจ integrity ผ่าน, และ `scripts/check.py` ผ่าน (585 passed / 4 skipped เดิม + ruff + mypy strict — parent รันยืนยันเอง) หลักฐานเต็มอยู่ใน platform plan section 18; `push_skipped_no_remote` (nested repo ไม่มี origin)
- **T2 Documents V1A.1: เสร็จสิ้น** — root commit `1dcf44b9` (7 ไฟล์, +328/−5 ภายใต้ `services/document-production-api/`): ตัวหลักของ T2.1–T2.3 พบว่ามีอยู่บน main แล้ว จึงเติมช่องว่างคือการทดสอบ 2 process จริงด้วย subprocess, concurrency/crash ระดับ job, ชุดทดสอบ 0/1/100/101 แถว + เอกสารหลายหน้า + ข้อความไทย, smoke command ที่ไม่ต้องตั้งค่า provider และ idempotency conflict → exit 5; pytest 83 passed (จากเดิม 69), ruff + mypy ผ่าน, smoke รันจริง 2 รอบ (fresh + replayed) — คงเหลือ: visual golden evidence แบบตาเปล่ายังไม่ทำ
- **T3 Audio: pilot-ready แบบมีเงื่อนไข (2026-09-27)** — ขอบเขตที่ยืนยัน: เสียงพูดไทยจาก voice recorder (ต้นทางสะอาด อุปกรณ์เดียว); ยังไม่ยืนยันเสียงมือถือ/ห้องเสียงก้อง/เสียงรบกวนสูง; เจ้าของฟัง EDGES แล้วไม่มีคลิก และเลือกเก็บคลิปประชุมใน `.runtime/` เป็น regression material; ข้อค้างก่อนต่อ Video ปิดแล้วใน recipe `audio-v1c` (nested commit `a879078`): ชดเชย `alimiter` latency (lag 219 → 0 samples) + fade 5 ms เฉพาะขอบที่ trim ตัด, suite 147 passed / 1 skipped, re-render ชุดไทย accepted 3/3 โดยสถิติ limiter เท่ากับ v1b; backup: `book-production-platform` และ `book-graphics-production` มี private origin และ sync กับ local แล้ว (ตรวจ 2026-09-27) — ประวัติเดิม: — T3.1 ผ่าน: full suite ด้วย FFmpeg 9.0.1 จริงได้ 142 passed / 1 skipped (skip เดียวเป็นข้อจำกัดสิทธิ์ symlink ของ Windows, ยืนยันเหตุผลแล้ว) ที่ nested commit `c31eaf0`; T3.2 เตรียมแล้ว: ชุดฟัง 8 track ผ่าน pipeline จริง 8/8 accepted + verify ผ่าน ที่ `book-audio-production/.runtime/listening-set-20260926/` พร้อม checklist ไทย `docs/engineering/2026-09-26-audio-listening-checklist.md` (มีตัวเลข LUFS/dBTP pre-filled) — ข้อจำกัด: ยังไม่มี track เสียงพูดไทยเพราะเครื่องไม่มี Thai TTS voice เจ้าของควรเตรียมไฟล์เสียงไทยจริงเพิ่มก่อนตัดสินความชัดของสระ/วรรณยุกต์ และพบว่าต้นทางเสียงร้อน/ไดนามิกสูงถูก QA ปฏิเสธ (loudnorm dynamic พลาดเป้า ±0.5 LU) อาจต้องเปิด fix ticket หลังฟัง
- **T4 Video Studio: ยังรอเจ้าของลงมือ** (smoke บนเครื่อง/Windows user ใหม่ + คลิปไทย 30–60 นาที)
- **T5 รวมทางเข้าเดียว: เสร็จสิ้น** (root commit `d255bccc`) — `catalog/production-tools.yaml` ได้ **15 tools** ไม่ใช่ 13 ตามคะเนเดิม (สอง audio เป็นคนละ product คนละ repo, game นับเพราะมี evidence จริง, Documents V1A.1+V1C รวมเป็น entry เดียว): สถานะ 11 accepted / 1 pilot-ready (documents — ค้าง visual golden) / 3 needs-human-gate (video-studio, audio-batch, audio-master); guard `production-tools:check` ต่อเข้า `docs:check` แล้ว (ตรวจ schema, repo/entry point มีจริง, runtime vocabulary, ownership กับ `runtime-boundaries.yaml`, drift ของ dashboard); `book-production doctor` ที่ nested commit `fc47c88` รันจริงแล้วรายงาน runtime พบ 4/5 พร้อมเวอร์ชัน + unity (ไม่ระบุเวอร์ชัน) และ product พร้อมใช้ 8/8; หน้า dashboard ใหม่ `dashboard/PRODUCTION-TOOLS.md` generate อัตโนมัติจาก catalog
- **T7 pilot: นิยามเสร็จ รอ run จริง (T7.3)** — เจ้าของเลือกคู่ pilot ครบทั้ง 3 คู่; ตัววัดและเกณฑ์ผ่าน (≥3 เท่าเร็วกว่ามือ, rework_rate ≤ 0.5) อยู่ใน `ventures/production-tools-pilot-plan-2026-09-26.md` — Pilot 1 (Graphics+3D → print-on-demand) กับ Pilot 2 (Documents+Spreadsheet+Data Viz → freelance-dev) เริ่ม run จริงได้ทันที, Pilot 3 (Video+Audio → content-creator) รอ T3.3+T4; ทุก pilot ห้ามส่งออกภายนอกก่อนอนุมัติ T7.4
- **T6 pipeline ข้าม domain: เสร็จสิ้น** — ทุกสายส่งงานกันผ่าน versioned artifact contract และไม่ย้าย ownership ของ code:
  - **T6.1** `book-production pipeline scene-to-graphics` ใน nested repo (commit `b19c2d3`, contract `scene-render-batch.v1`) — e2e จริงด้วย Blender 5.2.2 ได้ภาพ 1024→1080x1080/1080x1350/1080x1920, model_calls=0, rerun ได้ replayed โดยไม่เขียนทับ; platform gate 619 passed / 4 skipped + ruff + mypy strict (parent ยืนยันเอง)
  - **T6.2** ใน root repo (commit `b4f4edd8`): contract `transcript-artifact.v1` + SRT/VTT emitters + Video→Audio extractor (wav pcm_s24le ออก `audio-batch.v1.json` ตรง schema ของ audio tool) + transcript→documents; documents service เพิ่ม template `transcript@1` (verbatim มี timestamp) — documents 98 passed / video-postproduction 62 passed + e2e จริงถึง DOCX/PDF; **การสรุปด้วย AI เลื่อนไป T8/Phase 4 อย่างชัดเจน**
  - **T6.3** orchestrator ใหม่ `services/production-pipelines/` (commit `a9ac4f39`, contracts `pipeline-report-config.v1`/`pipeline-report.v1`) — คำสั่ง `python -m production_pipelines monthly_report` รันจริงครบ 4 stages (data → viz → spreadsheet → documents) ด้วย sha256 ทุก artifact, atomic publish, no-overwrite (rerun exit 5); documents service เพิ่ม template `report@1` — 33+111 tests passed, ruff/mypy ผ่าน, ตรวจตัวเลข aggregate ตรงใน DOCX/PDF (parent ยืนยันเอง); ข้อจำกัด: การฝังไฟล์ภาพกราฟ binary ในเอกสารต้องขยาย `document_ir` (shared contract) จึงอ้างอิงด้วย sha256 ก่อน
- **T3.2 เสริม — เสียงพูดไทยจริง: ติดข้อจำกัด QA ของ tool (เปิด fix ticket แล้ว)** — ตัดเสียงพูดไทยจริงจากไฟล์ประชุมของเจ้าของ 3 ช่วง (75 วินาที, ทดสอบทั้งแบบเต็มและ trim 35 วินาที) รันผ่าน pipeline จริงแล้ว **QA ปฏิเสธทั้งหมด**: ต้นทางเบา (-21.6 ถึง -23.8 LUFS) ต้องบูสต์ +6–8 dB ทำให้ two-pass `linear=true` ไม่ไหว (ถูก cap ด้วย true peak) จน fallback เป็น dynamic แล้วพลาด tolerance ±0.5 LU — บันทึกหลักฐานและทางเลือกแก้ไว้ใน checklist (nested commit `132e4f3`); T3.3 ของเจ้าของยังฟังชุด 8 track สังเคราะห์ได้ตามปกติ ส่วนเสียงไทยรอแก้ tool
- **T8.1/T8.2 รากฐาน Documents V1B: เสร็จสิ้น** (root commit `793be754`) — interface `brief-adapter.v1` + fake adapter แบบ scripted + ชุดทดสอบ 60 เคสตามสัดส่วน roadmap ครบ (ไทย/อังกฤษครึ่งต่อครึ่ง, บังคับด้วย composition test) — invariant สำคัญ: ยอดรวมมาจาก `recipe.py` เท่านั้น (adapter แอบใส่ totals → `derived_total_forbidden`), เคสกำกวมจบที่ `needs_input` ไม่เดาค่า, เคส adversarial ไม่มีทาง accepted เงียบ ๆ, และ canary พิสูจน์ว่า raw brief ไม่หลุดลง telemetry/manifest/log; real-LLM adapter เป็น stub ที่ fail-closed (`provider_not_configured`, ไม่แตะ network/คีย์/gateway — ทดสอบด้วย monkeypatch socket + static source check); suite รวม **184 passed** + ruff + mypy strict (parent ยืนยันเอง) — T8.3/T8.4 ยังถูกกักตามเงื่อนไข rate card + เพดานงบ
- **T3-fix (เสียงจริง): แก้แล้ว** — จาก fix ticket ที่เจ้าของสั่ง: engine เพิ่ม near-miss correction (linear พลาดเป้า loudness/TP → re-render จากต้นทางด้วย volume + alimiter margin ปรับตัว 0.5→3.0 dB ใต้เพดาน, สูงสุด 6 ครั้ง, QA tolerance ไม่เปลี่ยน, crest เกิน ~24 dB ยัง fail closed) recipe bump เป็น `audio-v1b` (nested `2c49af1`) — regression คลิปไทยจริง 3/3 accepted (I −16.2/−15.9/−16.1, TP −1.4, ตรวจอิสระด้วย ebur128), suite 144 passed / 1 skipped; ผลข้างเคียงที่ตั้งใจ: ต้นทางร้อน/dynamic สูงที่เคยถูกปฏิเสธจะ accepted ได้ด้วยกลไกเดียวกัน
- สถานะ `task docs:check` ของ root: check ที่เกี่ยวกับงานนี้ผ่านหมด (production-tools, catalog, entity-graph, links, numbers, structure) แต่ยังล้มเหลว 2 check ที่เป็นปัญหาเดิมก่อนงานนี้ — `book-dev:structure:check` (nested repo 2 ตัวยังไม่มี origin remote: book-graphics-production, book-production-platform — ส่วน book-audio-production ตั้ง origin เป็น github.com/bookchaowalit/book-audio-production (private) แล้วตามการตัดสินใจของเจ้าของ พร้อม inventory 333→334) และ `platforms:check` (13 issues จาก registry เดิม)

## พักไว้ก่อน (deferred)

GIS, Electronics, Robotics, Manufacturing, CAD Assembly, BIM Clash, Game Dialogue — ทำหลังจากมีหลักฐานว่าคนต้องการจาก Phase 3

## ลำดับที่แนะนำ

T1 กับ T2 ทำขนานกันได้เพราะอยู่คนละ repo → T3 กับ T4 ต้องให้เจ้าของลงมือ → จากนั้นทำ T5 และ T7

**จุดที่ต้องให้เจ้าของตัดสินใจ:** T4 (ทดสอบด้วยตัวเอง), T7.1 (เลือกคู่ venture), T7.4 (อนุมัติ offer ก่อนส่งออกภายนอก), T8 (ต้องมี rate card และเพดานงบก่อน)
