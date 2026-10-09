---
title: Video Post-production Desktop — Runbook การติดตั้งและดูแลระบบ
doc_id: VPD-DOC-04
doc_type: runbook
version: 0.1
date: 2026-10-07
status: ร่างเพื่อพิจารณา
owner: DevOps
audience: internal
related: 00-overview.md, 02-architecture.md, 03-nfr-security.md
---

# Video Post-production Desktop — Runbook

## Environments

| Environment | รายละเอียด |
|---|---|
| Dev | `npm start` (`electron-forge start`) บนเครื่องนักพัฒนา |
| Smoke runtime | project แยก `video-postproduction-native-smoke-test` พอร์ต loopback `18000`/`13400` |
| ผู้ใช้ Windows | ติดตั้ง `Solo Empire Video Studio Setup.exe` แบบ per-user; ต้องมี Docker Desktop + Linux engine |

## วิธี build/deploy

```bash
cd apps/video-postproduction-desktop
npm ci
npm run typecheck          # หรือ task video-postproduction:desktop:typecheck
npm test                   # หรือ task video-postproduction:desktop:test
npm run make               # typecheck + test + smoke:launcher + stage:runtime + electron-forge make
npm run verify:artifacts   # ตรวจ Setup.exe / nupkg / RELEASES และ sha256
# หรือ task video-postproduction:desktop:make -- <args>
```

ผลลัพธ์อยู่ที่ `dist/forge-out/make/squirrel.windows/x64` ช่องทางเผยแพร่ให้ผู้ใช้ **[ต้องยืนยัน]**
ทดสอบ runtime จริงแบบเลือกใช้: `npm run smoke:runtime` หลัง `stage:runtime` (ต้อง Docker Compose ≥ 2.24.4)

## Config/secret (ชื่อตัวแปรเท่านั้น)

shell ไม่อ่านตัวแปร environment เฉพาะของตัวเองในโค้ดที่ตรวจ ค่าคงที่อยู่ใน `src/runtime/constants.js`
(`PROJECT_NAME`, `SERVICE_NAMES`, `API_ORIGIN`, `WEB_ORIGIN`) ตัวแปรของ runtime (เช่น `TRANSCRIPTION_PROVIDER`,
`POSTGRES_PASSWORD`, `VIDEO_EDITOR_API_TOKEN`) เป็นของ Compose ดู runbook ของ `apps/video-postproduction-web`
**[ต้องยืนยัน]** วิธีส่งค่า override ให้ runtime ที่แอปเริ่มเอง

## Health check

- หน้า launcher แสดง phase `ready` แล้วเปิดหน้าต่าง Studio
- ตรวจด้วยมือ: `http://127.0.0.1:8000/healthz` และ `http://127.0.0.1:3400`
- `docker compose -p video-postproduction ps` ทุก service healthy

## Rollback

ติดตั้ง `Setup.exe` เวอร์ชันก่อนหน้าที่ตรวจที่มาแล้ว (Squirrel แทนที่ต่อผู้ใช้) ข้อมูลใน volume ไม่ถูกแตะ
**[ต้องยืนยัน]** การ downgrade ข้ามเวอร์ชันที่ runtime bundle มี migration ต่างกัน

## Incident/troubleshooting

| อาการ (code) | สาเหตุ | การแก้ |
|---|---|---|
| `missing-docker` | ไม่พบ Docker Desktop หรือ Docker CLI | ติดตั้งผ่านลิงก์ในหน้า launcher |
| `engine-unavailable` | Docker Desktop ไม่ทำงานหรือไม่ใช่ Linux engine | เปิด Docker Desktop และสลับเป็น Linux containers |
| `compose-failed` | Compose start/inspect ล้มเหลว | ดู diagnostics ในหน้า launcher แล้ว Recover |
| phase `needs-action` / `recover-required` | runtime อยู่ในสถานะบางส่วน | กด Recover |
| SmartScreen เตือน | installer ไม่ลงลายเซ็น | ใช้เฉพาะ artifact ที่ตรวจ sha256 ได้ |

## Backup/restore

shell ไม่มีข้อมูลต้องสำรอง ข้อมูลงานอยู่ใน volumes `postgres_data`, `redis_data`, `video_storage`, `whisper_cache`
ใช้ขั้นตอนเดียวกับ `apps/video-postproduction-web/DOCPACK/04-runbook.md` ห้ามใช้ `docker compose down -v`
การถอนการติดตั้งแอปไม่ลบ volumes

## การอนุมัติ

| บทบาท | สถานะ | วันที่ | ลงนาม |
|---|---|---|---|
| DevOps | รอพิจารณา | — | ________ |

## แหล่งข้อมูล

- `apps/video-postproduction-desktop/package.json`, `apps/video-postproduction-desktop/forge.config.js`, `apps/video-postproduction-desktop/src/runtime/constants.js`, `apps/video-postproduction-desktop/src/runtime/manager.js`
- `infra/tasks/products.yml` (`video-postproduction:desktop:test`, `:typecheck`, `:make`)
- `services/video-postproduction/README.md`
