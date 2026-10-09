# Production Console

หน้าเว็บของ [Production Hub](../../services/production-hub/README.md): ดูคลังไฟล์งานผลิต
ที่มาของไฟล์ (lineage) ส่งไฟล์ต่อข้ามเครื่องมือ ตรวจงานในโปรเจกต์ และสร้างแพ็กเกจส่งมอบ

## เปิดใช้งานในเครื่อง

ใช้ terminal สองหน้าต่าง:

```bash
cd services/production-hub
python -m production_hub.api --port 3460
```

```bash
cd apps/production-console-web
npm ci
npm run dev
```

เปิด `http://127.0.0.1:3500` ทั้งสอง process ผูกกับ loopback เท่านั้น

- Console เรียก Hub ผ่าน route `/api/hub/*` ซึ่งอนุญาตเฉพาะเส้นทางที่อยู่ใน `lib/routes.ts`
  และปฏิเสธ `PRODUCTION_HUB_API_ORIGIN` ที่ไม่ใช่ loopback
- คำสั่งที่เปลี่ยนข้อมูล (สร้างโปรเจกต์ ตรวจงาน ส่งต่อ สร้างแพ็กเกจ) ส่ง header `X-Hub-Client: console`
  และ `Authorization: Bearer` เมื่อตั้ง `PRODUCTION_HUB_API_TOKEN`
- `lib/request-guard.ts` กัน confused-deputy/CSRF: `Host` ต้องเป็น `127.0.0.1`/`localhost`/`[::1]` บนพอร์ตแอป
  (`PORT` หรือ 3500) ส่วน POST ต้องมาจาก origin เดียวกัน (`Origin` หรือ `Sec-Fetch-Site: same-origin`)
  และเป็น `Content-Type: application/json` ไม่เช่นนั้นตอบ 403/415 โดยไม่แตะ Hub
- แพ็กเกจส่งมอบถูกสร้างไว้ในเครื่องเท่านั้น Console ไม่ส่งไฟล์ออกภายนอก

## ตรวจสอบ

```bash
npm run typecheck
npm test
```
