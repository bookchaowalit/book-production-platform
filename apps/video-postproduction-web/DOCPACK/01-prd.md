---
title: Video Post-production Web — Product Requirements (PRD)
doc_id: VPW-DOC-01
doc_type: prd
version: 0.1
date: 2026-10-07
status: ร่างเพื่อพิจารณา
owner: Product
audience: internal
related: 00-overview.md, 02-architecture.md, 03-nfr-security.md, 04-runbook.md
---

# Video Post-production Web — PRD

## ปัญหาและเป้าหมาย

**ปัญหา:** การตัดคลิปสั้นพร้อม subtitle จากวิดีโอยาวด้วยมือใช้เวลามาก และการส่งวิดีโอเต็มไป provider ภายนอกมีความเสี่ยงด้านข้อมูล

**เป้าหมาย:** หน้าควบคุมภาษาไทย/อังกฤษผสมที่พาผู้ใช้จากอัปโหลดวิดีโอ → ถอดเสียง → เลือกช่วงและ caption → render MP4
ได้ในเครื่อง โดย original media ไม่ออกจากเครื่องโดย default

## ผู้ใช้/persona

| Persona | ความต้องการ |
|---|---|
| ผู้สร้างคอนเทนต์ (owner) | ตัด first cut แนวตั้งพร้อม subtitle อย่างรวดเร็ว |
| ผู้ตัดต่อที่ใช้ DaVinci Resolve | นำ EDL/FCPXML/OTIO และ `MANUAL-STEPS.md` ไปทำต่อเอง (ผลิตโดย API) |

## ขอบเขต

**In scope (ตามโค้ดเว็บ)**
- หน้า `/` (Projects), `/new` (สร้างโปรเจกต์และอัปโหลด), `/projects/[projectId]` (Project Editor)
- เลือกคุณภาพการถอดเสียง, สั่งถอดเสียงแบบแม่นยำ (`transcribe-accurately`)
- ติดตาม job (`queued` → `running` → `succeeded`/ล้มเหลว) และ retry
- แก้ EDL (PATCH) ช่วงเวลาจาก transcript, caption preset, รูปแบบผลลัพธ์ 9:16 หรือ 16:9
- สั่ง render, preview stream, ดาวน์โหลด

**Out of scope (V1B)**
- active speaker, color operation, B-roll, asset search, generated media, automatic highlight selection (API ปฏิเสธ EDL ที่ขอ)
- login/ผู้ใช้หลายคน และ hosted deployment ของเว็บ **[ต้องยืนยัน]** แผนในอนาคต

## ฟีเจอร์หลักพร้อมลำดับความสำคัญ

| ID | ฟีเจอร์ | API ที่เรียก | ลำดับ | สถานะ |
|---|---|---|---|---|
| FR-01 | รายการโปรเจกต์ | `GET /api/v1/projects` | P1 | มีในโค้ด |
| FR-02 | สร้างโปรเจกต์และอัปโหลด asset | `POST /api/v1/projects`, `POST /api/v1/projects/{id}/assets` | P1 | มีในโค้ด |
| FR-03 | ติดตาม job และ retry | `GET /api/v1/jobs/{id}`, `GET /api/v1/projects/{id}/jobs/latest`, `POST /api/v1/jobs/{id}/retry` | P1 | มีในโค้ด |
| FR-04 | ดู transcript และเลือกช่วงจาก segment | `GET /api/v1/projects/{id}/transcript` | P1 | มีในโค้ด |
| FR-05 | บันทึก EDL (ช่วง, caption, รูปแบบผลลัพธ์) | `GET/PATCH /api/v1/projects/{id}/edl` | P1 | มีในโค้ด |
| FR-06 | สั่ง render, preview และดาวน์โหลด | `POST /api/v1/projects/{id}/renders`, `GET /api/v1/renders/{id}[/stream\|/download]` | P1 | มีในโค้ด |
| FR-07 | ความสามารถถอดเสียงและถอดเสียงแบบแม่นยำ | `GET /api/v1/transcription/capabilities`, `POST .../transcribe-accurately` | P2 | มีในโค้ด |
| FR-08 | ดูประวัติ job/trace และไฟล์ interchange | `GET /api/v1/projects/{id}/history` (มีใน API) | P3 | ไม่พบการเรียกในเว็บ **[ต้องยืนยัน]** |

## ตัวชี้วัดความสำเร็จ

- ทำ flow ทดลอง 6 ขั้นใน runtime README ได้จนได้ MP4 สถานะ `succeeded`
- browser smoke (`tests/browser-smoke.mjs`) ผ่านเมื่อมี API และวิดีโอตัวอย่าง
- เวลาจากอัปโหลดถึง render เสร็จ **[ต้องยืนยัน]** เป้าหมายตัวเลข

## Roadmap/phase

| Phase | เนื้อหา | สถานะ |
|---|---|---|
| V1/V1B | ingest, transcript, first cut 1 ช่วง, caption, 9:16 | มีในโค้ด |
| Native V1 | ห่อด้วย Electron desktop shell | ดู `apps/video-postproduction-desktop` |
| Durable production | API บน Railway, pipeline บน Cloud Run Jobs หลัง benchmark | `proposed`, `remote_state: unverified` |
| ถัดไป | highlight, reframe อัตโนมัติ, audio/color | เป็น product phase แยก **[ต้องยืนยัน]** |

## ข้อสมมติฐานและความเสี่ยง

- **[สมมติฐาน]** ผู้ใช้คนเดียวบนเครื่องเดียว ไม่ต้องมีการยืนยันตัวตน
- R-01: `NEXT_PUBLIC_VIDEO_POSTPRODUCTION_API_ORIGIN` ถูกฝังตอน build การเปลี่ยน API origin ต้อง build ใหม่
- R-02: การถอดเสียงครั้งแรกอาจดาวน์โหลดโมเดล ใช้เวลานาน (smoke timeout ค่าเริ่มต้น 90 นาที)
- R-03: เอกสาร V1B ระบุเฉพาะ 9:16 แต่เว็บรองรับ 16:9 แล้ว — README runtime อาจล้าสมัย **[ต้องยืนยัน]**

## การอนุมัติ

| บทบาท | สถานะ | วันที่ | ลงนาม |
|---|---|---|---|
| Product owner | รอพิจารณา | — | ________ |

## แหล่งข้อมูล

- `apps/video-postproduction-web/lib/api.ts`, `apps/video-postproduction-web/lib/output-format.ts`, `apps/video-postproduction-web/components/project-editor.tsx`, `apps/video-postproduction-web/tests/browser-smoke.mjs`
- `services/video-postproduction/README.md` (Flow ทดลอง, ขอบเขต V1B)
- `services/video-postproduction-api/README.md`
- `repository-catalog/registries/service-deployments.yaml`
