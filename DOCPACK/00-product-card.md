---
title: Book Production Platform — Product Card
doc_id: CARD-book-production-platform
doc_type: product-card
verdict: invest
version: 0.1
date: 2026-10-08
status: ร่างเพื่อพิจารณา
owner: Product owner (Book Dev)
---

# Book Production Platform — Product Card

## 1. สรุปคุณค่าในหนึ่งประโยค

ช่วยเจ้าของ (และงานบริการของ Solo Empire) ผลิตชิ้นงานจริง เช่นชุดกราฟิก ภาพสินค้า 3D workbook ชิ้นส่วน CAD และงานเสียง จาก input ที่มีโครงสร้าง โดยใช้กฎ/โปรแกรมก่อนและเรียกโมเดลเฉพาะขั้นที่จำเป็น

## 2. ปัญหาและผู้ใช้เป้าหมาย

- งานผลิตซ้ำ ๆ (กราฟิกตามแบรนด์ ภาพสินค้า สเปรดชีต) ใช้เวลาและคุณภาพไม่คงที่เมื่อทำด้วยมือหรือ prompt ล้วน
- ผู้ใช้หลัก: เจ้าของในฐานะผู้ให้บริการงานผลิต (เช่น graphic job ของ book-sales) ผ่าน `catalog/production-tools.yaml`

## 3. สิ่งที่ทำได้จริงตอนนี้

- CLI `book-production` พร้อม `doctor` ตรวจ runtime (python, freecadcmd, ffmpeg, blender, unity) แบบ offline
- 8 สายการผลิตใน `src/book_production_platform/`: scene3d (Blender), graphics (ใหญ่สุด ~11.8k บรรทัด), spreadsheet, data SQL/pipeline, CAD (FreeCAD), audio (ffmpeg), game (Unity), BIM quantity takeoff
- แต่ละสายมี IR/schema แยก (`*_ir`), QA manifest และรายงาน `model_calls: 0` ในเส้นทางปกติ
- เป็นเจ้าของโมดูล graphics ตัวจริง; `book-graphics-production` เป็น fork ที่ถูกแช่แข็ง (ตรวจ drift ด้วย `scripts/graphics_fork_parity.py`)

## 4. หลักฐานสถานะ

- Stack: FastAPI, Python
- Commits: 78 (แรก 2026-09-24 → ล่าสุด 2026-10-06), branch `main`; working tree มีงานค้างที่ยังไม่ commit
- Source: 335 ไฟล์ / 64,713 บรรทัด; test 233 ไฟล์
- CI: 1 workflow
- Deploy config: ไม่มี
- Live URL: ไม่พบใน repo
- ลงทะเบียนเป็น production tool ใน `catalog/production-tools.yaml` (เช่น `graphics-v1a` → `book-production graphics batch`)
- `docs/UPGRADE-PLAN.md` ให้คะแนนตัวเอง 8.5/10 และระบุว่า Blender/FreeCAD/Unity ทดสอบเฉพาะบนเครื่อง Windows

## 5. คุณค่าและจุดต่าง

- จุดต่างคือ determinism และ provenance: ผลงานรันซ้ำได้ ตรวจได้ และไม่ส่ง asset ออกเครือข่าย ต่างจากเครื่องมือ generative ทั่วไป
- เป็นฐานของงานบริการที่ขายได้ เช่นงานกราฟิกชุดสำหรับลูกค้า **[สมมติฐาน]** ยังไม่มีตัวเลขรายได้ที่ยืนยัน
- ใช้ซ้ำข้าม product ของ Solo Empire ผ่าน packages `file-intake`, `production-trace`, `brief-adapter-kit`

## 6. ช่องว่างสำคัญ

- ขอบเขตกว้างมาก (8 สาย) เสี่ยงกระจายแรง; สาย game/BIM/CAD ยังไม่มีหลักฐานลูกค้า
- runtime หนัก (Blender, FreeCAD, Unity) ทดสอบบน Windows host เท่านั้น ไม่มี deploy config
- working tree มีงานค้างใน graphics_production ต้อง commit ให้เรียบร้อย

## 7. คำตัดสิน

**invest** — แก้ปัญหาจริงของงานผลิตที่เจ้าของใช้อยู่ มี core ที่ทำงานพร้อม test 233 ไฟล์และมีผู้บริโภคภายใน (production tools catalog) จึงคุ้มลงทุนต่อ โดยเน้นสาย graphics ที่ใกล้รายได้ที่สุด

จะลดเป็น `showcase` ถ้าภายในไตรมาสถัดไปไม่มีงานจริงที่ส่งมอบผ่าน platform นี้ และควรพิจารณาตัดสายที่ไม่มีผู้ใช้ออก

## 8. ขั้นต่อไป

1. ยกระดับเป็น docpack L1 โดยโฟกัสสาย graphics และ scene3d
2. commit งานค้างและยืนยัน CI run แรกเป็นสีเขียว (P0 ใน UPGRADE-PLAN)
3. จัดลำดับสายตามงานที่ขายได้จริง และพักสายที่ไม่มี demand

## การอนุมัติ

| บทบาท | สถานะ | วันที่ | ลงนาม |
|---|---|---|---|
| Product owner (Book Dev) | รอพิจารณา | — | ________ |

## แหล่งข้อมูล

- `README.md`
- `src/book_production_platform/`
- `scripts/graphics_fork_parity.py`
- `docs/UPGRADE-PLAN.md`
- `catalog/production-tools.yaml (Solo Empire root)`
