---
title: Video Post-production Web — NFR และความปลอดภัย
doc_id: VPW-DOC-03
doc_type: nfr-security
version: 0.1
date: 2026-10-07
status: ร่างเพื่อพิจารณา
owner: Solution Architect + Security
audience: internal
related: 00-overview.md, 02-architecture.md, 04-runbook.md
---

# Video Post-production Web — NFR และความปลอดภัย

## Performance

- worker รัน `--concurrency=1`; งานถอดเสียงและ render ทำทีละงาน
- ถอดเสียงด้วย `faster_whisper` บน CPU (ค่าเริ่มต้น `WHISPER_MODEL=small`) ครั้งแรกต้องดาวน์โหลดโมเดล
- browser smoke เผื่อเวลา transcription สูงสุด 90 นาที (`VIDEO_POSTPRODUCTION_TRANSCRIPTION_TIMEOUT_MS`)
- เป้าหมายเวลาต่อนาทีวิดีโอ **[ต้องยืนยัน]**

## Availability

- runtime ในเครื่อง ไม่มี SLA; Compose healthcheck ของ web (`wget /` ทุก 10 วินาที) และ API (`/healthz`)
- job มี lease (`JOB_LEASE_SECONDS`) และจำนวนครั้งต่อ stage (`MAX_STAGE_ATTEMPTS`) พร้อมปุ่ม retry บน UI

## Scalability

- ออกแบบสำหรับผู้ใช้คนเดียวบนเครื่องเดียว การขยายอยู่ในแผน durable production (`proposed`) **[ต้องยืนยัน]**

## Security controls

- พอร์ตทั้งหมด bind `127.0.0.1` (web 3400, API 8000)
- API รับ browser origin ตาม `CORS_ALLOWED_ORIGINS` (ค่าเริ่มต้น `http://127.0.0.1:3400`, `http://localhost:3400`)
- Upload ตรวจ MIME/นามสกุล (`ALLOWED_VIDEO_MIME_TYPES`), จำกัดขนาด (`MAX_UPLOAD_BYTES`), เขียนแบบ atomic และตรวจซ้ำด้วย FFprobe
- FFmpeg/FFprobe ใช้ typed argv ไม่รับ shell command; EDL ที่ขอ capability ที่ยังไม่เปิดถูกปฏิเสธ
- เว็บไม่ส่ง header ยืนยันตัวตนไป API; compose มีตัวแปร `VIDEO_EDITOR_API_TOKEN` ฝั่ง API **[ต้องยืนยัน]** ว่าบังคับใช้กับ endpoint ใดและเว็บต้องรองรับหรือไม่
- ไม่มี CSP/security header ที่ตั้งใน `next.config.ts` (desktop shell ใส่ CSP ให้เมื่อเปิดผ่าน Electron)

## Privacy/PDPA

- วิดีโอและเสียงอาจมีใบหน้า/เสียงของบุคคล จัดเป็นข้อมูลส่วนบุคคล; เก็บใน `video_storage` บนเครื่องผู้ใช้
- ค่าเริ่มต้นไม่ส่งวิดีโอเต็มออกภายนอก; เมื่อเลือก `openai_compatible` เสียงจะถูกส่งไป endpoint ที่ตั้ง **[ต้องยืนยัน]** ขอบเขตข้อมูลที่ส่ง
- `trace.json` ไม่เก็บข้อความ transcript/caption (มี canary test ฝั่ง API)
- retention และการลบข้อมูลโปรเจกต์ **[ต้องยืนยัน]**

## Observability

- UI แสดง stage และ safe error ของ job
- API เขียน `history.jsonl` และ `trace.json` ต่อ job; ดูด้วย `python -m production_trace history|show|verify`
- log ของ container ผ่าน `docker compose logs`

## Backup/DR

- ข้อมูลอยู่ใน named volumes 4 ตัว ไม่พบ backup อัตโนมัติ **[ต้องยืนยัน]**
- ห้ามใช้ `docker compose down -v` หากต้องการเก็บข้อมูล

## เป้าหมายที่วัดได้

| ID | เป้าหมาย | วิธีวัด | สถานะ |
|---|---|---|---|
| NFR-01 | web และ API ผ่าน healthcheck หลัง `up` | Compose healthcheck | ตั้งใน compose |
| NFR-02 | upload ไฟล์ผิดชนิดถูกปฏิเสธก่อนบันทึก | ทดสอบฝั่ง API | **[ต้องยืนยัน]** หลักฐาน |
| NFR-03 | พอร์ตไม่เปิดนอก loopback | ตรวจ `ports` ใน compose | ตั้งเป็น `127.0.0.1` |
| NFR-04 | trace ไม่มีข้อความ transcript | canary test ฝั่ง API | มีตาม API README |
| NFR-05 | เวลาประมวลผลต่อนาทีวิดีโอ | วัดจาก job | **[ต้องยืนยัน]** |

## การอนุมัติ

| บทบาท | สถานะ | วันที่ | ลงนาม |
|---|---|---|---|
| Solution Architect | รอพิจารณา | — | ________ |
| Security | รอพิจารณา | — | ________ |

## แหล่งข้อมูล

- `services/video-postproduction/docker-compose.yml`, `services/video-postproduction/README.md`, `services/video-postproduction/Dockerfile.web`
- `services/video-postproduction-api/README.md`
- `apps/video-postproduction-web/lib/api.ts`, `apps/video-postproduction-web/next.config.ts`, `apps/video-postproduction-web/tests/browser-smoke.mjs`
