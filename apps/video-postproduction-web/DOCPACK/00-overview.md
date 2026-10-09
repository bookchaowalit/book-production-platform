---
title: Video Post-production Web — ภาพรวมระบบและสารบัญเอกสาร
doc_id: VPW-DOC-00
doc_type: overview
version: 0.1
date: 2026-10-07
status: ร่างเพื่อพิจารณา
owner: Product
audience: internal
related: 01-prd.md, 02-architecture.md, 03-nfr-security.md, 04-runbook.md
---

# Video Post-production Web — ภาพรวมระบบ

## สรุประบบ

`apps/video-postproduction-web` (แพ็กเกจ `solo-empire-video-postproduction-web`) คือ control surface แบบ Next.js 16
ของ Video Post-production Engine V1 แบบ local-first ผู้ใช้สร้างโปรเจกต์ อัปโหลดวิดีโอ ติดตามสถานะ job
ดู transcript กำหนดช่วงตัดและ caption preset เลือกรูปแบบผลลัพธ์ (9:16 แนวตั้ง หรือ 16:9 แนวนอน) บันทึก EDL
สั่ง render แล้ว preview หรือดาวน์โหลด MP4 หน้าเว็บเรียก FastAPI (`services/video-postproduction-api`) โดยตรงจาก browser
ที่ `NEXT_PUBLIC_VIDEO_POSTPRODUCTION_API_ORIGIN` (ค่าเริ่มต้น `http://127.0.0.1:8000`) งานหนักทำใน Celery worker
และ media อยู่ใน Docker named volume บนเครื่องผู้ใช้

## สถานะปัจจุบัน

- ใช้งานในเครื่องผ่าน Docker Compose project `video-postproduction` (service `web` ที่ `127.0.0.1:3400`)
- เป็นหน้า Projects/Editor ที่ desktop shell (`apps/video-postproduction-desktop`) เปิดในหน้าต่าง Electron
- ขอบเขต V1B: render จาก EDL หนึ่งช่วง source, caption SRT/ASS, center crop 9:16; โค้ดเว็บมีตัวเลือก 16:9 (`render_profile: source`) เพิ่มแล้ว
- Registry: `service-deployments.yaml` มี `video-postproduction-api` (Railway, `repository_status: proposed`, `remote_state: unverified`) แต่ไม่มี entry ของเว็บ
  และ `runtime-boundaries.yaml` ยังไม่มี entry ของระบบนี้ **[ต้องยืนยัน]**

## ผู้ใช้หลัก

- ผู้สร้างคอนเทนต์ (owner) ที่ต้องการตัดคลิปสั้นพร้อม subtitle จากวิดีโอยาวสำหรับ Reels/TikTok/Shorts หรือ YouTube

## รายการเอกสาร

| รหัส | ชื่อ | ไฟล์ | ผู้อ่าน |
|---|---|---|---|
| VPW-DOC-00 | ภาพรวมระบบและสารบัญเอกสาร | `00-overview.md` | ทุกคน |
| VPW-DOC-01 | Product Requirements (PRD) | `01-prd.md` | Product, ผู้พัฒนา |
| VPW-DOC-02 | System Architecture | `02-architecture.md` | Solution Architect, ผู้พัฒนา |
| VPW-DOC-03 | NFR และความปลอดภัย | `03-nfr-security.md` | Solution Architect, Security |
| VPW-DOC-04 | Runbook การติดตั้งและดูแลระบบ | `04-runbook.md` | DevOps, ผู้ใช้ในเครื่อง |

## วิธีอ่าน

อ่าน `01-prd.md` เพื่อดู flow และขอบเขต V1B แล้วอ่าน `02-architecture.md` สำหรับความสัมพันธ์กับ API/worker
ใช้ `04-runbook.md` เมื่อเปิด Compose หรือแก้ปัญหา และดูเอกสาร desktop shell ใน `apps/video-postproduction-desktop/DOCPACK/`
ข้อความ **[ต้องยืนยัน]** คือสิ่งที่เจ้าของต้องยืนยัน

## การอนุมัติ

| บทบาท | สถานะ | วันที่ | ลงนาม |
|---|---|---|---|
| Product owner | รอพิจารณา | — | ________ |

## แหล่งข้อมูล

- `apps/video-postproduction-web/package.json`, `apps/video-postproduction-web/lib/api.ts`, `apps/video-postproduction-web/lib/output-format.ts`
- `services/video-postproduction/README.md`, `services/video-postproduction/docker-compose.yml`
- `services/video-postproduction-api/README.md`
- `repository-catalog/registries/service-deployments.yaml`, `repository-catalog/registries/first-party.yaml`
