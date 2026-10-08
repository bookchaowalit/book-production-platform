---
title: Book Production Platform — ภาพรวมระบบและสารบัญเอกสาร
doc_id: BPP-DOC-00
doc_type: overview
version: 0.1
date: 2026-10-08
status: ร่างเพื่อพิจารณา
owner: Product owner (Book Dev)
related: 00-product-card.md, 01-prd.md, 02-architecture.md, 03-nfr-security.md, 04-runbook.md, 05-srs.md, 06-ux-flows.md, 07-test-uat.md, 08-rtm.md
---

# Book Production Platform — ภาพรวมระบบและสารบัญเอกสาร

## 1. สรุประบบ

Book Production Platform เป็นแพ็กเกจ Python (`book-production-platform` v0.1.0, Python ≥ 3.12) ที่ให้ CLI `book-production` แปลงข้อมูลที่มีโครงสร้าง (IR ที่ตรวจ schema แล้ว) เป็นชิ้นงานจริงแบบ deterministic ตรวจซ้ำได้ และรายงาน `model_calls: 0` ในเส้นทางปกติ มี 8 สายการผลิต (graphics, scene3d, spreadsheet, data pipeline, CAD, audio, game, BIM) และ pipeline ข้ามสาย ชุดเอกสารนี้ **เน้นสาย Graphics** (`graphics_production`, tool id `graphics-v1a`) ซึ่งใกล้รายได้ที่สุด เพราะถูกใช้ใน graphic job runbook ของ book-sales และลงทะเบียนเป็น production tool `gate_status: accepted` ส่วนสายอื่นบันทึกในระดับ platform

## 2. สถานะปัจจุบัน

| หัวข้อ | สถานะ | หลักฐาน |
|---|---|---|
| Commit | 79 commits (2026-09-24 ถึง 2026-10-08), working tree สะอาดยกเว้น `DOCPACK/` | `git log`, `git status` |
| Graphics | 14 ธีม (`themes/*.json`), compose ต่องาน, campaign, art style, โลโก้, export print, review loop, golden test | `README.md`, `docs/GRAPHICS-THEMES-GUIDE.md` |
| AI ในสาย graphics | AI art director เปิด live แล้ว (เจ้าของอนุมัติ 2026-10-06); brief adapter, design editor และ campaign-brief ยังเป็น shadow เว้นแต่เจ้าของตั้ง `mode: live` **[ต้องยืนยัน]** สถานะ config ปัจจุบัน | `docs/GRAPHICS-THEMES-GUIDE.md` §5D |
| Test | 107 ไฟล์ `test_*.py`; รอบนี้รัน graphics + pipeline + doctor: 445 passed, 17 skipped | `07-test-uat.md` |
| CI | GitHub Actions `ci.yml` (Ubuntu, Python 3.12, FFmpeg) รัน `scripts/check.py`; run แรกเขียวหรือไม่ **[ต้องยืนยัน]** (P0 ใน UPGRADE-PLAN) | `.github/workflows/ci.yml`, `docs/UPGRADE-PLAN.md` |
| Deploy | ไม่มี — เป็นเครื่องมือ local; HTTP API (FastAPI) มีแต่ไม่มี config deploy | `src/.../graphics_production/main.py` |
| Fork | `book-graphics-production` เป็น fork แช่แข็ง รอ archive | `scripts/graphics_fork_parity.py` |

## 3. ผู้ใช้หลัก

| ผู้ใช้ | การใช้งาน | หลักฐาน |
|---|---|---|
| เจ้าของในฐานะผู้ให้บริการงานกราฟิก | รับ brief, compose, review, export ส่งลูกค้า | `book-sales/templates/offers/graphic-job-runbook.md` |
| AI agent ของ Solo Empire | เรียกผ่าน `catalog/production-tools.yaml` | `catalog/production-tools.yaml` (`graphics-v1a`) |
| ลูกค้างานกราฟิก | รับไฟล์ PNG/SVG/PDF ไม่ได้ใช้ระบบโดยตรง | **[ต้องยืนยัน]** จำนวนงานที่ส่งมอบจริง |

## 4. ตารางรายการเอกสาร

| รหัส | ไฟล์ | ชื่อ | ผู้อ่านหลัก |
|---|---|---|---|
| CARD-book-production-platform | `00-product-card.md` | Product Card | Product owner |
| BPP-DOC-00 | `00-overview.md` | ภาพรวมระบบและสารบัญเอกสาร | ทุกคน |
| BPP-DOC-01 | `01-prd.md` | Product Requirements (PRD) | Product owner |
| BPP-DOC-02 | `02-architecture.md` | System Architecture | Solution Architect, นักพัฒนา |
| BPP-DOC-03 | `03-nfr-security.md` | NFR และความปลอดภัย | Solution Architect, Security |
| BPP-DOC-04 | `04-runbook.md` | Runbook การติดตั้งและดูแลระบบ | Operator |
| BPP-DOC-05 | `05-srs.md` | Software Requirements (SRS) | BA, นักพัฒนา |
| BPP-DOC-06 | `06-ux-flows.md` | User Flow และ Screen Inventory | UX/UI, Operator |
| BPP-DOC-07 | `07-test-uat.md` | Test Strategy และ UAT | QA |
| BPP-DOC-08 | `08-rtm.md` | Requirements Traceability Matrix | BA + QA |

## 5. วิธีอ่าน

1. อ่าน `00-product-card.md` แล้ว `01-prd.md` เพื่อดูว่าทำไมโฟกัส graphics ก่อน
2. นักพัฒนาอ่าน `02-architecture.md` → `05-srs.md` → `08-rtm.md`
3. ผู้รับงานกราฟิกอ่าน `04-runbook.md` และ `06-ux-flows.md` คู่กับ `docs/GRAPHICS-THEMES-GUIDE.md` (คู่มือภาษาไทยฉบับเต็ม)
4. รายละเอียดคำสั่งของแต่ละสายอยู่ใน `README.md` ส่วน matrix ความสามารถเทียบ Figma/Canva/Photoshop อยู่ใน `docs/parity/`

## การอนุมัติ

| บทบาท | สถานะ | วันที่ | ลงนาม |
|---|---|---|---|
| Product owner (Book Dev) | รอตรวจ | — | [ต้องยืนยัน] |

## แหล่งข้อมูล

- `README.md`, `pyproject.toml`, `docs/GRAPHICS-THEMES-GUIDE.md`, `docs/UPGRADE-PLAN.md`, `docs/parity/graphics.md`
- `src/book_production_platform/cli.py`, `src/book_production_platform/graphics_production/`
- `.github/workflows/ci.yml`, `scripts/check.py`, `scripts/graphics_fork_parity.py`
- Solo Empire: `catalog/production-tools.yaml`, `projects/marketing/growth/book-sales/templates/offers/graphic-job-runbook.md`
- `DOCPACK/00-product-card.md`
