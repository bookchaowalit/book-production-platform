---
title: Video Post-production Desktop — ภาพรวมระบบและสารบัญเอกสาร
doc_id: VPD-DOC-00
doc_type: overview
version: 0.1
date: 2026-10-07
status: ร่างเพื่อพิจารณา
owner: Product
audience: internal
related: 01-prd.md, 02-architecture.md, 03-nfr-security.md, 04-runbook.md
---

# Video Post-production Desktop — ภาพรวมระบบ

## สรุประบบ

`apps/video-postproduction-desktop` (productName `Solo Empire Video Studio`, เวอร์ชัน `0.1.4`) คือ desktop shell
สำหรับ Windows x64 ที่สร้างด้วย Electron 44 และ Electron Forge (Squirrel.Windows) แอปไม่ได้มี UI ตัดต่อของตัวเอง
แต่ตรวจ Docker Desktop (Linux engine) และ Docker Compose เริ่มหรือเชื่อมต่อ Compose project `video-postproduction`
เดิม (`postgres`, `redis`, `api`, `worker`, `web`) รอ health แล้วเปิดหน้า Projects/Editor ของ
`apps/video-postproduction-web` (`http://127.0.0.1:3400`) ในหน้าต่างแอป พร้อมหน้า launcher ภาษาไทยสำหรับสถานะและการแก้ปัญหา

## สถานะปัจจุบัน

- โฟลเดอร์ **ไม่ใช่ placeholder**: มี source (`src/`), scripts สร้าง/ตรวจ installer และชุดทดสอบ `node --test` 15 ไฟล์ใน `tests/`
- ประวัติ commit ล่าสุด: v0.1.1 (แก้ launcher), รองรับ Docker Compose v5, v0.1.3 (runtime bundle ที่ปลอดภัยกับ PyAV),
  v0.1.4 "caption-ready installer candidate"
- ตัวติดตั้ง pilot ยังไม่ลงลายเซ็นดิจิทัล (Windows อาจแสดง SmartScreen)
- ไม่มีช่องทางแจกจ่ายหรือ auto-update ที่ระบุ **[ต้องยืนยัน]**; ไม่มีใน `service-deployments.yaml` (เป็นแอปติดตั้งในเครื่อง)
- เอกสารออกแบบมีสถานะ `proposed-written-design` (2026-09-23) **[ต้องยืนยัน]** ว่าได้รับการยอมรับเป็น baseline แล้วหรือไม่

## ผู้ใช้หลัก

- ผู้สร้างคอนเทนต์บน Windows ที่ต้องการเปิดระบบตัดต่อเหมือนแอปทั่วไป โดยไม่ต้องเปิด terminal หรือพิมพ์ URL

## รายการเอกสาร

| รหัส | ชื่อ | ไฟล์ | ผู้อ่าน |
|---|---|---|---|
| VPD-DOC-00 | ภาพรวมระบบและสารบัญเอกสาร | `00-overview.md` | ทุกคน |
| VPD-DOC-01 | Product Requirements (PRD) | `01-prd.md` | Product, ผู้พัฒนา |
| VPD-DOC-02 | System Architecture | `02-architecture.md` | Solution Architect, ผู้พัฒนา |
| VPD-DOC-03 | NFR และความปลอดภัย | `03-nfr-security.md` | Solution Architect, Security |
| VPD-DOC-04 | Runbook การติดตั้งและดูแลระบบ | `04-runbook.md` | DevOps, ผู้ใช้ Windows |

## วิธีอ่าน

อ่านคู่กับ docpack ของ `apps/video-postproduction-web` เพราะฟีเจอร์ตัดต่อทั้งหมดอยู่ในเว็บนั้น เอกสารชุดนี้ครอบคลุม
เฉพาะ shell: การเริ่ม/เชื่อม runtime, ความปลอดภัยของหน้าต่าง และการสร้าง installer ข้อความ **[ต้องยืนยัน]** คือสิ่งที่เจ้าของต้องยืนยัน

## การอนุมัติ

| บทบาท | สถานะ | วันที่ | ลงนาม |
|---|---|---|---|
| Product owner | รอพิจารณา | — | ________ |

## แหล่งข้อมูล

- `apps/video-postproduction-desktop/package.json`, `apps/video-postproduction-desktop/forge.config.js`, `apps/video-postproduction-desktop/src/runtime/constants.js`
- `docs/superpowers/specs/2026-09-23-video-postproduction-native-desktop-design.md`
- `services/video-postproduction/README.md` (หัวข้อ แอป Native สำหรับ Windows)
- `git log -- apps/video-postproduction-desktop`
- `repository-catalog/registries/first-party.yaml`, `repository-catalog/registries/docpack-coverage.yaml`
