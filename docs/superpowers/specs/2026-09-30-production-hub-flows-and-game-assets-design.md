---
title: Production Hub H5 — Flow ที่รันได้ (E13) และ asset สำหรับ Game (E12)
tags: [design, production-platform, workflow, game, handoff]
created: 2026-09-30
updated: 2026-09-30
status: approved-written-spec
description: ออกแบบ hub-flow.v1 ให้ลำดับการส่งไฟล์ข้าม tool รันได้พร้อมจุดอนุมัติ (E13) และ GameIR v2 ที่รับเสียงและโมเดล 3D จากคลัง (E12a/E12b) ก่อนลงมือ
parent: docs/superpowers/specs/2026-09-30-production-hub-and-tool-coverage-design.md
related: [services/production-hub/README.md, packages/workflow-production-ir/workflow_ir/models.py]
---

# Production Hub H5 — Flow ที่รันได้ (E13) และ asset สำหรับ Game (E12)

## 0. สรุป

- สาย handoff 17/20 ใช้งานได้แล้ว (เพิ่ม E12b) เหลือ E8 (รออนุมัติ OCR runtime), E12a (รออนุมัติ glTF importer) และ E13 edge ของ Workflow tool (`hub_send`); runner `hub-flow.v1` ใช้งานได้แล้ว
- ทั้ง E12 และ E13 เปลี่ยน contract ของ tool ปลายทาง จึงต้องผ่าน spec นี้ก่อน

## 1. E13 — `hub-flow.v1` ลำดับการส่งต่อที่รันได้

### ปัญหา

`workflow-ir.v1` เป็น `plan_only` (`external_effects_enabled=false`) และ step มีแค่
`collect_form / validate / render_document / human_review` ไม่ได้อ้าง handoff ของ Hub
การ "แปลง WorkflowIR เป็น pipeline" ตรง ๆ จึงต้องเดาความหมายของ step

### ข้อเสนอ

Hub มี contract ของตัวเอง `hub-flow.v1` และ runner; Workflow tool ภายหลังเพิ่ม step kind `hub_send`
ที่ compile ออกมาเป็น `hub-flow.v1` (ไม่บังคับในรอบแรก — เขียน flow ด้วยมือหรือจาก Console ได้)

```json
{
  "schema_version": "hub-flow.v1",
  "flow_id": "pod-launch",
  "project_id": "pod-launch",
  "steps": [
    {"id": "copy", "send": {"handoff": "E2-captions-to-marketing", "input": {"artifact": "sha256:..."},
                            "params": {"campaign_name": "..."}}},
    {"id": "card", "send": {"handoff": "E6-copy-to-graphics", "input": {"from_step": "copy", "kind": "copy.text",
                            "filter": {"channel": "facebook"}}, "params": {"headline": "..."}}},
    {"id": "review", "gate": {"approve_kinds": ["graphic.png"], "from_step": "card"}},
    {"id": "page", "send": {"handoff": "E7b-graphic-to-web", "input": {"from_step": "card", "kind": "graphic.png"},
                            "with": [{"from_step": "copy", "kind": "copy.text"}], "params": {"...": "..."}}}
  ]
}
```

### Requirement

| ID | Requirement |
|---|---|
| REQ-FLOW-01 | ทุก step `send` ต้องอ้าง handoff ที่ `implemented` และผ่าน validation ของ send เดิม (params, extra kinds) ก่อนเริ่ม flow — ตรวจทั้ง flow ก่อนรัน step แรก |
| REQ-FLOW-02 | `gate` หยุด flow จนทุก artifact ที่ระบุมีสถานะ review `approved` ในโปรเจกต์ (ใช้ระบบ review H3) ไม่มีการอนุมัติอัตโนมัติ |
| REQ-FLOW-03 | สถานะ flow (`pending/running/waiting_approval/completed/failed`) และผลแต่ละ step เก็บใน `hub.db`; รันต่อจาก step ที่ค้างได้ (resume) โดยไม่ส่งซ้ำ step ที่สำเร็จ |
| REQ-FLOW-04 | step ล้ม = flow หยุด บันทึก error; ไม่มี retry อัตโนมัติเกิน `max_attempts` (≤ 3) |
| REQ-FLOW-05 | flow ไม่มี step ที่ส่งออกภายนอก (publish/post/email) — ส่งมอบยังผ่าน delivery package + การอนุมัติของคน |
| REQ-FLOW-06 | lineage ของทุก output ต่อเนื่องจาก input ของ step แรก (ใช้ lineage ของ send เดิม) |

### Task

- [x] E13.1 schema `hub-flow.v1` + validator (edge implemented, อ้าง step ก่อนหน้าเท่านั้น, ไม่มี cycle)
- [x] E13.2 ตาราง `flows`, `flow_steps` + runner (resume, gate, หยุดเมื่อล้ม)
- [x] E13.3 CLI `flow run|status|resume` + API `POST /api/v1/flows`, `GET /api/v1/flows/{id}`, `POST .../resume`, `GET /api/v1/flows` + แท็บ Flow ใน Solo Web (`/production`): รายการ flow, ขั้นและไฟล์ผลลัพธ์, ปุ่มไปหน้าตรวจงานเมื่อติด gate, ปุ่มรันต่อ, ฟอร์มเริ่ม flow จาก JSON
- [x] E13.4 ทดสอบจริง: captions → copy → graphic → (gate) → landing page
- [ ] E13.5 (ภายหลัง) Workflow tool step `hub_send` → compile เป็น `hub-flow.v1`

### หลักฐาน E13 (2026-09-30)

`production_hub/flows.py` + ตาราง `flows`/`flow_steps`; ทดสอบจริง flow `pod-live-launch`:
SRT → E2 campaign → E6 ภาพประกาศ (เลือก copy ด้วย filter `channel=facebook`) → gate รออนุมัติ →
อนุมัติด้วย `review` → resume → E7b landing page; step ที่เสร็จแล้วไม่ถูกส่งซ้ำ และ lineage ของหน้าเว็บย้อนถึง SRT
พบและแก้: content-addressing ทำให้ copy ที่ข้อความเหมือนกันหลายช่องทางเป็น artifact เดียว (attribute ของช่องทางแรกชนะ)
ตัวเลือก input จึงตัด id ซ้ำก่อนนับ

ทดสอบผ่านหน้าเว็บ (flow `pod-ui-check`): เริ่มจากฟอร์ม → หยุดที่ gate → ปุ่มไปหน้าตรวจงาน → อนุมัติ → รันต่อ → เสร็จ 4/4
พบและแก้: ผลลัพธ์ที่เนื้อหาตรงกับไฟล์ที่เคยสร้างในโปรเจกต์อื่นไม่ถูกผูกกับโปรเจกต์ของ send ทำให้ gate อนุมัติไม่ได้ —
send ผูก manifest และผลลัพธ์ทุกไฟล์เข้าโปรเจกต์ปลายทางเสมอ (`project_artifacts`)

## 2. E12 — Game รับ asset จากคลัง

### ปัญหา

`game-ir.v1` มีแค่ layout (`world/player/walls/objectives/exit`) กับ palette พื้นฐาน ไม่มีช่องอ้างไฟล์ภายนอก

### ข้อเสนอ `game-ir.v2` (v1 คงเดิม)

```json
{
  "schema_version": "game-ir.v2",
  "assets": [
    {"asset_id": "music", "kind": "audio", "filename": "music.wav", "sha256": "..."},
    {"asset_id": "coin", "kind": "model", "filename": "coin.glb", "sha256": "..."}
  ],
  "audio": {"music": "music", "collect_sfx": null},
  "skins": {"objective": "coin", "player": null}
}
```

| ID | Requirement |
|---|---|
| REQ-GAM-10 | asset ทุกตัวต้องอยู่ใน asset root และ sha256 ตรง; ไม่มี URL หรือ path นอก root |
| REQ-GAM-11 | audio: WAV/OGG ≤ 20 MB, ≤ 10 นาที ตรวจด้วย ffprobe ก่อน build |
| REQ-GAM-12 | model: ผ่าน GLB preflight เดียวกับ Scene Composer (ไม่มี extension, ≤ 1M triangles) |
| REQ-GAM-13 | reachability validator และ Unity batch test เดิมต้องผ่านเหมือน v1 |
| REQ-GAM-14 | build ที่ใช้ v1 ให้ผลเหมือนเดิม (replay ไม่เปลี่ยน) |

### แบ่งเป็น 2 ขั้น

| ขั้น | ขอบเขต | ต้องการ |
|---|---|---|
| **E12b audio** (ทำก่อน) | Unity import WAV/OGG ได้ในตัว ไม่ต้องเพิ่ม package | เวลาเครื่องสำหรับ Unity batch build |
| **E12a model** | Unity ต้องมี glTF importer (เช่น glTFast) ใน project template | 👤 อนุมัติเพิ่ม package และการดาวน์โหลดครั้งแรก + pin เวอร์ชันใน template |

### Task

- [x] E12.1 `game-ir.v2` model + schema + validator (REQ-GAM-10, 11; REQ-GAM-12 ไปพร้อม E12a) ใน `book-production-platform`
- [x] E12.2 template Unity: AudioSource สำหรับ music/collect SFX (E12b)
- [x] E12.3 Hub adapter E12b: `audio.master` → game (params: `scene_id`, `role` = music|collect_sfx; layout ห้องเก็บของมาตรฐาน)
- [ ] E12.4 👤 ตัดสินใจ glTF importer → template + adapter E12a
- [x] E12.5a Unity batch test + gameplay trace ต่อ build (EditMode/PlayMode/reopen เดิม + ตรวจเสียง)
- [ ] E12.5b `game.build` (WebGL) ตาม F-P1 ของ Game

### หลักฐาน E12b (2026-09-30)

- BPP `11f412d`: `game-ir.v2` (`assets` WAV/Ogg ตรึงด้วย sha256 + `audio.music` / `audio.collect_sfx`) ตรวจ root/hash/≤20 MB/≤10 นาที/ffprobe ก่อนเปิด Unity;
  คัดลอกเข้า `Assets/BookProduction/Audio/` พร้อม `.meta` GUID คงที่; template มี slot เสียง (Music, AudioSource บน GameProgress, AudioListener)
  สร้างใหม่ด้วย `BuildComposerTemplate` ฉากจึงยัง byte-identical; เปิด module ในตัว `com.unity.modules.audio` (มากับ Editor ไม่ต้องดาวน์โหลด)
- v1 เดิม: canonical IR และ worker input เหมือนเดิมทุกไบต์ (6/6); composer เป็น v1a.2 จึงได้ fingerprint ใหม่
- Unity จริง 6000.6.2f1: v1 collection-room ผ่านผ่าน CLI (53 วิ); v2 เพลง WAV 6 วิ + เสียงเหรียญ Ogg ผ่าน (EditMode 2/2, PlayMode 1/1 ตรวจว่าเสียงเก็บของดังครบทุกชิ้น) และ replay ได้
- Hub: `E12b-audio-to-game` + ingest `game-production.qa.v1` → `game.unity-project` (zip แบบ deterministic) ทดสอบจริงจาก `master.wav` ในคลัง
  ได้ `pod-launch-arcade-unity-project.zip` ที่ lineage ย้อนถึงเสียงต้นทาง
- ข้อจำกัด: เพดานเวลา Unity 120 วิ ต่อ job (สัญญาเดิมของ Game) — compile รอบแรกของ project ใช้ 25–171 วิ ขึ้นกับโหลดเครื่อง
  ครั้งแรกที่ส่งผ่าน Hub จึงหมดเวลา; build ถูกสร้างด้วยการรันทดสอบที่ขยายเวลาเฉพาะรอบนั้นแล้ว Hub ใช้ผลแบบ replay; output ของเกมอยู่ที่
  `%LOCALAPPDATA%\BookProduction\Game` (path สั้นตามคำแนะนำของ Unity, ตั้งเองได้ด้วย `PRODUCTION_HUB_GAME_OUTPUT`)

## 3. สิ่งที่เจ้าของต้องตัดสินใจ

| # | เรื่อง | ค่าที่แนะนำ |
|---|---|---|
| H5-D1 | ใช้ `hub-flow.v1` ของ Hub แทนการตีความ WorkflowIR เดิม | ใช่ |
| H5-D2 | gate ใช้ review ของโปรเจกต์ (ไม่มี auto-approve) | ใช่ |
| H5-D3 | E12 ทำ audio ก่อน model | ใช่ |
| H5-D4 | อนุญาตเพิ่ม glTF importer package ใน Unity template (ต้องดาวน์โหลด) | รอเจ้าของ |
| H5-D5 | E8: อนุมัติติดตั้ง Poppler + Tesseract (ไทย/อังกฤษ) แบบ pin เวอร์ชัน | รอเจ้าของ |
| H5-D6 | เพดานเวลา Unity ต่อ job: คง 120 วิ หรือขยาย (เช่น 300 วิ) เพื่อให้ compile รอบแรกผ่านบนเครื่องที่โหลดสูง | รอเจ้าของ |
