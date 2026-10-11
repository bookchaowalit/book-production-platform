---
title: Game Scene Prototype Builder V1A — Design
tags: [design, game, unity, game-ir, production-platform]
created: 2026-09-24
updated: 2026-09-24
status: proposed-written-design
description: Deterministic Unity 2D prototype composition from a bounded GameIR, producing an editable scene and gameplay QA evidence.
---

# Game Scene Prototype Builder V1A

## 1. เป้าหมายและหลักการ

สร้างต้นแบบเกม 2D แบบหนึ่งฉากจากคำขอที่มีโครงสร้าง โดยให้กฎและ Unity เป็นตัวประกอบฉากและตรวจพฤติกรรม ผลลัพธ์ต้องเปิดแก้ไขต่อใน Unity ได้ เล่นผ่าน gameplay loop ที่กำหนดไว้ และสร้างซ้ำได้เมื่อ `GameIR`, recipe และ Unity build เหมือนเดิม

แนวทาง V1A ที่เสนอ:

- ใช้ Unity Editor แบบ batch เป็น engine และส่งออก Unity project ที่เปิดแก้ไขต่อได้ พร้อมไฟล์ `.unity` และ QA manifest
- รับ `game-ir.v1` แบบมี schema version; ไม่สร้างเกมจาก free-form prompt ในรุ่นนี้
- จำกัด gameplay เป็นฉาก top-down 2D ห้องเดียว: ผู้เล่นเดิน, เก็บ objective ที่กำหนด, และเปิดทางออกเมื่อเก็บครบ
- ประกอบฉากจาก primitive sprites, colliders และ behavior templates ที่มากับผลิตภัณฑ์; ไม่ดาวน์โหลดหรือสร้าง asset
- ใช้ deterministic compiler; เส้นทางปกติรายงาน `model_calls=0`
- เก็บ adapter, schema, fixtures, tests และ CLI ใน `book-production-platform`; ผลลัพธ์ Unity เป็น artifact แยกตาม job

ความสำเร็จวัดจากฉากเปิดได้, input ที่กำหนดให้เกิด event trace ตามคาด, collision/เป้าหมายถูกต้อง, replay hash ตรง และ invalid input ถูกปฏิเสธก่อน publish ไม่ใช้จำนวน agent หรือคุณภาพภาพที่โมเดลประเมินตนเองเป็นเกณฑ์

## 2. ขอบเขต V1A

หนึ่ง job รับ `GameIR` และสร้างต้นแบบที่เล่นได้หนึ่งฉาก รองรับ:

- world bounds แบบกริด 2D, spawn point หนึ่งจุด, exit หนึ่งจุด, static wall และ collectible แบบ allowlist
- player controller แบบ top-down ที่ใช้ input action ชุดตายตัว เช่น `move_x` และ `move_y`
- เงื่อนไขชนะเมื่อเก็บ objective ครบและผู้เล่นแตะ exit; ก่อนนั้น exit ต้องไม่จบเกม
- visual จากสีและ primitive shapes ที่กำหนดใน palette allowlist; ไม่มี external asset reference
- Unity EditMode validation และ PlayMode gameplay test ที่ขับด้วย input fixture แบบกำหนดแน่นอน
- artifact manifest, hash verification, replay protection, resource caps และ benchmark สังเคราะห์

V1A ไม่ทำ:

- แปลงข้อความอิสระเป็นเกม, LLM planner, generative asset, voice, image หรือ animation generation
- platformer, physics-based puzzle, multiplayer, dialogue trees, procedural campaign หรือหลายฉาก
- script, shader, prefab, package, URL, assembly หรือ C# ที่ผู้ใช้ส่งมา
- เปิดใช้ ads, analytics, network, purchases, leaderboard, cloud save หรือ external API
- build/sign/publish เกมขึ้น store หรือสั่งให้ Unity deploy ไปยังอุปกรณ์จริง
- รับ project หรือ asset จากผู้ใช้ที่ไม่เชื่อถือ; รุ่นนี้เป็น local pilot สำหรับ input ที่ตรวจขนาดและรูปแบบแล้ว

## 3. Ownership และ runtime boundary

Product code อยู่ใน `book-production-platform` ที่ลงทะเบียนเป็น shared domain-capability repository:

``

ขอบเขต product code ประกอบด้วย `GameIR`, validator, deterministic scene compiler, Unity worker templates, CLI, fixture และ tests ภายใน repo นี้ ไม่เพิ่ม agent runtime หรือ repository ต่อเกม ผล Unity project ที่สร้างเป็น output ของ job ใน output root ที่ผู้ใช้ระบุ โดยไม่เขียนลง source repository อัตโนมัติ

ปัจจุบัน `bookchaowalit-game-engine-mobile` ยังไม่มี commit และ remote `main` ไม่มี ref ที่ตรวจพบ; ส่วน `book-production-platform` เป็น local checkout บน `main` แต่ไม่มี Git remote ที่ตั้งค่าไว้ จึงออกแบบให้เป็น local pilot และยังไม่อ้างว่ามี published release. มี `bookchaowalit-game-collection-frontend` ซึ่งเป็นเกมเว็บอยู่แล้ว; V1A นี้เพิ่มเครื่องมือสร้าง Unity-native prototype และไม่แทนที่หรือย้ายเกมเว็บดังกล่าว

Unity Editor ต้องติดตั้งและมี license/runtime ที่ใช้ batch mode ได้อยู่แล้ว; V1A ไม่ดาวน์โหลด Editor, package หรือ asset อัตโนมัติ ไม่ถือว่า repo เกมมือถือที่ยังว่างเป็นเจ้าของ adapter หากภายหลังต้องนำ scene ไปต่อใน game app จริง ให้เชื่อมผ่าน artifact contract และตรวจ owner ของแอปแยกต่างหาก

Solo Empire เป็น control plane/catalog: เก็บ spec, plan, verified ownership และ pilot evidence เท่านั้น ไม่เก็บ generated Unity projects, customer assets หรือ runtime source ของ product capability ใน root repo

## 4. Input contract: `game-ir.v1`

ตัวอย่างคำขอเชิงแนวคิด:

    {
      "schema_version": "game-ir.v1",
      "scene_id": "crystal-room",
      "template": "top-down-collect.v1",
      "world": {"width": 12, "height": 8, "tile_size": 1.0},
      "player": {"id": "player", "spawn": [1, 1], "speed": 4.0},
      "walls": [{"id": "north-wall", "cells": [[0, 7], [1, 7]]}],
      "objectives": [{"id": "crystal-a", "cell": [4, 3]}],
      "exit": {"id": "exit", "cell": [10, 6]}
    }

ข้อกำหนดของ contract:

- JSON ต้องมี schema version, field allowlist, ชนิดข้อมูล และ numeric range ที่ชัดเจน; reject unknown fields และ NaN/infinity
- IDs ต้องไม่ซ้ำ และใช้รูปแบบที่จำกัด; จุด spawn, wall, objective และ exit ต้องอยู่ในขอบเขตกริดและไม่ชนกันตามกติกา
- dimensions, entity counts, tile size และ player speed มีเพดานตายตัว; ห้าม input ปิดหรือขยาย cap เอง
- template และ behavior เป็น versioned IDs ใน registry; ไม่มีชื่อ class, file path, Unity type หรือ code string ใน input
- text ที่ใช้เป็นชื่อหรือป้ายเป็นข้อมูลแสดงผลเท่านั้นและ escape ตาม renderer; ห้ามนำไปเป็น path, identifier, command หรือ code
- canonicalization ต้องคงลำดับตาม ID หรือพิกัดตามกติกาที่บันทึกไว้ เพื่อให้ hash และ output เสถียร

## 5. Processing flow

    GameIR + Unity runtime
      → strict schema / limits / geometry validation
      → canonicalize IR and compute content fingerprint
      → replay lookup and verify existing receipt/artifacts
      → create isolated staging Unity project from pinned templates
      → trusted Editor worker builds the scene from allowlisted entities
      → run EditMode checks and deterministic PlayMode input trace
      → reopen generated scene in a fresh Unity batch process
      → verify scene structure, gameplay evidence and output hashes
      → atomically publish the Unity project, scene and QA manifest

Unity worker เป็น product code ที่เชื่อถือได้และรับเฉพาะ IR ที่ผ่าน validation แล้ว ห้าม interpolate input ลง C#, command line หรือ Unity YAML; เรียก executable ด้วย argument array, working directory แยก, deadline และ log size cap การประกอบฉากใช้ Unity API ผ่าน Editor worker ที่ pin version ไว้

PlayMode acceptance ใช้ input trace คงที่: ยืนยันว่าผู้เล่นเคลื่อนที่ตามทิศ, ชน wall แล้วไม่ทะลุ, เก็บ objective แล้ว count เพิ่ม, แตะ exit ก่อนเก็บครบแล้วยังไม่ชนะ และชนะได้หลังเก็บครบเท่านั้น ค่าที่ตรวจได้มาจาก runtime event/state ไม่ใช้ภาพหน้าจอเป็นหลักฐาน gameplay เพียงอย่างเดียว

## 6. Resource limits และ artifacts

เพดานเริ่มต้นสำหรับ pilot (ปรับได้ก่อนตรึง contract เมื่อมี benchmark):

| ทรัพยากร | เพดานเริ่มต้น |
|---|---:|
| GameIR JSON | 256 KiB |
| ขนาด world | 64 × 64 cells |
| จำนวน entities รวม | 256 |
| จำนวน static walls | 192 |
| จำนวน objectives | 32 |
| Unity worker deadline | 120 วินาทีต่อ job |
| Published output | Unity project หนึ่งชุด, หนึ่ง scene และ QA manifest |

Artifact ที่ accepted ต้องมีอย่างน้อย:

- `project/` — Unity project เปิดแก้ไขได้ สร้างจาก template ที่ pin version
- `project/Assets/Scenes/<scene_id>.unity` — scene ที่ Unity เปิดใหม่ได้
- `qa-manifest.json` — source hash, normalized GameIR hash, recipe/schema/worker/Unity versions, checks, event-trace summary, output hashes, bytes, elapsed time, replay state และ `model_calls: 0`

เก็บ path ใน manifest แบบ relative เท่านั้น ใช้ staging directory ภายใน output filesystem และ publish หลัง QA ผ่านครบทุกข้อเท่านั้น replay ต้องตรวจ receipt และ hash ของ scene, project manifest และ QA manifest ก่อนคืนผล; output ที่ผิด hash หรือไม่ครบต้อง fail closed และห้ามเขียนทับอัตโนมัติ

## 7. Safety และ local data boundary

- รับเฉพาะ JSON ขนาดจำกัด; reject absolute paths, URLs, symlinks, external resources, arbitrary Unity packages และ code-like fields
- Compiler สร้างเฉพาะ primitive objects และ components จาก allowlist; ห้าม reflection หรือ dynamic type loading จาก input
- ปิด network capability ของ generated project และไม่ติดตั้ง package ระหว่าง build
- จำกัดเวลารัน, จำนวน objects, ขนาด logs และพื้นที่ staging; timeout หรือ QA fail ต้องไม่มี accepted manifest
- ลบได้เฉพาะ staging directory ของ job ปัจจุบัน; ไม่ลบ source หรือ artifact เก่าที่ publish แล้ว
- telemetry เก็บ hash, version, stable error code และ resource totals; ไม่เก็บ private payload หรือ absolute local path
- V1A เป็น local pilot ไม่ใช่ OS sandbox สำหรับ Unity project ที่ไม่เชื่อถือ; ก่อนเปิดรับ upload ต้องมี process/filesystem/network isolation แยกต่างหาก

## 8. AI และ cost policy

เส้นทางมาตรฐานใช้ schema validation, geometry checks, deterministic layout, Unity Editor และ scripted gameplay tests ไม่มี LLM, vision, model download หรือ remote call และต้องรายงาน `model_calls=0` อย่างชัดเจน ค่า CPU/เวลาเป็นต้นทุนการใช้เครื่อง; monetary cost เป็น `unknown` จนกว่าจะมี rate card ที่วัดได้

ภายหลังอาจเพิ่ม intent-to-GameIR extraction เป็น optional adapter เพื่อแปลง brief เป็น field proposals เท่านั้น โดยแยก `provided`, `inferred`, `missing` และ provenance ต่อ field; ข้อมูลที่จำเป็นแต่ขาดต้องคืน `needs_input` ห้ามสั่ง compiler ด้วย output ที่ยังไม่ผ่าน schema, geometry rules, budget และ user review การทดลองดังกล่าวต้องมี spec, benchmark, cost cap และ approval แยกก่อนเปิด provider

## 9. Acceptance และ rollback

Synthetic acceptance suite มี 20 cases ตามสัญญาที่จะตรึงในแผน: 5 accepted scenes, 5 exact replays และ 10 stable rejections ครอบคลุม malformed JSON/IR, duplicate IDs, overlap/out-of-bounds, cap exceed, unsupported behavior, corrupt output และ replay tampering เกณฑ์ผ่านคือไม่มี unexpected outcome, replay ไม่ rerun worker, output hashes ตรง, `model_calls=0` และทุก valid gameplay fixture ผ่าน event trace ที่คาดไว้

ก่อนประกาศ V1A usable ต้องเปิด generated scene ใน Unity Editor อย่างน้อยหนึ่งครั้ง ตรวจ hierarchy และเล่นด้วยมือหนึ่งรอบ พร้อมบันทึก Unity build และข้อสังเกตจากผู้ตรวจ อัตโนมัติยืนยันโครงสร้างและพฤติกรรมตาม test เท่านั้น ไม่อ้างว่ารับประกันความสนุกหรือ game balance

การ rollback ทำได้โดยปิด CLI capability หรือเลือก compiler/template version ก่อนหน้า ไม่มี database migration, remote side effect, store publishing หรือ deployment ใน V1A หาก Unity batch/license gate ใช้ไม่ได้ ให้รายงานว่า runtime acceptance ยังไม่ผ่าน ไม่อ้างว่า unit tests แทนการเปิด scene ได้

## 10. ข้อสมมติที่รอผู้ใช้ทบทวน

- Unity-native เป็น output target; HTML5 ไม่ใช่ deliverable หลักของ Game V1A
- `book-production-platform` เป็นเจ้าของ adapter ส่วน generated Unity project เป็น artifact ของงาน ไม่เขียนลง mobile app repo โดยอัตโนมัติ
- ขอบเขตเกมแรกเป็น top-down 2D collection loop ห้องเดียว ไม่ใช่ general-purpose game generator
- ไม่มี LLM หรือ generative asset ใน V1A; ถ้าจะเพิ่มต้องผ่าน design และ cost gate แยก

รายการนี้เป็น design proposal สำหรับ review; ยังไม่มี implementation plan, benchmark result หรือ product-code change สำหรับ Game V1A
