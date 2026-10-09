---
title: Production Console Web — Runbook การติดตั้งและดูแลระบบ
doc_id: PCW-DOC-04
doc_type: runbook
version: 0.1
date: 2026-10-07
status: ร่างเพื่อพิจารณา
owner: DevOps
audience: internal
related: 00-overview.md, 02-architecture.md, 03-nfr-security.md
---

# Production Console Web — Runbook

## Environments

| Environment | URL/พอร์ต | หมายเหตุ |
|---|---|---|
| Local | Console `http://127.0.0.1:3500`, Hub `http://127.0.0.1:3460` | ทั้งสอง process ผูก loopback |
| Hosted | ไม่มี | ไม่มี entry ใน `service-deployments.yaml` (deployment_skipped_no_target) |

## วิธี build/deploy

```bash
# terminal 1
cd services/production-hub
python -m production_hub.api --port 3460

# terminal 2
cd apps/production-console-web
npm ci
npm run dev            # หรือ npm run build && npm start
npm run typecheck
npm test
```

ไม่พบ task เฉพาะของ Console ใน `infra/tasks/*.yml`; มี `task production-hub:check` สำหรับฝั่ง Hub
(รวมอยู่ในขั้นตอนตรวจของ root `Taskfile.yml`)

## Config/secret (ชื่อตัวแปรเท่านั้น)

| ตัวแปร | ความหมาย |
|---|---|
| `PRODUCTION_HUB_API_ORIGIN` | origin ของ Hub (ค่าเริ่มต้น `http://127.0.0.1:3460`, ต้องเป็น loopback) |
| `PRODUCTION_HUB_API_TOKEN` | bearer token ของ Hub (optional, ฝั่งเซิร์ฟเวอร์เท่านั้น) |
| `PORT` | พอร์ตที่ guard ใช้ตรวจ Host (ค่าเริ่มต้น 3500) |
| `PRODUCTION_HUB_HOME` | (ฝั่ง Hub) ตำแหน่ง library |

## Health check

- `GET http://127.0.0.1:3500/api/hub/v1/health` ผ่าน proxy
- ฝั่ง Hub: `python -m production_hub verify` และ `task production-hub:check`

## Rollback

แอปรันจาก working tree ในเครื่อง: rollback คือกลับไป commit ก่อนหน้าของโฟลเดอร์นี้แล้ว `npm ci` และเปิดใหม่
ไม่มี release artifact แยก **[ต้องยืนยัน]**

## Incident/troubleshooting

| อาการ (code) | สาเหตุ | การแก้ |
|---|---|---|
| 503 `hub_unavailable` | Hub ไม่ได้เปิดหรือ timeout | เปิด `python -m production_hub.api --port 3460` แล้วลองใหม่ |
| 503 `origin_not_allowed` / `origin_invalid` | `PRODUCTION_HUB_API_ORIGIN` ไม่ใช่ loopback หรือรูปแบบผิด | ตั้งเป็น `http://127.0.0.1:<port>` |
| 403 `host_not_allowed` | เปิดผ่าน hostname อื่นหรือพอร์ตไม่ตรง `PORT` | เปิดผ่าน `127.0.0.1`/`localhost` บนพอร์ตแอป |
| 403 `cross_origin` / 415 | คำขอ POST ไม่ได้มาจากหน้าเว็บหรือไม่ใช่ JSON | ใช้งานผ่าน UI ของ Console |
| พอร์ต 3500 ถูกใช้ | `apps/awesome-web` dev ใช้พอร์ตเดียวกัน | ปิดอีกแอปหรือเปลี่ยนพอร์ตพร้อมตั้ง `PORT` |

## Backup/restore

Console ไม่มีข้อมูลต้อง backup ข้อมูลอยู่ใน `.runtime/production-hub/` (หรือ `PRODUCTION_HUB_HOME`) ของ Hub
ควรสำรองทั้ง `store/` และ `hub.db` พร้อมกันขณะ Hub หยุด **[สมมติฐาน]** ไม่พบขั้นตอนทางการ **[ต้องยืนยัน]**

## การอนุมัติ

| บทบาท | สถานะ | วันที่ | ลงนาม |
|---|---|---|---|
| DevOps | รอพิจารณา | — | ________ |

## แหล่งข้อมูล

- `apps/production-console-web/README.md`, `apps/production-console-web/package.json`, `apps/production-console-web/app/api/hub/[...path]/route.ts`
- `services/production-hub/README.md`
- `infra/tasks/governance.yml` (`production-hub:check`)
