---
title: Book Production Platform — Product Requirements (PRD)
doc_id: BPP-DOC-01
doc_type: prd
version: 0.1
date: 2026-10-08
status: ร่างเพื่อพิจารณา
owner: Product owner (Book Dev)
related: 00-overview.md, 00-product-card.md, 02-architecture.md, 05-srs.md, 08-rtm.md
---

# Book Production Platform — Product Requirements (PRD)

> ขอบเขต: เน้นสาย Graphics (`graphics-v1a`) เป็นลำดับแรก และบันทึกสายอื่นของ platform ในระดับขอบเขต ไม่มีตัวเลขรายได้ที่ยืนยันในเอกสารนี้

## 1. ปัญหาและเป้าหมาย

### 1.1 ปัญหา

| # | ปัญหา | ผลกระทบ |
|---|---|---|
| PB-1 | งานโพสต์โซเชียลเป็นชุด (10–30 โพสต์/เดือน) ทำด้วยมือใน Figma/Canva ช้าและหน้าตาไม่สม่ำเสมอ | รับงานได้น้อย คุณภาพแกว่ง |
| PB-2 | เครื่องมือ generative ล้วนผลไม่ซ้ำ ตรวจย้อนไม่ได้ และอาจแต่งข้อความเอง | ส่งงานผิดข้อความลูกค้า, ไม่มี provenance |
| PB-3 | template ตายตัวทำให้งานของลูกค้าหลายรายหน้าตาเหมือนกัน | ขายเป็นงานออกแบบได้ยาก |
| PB-4 | ภาษาไทย (วรรณยุกต์, การตัดคำ) มักแตกในเครื่องมือทั่วไป | งานลูกค้าไทยต้องแก้มือ |

### 1.2 เป้าหมาย

| ID | เป้าหมาย | ตัวชี้วัด | ค่าเป้าหมาย / หลักฐาน |
|---|---|---|---|
| G-1 | ผลิตชุดกราฟิกได้เร็ว | เวลา render campaign | วัดได้ 10 โพสต์ × 2 ขนาด = 21 วินาที บน 4 workers (`GRAPHICS-THEMES-GUIDE.md`) |
| G-2 | คุณภาพการจัดวางพอส่งลูกค้า | EVAL-GFX-compose14: ได้แบบ ≥ 85% ของงาน, คะแนนเฉลี่ย ≥ 70, คู่ซ้ำ ≤ 10% | ผ่าน: ไม่ใช้ AI 10/10 คะแนน 89.4; AI director v2 9/10 คะแนน 94.5 |
| G-3 | ไม่แต่งข้อความเอง | ข้อความที่ AI แต่งขึ้นเอง (campaign-brief) | 0 ในรอบ 2026-10-07 |
| G-4 | ตรวจซ้ำได้ | replay ได้ผล `replayed`; artifact ที่ถูกแก้ถูกปฏิเสธ | มี test (`test_jobs.py`, `test_trace_manual.py`) |
| G-5 | สร้างรายได้จากงานบริการกราฟิก | จำนวนงานที่ส่งมอบ / รายได้ | **[ต้องยืนยัน]** ยังไม่มีข้อมูล |

## 2. ผู้ใช้ / persona

| ID | Persona | เป้าหมาย |
|---|---|---|
| P-OWN | เจ้าของ/ผู้ให้บริการงานผลิต | รับ brief → ส่งไฟล์ได้เร็ว ตรวจได้ |
| P-AGT | AI agent ใน Solo Empire | เรียกเครื่องมือผ่าน CLI/catalog แบบมี schema และ error code คงที่ |
| P-CLI | ลูกค้า SME ที่ซื้อแพ็กเกจโพสต์โซเชียล | ได้ไฟล์ตรงแบรนด์ ข้อความถูกต้อง หลายทางเลือก — **[สมมติฐาน]** จาก `graphic-social-pack.md` |
| P-DEV | นักพัฒนาที่เพิ่มธีม/motif | เพิ่มหน้าตาใหม่ด้วย JSON ไม่ต้องแก้โค้ด |

## 3. ขอบเขต

### 3.1 In scope

- Graphics: batch จาก JSON, ธีมแบบข้อมูล 14 ธีม, design override, art style, compose เลย์เอาต์ต่องาน, campaign, โลโก้, รูป/ตัดพื้นหลัง, QA, export print, trace/manual, review/feedback, HTTP API
- AI ที่มีขอบเขต: brief adapter, design editor, art director, campaign-brief, style designer — ทุกตัวผ่าน Central AI Gateway, มี shadow eval และต้องได้รับอนุมัติก่อน live
- Pipeline scene-to-graphics
- สายอื่นของ platform (scene3d, spreadsheet, data, CAD, audio, game, BIM) และ `doctor` — คงไว้ตามที่มี

### 3.2 Out of scope

- Editor แบบ free-form และการทำงานร่วมกันแบบ realtime (`docs/parity/graphics.md` ระบุ "not an editor")
- สร้างภาพหรือ geometry จาก prompt
- Print CMYK, bleed, crop marks (missing, P1 ใน parity)
- โฮสต์เป็นบริการสาธารณะ/รับไฟล์จากผู้ใช้ที่ไม่น่าเชื่อถือ (README: "ยังไม่ใช่ OS sandbox")

## 4. ฟีเจอร์หลักพร้อมลำดับความสำคัญ

| ID | ฟีเจอร์ | Priority | สถานะ | FR |
|---|---|---|---|---|
| F-01 | Graphics batch + replay + idempotency | Must | มีแล้ว | FR-01 |
| F-02 | ธีมแบบข้อมูลและ `make` | Must | มีแล้ว (14 ธีม) | FR-02 |
| F-03 | Compose เลย์เอาต์ต่องาน + คะแนนดีไซน์ + กันซ้ำ | Must | มีแล้ว | FR-06 |
| F-04 | Campaign หลายโพสต์ | Must | มีแล้ว | FR-07 |
| F-05 | QA (ข้อความล้น, ทับ, contrast, safe zone, ภาษาไทย) | Must | มีแล้ว | FR-10 |
| F-06 | Export print (PNG scale, raster PDF) | Should | มีแล้ว | FR-11 |
| F-07 | Review/feedback loop | Should | มีแล้ว | FR-13 |
| F-08 | Trace, history, MANUAL-STEPS | Should | มีแล้ว | FR-12 |
| F-09 | Design override + AI design editor | Should | มีแล้ว (AI ต้อง live) | FR-03, FR-04 |
| F-10 | Art style + AI style designer | Should | มีแล้ว | FR-08 |
| F-11 | รูป, cutout, โลโก้ | Should | มีแล้ว | FR-09 |
| F-12 | Brief adapter / campaign-brief | Could | shadow | FR-05, FR-07 |
| F-13 | HTTP API | Could | มีแล้ว ไม่ได้ deploy | FR-14 |
| F-14 | Scene-to-graphics pipeline | Could | มีแล้ว (ต้องมี Blender) | FR-15 |
| F-15 | สายอื่นของ platform + doctor | Could | มีแล้ว ไม่ใช่โฟกัส | FR-16 ถึง FR-23 |

## 5. ตัวชี้วัดความสำเร็จ

| ID | ตัวชี้วัด | แหล่งวัด | เป้าหมาย |
|---|---|---|---|
| M-1 | อัตราแบบที่ผ่าน QA ต่องาน compose | `compose-shadow` | ≥ 85% |
| M-2 | คะแนนดีไซน์เฉลี่ย | `compose-shadow`, `campaign-summary.json` | ≥ 70 |
| M-3 | อัตราที่คน "ไม่ผ่าน" ใน review | `feedback-report` | **[ต้องยืนยัน]** ยังไม่มี baseline |
| M-4 | ต้นทุน AI ต่อแบบ | ledger ของ gateway | ~$0.003/แบบ (art director, ตามบันทึก 2026-10-06) |
| M-5 | งานลูกค้าที่ส่งผ่าน platform ต่อไตรมาส | บันทึกงาน book-sales | **[ต้องยืนยัน]** — เงื่อนไขคง verdict `invest` ตาม card |

## 6. Roadmap / phase

| Phase | งาน | สถานะ |
|---|---|---|
| R0 (ปัจจุบัน) | ยืนยัน CI run แรกเขียว (P0), รัน browser test บน CI | ค้าง |
| R1 | ส่งงานกราฟิกลูกค้าจริงผ่าน runbook และเก็บ feedback | **[ต้องยืนยัน]** |
| R2 | Gap P1 ใน parity: canvas ขนาดอิสระ/A4, print CMYK/bleed (บางรายการอาจล้าสมัยแล้ว — ดู R-4) | วางแผน |
| R3 | Windows CI job, archive fork `book-graphics-production` | backlog P1/P2 |
| R4 | จัดลำดับสายอื่นตาม demand และพักสายที่ไม่มีผู้ใช้ | ตาม card §8 |

## 7. ข้อสมมติฐานและความเสี่ยง

| ID | ประเภท | รายละเอียด | การจัดการ |
|---|---|---|---|
| A-1 | สมมติฐาน | งานกราฟิกชุดโพสต์มี demand ที่จ่ายเงิน **[สมมติฐาน]** | ยืนยันด้วยงานจริงใน R1 |
| A-2 | สมมติฐาน | เครื่องที่ทำงานมีฟอนต์ที่ pin ไว้และ Chrome/Edge หรือ resvg | `doctor`, runbook §เตรียมเครื่อง |
| R-1 | ความเสี่ยง | ขอบเขต 8 สายกระจายแรงผู้ดูแลคนเดียว | โฟกัส graphics, พักสายไม่มี demand |
| R-2 | ความเสี่ยง | คะแนนดีไซน์อัตโนมัติไม่ตรงกับสายตาคน | review/feedback loop, คนต้องเปิดดูก่อนส่งทุกครั้ง |
| R-3 | ความเสี่ยง | ต้นทุน/latency ของ AI director (~57 วินาที, ~10.6k token ต่องาน) | เส้นทางไม่ใช้ AI เป็นค่าเริ่มต้น |
| R-4 | ความเสี่ยง | เอกสาร parity บางส่วนล้าสมัย (ระบุ 3 template, ไม่มี landscape/weight) | ปรับ `docs/parity/graphics.md` |
| R-5 | ความเสี่ยง | ใบอนุญาตฟอนต์/รูป | ใช้ฟอนต์ OFL, รูป eval เป็น CC0 พร้อม `CREDITS.md` |

## การอนุมัติ

| บทบาท | สถานะ | วันที่ | ลงนาม |
|---|---|---|---|
| Product owner (Book Dev) | รอตรวจ | — | [ต้องยืนยัน] |

## แหล่งข้อมูล

- `README.md` (Graphics V1A, AI brief adapter, Pipeline)
- `docs/GRAPHICS-THEMES-GUIDE.md` §5D (compose, campaign, eval results, approval 2026-10-06)
- `docs/parity/graphics.md`, `docs/UPGRADE-PLAN.md`
- Solo Empire: `projects/marketing/growth/book-sales/templates/offers/graphic-job-runbook.md`, `graphic-social-pack.md`, `catalog/production-tools.yaml`
- `DOCPACK/00-product-card.md`
