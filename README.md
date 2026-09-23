# Book Production Platform

แพลตฟอร์ม local สำหรับแปลงคำสั่งที่มีโครงสร้างเป็นผลผลิตจริงผ่าน domain
adapters และ engines ที่ตรวจสอบซ้ำได้ โดยใช้กฎและโปรแกรมทั่วไปกับงานที่ทำซ้ำ
ได้ และสงวนโมเดล AI ไว้ให้เฉพาะขั้นที่จำเป็นจริง

## สถานะ

กำลังกู้คืน V1A ของ 3D Product Scene Composer จากแบบที่อนุมัติแล้ว ขณะนี้มี
`scene-ir.v1` contract แบบ strict; CLI และ Blender pipeline จะเพิ่มตามลำดับ
ของ implementation plan

## Contract เริ่มต้น

SceneIR ต้องระบุ asset `.glb` ในเครื่องและ preset ที่อยู่ใน allowlist ทุกช่อง
ที่สำคัญเป็นค่าบังคับ ไม่มี prompt, Python, node graph, remote URL หรือ output
path ใน input contract

การตรวจ SceneIR ใช้ Pydantic แบบ strict; API ปัจจุบันอยู่ที่
`book_production_platform.scene3d.models.parse_scene_ir` และรายงานข้อผิดพลาด
ด้วย stable code `invalid_scene_ir`
