---
title: Production Console Web — NFR และความปลอดภัย
doc_id: PCW-DOC-03
doc_type: nfr-security
version: 0.1
date: 2026-10-07
status: ร่างเพื่อพิจารณา
owner: Solution Architect + Security
audience: internal
related: 00-overview.md, 02-architecture.md, 04-runbook.md
---

# Production Console Web — NFR และความปลอดภัย

## Performance

- GET ผ่าน proxy timeout 10 วินาที; POST (เช่น package) timeout 30 นาที
- ตอบ `Cache-Control: no-store` ทุกคำขอ เพื่อไม่ cache ข้อมูลงานผลิต
- เนื้อหา artifact ถูก stream ผ่าน (`response.body`) ไม่โหลดทั้งไฟล์ในหน่วยความจำของ proxy

## Availability

- ใช้งานในเครื่องเดียว ไม่มีเป้าหมาย uptime; เมื่อ Hub ไม่ตอบ proxy คืน 503 `hub_unavailable` พร้อมวิธีแก้ภาษาไทย

## Scalability

- ออกแบบสำหรับผู้ใช้คนเดียวบน loopback; การขยายเป็น multi-user ต้องผ่าน design gate (HD5) **[ต้องยืนยัน]**

## Security controls

- Host ต้องเป็น `127.0.0.1`/`localhost`/`[::1]` บนพอร์ตแอป (`PORT` หรือ 3500) มิฉะนั้น 403 `host_not_allowed` (กัน DNS rebinding)
- POST ต้องมาจาก origin เดียวกัน (`Origin` หรือ `Sec-Fetch-Site: same-origin`) มิฉะนั้น 403 `cross_origin`
- POST ต้องเป็น `Content-Type: application/json` มิฉะนั้น 415 `unsupported_media_type`
- Allowlist ใน `lib/routes.ts` เส้นทางอื่นตอบ 404 `not_found`
- Hub origin ต้องเป็น `http:` บน loopback มิฉะนั้น 503 `origin_not_allowed`
- `redirect: 'error'` ทุกคำขอไป Hub
- เนื้อหาที่ไม่ใช่ JSON ตอบพร้อม `X-Content-Type-Options: nosniff` และ CSP `default-src 'none'; style-src 'unsafe-inline'`
- Bearer token ของ Hub (ถ้าตั้ง) อยู่ฝั่งเซิร์ฟเวอร์เท่านั้น browser ไม่เห็น
- ไม่มีการยืนยันตัวตนผู้ใช้ของ Console เอง พึ่งการผูก loopback **[สมมติฐาน]** เครื่องผู้ใช้เชื่อถือได้

## Privacy/PDPA

- ไฟล์งานผลิตอาจมีข้อมูลลูกค้าหรือบุคคล (เช่น วิดีโอ/เสียง) แต่อยู่ในเครื่องเท่านั้น และ Console ไม่ส่งไฟล์ออกภายนอก
- **[ต้องยืนยัน]** การจัดชั้นข้อมูลและ retention ของ `.runtime/production-hub`

## Observability

- ไม่มี logging/metrics เฉพาะของ Console ในโค้ด ข้อผิดพลาดแสดงเป็น `Notice` บนหน้าเว็บ
- สุขภาพ Hub ตรวจผ่าน `GET /api/hub/v1/health`; ความถูกต้องของ store ตรวจด้วย `python -m production_hub verify`

## Backup/DR

- Console ไม่มีสถานะ ข้อมูลอยู่ใน Hub (`store/` + `hub.db`) ไม่พบขั้นตอน backup อัตโนมัติ **[ต้องยืนยัน]**

## เป้าหมายที่วัดได้

| ID | เป้าหมาย | วิธีวัด | สถานะ |
|---|---|---|---|
| NFR-01 | คำขอที่ Host ไม่ใช่ loopback ถูกปฏิเสธ 403 | `tests/hub-proxy-route.test.ts` | มีทดสอบ |
| NFR-02 | POST ข้าม origin ถูกปฏิเสธ 403 และ content-type ผิดได้ 415 | `tests/hub-proxy-route.test.ts` | มีทดสอบ |
| NFR-03 | เส้นทางนอก allowlist ได้ 404 และไม่แตะ Hub | unit test | มีทดสอบ |
| NFR-04 | Hub ไม่ตอบภายใน 10 วินาที (GET) ได้ 503 ภาษาไทย | proxy timeout | ตั้งในโค้ด |
| NFR-05 | ทุกหน้าผ่าน REQ-UX-01..04 | ตรวจ UX | **[ต้องยืนยัน]** |

## การอนุมัติ

| บทบาท | สถานะ | วันที่ | ลงนาม |
|---|---|---|---|
| Solution Architect | รอพิจารณา | — | ________ |
| Security | รอพิจารณา | — | ________ |

## แหล่งข้อมูล

- `apps/production-console-web/lib/request-guard.ts`, `apps/production-console-web/lib/routes.ts`, `apps/production-console-web/app/api/hub/[...path]/route.ts`
- `apps/production-console-web/tests/hub-proxy-route.test.ts`
- `services/production-hub/README.md`
- `docs/superpowers/specs/2026-09-30-production-hub-and-tool-coverage-design.md`
