---
title: Video Post-production Desktop — NFR และความปลอดภัย
doc_id: VPD-DOC-03
doc_type: nfr-security
version: 0.1
date: 2026-10-07
status: ร่างเพื่อพิจารณา
owner: Solution Architect + Security
audience: internal
related: 00-overview.md, 02-architecture.md, 04-runbook.md
---

# Video Post-production Desktop — NFR และความปลอดภัย

## Performance

- probe health ใช้ timeout ค่าเริ่มต้น 2000 ms ต่อ endpoint
- การเริ่มครั้งแรกต้อง build image และดาวน์โหลด dependency/โมเดล อาจใช้เวลานาน **[ต้องยืนยัน]** เป้าหมายเวลาเริ่มครั้งแรกและครั้งถัดไป

## Availability

- runtime ทำงานต่อเมื่อปิดหน้าต่าง; เปิดแอปใหม่จะ attach runtime เดิม
- single-instance lock ป้องกันการเปิดหลาย instance ที่สั่ง Compose ชนกัน

## Scalability

- ไม่เกี่ยวข้อง (แอปผู้ใช้คนเดียวต่อเครื่อง)

## Security controls

- หน้าต่าง launcher และ Studio: `nodeIntegration: false`, `contextIsolation: true`, `sandbox: true`, `webSecurity: true`
- preload เปิด bridge เฉพาะ `getStatus`, `start`, `recover` และ event `status`; main ตรวจว่า sender เป็น `solo-video://launcher/index.html` (`isTrustedLauncherSender`)
- navigation จำกัดที่ Studio origin `http://127.0.0.1:3400` และหน้า launcher; URL ภายนอกที่เปิดได้มีเพียงหน้าเอกสารติดตั้ง Docker Desktop
- CSP ของ Studio: `default-src 'self'`, `connect-src` เฉพาะ `127.0.0.1:8000`/`localhost:8000`, `object-src 'none'`, `frame-src 'none'`, `frame-ancestors 'none'`
  (ยังอนุญาต `script-src 'unsafe-inline'` สำหรับ Next.js) และคง CSP เดิมของเซิร์ฟเวอร์ไว้เป็น header แยก
- diagnostics ปิดค่าที่ชื่อตัวแปรมี PASSWORD/TOKEN/SECRET/KEY และตัดเหลือ 4096 ตัวอักษร
- `stage-runtime` และ `verify-artifacts` ปฏิเสธ symlink และ path นอกโฟลเดอร์ที่กำหนด
- installer ยังไม่ลงลายเซ็นดิจิทัล **[ต้องยืนยัน]** แผน code signing
- ติดตั้งแบบ per-user ไม่ต้องใช้สิทธิ์ Administrator

## Privacy/PDPA

- วิดีโอ เสียง และ transcript อยู่ใน Docker volumes บนเครื่องผู้ใช้; shell ไม่ส่งข้อมูลออกภายนอก
- shell ไม่มี telemetry ในโค้ดที่ตรวจ

## Observability

- หน้า launcher แสดง phase, action, code และข้อความไทยที่แก้ต่อได้ (`missing-docker`, `engine-unavailable`, `compose-failed`)
- diagnostics แบบ sanitize สำหรับแนบเมื่อแก้ปัญหา
- ไม่มีการเก็บ log กลาง **[ต้องยืนยัน]** ตำแหน่ง log ใน `userData`

## Backup/DR

- shell ไม่มีข้อมูลต้องสำรอง; ถอนการติดตั้งไม่ลบ Compose project หรือ named volumes
- การสำรองข้อมูลงานดู runbook ของ `apps/video-postproduction-web`

## เป้าหมายที่วัดได้

| ID | เป้าหมาย | วิธีวัด | สถานะ |
|---|---|---|---|
| NFR-01 | renderer ไม่มี Node integration และเป็น sandbox | `tests/window-policy.test.js`, `tests/ipc-policy.test.js` | มีทดสอบ |
| NFR-02 | CSP ของ Studio ใส่เฉพาะ origin ที่ตรงกัน | `tests/studio-csp.test.js` | มีทดสอบ |
| NFR-03 | หยุด engine/ถอนการติดตั้งไม่ลบ volume | `tests/manager.test.js`, `tests/compose.test.js` และ smoke runtime | มีทดสอบบางส่วน **[ต้องยืนยัน]** บน Windows จริง |
| NFR-04 | installer ที่สร้างผ่านการตรวจ artifact (ไม่มี symlink, มี sha256) | `npm run verify:artifacts` | มีสคริปต์ |
| NFR-05 | diagnostics ไม่มีค่าลับ | `tests/diagnostics.test.js` | มีทดสอบ |

## การอนุมัติ

| บทบาท | สถานะ | วันที่ | ลงนาม |
|---|---|---|---|
| Solution Architect | รอพิจารณา | — | ________ |
| Security | รอพิจารณา | — | ________ |

## แหล่งข้อมูล

- `apps/video-postproduction-desktop/src/security/window-options.js`, `apps/video-postproduction-desktop/src/security/window-policy.js`, `apps/video-postproduction-desktop/src/security/studio-csp.js`, `apps/video-postproduction-desktop/src/security/launcher-bridge.js`
- `apps/video-postproduction-desktop/src/runtime/diagnostics.js`, `apps/video-postproduction-desktop/src/runtime/health.js`, `apps/video-postproduction-desktop/src/runtime/manager.js`
- `apps/video-postproduction-desktop/tests/`
- `services/video-postproduction/README.md`
