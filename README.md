# Book Production Platform

แพลตฟอร์ม local สำหรับแปลงข้อมูลโดเมนที่มีโครงสร้างให้เป็นผลผลิตจริงผ่าน
adapter และ engine ที่ตรวจสอบซ้ำได้ ใช้กฎ โปรแกรม และ API กับงานที่ทำซ้ำได้
แล้วเรียกโมเดลเฉพาะขั้นที่ต้องใช้ความสามารถเชิงกำเนิดจริง ๆ

## 3D Product Scene Composer V1A

รุ่นทดลองนี้รับ `scene-ir.v1` กับไฟล์ `.glb` ในเครื่อง แล้วสร้างฉากสินค้าแบบ
แก้ไขต่อได้ใน Blender พร้อมภาพ PNG และ QA manifest เส้นทางปกติเป็น
deterministic และรายงาน `model_calls: 0`; ไม่มีการส่ง asset ออกเครือข่าย
หรือสร้าง geometry จาก prompt

### Runtime และติดตั้ง

- Python 3.12 ขึ้นไป
- Blender 5.2.2 LTS build ที่ผ่านการตรวจเวอร์ชัน
- Windows, macOS หรือ Linux ที่ Blender รองรับ

ติดตั้งใน virtual environment ของ repository:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[test]"
```

ถ้า Blender ไม่อยู่ใน `PATH` ให้กำหนด `BLENDER_BIN` หรือส่ง `--blender`
ในคำสั่ง คำสั่ง `scene compose` ต้องระบุ executable อย่างชัดเจน

### SceneIR และ asset

ตัวอย่าง `scene.json`:

```json
{
  "schema_version": "scene-ir.v1",
  "asset": {"path": "product.glb"},
  "recipe": "product-hero.studio.v1",
  "composition": "three-quarter.v1",
  "lighting": "softbox-three-point.v1",
  "background": "studio-neutral.v1",
  "material_preset": "preserve-source.v1",
  "render_profile": "square-1024.v1"
}
```

วาง `scene.json` และ `product.glb` ไว้ใน input root เดียวกัน แล้วเรียก:

```powershell
book-production scene compose `
  --scene .\input\scene.json `
  --input-root .\input `
  --output-root .\jobs `
  --blender "$env:BLENDER_BIN"
```

ผลที่ผ่าน QA อยู่ในโฟลเดอร์ชื่อ content fingerprint และมีไฟล์สามรายการ:

- `scene.blend` — ฉาก Blender ที่เปิดและแก้ไขต่อได้
- `render.png` — render ที่ตรวจ format, ขนาด และความแบนของภาพแล้ว
- `qa-manifest.json` — input hash, Blender build, preset, QA, output hash,
  เวลา, ขนาดไฟล์, `model_calls: 0` และ monetary cost ที่ระบุเป็น `unknown`

ระบบเก็บ `output-root/.receipts/<fingerprint>.json` แยกจากโฟลเดอร์ผลลัพธ์
เพื่อ pin hash ของ artifact ทั้งสาม รวมถึง manifest เอง; replay จะปฏิเสธ
manifest หรือ artifact ที่ถูกแก้ไข และคืน hash เดิมจาก receipt ที่ตรวจผ่าน

การเรียกซ้ำด้วย SceneIR, asset และ Blender build เดิมจะตรวจ hash แล้วตอบ
`replayed` โดยไม่สร้างไฟล์ใหม่ หาก artifact เดิมถูกแก้ไขจะปฏิเสธด้วย
`replay_integrity_error` และจะไม่เขียนทับงานเดิม

### ขอบเขต V1A

- รับ GLB 2.0 แบบ self-contained เท่านั้น ไม่อนุญาต external buffer, URL,
  Python, node graph, animation หรือ extension ที่ไม่ได้อนุมัติ
- Recipe, composition, lighting, background, material และ render profile
  ต้องตรงกับ allowlist ใน schema
- ขีดจำกัด: GLB 256 MiB, mesh object 100 ชิ้น, 1,000,000 triangles,
  texture ขนาดด้านละไม่เกิน 4096 px และ texture รวมไม่เกิน 128 MiB
- จำกัดเวลา Blender worker ไว้ไม่เกิน 300 วินาที; input หรือ output ที่เกิน
  ขอบเขตจะถูกปฏิเสธก่อน publish
- output profiles: `square-1024.v1` และ `landscape-1920x1080.v1`
- local pilot นี้ยังไม่ใช่ OS sandbox สำหรับเปิดรับไฟล์จากผู้ใช้ที่ไม่เชื่อถือ

คำสั่งคืน JSON ที่อ่านได้ด้วยโปรแกรมและใช้ stable error code เมื่อปฏิเสธ
ตัวอย่างกลุ่มรหัส ได้แก่ `invalid_scene_ir`, `unsafe_glb_resource`,
`unsupported_glb_feature`, `resource_cap_exceeded`, `invalid_glb`,
`blender_unavailable`, `unsupported_blender_version`, `deadline_exceeded`,
`invalid_render`, `publication_failed` และ `replay_integrity_error`

### Acceptance benchmark

ชุด fixture สังเคราะห์ 20 กรณีตรวจ input ที่รับได้, replay, input ที่ควร
ปฏิเสธ และการปฏิเสธ artifact ที่ถูกแก้ไข ตัวอย่างสินค้าเกิดจาก geometry
พื้นฐานในเครื่อง ไม่ใช้ customer asset หรือ model call:

```powershell
book-production scene benchmark `
  --fixtures .\tests\fixtures\scene3d `
  --output .\.runtime\scene3d-benchmark.json `
  --blender "$env:BLENDER_BIN"
```

รายงานมีจำนวนผลลัพธ์, stable error, latency, bytes และ model calls พร้อม
ภาพ golden EEVEE ห้าภาพและ Cycles CPU candidate สำหรับตรวจเปรียบเทียบ
ตัวเลขเวลาเป็นผลจากเครื่องที่ใช้รันครั้งนั้น ไม่ใช่การรับประกัน throughput
การตรวจอัตโนมัติไม่ให้คะแนนความสวย; ต้องเปิดดูภาพทั้งห้าก่อนสรุปคุณภาพ
และไม่มีการอ้างค่าใช้จ่ายเป็นเงินจนกว่าจะมี rate card ที่วัดได้

รันทดสอบ package:

```powershell
py -3 -m pytest -q
```
