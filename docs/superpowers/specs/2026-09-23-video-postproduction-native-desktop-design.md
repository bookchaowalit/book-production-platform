---
title: Video Post-Production — Native Windows Desktop Design
aliases: [Video Studio Desktop, Native Video Post-Production App]
tags: [design, video, desktop, electron, docker-compose]
created: 2026-09-23
updated: 2026-09-23
status: proposed-written-design
description: Design for packaging the existing local-first video post-production control surface as a Windows desktop app.
---

# Video Post-Production — Native Windows Desktop Design

## 1. Intent and decision

ผู้ใช้ต้องการเปิดระบบจาก Windows เหมือนแอป ไม่ต้องเปิด terminal แล้วพิมพ์ URL
เอง และยังต้องการให้ผลิตภัณฑ์มีความสามารถตัดต่อเพิ่มขึ้นในอนาคต

ขอบเขตของเอกสารนี้คือ **Native Windows desktop shell** สำหรับเปิดและดูแล
runtime ที่มีอยู่ โดยใช้ Electron ครอบ Next.js control surface และเชื่อมกับ
Docker Compose ชุดเดิม การเลือกนี้เป็นสมมติฐานที่สอดคล้องกับแนวทาง Electron ที่
คุยกันก่อนหน้า แต่ต้องเข้าใจข้อจำกัดให้ตรงกัน: shell นี้ทำให้การติดตั้งและเปิดใช้
เป็นแอปขึ้น แต่ **ไม่ได้เพิ่ม AI highlight, auto-reframe, audio หรือ color
editing** ความสามารถเหล่านั้นต้องเป็นงาน product phase แยกต่างหาก

หลักฐาน runtime ณ 2026-09-23: Compose project ชื่อ `video-postproduction`
มี service `postgres`, `redis`, `api`, `worker` และ `web` ทำงานและ healthy อยู่
บนเครื่อง การออกแบบต้องต่อกับ project และ volumes เดิม ไม่สร้างฐานข้อมูลหรือ
คลัง media ชุดใหม่

### ทางเลือก

| ทางเลือก | ข้อดี | ข้อแลกเปลี่ยน |
|---|---|---|
| **Electron shell + Compose เดิม (เลือก)** | ใช้ Next.js UI, API, worker และข้อมูลเดิม; ทำ installer และเปิดเป็นหน้าต่าง Windows ได้ | ต้องติดตั้งและเปิด Docker Desktop; หน้าหลักยังเป็นเว็บ UI ภายในหน้าต่าง Electron |
| ติดตั้งเว็บเป็น PWA | เปลี่ยนโค้ดน้อยที่สุด | ยังพึ่ง browser และการจัดการ Compose; ไม่ได้ประสบการณ์ desktop app ที่ผู้ใช้ร้องขอเท่าทางเลือกแรก |
| เขียน desktop UI ใหม่ทั้งหมด | ควบคุม native interaction ได้มาก | สร้างและดูแล UI ซ้ำกับ Next.js พร้อมเพิ่มภาระทดสอบ โดยไม่เพิ่มความสามารถ media engine โดยตรง |

## 2. เป้าหมายและเกณฑ์สำเร็จ

รุ่นแรกถือว่าผ่านเมื่อ:

1. ผู้ใช้ติดตั้งแอป Windows แบบ per-user แล้วเปิดจาก Start Menu หรือ shortcut ได้
   โดยไม่ต้องติดตั้ง Node.js, Python หรือ FFmpeg บน Windows host
2. แอปตรวจ Docker Desktop ที่เปิด Linux container engine และ Docker Compose
   ก่อนเปิดหน้าโปรเจกต์ (ใช้ WSL2 backend ที่ติดตั้งอยู่แล้วได้)
   พร้อมข้อความและวิธีแก้ปัญหาภาษาไทยเมื่อ dependency ไม่พร้อม
3. ถ้า runtime เดิม healthy แอปเชื่อมไปที่ runtime นั้นโดยไม่สร้าง stack,
   database หรือ named volumes ซ้ำ
4. ถ้ายังไม่มี runtime แอปเริ่ม Compose project `video-postproduction`
   และรอ API กับ web health ก่อนเปิดหน้าใช้งาน พร้อมแสดงความคืบหน้าการ build
   ครั้งแรก
5. เมื่อปิดหน้าต่าง งานที่กำลังทำและ container ต้องไม่ถูกหยุด; เมื่อเปิดแอปใหม่
   ต้องกลับมาเห็น project และ artifacts เดิม
6. การหยุด engine หรือถอนการติดตั้งแอปต้องไม่ลบข้อมูล PostgreSQL, media,
   render, transcript หรือ model cache
7. การสื่อสารระหว่าง renderer กับเครื่องต้องไม่มี Node.js integration และต้อง
   จำกัด navigation ให้อยู่ใน `127.0.0.1` ของแอป

## 3. ขอบเขต

### รวมใน Native V1

- Windows x64 desktop app ที่ใช้ Electron; หน้า Projects/Editor ยังคงมาจาก
  `apps/video-postproduction-web`
- หน้าสถานะเริ่มต้นภาษาไทยสำหรับตรวจ prerequisite, เริ่ม runtime, รอ health
  และแสดงข้อผิดพลาดที่แก้ต่อได้
- Windows per-user `Setup.exe` โดยใช้ Electron Forge กับ Squirrel.Windows
- runtime bundle แบบเล็กที่สร้างจาก source paths canonical ใน repository:
  Compose file, Dockerfiles, web app และ API package เท่าที่ใช้ build image
- คำสั่งเปิด Compose ที่ระบุ project name, compose file และ project directory
  อย่างชัดเจน เรียก `docker.exe` โดยไม่ผ่าน shell
- การตรวจ/รอ API `http://127.0.0.1:8000/healthz` และหน้าเว็บ
  `http://127.0.0.1:3400`
- single-instance behavior, app icon/title, Start Menu entry และการเก็บ log
  สำหรับ desktop lifecycle แบบไม่บันทึก secrets
- คำสั่ง `Stop engine` ในแอปพร้อมคำเตือนว่าการประมวลผลอาจหยุด; ใช้ Compose
  stop เท่านั้น ไม่ใช้ `down -v` และไม่ลบ volumes
- การแนบกับ runtime เดิมที่ใช้ Compose project name `video-postproduction`
  เพื่อให้คง named volumes และ project ID ที่มีอยู่

### ไม่รวมใน Native V1

- AI highlight detection/ranking, hook/title generation หรือ natural-language
  editing instructions
- active-speaker tracking, dynamic reframe, silence/filler removal, animated
  captions เพิ่มเติม, B-roll/asset library, graphics, publishing, audio cleanup,
  music/SFX หรือ color/exposure grading
- เขียน Project Editor ใหม่เป็น WinUI/Qt หรือสร้าง nonlinear timeline
- ติดตั้ง Docker Desktop, เปิด Windows features/WSL, เปลี่ยนสิทธิ์ Administrator
  หรือแก้ system settings ให้อัตโนมัติ
- embedded container runtime, offline image distribution, background service
  ที่ auto-start พร้อม Windows, cloud sync, telemetry หรือ auto-update
- code-signing certificate หรือค่าใช้จ่ายสำหรับ SmartScreen reputation

**หมายเหตุเรื่องคุณค่าผลิตภัณฑ์:** หากเป้าหมายหลักของคำว่า “ทำได้เยอะกว่านี้” คือ
ให้ระบบคัด highlight และเสนอคลิปเอง Native V1 อย่างเดียวไม่ตอบโจทย์นั้น งานต่อไป
หลังขอบเขตนี้ควรเป็น AI clip discovery จาก transcript และสร้าง editable EDL
ก่อนพิจารณา audio/color/B-roll ขั้นสูง

## 4. Architecture และ runtime lifecycle

```text
Windows shortcut
  → Electron main process
  → preflight Docker Desktop + Compose
  → ตรวจ runtime ที่ 127.0.0.1
       ├─ healthy: ต่อกับ runtime เดิม ไม่แก้ Compose state
       ├─ ไม่มี runtime: เริ่ม project video-postproduction
       ├─ project เดิมบางส่วน/ไม่ healthy: แสดงทางเลือกให้ผู้ใช้กู้คืนก่อน
       └─ port ถูกบริการอื่นใช้: หยุดและแสดงวิธีแก้ ไม่แย่งหรือ kill process
  → รอ API + web health
  → เปิด Next.js control surface ใน Electron window
```

Desktop package อยู่ใต้ `apps/video-postproduction-desktop/` เป็น product package
แยกจาก Next.js web package ไม่สร้าง root `package.json` ใหม่ ตัว build ของ
installer สร้าง runtime bundle จากไฟล์ต้นทางที่ระบุไว้ แล้ววางตาม layout ที่ทำให้
Compose build context `../..` ยังคง resolve ไปยัง root ของ bundle ได้
ไม่คัดลอกข้อมูลผู้ใช้หรือ `.env` เข้า installer

การจัดการ Compose ใช้ project name `video-postproduction` อย่างชัดเจน ชื่อนี้ตรง
กับ runtime ที่กำลังทำงานและเป็นเงื่อนไขสำคัญสำหรับการ reuse volumes หาก health
check สำเร็จอยู่แล้ว แอปต้องไม่เรียก `up`, `restart`, `down` หรือ build image
กรณีไม่มี project จึงเริ่ม runtime ได้; กรณีมี project แต่ไม่ healthy ต้องขอให้ผู้ใช้
เลือก `Recover` ก่อนเรียก Compose ที่อาจ recreate container

ครั้งแรกที่ไม่มี image แอป build/download image และ dependency ตาม Compose โดย
ไม่รวม Whisper model ขนาดใหญ่ไว้ใน installer; model ยังคงดาวน์โหลดเมื่อเริ่ม
transcription ตามพฤติกรรมปัจจุบัน การดาวน์โหลดอาจต้องใช้อินเทอร์เน็ตและพื้นที่ดิสก์
และต้องแจ้งผู้ใช้ก่อนเริ่มครั้งแรก

การปิด window หรือ process ของ Electron ไม่สั่ง Compose stop/down เพื่อให้งาน
ยาวทำต่อได้ เมื่อผู้ใช้เลือก `Stop engine` จึงส่งคำสั่ง stop แบบระบุ services ของ
product และยืนยันก่อน หากการหยุดอาจขัดจังหวะงาน ข้อมูลใน volumes ต้องคงอยู่
หลังหยุดและหลังถอนแอป

## 5. Security และ data boundary

- Renderer ปิด `nodeIntegration`, เปิด `contextIsolation` และ sandbox; V1 ไม่
  expose privileged preload API ถ้ายังไม่จำเป็น
- โหลดเฉพาะ UI local ที่ `127.0.0.1:3400`; ปฏิเสธ navigation, popup และ
  redirect ไป host อื่น ยกเว้นเปิด HTTPS link ที่ allowlist ผ่าน system browser
- ตรวจ sender ของ IPC ทุกครั้ง หากมี IPC ในอนาคต; renderer ห้ามส่ง command,
  executable path หรือ environment variables ให้ main process โดยตรง
- เรียก Docker ด้วย fixed executable discovery และ argument array ผ่าน
  `spawn`/`execFile` โดยปิด shell; ห้ามประกอบ shell string จาก input
- ตรวจ response/identity ของ local health endpoint ก่อนโหลดหน้าเว็บ หาก port
  ถูกบริการอื่นยึดอยู่ให้ fail closed ไม่เปิด arbitrary localhost content
- ใช้ Electron รุ่นที่ยังได้รับ security updates และกำหนด Content Security
  Policy ให้จำกัด source ของ UI/API ไว้เฉพาะ local app origins ที่จำเป็น
- ไม่ log API keys, authorization, `.env`, media paths ที่เป็น private หรือ body
  ของ API; log เฉพาะ service state, exit code และข้อความที่ sanitize แล้ว
- user settings/logs อยู่ใน Electron `userData` ของผู้ใช้ ไม่อยู่ใน install path;
  project/media/transcript/render/model data ยังคงอยู่ใน Compose named volumes
- ไม่ส่งวิดีโอหรือ telemetry ออกจากเครื่องโดย desktop shell; ใช้ transcription
  local ตามค่าเริ่มต้นเดิม
- Installer เป็น unsigned local pilot; แจ้งชัดว่า Windows อาจแสดง SmartScreen
  warning จนกว่าจะมีการอนุมัติและจัดเตรียมการ signing

ข้อกำหนด security ของ Electron อิงแนวทางทางการเรื่อง security checklist,
context isolation และ renderer sandboxing:
[Electron Security](https://www.electronjs.org/docs/latest/tutorial/security),
[Context Isolation](https://www.electronjs.org/docs/latest/tutorial/context-isolation),
[Sandbox](https://www.electronjs.org/docs/latest/tutorial/sandbox).
Electron Forge Squirrel.Windows สร้าง `Setup.exe` แบบติดตั้งต่อผู้ใช้โดยไม่ต้อง
ขอสิทธิ์ admin ตามเอกสารของโครงการ:
[Squirrel.Windows maker](https://www.electronforge.io/config/makers/squirrel.windows).

## 6. Failure behavior

| เงื่อนไข | พฤติกรรมที่ผู้ใช้เห็น |
|---|---|
| ไม่พบ Docker Desktop/`docker.exe` หรือ Linux engine | หน้าตรวจระบบภาษาไทย พร้อมลิงก์คู่มือ; ไม่พยายามติดตั้ง เปิด WSL features หรือขอ admin |
| Docker Desktop ปิดหรือ engine ยังไม่พร้อม | แสดงวิธีเปิด Docker Desktop และปุ่มตรวจซ้ำ |
| Compose build/start ล้มเหลว | เก็บ exit code และ log ที่ sanitize แล้ว; แสดง retry และวิธีดูรายละเอียด |
| API หรือ web ไม่ healthy ภายในเวลารอ | ไม่เปิดหน้า editor; แสดง service ที่รอและทางเลือก retry/recover |
| port 8000 หรือ 3400 ถูก process อื่นใช้ | ไม่ฆ่า process และไม่เปลี่ยน port เงียบ ๆ; แสดง port ที่ชน |
| project `video-postproduction` มี container แต่ไม่ healthy | ไม่ recreate อัตโนมัติ; ให้ผู้ใช้เลือก Recover หรือยกเลิก |
| ปิดแอประหว่าง build/start | ไม่เรียก down; รอบถัดไปตรวจสถานะ Compose ก่อนทำต่อ |
| ถอนการติดตั้ง | ลบเฉพาะไฟล์ desktop package; แจ้งว่า Docker volumes และข้อมูลไม่ถูกลบ |

## 7. Verification และ acceptance

### Automated

- unit tests ของ lifecycle state machine, URL allowlist, port conflict และการจัดการ
  error ที่ sanitize แล้ว
- unit tests ของ Docker runner ที่ยืนยัน executable/argument allowlist, การปิด
  shell execution และไม่มี `down -v`
- tests ของ runtime bundle manifest ว่ารวม Dockerfile, Compose, migrations,
  package lock/source ที่จำเป็น และไม่รวม `.env`, user data, media หรือ secrets
- Electron smoke tests สำหรับ single-instance, window load, navigation policy,
  IPC sender policy และการปิด app โดย Compose ยังคงทำงาน
- Windows package test ที่สร้าง Squirrel `Setup.exe` และทดสอบ install/launch/
  uninstall ใน user profile แยก

### Manual acceptance บน Windows + Docker Desktop

1. เปิด installer และติดตั้งแบบไม่ใช้ Administrator; เปิดจาก Start Menu
2. ทดสอบ Docker Desktop ปิด, เปิดแล้ว, และไม่ติดตั้ง เพื่อยืนยันข้อความ/ทางแก้
3. ทดสอบต่อกับ stack ปัจจุบันและยืนยัน project เดิม, project ID, transcript และ
   artifacts ยังปรากฏ
4. ปิด window ขณะมีงาน แล้วเปิดใหม่; ยืนยัน worker/job ไม่ถูกหยุดและข้อมูลยังอยู่
5. ทดสอบ port conflict และ Compose start failure โดยแอปไม่ฆ่า process อื่น
6. ทดสอบ `Stop engine` และ uninstall; ยืนยัน named volumes ไม่ถูกลบ
7. ตรวจ Developer Tools/security warnings และยืนยันไม่มี Node integration,
   external navigation หรือ secrets ใน log

ไม่อ้างว่าพร้อมแจกจ่ายจนกว่า installer จะผ่าน clean-user Windows smoke test จริง
การ build installer อย่างเดียวไม่เพียงพอ

## 8. Delivery sequence

1. สร้าง desktop package และทดสอบ main-process/runtime manager ด้วย fake Docker
   runner ก่อนเรียก Docker จริง
2. สร้าง runtime bundle แบบ reproducible จาก source paths ที่ allowlist ไว้
3. ต่อ Electron window กับ launcher states และ existing Next.js UI
4. ทดสอบ reuse/first-start/recover/stop behavior โดยตรวจ volume/project identity
5. สร้าง Squirrel Windows installer และทำ manual acceptance บนเครื่อง Windows
   ที่ติดตั้ง Docker Desktop
6. อัปเดต README ให้ผู้ใช้รู้ prerequisite, เวลา/พื้นที่ build ครั้งแรก,
   SmartScreen unsigned warning, data retention และวิธีหยุด runtime

หลัง Native V1 ผ่าน acceptance ให้เขียน design/plan แยกสำหรับ **AI clip discovery**
เป็นความสามารถแรกที่จะทำให้ผู้ใช้เลือกหลายช่วงที่ระบบเสนอ แก้ไขช่วง/ชื่อ/เหตุผล
แล้วสร้าง EDL ที่ตรวจสอบได้ ก่อนขยายไป auto-reframe, audio, color หรือ B-roll
งานต่อไปนั้นยังไม่รวมใน scope และไม่ควรเริ่มจากการเพิ่ม UI placeholder

## 9. Risks and trade-offs

| ความเสี่ยง | แนวทางควบคุม |
|---|---|
| Electron shell ดูเหมือน browser มากกว่า native UI | ใช้ installer, Start Menu, app identity, native window และ Thai first-run states; ระบุชัดว่า editor หลักยังเป็น web UI |
| Docker เป็น prerequisite หนัก | ตรวจ dependency ก่อน start, ไม่ติดตั้งแทนผู้ใช้, แจ้งทรัพยากรและวิธีแก้ |
| runtime bundle ไม่ตรงกับ source tree | สร้าง bundle ด้วย manifest allowlist และตรวจ Compose build แบบ clean install ใน CI |
| app ชนกับ runtime dev ที่มีอยู่ | ยึด project name `video-postproduction`; health-first attach และไม่ mutate stack ที่ healthy |
| installer unsigned ทำให้ Windows เตือน | จำกัดเป็น local pilot; signing เป็นการตัดสินใจ/ค่าใช้จ่ายแยกต่างหาก |
| shell ไม่เพิ่มความสามารถ AI | บอกเป็น non-goal และให้ AI clip discovery เป็น phase ถัดไปที่ต้องผ่าน design/approval แยก |

## 10. Open approval points

- ยืนยันว่า Native V1 นี้หมายถึง Electron Windows app ที่ครอบ Next.js UI ไม่ใช่
  การเขียน UI ใหม่ด้วย WinUI/Qt
- ยืนยันว่าใช้ Docker Desktop ที่ติดตั้งอยู่แล้วได้ และยังไม่ให้ installer จัดการ
  WSL/Windows features เอง
- ยืนยันว่าเข้าใจข้อจำกัด: scope นี้ทำให้เปิดใช้สะดวกขึ้น แต่ยังไม่เพิ่ม AI
  highlight; ถ้าต้องการให้ Native V1 รวม clip discovery ต้องแก้ scope และ spec ก่อน
- ยืนยันว่า unsigned local installer ที่อาจขึ้น SmartScreen warning ยอมรับได้
  ในช่วงทดสอบ
