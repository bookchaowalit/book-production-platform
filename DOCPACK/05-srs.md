---
title: Book Production Platform — Software Requirements (SRS)
doc_id: BPP-DOC-05
doc_type: srs
version: 0.1
date: 2026-10-08
status: ร่างเพื่อพิจารณา
owner: BA (Book Dev)
related: 01-prd.md, 02-architecture.md, 06-ux-flows.md, 07-test-uat.md, 08-rtm.md
---

# Book Production Platform — Software Requirements (SRS)

## 1. บทบาทและสิทธิ์

ระบบไม่มีการ login; สิทธิ์ขึ้นกับการเข้าถึงเครื่องและไฟล์ config

| บทบาท | ทำได้ | ข้อจำกัด |
|---|---|---|
| Operator | รันทุกคำสั่ง CLI, review, export | AI live ได้เฉพาะเมื่อ config เป็น `mode: live` |
| เจ้าของ (approver) | เปลี่ยน provider config เป็น `live`, อนุมัติ rate card/caps | ต้องดูผล shadow eval ก่อน |
| AI agent | เรียก CLI ตาม `catalog/production-tools.yaml` | ไม่ควรเปลี่ยน `mode` เอง **[สมมติฐาน]** |
| HTTP client (local) | `GET /health`, `POST /v1/graphics/batches` | ไม่มี auth — localhost เท่านั้น |
| AI model | เสนอค่าภายใน registry/controls เท่านั้น | engine ตรวจทุกค่า; ข้อความต้องคัดลอกจาก brief |

## 2. User story และ acceptance criteria

| ID | Story | Acceptance criteria |
|---|---|---|
| US-01 | ในฐานะ operator ฉันต้องการสร้างหลายภาพจาก JSON ครั้งเดียว | `graphics batch` รับ ≤ 100 items, ได้ SVG+PNG ต่อ item ใน `<output>/<batch_id>/`; รันซ้ำด้วย key เดิมได้ `replayed` ไม่เขียนไฟล์ใหม่ |
| US-02 | ในฐานะ operator ฉันต้องการทำภาพจากธีมด้วยคำสั่งเดียว | `graphics make --theme <id> --set slot=...` ได้ภาพทุกขนาดที่ธีมรองรับ; `graphics themes` แสดงขนาด slot และ brief words |
| US-03 | ในฐานะ operator ฉันต้องการปรับดีไซน์โดยไม่แก้ธีม | `design` block (style/palette/layers/items) ใช้ได้เฉพาะ target ที่มีจริง; `graphics controls` แสดงค่าปัจจุบัน; `--patch` ใช้ได้โดยไม่ใช้ AI |
| US-04 | ในฐานะ operator ฉันต้องการสั่งแก้ด้วยภาษาคน | `graphics edit` ทำงานเฉพาะ `mode: live`; target ไม่ชัด/ทำไม่ได้ → `needs_input`; ข้อความใหม่ต้องคัดลอกตรงจากคำสั่ง |
| US-05 | ในฐานะ operator ฉันต้องการเปลี่ยน brief ข้อความอิสระเป็น batch | `graphics brief` ใช้เฉพาะ template ใน registry; ค่านอก registry → `rejected`; ข้อความล้นหลัง repair หนึ่งครั้ง → `needs_input` (`text_overflow`) |
| US-06 | ในฐานะผู้ให้บริการ ฉันต้องการเลย์เอาต์ใหม่ที่ไม่ซ้ำงานเก่า | `graphics compose --count N` คืน N แบบที่ผ่าน QA ทุกขนาด, คะแนนดีไซน์ 0–100, คล้ายงานเก่า > 80% ไม่ถูกใช้ |
| US-07 | ในฐานะผู้ให้บริการ ฉันต้องการทำทั้งแคมเปญในครั้งเดียว | `compose-campaign --plan` รองรับ ≤ 200 โพสต์, ลุคเดียวกัน, เลย์เอาต์ไม่ซ้ำกันเอง, `--dry-run` ไม่ render, สรุปใน `campaign-summary.json` |
| US-08 | ในฐานะผู้ให้บริการ ฉันต้องการเปลี่ยน brief ลูกค้าเป็นแผน campaign | `campaign-brief` ไม่แต่งข้อความ; สิ่งที่ขาดถูกถามใน `questions`; brief โจมตีถูกปฏิเสธโดยไม่เรียก AI |
| US-09 | ในฐานะผู้ให้บริการ ฉันต้องการใส่โลโก้และรูปลูกค้า | `--logo` วางมุมเดียว ~7% ของด้านสั้น, ตรวจ `logo_crowded`/`logo_covered`; รูปที่ตัดพื้นหลังไม่ได้ใช้กรอบแทน |
| US-10 | ในฐานะผู้ให้บริการ ฉันต้องการไฟล์พิมพ์ | `graphics export --scale --dpi` ได้ PNG ความละเอียดสูง + PDF raster |
| US-11 | ในฐานะ operator ฉันต้องการทำซ้ำด้วยมือหรือตรวจย้อน | ทุก batch ใหม่มี `trace.json`, `ir.json`, `MANUAL-STEPS.md`, บรรทัดใน `history.jsonl`; `production_trace verify` ตรวจ hash |
| US-12 | ในฐานะเจ้าของ ฉันต้องการให้ระบบเรียนรู้จากสายตาคน | `review` เขียน `review.html`; `feedback` เก็บผล; แนวที่ไม่ผ่าน ≥ 3 ครั้งถูกหักคะแนนสูงสุด 15 |
| US-13 | ในฐานะ AI agent ฉันต้องการ error ที่อ่านได้ด้วยโปรแกรม | ทุกคำสั่งคืน JSON และ stable error code; exit code `2` สำหรับ error ที่กำหนด |

## 3. Functional requirements

### 3.1 สาย Graphics

| ID | Requirement | Priority | Phase | สถานะ |
|---|---|---|---|---|
| FR-01 | Batch: JSON + brand tokens → SVG/PNG, idempotency, โฟลเดอร์ต่อ batch, replay, `expand_variants` | Must | V1A | มีแล้ว |
| FR-02 | ธีมแบบข้อมูล (`graphic-theme.v1`) 14 ธีม, motif ที่ใช้ซ้ำ, คำสั่ง `themes`/`make` | Must | V1A | มีแล้ว |
| FR-03 | Design override (`style`, `palette`, `layers`, `items`), `controls`, `--patch` | Should | V1A | มีแล้ว |
| FR-04 | AI design editor `edit` + `edit-shadow` (EVAL-GFX-edit24, edit-art20) | Should | V1B | มีแล้ว; live ตาม config |
| FR-05 | Brief adapter `brief` + `brief-shadow` (EVAL-GFX-brief40) | Could | V1B | มีแล้ว; shadow |
| FR-06 | Compose เลย์เอาต์ต่องาน 13 แนว, คะแนนดีไซน์, คลังกันซ้ำ, AI art director ทางเลือก, `compose-shadow` | Must | V1A+ | มีแล้ว; director live 2026-10-06 |
| FR-07 | Campaign: `compose-campaign`, `campaign-brief`, `campaign-brief-shadow` | Must | V1A+ | มีแล้ว; campaign-brief shadow |
| FR-08 | Art style pack: `styles`, `--art-style`, `style-create` (AI) | Should | V1A+ | มีแล้ว |
| FR-09 | รูป, cutout (rembg + cache), สติกเกอร์/collage, โลโก้ | Should | V1A+ | มีแล้ว |
| FR-10 | QA: fit text, ไม่ทับ, ไม่หลุดขอบ, WCAG AA, story safe zone, ตัดคำไทย, จัดกึ่งกลางตามหมึก | Must | V1A | มีแล้ว |
| FR-11 | Export print: `export --scale --dpi` PNG + PDF raster | Should | V1A | มีแล้ว (ไม่มี CMYK/bleed) |
| FR-12 | Trace/history/MANUAL-STEPS + `production_trace history/manual/verify` | Should | V1A | มีแล้ว |
| FR-13 | Review/feedback: `review`, `feedback`, `feedback-report` | Should | V1A+ | มีแล้ว |
| FR-14 | HTTP API: `GET /health`, `POST /v1/graphics/batches` (201, 409 idempotency conflict, 422 rejected) | Could | V1A | มีแล้ว |
| FR-15 | Pipeline `scene-to-graphics` ผ่าน contract `scene-render-batch.v1` | Could | V1A | มีแล้ว (ต้องมี Blender) |

### 3.2 ระดับ platform

| ID | Requirement | Priority | สถานะ |
|---|---|---|---|
| FR-16 | `doctor` ตรวจ runtime offline, JSON นิ่ง, exit code 0/2 | Should | มีแล้ว |
| FR-17 | Scene3d: `scene compose`, `scene benchmark` (20 กรณี), brief adapter | Could | มีแล้ว |
| FR-18 | Spreadsheet: workbook จาก WorkbookIR หรือ CSV/XLSX + HTTP API | Could | มีแล้ว |
| FR-19 | Data SQL/Pipeline builder + HTTP API | Could | มีแล้ว |
| FR-20 | CAD parametric part (FreeCAD 1.1.3 pinned) | Could | มีแล้ว |
| FR-21 | Audio master (FFmpeg) | Could | มีแล้ว |
| FR-22 | Game scene prototype (Unity) + export player | Could | มีแล้ว |
| FR-23 | BIM quantity takeoff + `bim cost` | Could | มีแล้ว |
| FR-24 | Fork parity check `scripts/graphics_fork_parity.py` | Should | มีแล้ว |

## การอนุมัติ

| บทบาท | สถานะ | วันที่ | ลงนาม |
|---|---|---|---|
| BA (Book Dev) | รอตรวจ | — | [ต้องยืนยัน] |
| Product owner | รอตรวจ | — | [ต้องยืนยัน] |

## แหล่งข้อมูล

- `src/book_production_platform/cli.py`, `src/book_production_platform/graphics_production/cli.py`, `main.py`
- `README.md` (ทุกสาย), `docs/GRAPHICS-THEMES-GUIDE.md`
- `catalog/production-tools.yaml` (Solo Empire)
