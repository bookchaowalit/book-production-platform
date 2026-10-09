---
title: Production Console Web — ภาพรวมระบบและสารบัญเอกสาร
doc_id: PCW-DOC-00
doc_type: overview
version: 0.1
date: 2026-10-07
status: ร่างเพื่อพิจารณา
owner: Product
audience: internal
related: 01-prd.md, 02-architecture.md, 03-nfr-security.md, 04-runbook.md
---

# Production Console Web — ภาพรวมระบบ

## สรุประบบ

`apps/production-console-web` (แพ็กเกจ `solo-empire-production-console-web`) คือหน้าเว็บ Next.js 16 ที่รันในเครื่อง
(loopback เท่านั้น) บน Production Hub API (`services/production-hub`) ใช้ดูคลังไฟล์งานผลิต (artifact) ดูที่มา
ของไฟล์ (lineage) ส่งไฟล์ต่อข้ามเครื่องมือผลิต ตรวจงาน (review) ในโปรเจกต์ และสร้างแพ็กเกจส่งมอบในเครื่อง
ทุกคำขอจาก browser ผ่าน route proxy `/api/hub/*` ที่มี allowlist และ guard กัน CSRF/DNS rebinding
ตรงกับเฟส **H4 Console** ในเอกสารออกแบบ Production Hub

## สถานะปัจจุบัน

- โค้ดมีสองแท็บ: `คลังไฟล์` (Library) และ `โปรเจกต์` (Projects) ใน `app/console-client.tsx`
- ใช้งานในเครื่องเท่านั้น (`next dev/start --hostname 127.0.0.1 -p 3500`) ตามการตัดสินใจ HD5 "Console เปิดเฉพาะในเครื่องก่อน"
- ไม่มี hosted deployment และไม่มี entry ของแอปนี้ใน `service-deployments.yaml` (มีเฉพาะ `production-hub` ที่ `remote_state: unverified`)
- หน้าที่ระบุในเอกสารออกแบบ เช่น Tool runner และ Job/คิว ยังไม่พบในโค้ด **[ต้องยืนยัน]** ว่าอยู่ในแผนรุ่นใด
- สคริปต์ `test:browser` อ้าง `tests/browser-smoke.mjs` ซึ่งไม่มีในโฟลเดอร์ **[ต้องยืนยัน]**

## ผู้ใช้หลัก

- เจ้าของ Solo Empire ในบทบาทผู้ผลิตงาน/ผู้ตรวจงาน ที่ใช้เครื่องมือผลิตหลายตัวและต้องการส่งต่อ ตรวจ และส่งมอบไฟล์

## รายการเอกสาร

| รหัส | ชื่อ | ไฟล์ | ผู้อ่าน |
|---|---|---|---|
| PCW-DOC-00 | ภาพรวมระบบและสารบัญเอกสาร | `00-overview.md` | ทุกคน |
| PCW-DOC-01 | Product Requirements (PRD) | `01-prd.md` | Product, ผู้พัฒนา |
| PCW-DOC-02 | System Architecture | `02-architecture.md` | Solution Architect, ผู้พัฒนา |
| PCW-DOC-03 | NFR และความปลอดภัย | `03-nfr-security.md` | Solution Architect, Security |
| PCW-DOC-04 | Runbook การติดตั้งและดูแลระบบ | `04-runbook.md` | DevOps, ผู้ใช้ในเครื่อง |

## วิธีอ่าน

อ่าน `01-prd.md` ก่อนเพื่อเข้าใจบทบาทของ Console เทียบกับ Production Hub แล้วอ่าน `02-architecture.md`
สำหรับ proxy และ allowlist ส่วน `03-nfr-security.md` อธิบาย guard ความปลอดภัย และ `04-runbook.md`
ใช้เมื่อเปิดระบบหรือแก้ปัญหา ข้อความ **[ต้องยืนยัน]** คือสิ่งที่เจ้าของต้องยืนยัน

## การอนุมัติ

| บทบาท | สถานะ | วันที่ | ลงนาม |
|---|---|---|---|
| Product owner | รอพิจารณา | — | ________ |

## แหล่งข้อมูล

- `apps/production-console-web/README.md`, `apps/production-console-web/package.json`, `apps/production-console-web/app/console-client.tsx`
- `services/production-hub/README.md`
- `docs/superpowers/specs/2026-09-30-production-hub-and-tool-coverage-design.md` (หัวข้อ 4 Web Console, H4, HD5)
- `repository-catalog/registries/first-party.yaml`, `repository-catalog/registries/service-deployments.yaml`, `repository-catalog/registries/docpack-coverage.yaml`
