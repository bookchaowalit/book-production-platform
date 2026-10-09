---
title: Video Post-production Web — Runbook การติดตั้งและดูแลระบบ
doc_id: VPW-DOC-04
doc_type: runbook
version: 0.1
date: 2026-10-07
status: ร่างเพื่อพิจารณา
owner: DevOps
audience: internal
related: 00-overview.md, 02-architecture.md, 03-nfr-security.md
---

# Video Post-production Web — Runbook

## Environments

| Environment | URL/พอร์ต | หมายเหตุ |
|---|---|---|
| Local (Compose) | web `http://127.0.0.1:3400`, API `http://127.0.0.1:8000/healthz` | project `video-postproduction` |
| Local dev (เว็บอย่างเดียว) | `next dev` ที่ `127.0.0.1:3400` | ต้องมี API ทำงานอยู่ |
| Desktop | หน้าต่าง Electron ของ `apps/video-postproduction-desktop` | ใช้ Compose project เดิม |
| Hosted | ไม่มีสำหรับเว็บ | **[ต้องยืนยัน]** |

## วิธี build/deploy

```bash
# ทั้ง runtime จาก repository root
docker compose -f services/video-postproduction/docker-compose.yml up --build

# พัฒนาเว็บอย่างเดียว
cd apps/video-postproduction-web
npm ci
npm run dev
npm run typecheck
npm test -- --run
npm run test:browser   # ตั้ง VIDEO_POSTPRODUCTION_API_URL และ VIDEO_POSTPRODUCTION_SAMPLE_VIDEO เพื่อรันเต็ม flow
```

ไม่พบ task ของเว็บนี้ใน `infra/tasks/*.yml` (มีเฉพาะ desktop) **[ต้องยืนยัน]**

## Config/secret (ชื่อตัวแปรเท่านั้น)

| ตัวแปร | ฝั่ง | ความหมาย |
|---|---|---|
| `NEXT_PUBLIC_VIDEO_POSTPRODUCTION_API_ORIGIN` | web (build-time) | origin ของ API |
| `VIDEO_POSTPRODUCTION_WEB_URL`, `VIDEO_POSTPRODUCTION_API_URL`, `VIDEO_POSTPRODUCTION_SAMPLE_VIDEO`, `VIDEO_POSTPRODUCTION_TRANSCRIPTION_TIMEOUT_MS` | browser smoke | ค่าทดสอบ |
| `CORS_ALLOWED_ORIGINS`, `MAX_UPLOAD_BYTES`, `ALLOWED_VIDEO_MIME_TYPES` | API | ต้องสอดคล้องกับ origin ของเว็บ |
| `TRANSCRIPTION_PROVIDER`, `TRANSCRIPTION_LANGUAGE`, `WHISPER_MODEL`, `WHISPER_DEVICE`, `OPENAI_COMPATIBLE_BASE_URL`, `OPENAI_COMPATIBLE_TRANSCRIPTION_MODEL` | API/worker | การถอดเสียง |
| `DATABASE_URL`, `REDIS_URL`, `STORAGE_ROOT`, `VIDEO_EDITOR_API_TOKEN`, `JOB_LEASE_SECONDS`, `MAX_STAGE_ATTEMPTS` | API/worker | runtime |
| `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB` | postgres | ฐานข้อมูล |

ค่า override เก็บในไฟล์ env ในเครื่องตามคู่มือ runtime เท่านั้น ห้าม commit

## Health check

- `curl http://127.0.0.1:8000/healthz`
- เปิด `http://127.0.0.1:3400` เห็นหัวข้อ `Projects`
- `docker compose -f services/video-postproduction/docker-compose.yml ps` ทุก service healthy

## Rollback

กลับไป commit ก่อนหน้าของ `apps/video-postproduction-web` แล้ว `up --build` service `web` ใหม่ named volumes ไม่ถูกแตะ
ถ้ามี migration ฝั่ง API ต้องตรวจ compatibility ก่อน **[ต้องยืนยัน]** ขั้นตอน downgrade ของ Alembic

## Incident/troubleshooting

| อาการ | สาเหตุที่เป็นไปได้ | การแก้ |
|---|---|---|
| หน้าเว็บโหลดแต่เรียก API ไม่ได้ (CORS) | origin ของเว็บไม่อยู่ใน `CORS_ALLOWED_ORIGINS` | เพิ่ม origin แล้ว restart API |
| job ค้าง `queued` | worker หรือ Redis ไม่ทำงาน | ตรวจ `docker compose ps` และ log ของ worker |
| ถอดเสียงช้ามากครั้งแรก | ดาวน์โหลดโมเดลลง `whisper_cache` | รอ หรือเลือกโมเดลเล็กกว่า |
| ภาษาถอดเสียงผิด | ตรวจภาษาอัตโนมัติผิด | ตั้ง `TRANSCRIPTION_LANGUAGE=th` |
| job ล้มเหลว | provider/worker error | ดู stage และ safe error บน editor แล้วกด retry |

## Backup/restore

สำรอง named volumes `postgres_data` และ `video_storage` (และ `redis_data`, `whisper_cache` ถ้าต้องการ) ขณะหยุด service
**[สมมติฐาน]** ใช้ `docker run` mount volume เพื่อ archive — ยังไม่มีขั้นตอนทางการ **[ต้องยืนยัน]**
ห้ามใช้ `docker compose down -v`

## การอนุมัติ

| บทบาท | สถานะ | วันที่ | ลงนาม |
|---|---|---|---|
| DevOps | รอพิจารณา | — | ________ |

## แหล่งข้อมูล

- `services/video-postproduction/README.md`, `services/video-postproduction/docker-compose.yml`
- `apps/video-postproduction-web/package.json`, `apps/video-postproduction-web/tests/browser-smoke.mjs`
