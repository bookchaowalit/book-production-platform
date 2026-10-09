---
title: Production Console Web — Product Requirements (PRD)
doc_id: PCW-DOC-01
doc_type: prd
version: 0.1
date: 2026-10-07
status: ร่างเพื่อพิจารณา
owner: Product
audience: internal
related: 00-overview.md, 02-architecture.md, 03-nfr-security.md, 04-runbook.md
---

# Production Console Web — PRD

## ปัญหาและเป้าหมาย

**ปัญหา:** เครื่องมือผลิตงาน (video, เสียง, เอกสาร ฯลฯ) ของ Solo Empire สร้างไฟล์แยกกัน การส่งต่อไฟล์ ตรวจงาน
และรวมแพ็กเกจส่งมอบต้องทำผ่าน CLI ของ Production Hub (`python -m production_hub ...`) ซึ่งไม่สะดวก
(ช่องว่าง G7, G9 ในเอกสารออกแบบ)

**เป้าหมาย:** หน้าเว็บในเครื่องที่ให้ใช้งาน Pilot ได้จนจบผ่านหน้าเว็บอย่างเดียว: ดูคลังไฟล์และ lineage ส่งต่อไฟล์
ตรวจงานและสร้างแพ็กเกจส่งมอบ โดยไม่ส่งไฟล์ออกภายนอก

## ผู้ใช้/persona

| Persona | ความต้องการ |
|---|---|
| ผู้ผลิตงาน (owner) | หาไฟล์ ดูที่มา และส่งต่อให้เครื่องมือถัดไป |
| ผู้ตรวจงาน (owner ในบทบาท reviewer) | เปลี่ยนสถานะ review ของงานในโปรเจกต์ |
| ผู้ส่งมอบ | สร้างแพ็กเกจส่งมอบพร้อมรายการไฟล์และ sha256 |

## ขอบเขต

**In scope**
- Library: รายการ artifact, รายละเอียด, lineage, preview เนื้อหาที่ preview ได้
- Projects: รายการ/สร้างโปรเจกต์, assign, review, package
- Send: ส่ง artifact ไปยังเครื่องมือที่รองรับ (`v1/send`)
- Proxy `/api/hub/*` ไปยัง Hub บน loopback พร้อม allowlist

**Out of scope**
- การรันเครื่องมือผลิตเอง (Hub ไม่รันเครื่องมือ) และการ import โค้ดของเครื่องมือ
- การส่งไฟล์ออกภายนอกอัตโนมัติ (ส่งเองหลังเจ้าของอนุมัติ)
- multi-user/hosting (ต้องผ่าน design gate แยกตาม HD5)
- หน้าเว็บของ Video Studio (Console ลิงก์เข้าและ ingest ผลลัพธ์ ไม่สร้างซ้ำ)

## ฟีเจอร์หลักพร้อมลำดับความสำคัญ

| ID | ฟีเจอร์ | Hub route | ลำดับ | สถานะ |
|---|---|---|---|---|
| FR-01 | ดู registry และรายการ artifact | `GET v1/registry`, `GET v1/artifacts` | P1 | มีในโค้ด |
| FR-02 | ดูรายละเอียด lineage และเนื้อหา artifact | `GET v1/artifacts/<sha256>[/lineage\|/content]` | P1 | มีในโค้ด |
| FR-03 | ส่งต่อ artifact ไปเครื่องมืออื่น | `POST v1/send` | P1 | มีในโค้ด |
| FR-04 | รายการ/สร้าง/ดูโปรเจกต์ | `GET/POST v1/projects`, `GET v1/projects/<id>` | P1 | มีในโค้ด |
| FR-05 | ตรวจงาน, assign และสร้างแพ็กเกจส่งมอบ | `POST v1/projects/<id>/review\|assign\|package` | P1 | มีในโค้ด |
| FR-06 | ตรวจสุขภาพ Hub | `GET v1/health` | P2 | มีใน allowlist |
| FR-07 | Tool runner (ฟอร์มจาก JSON Schema) และ Job/คิวสถานะสด | — | P2 | ยังไม่พบในโค้ด **[ต้องยืนยัน]** |

## ตัวชี้วัดความสำเร็จ

- Pilot 1 ใช้ได้จนจบผ่านหน้าเว็บอย่างเดียว (เกณฑ์ H4 ในเอกสารออกแบบ)
- ทุกหน้าผ่าน REQ-UX-01..04 (สถานะสด, error ภาษาไทยบอกทางแก้, ภาษาไทยแสดงถูก, keyboard/a11y) — REQ-HUB-07 **[ต้องยืนยัน]** หลักฐานการตรวจ

## Roadmap/phase

| Phase | เนื้อหา | สถานะ |
|---|---|---|
| H0–H1 | contracts และ library ใน Hub | มีใน `services/production-hub` |
| H4 | Console บน Hub API (loopback, header กัน CSRF, token optional) | มีโค้ดใน repo นี้ |
| Durable production | Hub บน Railway (ต้องมี scoped auth, Postgres, object storage) | `remote_state: unverified` **[ต้องยืนยัน]** ว่า Console จะ deploy ด้วยหรือไม่ |

## ข้อสมมติฐานและความเสี่ยง

- **[สมมติฐาน]** มีผู้ใช้คนเดียวบนเครื่องเดียว จึงยอมรับ token แบบ optional
- R-01: ใช้พอร์ต 3500 ซ้ำกับ `apps/awesome-web` เปิดพร้อมกันไม่ได้
- R-02: POST มี timeout 30 นาที งาน package ใหญ่อาจค้างหน้าเว็บ **[ต้องยืนยัน]** ขนาดงานที่คาด
- R-03: สคริปต์ `test:browser` อ้างไฟล์ที่ไม่มี ทำให้ไม่มี browser smoke จริง

## การอนุมัติ

| บทบาท | สถานะ | วันที่ | ลงนาม |
|---|---|---|---|
| Product owner | รอพิจารณา | — | ________ |

## แหล่งข้อมูล

- `apps/production-console-web/README.md`, `apps/production-console-web/lib/routes.ts`, `apps/production-console-web/app/console-client.tsx`
- `docs/superpowers/specs/2026-09-30-production-hub-and-tool-coverage-design.md`
- `services/production-hub/README.md`
- `repository-catalog/registries/service-deployments.yaml` (`production-hub`)
