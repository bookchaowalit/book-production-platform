# Video Studio Desktop สำหรับ Windows

Native V1 เป็นแอปติดตั้งต่อผู้ใช้ ไม่ต้องใช้สิทธิ์ Administrator สำหรับการติดตั้ง
ต้องติดตั้ง Docker Desktop และเปิด Linux engine ก่อนใช้งาน หากเป็นการเริ่ม runtime
ครั้งแรก แอปจะสร้าง image และดาวน์โหลด dependency ซึ่งต้องใช้อินเทอร์เน็ตและพื้นที่ดิสก์
เพิ่มเติม; งานถอดเสียงครั้งแรกอาจดาวน์โหลดโมเดลลง volume ของ Docker ด้วย

สร้างตัวติดตั้ง x64 สำหรับทดลองในเครื่องด้วย
`npm run make -- --platform=win32 --arch=x64` ในโฟลเดอร์นี้ แล้วติดตั้ง
`Solo Empire Video Studio Setup.exe` จากโฟลเดอร์ผลลัพธ์
`dist/forge-out/make/squirrel.windows/x64/`
ตัวติดตั้งยังไม่ได้ลงลายเซ็นดิจิทัล Windows จึงอาจแสดง SmartScreen; ใช้เฉพาะ
artifact ที่สร้างและตรวจสอบจากแหล่งที่เชื่อถือได้ และยังไม่เผยแพร่ต่อสาธารณะ

รุ่น `v0.1.1` แก้หน้าเริ่มต้นที่ค้างข้อความ “กำลังตรวจสอบระบบ” เพราะ preload
ภายใต้ Electron sandbox โหลดโมดูลภายในไม่ได้ และแก้การตรวจสิทธิ์ IPC ให้ใช้
main frame จริงของ Electron โดยคำสั่ง `make` จะรัน `smoke:launcher` ก่อนสร้างตัวติดตั้ง
คำสั่งนี้เปิดหน้าต่างทดสอบแบบซ่อน
พร้อมสถานะจำลอง เพื่อตรวจ preload, IPC และการแสดงสถานะโดยไม่เรียก Docker
รุ่น `v0.1.2` รองรับ Docker Compose v5 ซึ่งใช้คำสั่ง `docker compose` แบบเดียวกับ v2
และไม่แสดงข้อผิดพลาดว่า Compose v5 ขาดหายเมื่อบริการพร้อมอยู่แล้ว
รุ่น `v0.1.3` แพ็ก dependency ของ runtime ที่ pin `av>=11,<19` เพื่อให้
`faster-whisper` ถอดเสียงได้บนเครื่องที่ build image ใหม่โดยไม่มี cache
และหยุดการ stage ทันทีหาก pin นี้หายไป; ไม่ถือว่าแทนการทดสอบ fresh install
ด้วย Windows user ใหม่
bundle รวม production helpers ใน `lib/` ของเว็บด้วย เพื่อให้ first-build
ไม่ล้มจาก `output-format` หรือ `transcript-range` ที่ขาดหาย โดยยังไม่แพ็ก test
รุ่น `v0.1.4` เพิ่มการตัดคำไทยด้วย PyThaiNLP `newmm` แบบไม่ดาวน์โหลดโมเดล
และตรวจว่ามี migrations `0001`–`0003` ครบใน bundle ตัวติดตั้งที่สร้างในเครื่อง
ยังเป็น candidate สำหรับ owner review/fresh-install gate ไม่ใช่ `pilot-ready`

เปิดแอปจาก Start Menu แอปจะเชื่อมต่อกับ Compose project เดิมชื่อ
`video-postproduction` และใช้ named volumes เดิม รวมถึงฐานข้อมูลและไฟล์สื่อ
การปิดหน้าต่างแอปไม่หยุดบริการเบื้องหลัง ปุ่ม “หยุด Engine” จะหยุดบริการที่ใช้ร่วมกัน
จึงควรกดเมื่อจำเป็นต้องหยุด runtime เท่านั้น การถอนการติดตั้งแอปจะไม่ลบ container,
named volumes หรือข้อมูลสื่อ; แอปไม่มีคำสั่งลบ volume อัตโนมัติ

การตรวจ runtime แบบ smoke เป็นคำสั่งเลือกใช้ด้วยตนเองเท่านั้น: หลัง stage runtime
แล้วให้รัน `npm run smoke:runtime` จากโฟลเดอร์นี้
สคริปต์ใช้เฉพาะ Compose project แยกชื่อ
`video-postproduction-native-smoke-test` และพอร์ต loopback `18000`/`13400`
จาก `dist/video-runtime` กับ fixture ที่กำหนดไว้ หาก Compose ต่ำกว่า `2.24.4`
สคริปต์จะแจ้ง `SKIPPED` โดยไม่เริ่ม project นี้ คำสั่งนี้ไม่ถูกรันใน `npm test`
หรือ build ปกติ และจะ start/stop เฉพาะ project ทดสอบโดยไม่สั่ง `down` และไม่ลบ
named volume ใด ๆ; volume ของ smoke project จึงยังคงอยู่หลังตรวจเสร็จ หากพบ
partial state การตรวจล้มเหลว หรือ project ที่กำลังทำงานอยู่ก่อนแล้ว สคริปต์จะไม่
กู้คืน ไม่หยุดบริการเดิม หรือล้างข้อมูลให้อัตโนมัติ
