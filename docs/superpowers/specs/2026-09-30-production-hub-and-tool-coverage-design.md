---
title: Production Hub และความครอบคลุมของ Production Tools
tags: [design, production-platform, artifact-contract, handoff, roadmap]
created: 2026-09-30
updated: 2026-09-30
status: approved-written-spec
description: ช่องว่างที่ทำให้ production tools 15 ตัวยังใช้ทำงานจริงไม่ครบ, Production Hub สำหรับส่งไฟล์ข้าม tool ด้วย artifact contract เดียว, feature ที่ต้องเพิ่มราย tool และลำดับงานที่ผูกกับ pilot
parent: docs/superpowers/specs/2026-09-29-production-tools-spec-driven-development-design.md
related: [catalog/production-tools.yaml, services/production-pipelines/README.md, docs/superpowers/specs/2026-09-22-logic-first-production-platform-design.md, ventures/production-tools-pilot-plan-2026-09-26.md]
---

# Production Hub และความครอบคลุมของ Production Tools

## 0. สรุปสั้น

tool ทั้ง 15 ตัวผ่าน gate ของตัวเองแล้ว แต่ยังใช้เป็น **ระบบผลิตงานจริง** ไม่ได้ครบ เพราะ:

1. **ส่งไฟล์ข้าม tool ไม่สะดวก** — แต่ละ tool เขียนโฟลเดอร์และ manifest คนละแบบ
   มีเส้นเชื่อมเพียง 3 สาย (T6.1–T6.3) และต้องสั่งผ่าน CLI
2. **ไม่มีที่รวมงาน** — ไม่มี project/ลูกค้า/brand กลาง, ไม่มีประวัติเวอร์ชัน, ไม่มีการอนุมัติ
3. **ไม่มีหน้าจอ** — 14 จาก 15 tool ใช้ได้เฉพาะ CLI
4. **output ยังไม่ครบรูปแบบที่งานจริงต้องส่งลูกค้า** — เช่น graphics ไม่มี PNG/PDF สำหรับพิมพ์,
   video ไม่มีไฟล์ซับแยก, BIM ไม่มี BOQ ราคา

ข้อเสนอ: สร้าง **Production Hub** เป็นชั้นบาง ๆ เหนือ tool เดิม (ไม่ย้าย ownership ของ code)
ประกอบด้วย artifact contract เดียว (`artifact.v1`), คลังไฟล์กลาง, ทะเบียนเส้นส่งต่อ
(`handoffs.yaml`), project/brand kit และแพ็กเกจส่งมอบ แล้วค่อยวาง Web Console บนนั้น
พร้อมเพิ่ม feature ราย tool เฉพาะที่ pilot ต้องใช้ก่อน

เจ้าของอนุมัติทิศทางนี้แล้ว (2026-09-30, §8) แต่ละ phase หลัง H0 ยังต้องผ่าน Spec → Plan ตาม SDD ก่อนลงมือ

## 1. ขอบเขต

**In scope:** artifact contract ร่วม, คลังไฟล์และ lineage, เส้นส่งต่อข้าม tool, project/brand kit,
แพ็กเกจส่งมอบ, Web Console, feature ราย tool ที่ทำให้ใช้งาน production ได้จริง

**Out of scope:** multi-user/สิทธิ์หลายคน, hosting สาธารณะ, การชำระเงิน, domain ใหม่ใน backlog
(GIS, Electronics ฯลฯ), การเปิด AI adapter (อยู่ใน SDD W3–W6)

**หลักที่ต้องคงไว้** (จาก [Logic-first design](2026-09-22-logic-first-production-platform-design.md) §6):
tool เป็นเจ้าของ IR/validator/engine ของตัวเอง; Hub เป็นเจ้าของ routing, artifact metadata
และ lineage เท่านั้น; ส่งงานข้าม tool ผ่าน versioned contract ที่ validate ทั้งสองฝั่ง;
เส้นทางปกติ `model_calls=0`

## 2. ช่องว่างที่ทำให้ยังใช้งานจริงไม่ได้

| # | ช่องว่าง | ผลกระทบในงานจริง | แก้ใน |
|---|---|---|---|
| G1 | ไม่มี artifact contract ร่วม (ArtifactManifest อยู่ในเอกสารแต่ไม่มี schema กลาง) | ต้องรู้โครงโฟลเดอร์ของแต่ละ tool เอง; ต่อ pipeline ใหม่ต้องเขียน validator ซ้ำ | H0 |
| G2 | ไม่มีคลังไฟล์กลางและ lineage | หาไม่เจอว่าไฟล์ส่งลูกค้ามาจาก input ไหน; ไฟล์ซ้ำกระจัดกระจาย | H1 |
| G3 | ส่งต่อข้าม tool ได้แค่ 3 สายและต้องใช้ CLI | งานที่ต้องใช้ 2–3 tool ต่อกันต้องคัดลอกไฟล์และเขียน JSON เอง | H2 |
| G4 | ไม่มี project / ลูกค้า / brand กลาง | brand tokens, สี, ฟอนต์, โลโก้ ต้องใส่ซ้ำทุก tool; งานหลายชิ้นของลูกค้าเดียวไม่รวมกัน | H3 |
| G5 | ไม่มีเวอร์ชัน, review, อนุมัติ | แก้งานตาม feedback แล้วไม่รู้ว่าเวอร์ชันไหนส่งไปแล้ว | H3 |
| G6 | ไม่มีแพ็กเกจส่งมอบ | ต้องรวบไฟล์ส่งลูกค้าเอง, ไม่มี checksum หรือใบรายการ | H3 |
| G7 | ไม่มีหน้าจอ 14 tool | ใช้ได้เฉพาะคนที่รู้ CLI; ขายเป็นบริการ self-serve ไม่ได้ | H4 |
| G8 | output format ไม่ครบตามงานจริง | ต้องแปลงไฟล์ต่อด้วยมือทุกครั้ง | §5 |
| G9 | batch / คิว / แจ้งเตือนไม่เป็นมาตรฐาน | งาน 30 ชิ้นขึ้นไปต้องเฝ้าหน้าจอ | H4 |

## 3. สถาปัตยกรรม Production Hub

```text
                 ┌──────────── Web Console (H4) ────────────┐
                 │  Projects · Library · Send to · Review    │
                 └───────────────┬──────────────────────────┘
                                 │ hub API (local, 127.0.0.1)
┌────────────────────────────────▼─────────────────────────────────┐
│ Production Hub (services/production-hub)                          │
│  artifact.v1 registry · content-addressed store · lineage graph   │
│  handoffs.yaml (edges + adapters) · projects/brand kit · packages │
└──────┬──────────────┬──────────────┬──────────────┬──────────────┘
       │ ingest       │ send-to      │ ingest       │ send-to
   tool manifest   tool request   tool manifest   tool request
  (15 tools เดิม เรียกผ่าน CLI/API ที่ owning repo กำหนด ไม่ import code)
```

### 3.1 `artifact.v1` (แก้ G1)

ทุกไฟล์ที่ Hub รู้จักมี record เดียวกัน:

| Field | ความหมาย |
|---|---|
| `artifact_id` | `sha256:<hex>` ของเนื้อไฟล์ (content-addressed; ไฟล์เดียวกันได้ id เดียว) |
| `kind` | ชนิดเชิงความหมายจาก vocabulary §3.2 เช่น `video.render`, `transcript`, `dataset.table` |
| `media_type`, `byte_size`, `filename` | ข้อมูลไฟล์ |
| `producer` | `{tool_id, tool_version, recipe_version, job_id}` |
| `lineage` | รายการ `artifact_id` ที่เป็น input ของงานที่สร้างไฟล์นี้ |
| `project_id`, `brand_id` | ผูกกับงานลูกค้า (optional) |
| `qa` | `{status: accepted / rejected / needs_review, manifest_ref}` จาก QA ของ tool เอง |
| `privacy_class` | `public` / `internal` / `client-confidential` / `personal` |
| `attributes` | ค่าเฉพาะชนิด เช่น duration, width×height, sheet names |

- ไม่เก็บ path แบบ absolute (ใช้กติกาเดียวกับ `pipeline-report.v1`)
- schema เผยแพร่ที่ `services/production-hub/schemas/artifact.v1.schema.json`

### 3.2 Kind vocabulary และการประกาศ produces/consumes

vocabulary อยู่ที่ `services/production-hub/registry/kinds.yaml`; ต่อ tool ประกาศ
`produces`/`consumes` (ที่ส่งมอบแล้ว) และ `planned_produces`/`planned_consumes` ใน
`services/production-hub/registry/tool-io.yaml` โดย id ต้องตรงกับ `catalog/production-tools.yaml`
ทุกตัว — แยกจาก catalog เพื่อให้ Hub เป็นเจ้าของ contract ของตัวเองโดยไม่แตะ schema ของ catalog
`task production-hub:check` (อยู่ใน `docs:check`) ตรวจว่าทุก kind อยู่ใน vocabulary และทุก edge ใน
`handoffs.yaml` ต้นทาง produce และปลายทาง consume kind นั้นจริง

Vocabulary เริ่มต้น:
`document.docx` `document.pdf` `document.scan` · `dataset.table` `chart.svg` `chart.png` `workbook.xlsx`
`sql.pipeline` · `campaign.package` `copy.text` · `web.page` · `workflow.plan` ·
`video.source` `video.render` `transcript` `captions.srt` `captions.vtt` `edl` ·
`audio.track` `audio.master` · `scene3d.blend` `image.render` `model3d.glb` ·
`graphic.svg` `graphic.png` `graphic.pdf` · `cad.fcstd` `cad.step` `cad.stl` `cad.drawing.pdf` ·
`bim.ifc` `bim.takeoff` · `game.unity-project` `game.build` · `brand.kit`

### 3.3 คลังไฟล์กลาง (แก้ G2)

- เก็บที่ `.runtime/production-hub/store/sha256/<2>/<hex>` (git ignore) + index SQLite
  `.runtime/production-hub/hub.db` แยกจาก `the retired Solo Empire control-plane database` เพื่อไม่ปนข้อมูล control plane
- **Ingest adapter ต่อ tool**: อ่าน manifest เดิมของ tool (qa-manifest.json, report.json,
  batch result manifest ฯลฯ) แล้วลงทะเบียนเป็น `artifact.v1` — ไม่ต้องแก้ code ของ tool ในรอบแรก
- ลบได้เฉพาะ artifact ที่ไม่มีใครอ้างใน lineage หรือ package; ไม่มีคำสั่งลบอัตโนมัติ

### 3.4 เส้นส่งต่อ `handoffs.yaml` (แก้ G3)

แต่ละ edge ประกาศ: `from_kind` → `to_tool` + `request_contract` + adapter ที่แปลง artifact
เป็น request ของ tool ปลายทาง + ค่าที่ต้องให้ผู้ใช้กรอกเพิ่ม (ถ้ามี) ผู้ใช้สั่ง
`book-hub send <artifact_id> --to <tool>` หรือกด "ส่งต่อไป…" ใน Console

| Edge | สถานะ | ใช้ใน |
|---|---|---|
| `model3d.glb` → 3D Scene → `image.render` → Graphics | ✅ T6.1 | Pilot 1 |
| `video.source` → Audio (extract) | ✅ T6.2 | Pilot 3 |
| `transcript` → Documents (`transcript@1`) | ✅ T6.2 | Pilot 3 |
| `dataset.table` → Data Viz → Spreadsheet → Documents (`report@1`) | ✅ T6.3 | Pilot 2 |
| `audio.master` → Video (แทนเสียงในคลิป) | 🆕 E1 | Pilot 3 |
| `transcript` / `captions.srt` → Marketing (ทำ caption โพสต์) | 🆕 E2 | Pilot 3 |
| `video.render` + `graphic.png` → Video (thumbnail / overlay / lower third) | 🆕 E3 | Pilot 3 |
| `chart.svg` → Graphics (ใส่กราฟใน template social) | 🆕 E4 | Pilot 2 |
| `chart.svg` / `workbook.xlsx` → Documents (ฝังภาพกราฟจริง แทนอ้าง sha256) | 🆕 E5 | Pilot 2 |
| `copy.text` (Marketing) → Graphics text slots | 🆕 E6 | Pilot 1 |
| `copy.text` + `graphic.png` → Web landing | 🆕 E7 | — |
| `document.scan` (OCR table) → `dataset.table` | 🆕 E8 | Pilot 2 |
| `cad.stl` / `cad.step` → 3D Scene (render สินค้าจากแบบ) | 🆕 E9 | — |
| `cad.fcstd` → Documents (spec sheet) | 🆕 E10 | — |
| `bim.takeoff` → Spreadsheet (BOQ) → Documents | 🆕 E11 | — |
| `model3d.glb` / `audio.track` → Game | 🆕 E12 | — |
| `workflow.plan` → Hub pipeline (แผน workflow กลายเป็นลำดับ send-to ที่รันได้) | 🆕 E13 | ทุก pilot |

### 3.5 Project, Brand kit, Review และแพ็กเกจส่งมอบ (แก้ G4–G6)

- **Project**: `project_id`, ลูกค้า (อ้าง CRM ถ้ามี), deadline, สถานะ; artifact ทุกตัวผูก project ได้
- **Brand kit `brand.kit` v1**: สี, ฟอนต์ (ไฟล์ในเครื่อง), โลโก้, tone/คำต้องห้าม —
  ใช้ร่วมกันโดย Graphics (brand tokens), Marketing (brand rules), Web, Documents (template), Video (สี/ฟอนต์ซับ)
  ทำ adapter แปลงเป็นรูปแบบที่แต่ละ tool ใช้อยู่แล้ว ไม่บังคับให้ tool เปลี่ยน IR
- **Review**: สถานะ `draft → in_review → approved → delivered`; เก็บ comment และเวอร์ชัน
  (artifact ใหม่ที่ lineage ชี้ตัวเดิม = revision)
- **Delivery package**: zip ที่มีเฉพาะ artifact `approved` + `delivery-manifest.json`
  (รายการไฟล์, sha256, เวอร์ชัน, วันที่) + README ภาษาไทย; ไม่ส่งออกภายนอกอัตโนมัติ
  (ส่งเองหลังเจ้าของอนุมัติ ตามกติกา T7.4)

## 4. Web Console (แก้ G7, G9)

- `apps/production-console-web` + `services/production-hub` (API บน 127.0.0.1)
- หน้า: Projects · Library (กรองตาม kind/project/QA) · Tool runner (ฟอร์มจาก JSON Schema ของ tool) ·
  Job/คิว (สถานะสด, retry) · "ส่งต่อไป…" (แสดงเฉพาะ edge ที่ kind รองรับ) · Review · Package
- Video Studio มีหน้าเว็บเองอยู่แล้ว: Console ลิงก์เข้าและ ingest ผลลัพธ์ ไม่สร้างซ้ำ
- ต้องผ่าน REQ-UX-01..04 ของ SDD (สถานะสด, error ไทยบอกทางแก้, ไทยแสดงถูก, keyboard/a11y)

## 5. Feature ที่ต้องเพิ่มราย tool

ระดับความสำคัญ: **P1** = pilot ต้องใช้หรือขาดแล้วส่งงานจริงไม่ได้ · **P2** = ทำให้ครอบคลุมงานทั่วไป · **P3** = ภายหลัง

| Tool | P1 | P2 | P3 |
|---|---|---|---|
| **Video Studio** | export `captions.srt/vtt` แยก; render หลาย aspect (9:16/1:1/16:9) จาก EDL เดียว; รับ `audio.master` แทนเสียง (E1) | ตัดหลายช่วงใน EDL เดียว (multi-cut); burn-in logo/lower third จาก Graphics (E3); preset loudness ตาม platform | B-roll อัตโนมัติ, highlight AI (SDD W4) |
| **Audio batch/master** | ลดเสียงรบกวนพื้นฐาน (deterministic filter); preset podcast/reels/YouTube | ตัดช่วงเงียบ; export MP3/AAC สำหรับเผยแพร่ | สร้าง chapter จาก transcript |
| **Graphics** | export PNG/JPG หลายขนาดต่อ platform + PDF พิมพ์ 300 DPI; รับข้อความจาก Marketing (E6) | CMYK/bleed สำหรับ print-on-demand; template carousel หลายหน้า | animated (GIF/MP4 สั้น) |
| **3D Scene** | render หลายมุมเป็น batch (REQ-S3D-02); พื้นหลังโปร่งใส | turntable MP4; รับ STL/STEP จาก CAD (E9) | จัดฉากหลายสินค้า |
| **Documents** | template ใบเสนอราคา/ใบแจ้งหนี้/proposal; ฝังภาพกราฟจริง (E5, ขยาย `document_ir`) | OCR จริง (REQ-DOC-02); visual golden | ลายเซ็นอิเล็กทรอนิกส์ (ต้องมี design gate) |
| **Spreadsheet** | นำเข้า CSV/`dataset.table` ตรง ๆ; รับ BOQ จาก BIM (E11) | chart ใน workbook; หลาย template (งบ, stock, timesheet) | pivot |
| **Data + Viz** | export PNG นอกจาก SVG; ส่งกราฟให้ Graphics/Documents (E4/E5) | dashboard หลายกราฟ (REQ-DVZ-02); ตัวเลขไทย/พ.ศ. | อัปเดตตามรอบ (schedule) |
| **Data Pipeline** | รันจริงแบบ read-only บน SQLite/CSV (ต้องมี design gate REQ-DPL-03) | ต่อ Postgres read-only | incremental |
| **Marketing** | ปฏิทินโพสต์ (วันที่/ช่องทาง); ส่ง copy ให้ Graphics/Web (E6/E7) | ดึง caption จาก transcript (E2); UTM link builder | ส่งเข้า scheduler ภายนอก (ต้องอนุมัติทุกครั้ง) |
| **Web Landing** | ฟอร์มติดต่อ (ส่งเข้า CRM local); รับภาพจาก Graphics (E7) | หลายหน้า + SEO meta/OG image; static export พร้อม deploy | A/B variant |
| **Workflow** | export แผนเป็นลำดับ send-to ที่ Hub รันได้ (E13) | export เป็น checklist/task ใน control plane | trigger ตามเวลา |
| **CAD** | แบบ 2D PDF พร้อม dimension (REQ-CAD-02) | ส่งเข้า 3D render (E9) / spec sheet (E10); part family หลายขนาด | assembly check |
| **BIM** | แมป IFC class → รหัสงาน + ราคา → BOQ (E11) | ทดสอบ IFC จริง ≥ 2 ไฟล์ (REQ-BIM-01) | clash check |
| **Game** | WebGL build สำหรับให้ลูกค้าเปิดดูได้ | รับ asset 3D/เสียง (E12) | dialogue |

## 6. ลำดับงาน (ผูกกับ SDD waves และ pilot)

หลักการ: ทำ contract ก่อน UI, ทำ edge ที่ pilot ใช้ก่อน, WIP ≤ 2 ตามความเสี่ยงใน SDD §9

| Phase | เป้าหมาย | งานหลัก | Done เมื่อ | ขนานกับ |
|---|---|---|---|---|
| **H0** Contract | แก้ G1 | `artifact.v1` schema + kind vocabulary; เพิ่ม `produces/consumes` ใน catalog; ขยาย `production-tools:check` | ทุก 15 tool ประกาศ kind; guard ผ่านใน `docs:check` | W1 |
| **H1** Library | แก้ G2 | `services/production-hub` + ingest adapter 15 ตัว; CLI `book-hub import/list/show/lineage` | ingest ผลจาก pilot run ได้ครบพร้อม lineage | W1–W2 |
| **H2** Handoff | แก้ G3 | `handoffs.yaml` + adapter ของ edge P1: E1, E4, E5, E6 (+ ย้าย T6.1–T6.3 มาเรียกผ่าน Hub) ; `book-hub send` | ส่งต่อได้โดยไม่แก้ JSON มือใน Pilot 1–3 | W2 |
| **H3** Project/Brand/Package | แก้ G4–G6 | project, `brand.kit` v1 + adapter 5 tool, review states, `book-hub package` | ทำ delivery package ของ pilot 1 ชุดจริง | W2–W3 |
| **H4** Console | แก้ G7, G9 | Web Console บน hub API | ใช้ Pilot 1 จนจบผ่านหน้าเว็บอย่างเดียว | W3–W4 |
| **F-P1** | แก้ G8 | feature P1 ใน §5 เรียงตาม pilot: Graphics export → Video SRT/multi-aspect → Documents template+ฝังกราฟ → 3D batch | pilot วัดเวลา/rework ผ่านเกณฑ์ (≥3 เท่า, rework ≤ 0.5) | ตาม pilot |
| **F-P2/P3** | ครอบคลุม | ตามผล pilot และ demand จริง | มีหลักฐานความต้องการ | W5+ |

### 6.1 Task ย่อยของ H0 — เสร็จ 2026-09-30

- [x] H0.1 `schemas/artifact.v1.schema.json` + `validate_artifact` (kind/media type, lineage ห้ามอ้างตัวเอง, timestamp มี timezone, `manifest_ref` เป็น path สัมพัทธ์)
- [x] H0.2 `registry/kinds.yaml` 36 kinds พร้อม media type ที่อนุญาต
- [x] H0.3 `registry/tool-io.yaml` ครบ 15 tool (แทนการเพิ่ม field ใน catalog ตาม §3.2)
- [x] H0.4 `task production-hub:check` ต่อเข้า `docs:check` ถัดจาก `production-tools:check`
- [x] H0.5 `registry/handoffs.yaml`: 5 edge implemented (T6.1–T6.3) + 15 edge planned (E1–E13)
- [x] H0.6 pytest 25 passed, ruff, mypy strict ผ่าน; `production-hub:check` ผ่านในสาย `docs:check`

### 6.2 Task ย่อยของ H1 — เสร็จ 2026-09-30

- [x] H1.1 `hub.db` (artifacts, lineage) แยกจาก `the retired Solo Empire control-plane database`; ตาราง projects เลื่อนไป H3 พร้อม brand kit
- [x] H1.2 content-addressed store: put แบบ atomic, ไฟล์ซ้ำเก็บครั้งเดียว, transaction ล้มแล้วลบ object ที่เพิ่งเก็บ, `verify` hash ซ้ำทุกไฟล์
- [x] H1.3 ingest adapter 9 รูปแบบ: Graphics, scene-to-graphics, 3D, Spreadsheet, Data Viz, Documents, monthly report pipeline, Audio batch, Audio master — Video ไม่มี output manifest จึงใช้ `import-file`; tool ที่เหลือ (Marketing, Web, Workflow, Data Pipeline, CAD, BIM, Game) ทำตอนเข้า pilot
- [x] H1.4 CLI `ingest/import-file/list/show/lineage/verify` + exit code 0–5
- [x] H1.5 ingest ผล dry-run จริง 8 manifest (95 ไฟล์, 15 MB) ลง `.runtime/production-hub`; `verify` ผ่านทุกไฟล์; lineage ย้อนจาก PDF monthly report → workbook/chart → CSV และจากภาพ graphics → render 3D ได้

ข้อจำกัดที่พบ: Graphics, Spreadsheet, Data และ Documents บันทึกแค่ hash ของ request ทั้งก้อน จึงต้องส่ง
`--input-file` เองถ้าต้องการ lineage ถึงไฟล์ต้นทาง — ระยะยาวให้ tool เขียน input hash ต่อไฟล์ลง manifest

### 6.3 H2–H4 — หลักฐานการส่งมอบ 2026-09-30

- **Send framework** (`production_hub/send.py`): หา edge → ดึงไฟล์จากคลัง → adapter เขียน request ของ tool →
  รัน CLI ของ tool → ingest manifest พร้อม lineage; บันทึกทุกครั้งในตาราง `sends`
- **E4 chart → Graphics (implemented):** ส่ง `chart.svg` ของ monthly report จริงเข้า template `product_card`
  ได้ภาพ square + portrait 4 ไฟล์ lineage ชี้กลับ chart; ตรวจภาพจริงแล้วข้อความไทยถูกต้อง
- **E6 copy → Graphics (implemented):** เพิ่ม ingest adapter `campaign-manifest.v1` แยก copy ต่อช่องทางเป็น
  `copy.text` (hash ตรงกับ `lineage.asset_hashes` ของ Marketing); ส่งเข้า `announcement` ได้ภาพจริง
  — พบ bug ของ Graphics เอง 2 จุด (ปุ่ม CTA ถูกตัดใน square, ตัดบรรทัดไทยกลาง cluster) แก้ใน
  `book-production-platform` commit `403f454` (template@2, recipe `template-batch-v3`)
- **H3:** project, review states + history, `brand-kit.v1`, delivery package (เฉพาะ approved, checksum ก่อน/หลัง zip)
- **H4:** `production_hub.api` (loopback, header กัน CSRF, token optional) + `apps/production-console-web`
  ตรวจในเบราว์เซอร์กับคลังจริงแล้ว
- **E1 master → Video (implemented):** เพิ่ม `video_postpipeline replace-audio` (`video-remux.v1`) ยึดความยาววิดีโอ
  ทดสอบจริงกับคลิป 75 วิ + `master.wav` ได้ MP4 h264 copy + AAC lineage ชี้ทั้งสองไฟล์
- **E5 chart → Documents (implemented):** Documents รับ `report --chart-root` ฝัง PNG ที่ sha256/size ตรงกับ `charts[]`
  ลง DOCX/PDF (IR เพิ่ม `DocumentImage`, request hash รวมการฝังภาพ); Hub แปลง SVG→PNG ด้วย headless Edge/Chrome
  และสร้างตาราง/ยอดรวมจาก `report.json` ของ Data Viz — ส่งจริงได้ PDF 1 หน้าที่มีภาพกราฟ 1 ภาพ
- **E11 BIM takeoff → Spreadsheet (implemented):** ingest `bim-takeoff-qa.v1`; adapter pivot ปริมาณเป็นคอลัมน์ละหน่วย
  และ method (รวมผลได้ถูกต้อง) ส่งจริงได้ BOQ 3 sheet — พบ bug Spreadsheet ทิ้งตารางที่ 2 เป็นต้นไปเงียบ ๆ
  แก้ใน `book-production-platform` `0173b41` (ทุกตารางได้ sheet, recipe `workbook-builder-v2`)
- **E7a copy → Web (implemented):** ingest `web-manifest.v1`; adapter ใช้ copy เป็น hero body + features จากพารามิเตอร์
  + สีจาก brand kit — ส่งจริงได้ landing page ไทยที่ render ถูกต้อง (E7b ภาพ → Web รอ Web Builder รองรับภาพ hero)
- **E2 captions → Marketing (implemented):** adapter อ่าน cue จาก SRT เลือกช่วงด้วย `cues` แล้วสร้าง campaign review-only
  ต่อช่องทาง — ส่งจริงจาก SRT ที่ `video_postpipeline subtitles` สร้าง ได้ campaign + copy.text ที่ส่งต่อ Graphics/Web ได้
- **E3 graphic → Video (implemented):** เพิ่ม `video_postpipeline overlay` (`video-overlay.v1`) วางภาพตามมุม/สัดส่วน
  encode ใหม่และคงเสียงเดิม — ส่งจริงภาพ announcement ลงคลิป 75 วิ มุมขวาบน 25% ตรวจเฟรมแล้วถูกต้อง
- **F-P1 Graphics print export:** `book-production graphics export` (`18a246c`) render SVG ใหม่ที่ scale 1–6 เท่า + PDF ตาม DPI
  พร้อม `graphics-export.v1`; Hub ingest แล้ว lineage ชี้ SVG ต้นฉบับ — ทดสอบจริง 1080 px ×3 @300 DPI = PNG 3240 px, PDF 274.3 mm
- **E10 CAD → Documents (implemented):** Documents เพิ่ม template `spec-sheet@1` (`spec-sheet-request.v1`, ไม่มียอดรวม,
  ฝังภาพที่ตรวจ hash) และหัวข้อลูกค้า/วันที่เป็นภาษาไทยในเอกสาร th-*; adapter ดึงขนาด ปริมาตร จำนวนรูจาก
  `cad-qa-manifest.v1` + ภาพ preview + ข้อความ "ไม่ใช่การรับรองทางวิศวกรรม" (REQ-CAD-03) — ส่งจริงได้ PDF 2 หน้า
- **E7b graphic → Web (implemented):** `page-request.v1` เพิ่ม `hero_image` (ไฟล์ในชุดเดียวกัน ตรวจ sha256 ต้องมี alt);
  QA ยอมให้ `src` เฉพาะไฟล์ในชุด ส่วน URL ภายนอกยังห้าม; recipe `landing-template-v2` — ส่งจริงภาพประกาศเป็น hero
  (กว้างสูงสุด 480 px) พร้อม copy Facebook เป็นเนื้อหา
- **E9 CAD STL → 3D (implemented):** Hub แปลง STL (mm) เป็น GLB (m) ด้วย Blender (`blender_stl_to_glb.py`, วัสดุโลหะพื้นฐาน,
  ไม่มี glTF extension — ผ่าน GLB preflight) แล้วเรียก `scene compose` — ส่งจริงได้ render แผ่นยึด 4 รู;
  framing: composition `three-quarter-fit.v1` (`39266df`) + `front-fit.v1`/`side-fit.v1`/`top-fit.v1` (`b7a630d`) เล็งกึ่งกลางชิ้นงานและถอยกล้องให้พอดีเฟรม — E9 เลือกได้ด้วย `--param composition`
- **F-P1 Documents ใบเสนอราคา:** `quotation@1` ใช้การคำนวณเดียวกับ invoice (Decimal half-up, ภาษีรายบรรทัด) หัวตารางไทยสำหรับ th-*
  recipe `quotation-v1` ทั้ง CLI และ API; invoice@1 เดิมไม่เปลี่ยน
- **F-P1 Documents proposal:** `proposal@1` (`proposal-request.v1`) มีวัตถุประสงค์ ขอบเขตงาน สิ่งที่ส่งมอบ ไทม์ไลน์ เงื่อนไข
  และวันหมดอายุข้อเสนอ ตารางราคามีตารางเดียว (QA รวมคอลัมน์สุดท้ายทุกตาราง) คำนวณแบบ invoice
- **Ingest เพิ่ม:** `cad-qa-manifest.v1` (FCStd/STEP/STL/preview พร้อมขนาดและปริมาตร), `bim-takeoff-qa.v1`,
  `web-manifest.v1`, `video-remux.v1`, `video-overlay.v1`, `graphics-export.v1`, `campaign-manifest.v1` — รวม 16 รูปแบบ

#### งานที่ยังเหลือ (3 edge planned) และเหตุผล

| Edge / งาน | สิ่งที่ขาด | ต้องการ |
|---|---|---|
| E8 OCR table → Data | Poppler + Tesseract ยังไม่ติดตั้ง (REQ-DOC-02) | 👤 อนุมัติติดตั้ง runtime |
| E12a model3d.glb → Game | ยังไม่มี glTFast ใน Unity template และ game-ir.v2 ยังไม่รับ asset kind model | 👤 อนุมัติดาวน์โหลด glTFast + งานพัฒนา Game |
| E13 Workflow → Hub | ต้องออกแบบการแปลง WorkflowIR เป็นลำดับ send ที่รันได้ พร้อมจุดอนุมัติ | spec แยก |
| F-P1 อื่น | Video multi-aspect จาก EDL เดียว | งานพัฒนา Video (รอ session ที่ทำ Video อยู่) |

(เดิมหัวข้อนี้เขียนว่า "7 edge planned"; ตอนนี้ E1–E7, E9–E12b implemented ทั้งหมด เหลือ planned เฉพาะ E8, E12a, E13)

#### สถานะ F-P1 ต่อ tool (ตรวจจากโค้ดจริง 2026-09-30)

ตารางนี้ตรวจจากโค้ดใน `book-production-platform`, `services/video-postproduction`, `services/data-production-api`,
`services/marketing-production-api`, `services/web-production-api`, `services/document-production-api` และ `packages/*-ir`:

| Tool | F-P1 (§5) | สถานะ | หลักฐาน / สิ่งที่ขาด |
|---|---|---|---|
| Video Studio | export captions srt/vtt แยก | ✅ | `video_postpipeline subtitles --format srt\|vtt\|both` (`cli.py:45`, `subtitles.py:72-96`) |
| Video Studio | render หลาย aspect จาก EDL เดียว | ⏳ บางส่วน | EDL รับ 9:16/1:1/16:9 (`edl.py:72`) แต่ engine รีเฟรมได้แค่ center 9:16 (`render/ffmpeg.py:51`) และ 1 request = 1 ไฟล์ — รอ session Video |
| Video Studio | รับ `audio.master` (E1) | ✅ | `replace-audio` (`cli.py:55`, `audio_replace.py`) |
| Audio | ลดเสียงรบกวน deterministic | ❌ | filter เดียวที่มีคือ `loudnorm` (`audio_production/jobs.py:172-191`) — ไม่มี denoise/notch/highpass |
| Audio | preset podcast/reels/YouTube | ⏳ บางส่วน | มี `podcast-mono.v1` (-19 LUFS), `podcast-stereo.v1` (-16), `music-stereo.v1` (-14) (`presets.py:18-22`) — ยังไม่มีชื่อ reels/youtube |
| Graphics | export หลายขนาด + PDF 300 DPI | ✅ | `graphics export` (`graphics_production/cli.py:24`, `export.py:92-127`) — PNG เท่านั้น ยังไม่มี JPG |
| Graphics | รับข้อความจาก Marketing (E6) | ✅ | adapter E6 + ingest `campaign-manifest.v1` |
| 3D Scene | render หลายมุมเป็น batch | ❌ | 1 SceneIR = 1 composition (`scene_builder.py:41-60`), job ล็อก `render.png` ไฟล์เดียว (`jobs.py:36`) |
| 3D Scene | พื้นหลังโปร่งใส | ❌ | `film_transparent = False` hard-code (`scene_builder.py:159`) |
| Documents | template quotation/proposal + ฝังกราฟ (E5) | ✅ | `renderers.py:31` (6 template), `report --chart-root` + `DocumentImage` (sha256 ตรวจ) |
| Spreadsheet | นำเข้า CSV/dataset.table ตรง; รับ BOQ (E11) | ✅ | CSV→xlsx ผ่าน T6.3/Hub (`production_pipelines/runner.py:356-371`) + E11 pivot BOQ — ไม่มี subcommand ตรงใน CLI (ใช้ผ่าน Hub พอ) |
| Data + Viz | export PNG นอกจาก SVG | ❌ | มีแต่ `render_chart_svg` (`data_production/render.py:167-169`) |
| Data + Viz | ส่งกราฟให้ Graphics/Documents (E4/E5) | ✅ | adapter E4/E5 + ingest `report.json` |
| Data Pipeline | รันจริง read-only บน SQLite/CSV | ❌ | มีแค่ `build`/EXPLAIN บน DDL stub (`data_pipeline/cli.py:33`, `pipeline.py:227-239`) — ตาม REQ-DPL-03 ต้องมี design gate ก่อน |
| Marketing | ปฏิทินโพสต์ (วันที่/ช่องทาง) | ❌ | IR ไม่มี field วันที่โพสต์ (`marketing_ir/models.py:39-61`, `publish_enabled: False`) |
| Marketing | ส่ง copy ให้ Graphics/Web (E6/E7) | ✅ | `AssetSpec{kind:"copy", channel, text}` + `campaign-manifest.v1` |
| Web Landing | ฟอร์มติดต่อ → CRM local | ❌ | page-request.v1 ไม่มี field ฟอร์ม (`web_ir/models.py:50-64`); crm-api มี endpoint แล้วแต่ยังไม่มีใครเรียก |
| Web Landing | รับภาพจาก Graphics (E7b) | ✅ | `HeroImageSpec` + E7b (`web_ir/models.py:37,64`) |
| Workflow | export แผนเป็น send-to ที่ Hub รันได้ (E13) | ❌ | WorkflowIR มีแค่ `collect_form\|validate\|render_document\|human_review`, `plan_only` — E13 planned |
| CAD | แบบ 2D PDF พร้อม dimension (REQ-CAD-02) | ❌ | artifact ชุดเดิมไม่มี PDF แบบ (`cad_production/jobs.py:44`) — dimension ถูกตรวจแต่ไม่ได้ render ลงแบบ |
| BIM | แมป IFC class → รหัสงาน+ราคา → BOQ (E11) | ⏳ บางส่วน | takeoff → BOQ workbook ได้แล้วผ่าน E11 แต่ยังไม่มีตารางแมป work code/ราคา (`bim_ir/models.py:14-47`, `reports.py:23-31`) |
| Game | WebGL build | ❌ | ยังมีแต่ Unity scene build ใน project (`game_production/runtime.py:23`) — งานข้อ 2.3 |

สรุป: ✅ 9 รายการ, ⏳ บางส่วน 3 รายการ, ❌ ยังไม่มี 10 รายการ — อันดับถัดไปตาม pilot คือ WebGL build (ข้อ 2.3),
Audio noise reduction, 3D batch/โปร่งใส, Data Viz PNG และปฏิทินโพสต์ (P1 ฝั่ง Marketing)

- **แก้แล้ว:** แกนกราฟ Data Viz ใช้ระยะห่างเลขกลม (recipe `csv-cleaner-chart-builder-v3`) และหัวข้อลูกค้า/วันที่ของ Documents เป็นภาษาไทยในเอกสาร th-*

## 7. Requirement ใหม่

| ID | Requirement |
|---|---|
| REQ-HUB-01 | ทุก artifact ที่ Hub ลงทะเบียนผ่าน `artifact.v1` และ sha256 ตรงกับเนื้อไฟล์ |
| REQ-HUB-02 | send-to validate ทั้ง kind ต้นทางและ request contract ปลายทางก่อนรัน tool; ไม่ผ่าน = ไม่รัน |
| REQ-HUB-03 | Hub ไม่ import code ของ tool; เรียกผ่าน CLI/API ที่ owning repo ประกาศใน catalog |
| REQ-HUB-04 | lineage ย้อนจากไฟล์ส่งมอบถึง input ต้นทางได้ทุกชั้น |
| REQ-HUB-05 | delivery package มีเฉพาะ artifact `approved` และตรวจ checksum ซ้ำก่อนปิด zip |
| REQ-HUB-06 | privacy_class `personal`/`client-confidential` ไม่ถูกใส่ใน log, telemetry หรือ package ของ project อื่น |
| REQ-HUB-07 | ทุกหน้าของ Console ผ่าน REQ-UX-01..04 |

## 8. การตัดสินใจของเจ้าของ

เจ้าของอนุมัติให้ลงมือตามค่าที่แนะนำทั้ง 5 ข้อเมื่อ 2026-09-30

| # | เรื่อง | ข้อสรุป |
|---|---|---|
| HD1 | อนุมัติทิศทาง Hub เป็นชั้นบางเหนือ tool (ไม่รวม code) | อนุมัติ |
| HD2 | Hub อยู่ root repo (`services/production-hub`) หรือ nested `book-production-platform` | root repo — ต้องเรียก tool ทั้งสองฝั่ง ownership |
| HD3 | ลำดับ H0–H4 เทียบกับ W1 (Video) และ W3 (AI Documents) | H0–H1 ขนานกับ W1; H4 หลัง pilot แรกจบ |
| HD4 | P1 feature ตัวแรก | Graphics export PNG/PDF พิมพ์ (Pilot 1 print-on-demand ใช้ทันที) |
| HD5 | Console เปิดเฉพาะในเครื่องก่อน | ใช่ — multi-user/hosting แยก design gate |

## 9. ความเสี่ยง

| ความเสี่ยง | การป้องกัน |
|---|---|
| Hub กลายเป็นงานใหญ่จน pilot ไม่เดิน | H0–H2 ทำเฉพาะ edge ที่ pilot ใช้; Console หลัง pilot แรก |
| adapter ของ Hub ผูกกับรายละเอียด manifest ภายในของ tool แล้วพังเมื่อ tool เปลี่ยน | contract test ต่อ adapter + tool ประกาศเวอร์ชัน manifest; ระยะยาวให้ tool emit `artifact.v1` เอง |
| คลังไฟล์โตเร็ว (วิดีโอ) | content-addressed ไม่เก็บซ้ำ; รายงานขนาดต่อ project; ลบเฉพาะที่ไม่มีการอ้าง |
| ข้อมูลลูกค้าปนข้าม project | REQ-HUB-06 + test ข้าม project |

## 10. การเปลี่ยนแปลง

- 2026-09-30: ร่างแรกสำหรับเจ้าของรีวิว
- 2026-09-30: เจ้าของอนุมัติ HD1–HD5; H0 ส่งมอบใน `services/production-hub` และย้าย produces/consumes ไปไว้ใน registry ของ Hub แทน catalog
