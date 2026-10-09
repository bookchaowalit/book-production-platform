---
title: Video Post-production Desktop — Product Requirements (PRD)
doc_id: VPD-DOC-01
doc_type: prd
version: 0.1
date: 2026-10-07
status: ร่างเพื่อพิจารณา
owner: Product
audience: internal
related: 00-overview.md, 02-architecture.md, 03-nfr-security.md, 04-runbook.md
---

# Video Post-production Desktop — PRD

## ปัญหาและเป้าหมาย

**ปัญหา:** ผู้ใช้ต้องการเปิดระบบตัดต่อจาก Windows เหมือนแอป ไม่ต้องเปิด terminal รันคำสั่ง Compose และพิมพ์ URL เอง

**เป้าหมาย:** แอป Windows แบบติดตั้งต่อผู้ใช้ที่ตรวจ prerequisite เริ่มหรือเชื่อม runtime เดิมโดยไม่สร้างข้อมูลซ้ำ
และไม่ทำข้อมูลหายเมื่อปิดแอป หยุด engine หรือถอนการติดตั้ง shell นี้ **ไม่เพิ่ม** AI highlight, auto-reframe, audio หรือ color editing

## ผู้ใช้/persona

| Persona | ความต้องการ |
|---|---|
| ผู้สร้างคอนเทนต์บน Windows | เปิดจาก Start Menu แล้วใช้งาน Projects/Editor ได้ทันที |
| ผู้ดูแลเครื่อง (owner) | เห็นสาเหตุเมื่อ Docker ไม่พร้อม และหยุด engine โดยตั้งใจ |

## ขอบเขต

**In scope (Native V1)**
- Windows x64, Electron, installer per-user `Solo Empire Video Studio Setup.exe` (Squirrel.Windows) ไม่ต้องใช้สิทธิ์ Administrator
- หน้า launcher ภาษาไทย (`solo-video://launcher/index.html`): ตรวจ prerequisite, เริ่ม runtime, รอ health, แสดงข้อผิดพลาดที่แก้ต่อได้
- runtime bundle ขนาดเล็กจาก source paths canonical (compose, Dockerfiles, web app, API package)
- เชื่อม project `video-postproduction` เดิมเมื่อ healthy; เริ่มใหม่เมื่อยังไม่มี

**Out of scope**
- ความสามารถตัดต่อใหม่ (อยู่ใน `apps/video-postproduction-web` และ API)
- macOS/Linux installer **[ต้องยืนยัน]** (task `make` รับ argument แพลตฟอร์ม แต่ forge config มีเฉพาะ maker-squirrel)
- การลงลายเซ็นดิจิทัลและ auto-update **[ต้องยืนยัน]**

## ฟีเจอร์หลักพร้อมลำดับความสำคัญ

| ID | ฟีเจอร์ | ลำดับ | สถานะ |
|---|---|---|---|
| FR-01 | ตรวจ Docker CLI, engine (Linux) และ Compose ก่อนเริ่ม (`preflight`) พร้อมข้อความไทย | P1 | มีในโค้ด |
| FR-02 | เชื่อม runtime เดิมที่ healthy (`attached`) หรือเริ่ม Compose (`started`) | P1 | มีในโค้ด |
| FR-03 | รอ API/web health แล้วเปิดหน้าต่าง Studio ที่ `127.0.0.1:3400` | P1 | มีในโค้ด |
| FR-04 | แสดงสถานะ phase (`checking`, `starting`, `waiting-health`, `ready`, `needs-action`, `stopped`, `failed`) ผ่าน IPC | P1 | มีในโค้ด |
| FR-05 | Recover เมื่อ runtime ไม่สมบูรณ์ (`recover-required`) | P1 | มีในโค้ด |
| FR-06 | "หยุด Engine" โดยยืนยัน (`confirmStopEngine`) ไม่ลบ volume | P1 | มีในโค้ด |
| FR-07 | ปิดหน้าต่างแล้ว runtime ทำงานต่อ; single-instance lock | P1 | มีในโค้ด |
| FR-08 | ลิงก์ติดตั้ง Docker Desktop (URL เดียวที่อนุญาตเปิดภายนอก) | P2 | มีในโค้ด |
| FR-09 | diagnostics ที่ปิดบังค่าลับและจำกัด 4096 ตัวอักษร | P2 | มีในโค้ด |

## ตัวชี้วัดความสำเร็จ

ตามเกณฑ์ผ่านรุ่นแรกในเอกสารออกแบบ: ติดตั้งและเปิดจาก Start Menu ได้โดยไม่ต้องลง Node.js/Python/FFmpeg บน host;
เชื่อม runtime เดิมโดยไม่สร้าง volume ซ้ำ; ปิดแล้วเปิดใหม่เห็นโปรเจกต์เดิม; หยุด engine/ถอนการติดตั้งไม่ลบข้อมูล;
renderer ไม่มี Node integration และ navigation จำกัดที่ `127.0.0.1` — หลักฐานการทดสอบบน Windows จริง **[ต้องยืนยัน]**

## Roadmap/phase

| Phase | เนื้อหา | สถานะ |
|---|---|---|
| v0.1.1–v0.1.4 | launcher, Compose v5, runtime bundle PyAV-safe, caption-ready installer candidate | มีใน git log |
| ถัดไป | ลงลายเซ็น, ช่องทางแจกจ่าย, auto-update | **[ต้องยืนยัน]** |

## ข้อสมมติฐานและความเสี่ยง

- **[สมมติฐาน]** ผู้ใช้ติดตั้ง Docker Desktop พร้อม WSL2/Linux engine ได้เอง
- R-01: installer ไม่ลงลายเซ็น → SmartScreen เตือน และผู้ใช้อาจรัน artifact ที่ไม่ได้ตรวจที่มา
- R-02: build ครั้งแรกต้องดาวน์โหลด image/dependency และโมเดล ใช้เวลาและพื้นที่ดิสก์มาก
- R-03: ผู้ใช้อาจลบข้อมูลด้วย `docker compose down -v` นอกแอป

## การอนุมัติ

| บทบาท | สถานะ | วันที่ | ลงนาม |
|---|---|---|---|
| Product owner | รอพิจารณา | — | ________ |

## แหล่งข้อมูล

- `docs/superpowers/specs/2026-09-23-video-postproduction-native-desktop-design.md` (หัวข้อ 1–3)
- `apps/video-postproduction-desktop/src/main.js`, `apps/video-postproduction-desktop/src/runtime/manager.js`, `apps/video-postproduction-desktop/src/runtime/types.d.ts`, `apps/video-postproduction-desktop/src/runtime/diagnostics.js`
- `apps/video-postproduction-desktop/forge.config.js`, `apps/video-postproduction-desktop/package.json`
- `services/video-postproduction/README.md`
