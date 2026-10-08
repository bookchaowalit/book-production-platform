---
title: Book Production Platform — Requirements Traceability Matrix
doc_id: BPP-DOC-08
doc_type: rtm
version: 0.1
date: 2026-10-08
status: ร่างเพื่อพิจารณา
owner: BA + QA (Book Dev)
related: 01-prd.md, 05-srs.md, 06-ux-flows.md, 07-test-uat.md
---

# Book Production Platform — Requirements Traceability Matrix

## 1. ตาราง FR ↔ US ↔ พื้นผิว ↔ TC ↔ หลักฐาน

| FR | ฟีเจอร์ (PRD) | US | พื้นผิว | TC | สถานะหลักฐาน |
|---|---|---|---|---|---|
| FR-01 | F-01 | US-01 | SC-02 | TC-01, TC-02 | อัตโนมัติ — ผ่านรอบ 2026-10-08 |
| FR-02 | F-02 | US-02 | SC-01, SC-02 | TC-03 | อัตโนมัติ — ผ่าน |
| FR-03 | F-09 | US-03 | SC-05 | TC-04 | อัตโนมัติ — ผ่าน |
| FR-04 | F-09 | US-04 | SC-05 | TC-05 | อัตโนมัติ (stub) — ผ่าน; eval จริงตาม config |
| FR-05 | F-12 | US-05 | SC-02 | TC-06 | อัตโนมัติ (stub) — ผ่าน; shadow |
| FR-06 | F-03 | US-06 | SC-03 | TC-07 | อัตโนมัติ + eval compose14/real14 |
| FR-07 | F-04, F-12 | US-07, US-08 | SC-04 | TC-08 | อัตโนมัติ + eval campaign-brief10 |
| FR-08 | F-10 | US-03 | SC-01 | TC-09 | อัตโนมัติ — ผ่าน |
| FR-09 | F-11 | US-09 | SC-03, SC-04 | TC-10 | อัตโนมัติ — ผ่าน |
| FR-10 | F-05 | US-01, US-06 | ทุกพื้นผิวที่ render | TC-11 | อัตโนมัติ — ผ่าน; golden skip |
| FR-11 | F-06 | US-10 | SC-08 | TC-12, TC-U2 | อัตโนมัติ — ผ่าน; UAT ค้าง |
| FR-12 | F-08 | US-11 | SC-10 | TC-13 | อัตโนมัติ — ผ่าน |
| FR-13 | F-07 | US-12 | SC-06, SC-07 | TC-14, TC-U1 | อัตโนมัติ — ผ่าน; UAT ค้าง |
| FR-14 | F-13 | US-13 | SC-11 | TC-15 | อัตโนมัติ — ผ่าน |
| FR-15 | F-14 | — | SC-13 | TC-16 | อัตโนมัติ — ผ่าน (ส่วน Blender จริง skip/stub **[ต้องยืนยัน]**) |
| FR-16 | F-15 | US-13 | SC-12 | TC-17 | อัตโนมัติ — ผ่าน |
| FR-17 ถึง FR-23 | F-15 | — | SC-13 | TC-19 | มี test ในแต่ละสาย; ไม่ได้รันรอบนี้ |
| FR-24 | — | — | script | TC-18 | อัตโนมัติ (`tests/test_graphics_fork_parity.py`) ไม่ได้รันรอบนี้ |

## 2. ช่องว่างที่ยังไม่ครอบคลุม

| ID | ช่องว่าง | ผลกระทบ | ข้อเสนอ |
|---|---|---|---|
| GAP-01 | ยังไม่มีบันทึก UAT จากงานลูกค้าจริง (TC-U1, TC-U2) | ไม่มีหลักฐานว่าขายได้ — เงื่อนไขคง verdict `invest` | บันทึกงานแรกที่ส่งผ่าน runbook |
| GAP-02 | Golden test skip เมื่อไม่ตั้งฟอนต์ (รวม CI) **[ต้องยืนยัน]** ว่า CI ตั้งหรือไม่ | หน้าตาเพี้ยนโดยไม่รู้ตัว | ตั้งฟอนต์และ resvg ใน CI |
| GAP-03 | CI run แรกยังไม่ยืนยัน และไม่มี Windows CI | branch `nt` และ runtime หนักไม่ถูกตรวจอัตโนมัติ | P0/P1 ใน UPGRADE-PLAN |
| GAP-04 | Coverage ต่ำใน `graphics_production/export.py`, `cli.py` (< 35% เมื่อไม่มี browser) | regression ใน export | stubbed-runtime tests ตาม P2 |
| GAP-05 | `docs/parity/graphics.md` ล้าสมัยบางส่วน | การวางแผน gap ผิด | อัปเดต matrix |
| GAP-06 | HTTP API ไม่มี auth และไม่มี test ว่าผูก localhost | เสี่ยงถ้าเปิดออกนอกเครื่อง | เอกสาร/ค่าเริ่มต้น bind localhost |

## การอนุมัติ

| บทบาท | สถานะ | วันที่ | ลงนาม |
|---|---|---|---|
| BA (Book Dev) | รอตรวจ | — | [ต้องยืนยัน] |
| QA (Book Dev) | รอตรวจ | — | [ต้องยืนยัน] |

## แหล่งข้อมูล

- `DOCPACK/01-prd.md`, `DOCPACK/05-srs.md`, `DOCPACK/06-ux-flows.md`, `DOCPACK/07-test-uat.md`
- `docs/UPGRADE-PLAN.md`, `docs/parity/graphics.md`
- ผลรัน pytest 2026-10-08
