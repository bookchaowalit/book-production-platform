---
title: 3D Product Scene Composer V1A — Design
tags: [design, 3d, blender, scene-ir, production-platform]
created: 2026-09-23
updated: 2026-09-23
status: implementation-complete-local
description: Deterministic local Blender scene composition from a bounded SceneIR and one local GLB asset, producing an editable blend file, render, and QA evidence.
---

# 3D Product Scene Composer V1A

## 1. เป้าหมาย

สร้างฉากนำเสนอสินค้าแบบ product-hero จากคำขอที่มีโครงสร้างและไฟล์ asset ในเครื่อง โดยใช้กฎและ Blender เป็นตัวประมวลผล ผลลัพธ์ต้องแก้ไขต่อใน Blender ได้ ตรวจย้อนกลับได้ และสร้างซ้ำได้จาก input กับ recipe รุ่นเดิม

แนวทางที่ผู้ใช้ยืนยันสำหรับ V1A:

- ใช้ Blender แบบ headless เป็น engine และส่งมอบไฟล์ `.blend` ที่แก้ไขต่อได้ พร้อมภาพ render
- รับ `SceneIR` แบบมี schema version และ asset `.glb` ภายในเครื่อง
- ใช้ Scene Composer แบบ deterministic; เส้นทางปกติมี `model_calls=0`
- รวม adapters และ product modules ไว้ใน production-platform capability repo เดียว ไม่สร้าง repo แยกต่อโดเมน
- ตรวจความถูกต้องและ QA ก่อนเผยแพร่ artifacts

ผู้ใช้อนุมัติให้เริ่ม implementation แล้ว; เอกสารนี้กำหนดเป้าหมายและเกณฑ์ ไม่ใช่หลักฐานว่า benchmark หรือความพร้อมใช้งานผ่านแล้ว

## 2. ขอบเขต V1A

คำสั่ง local CLI รับ SceneIR หนึ่งชุดและ asset `.glb` หนึ่งไฟล์ต่อ job จากนั้นสร้างฉาก product-hero ด้วย composition presets ที่กำหนดไว้ล่วงหน้า ตัว asset อาจประกอบด้วยหลาย mesh ภายใน GLB เดียวได้

V1A รองรับ:

- import geometry และวัสดุพื้นฐานจาก GLB ที่ฝัง resource ไว้ครบ
- จัดวางและปรับสเกลสินค้าแบบ deterministic จาก bounding box ตาม recipe
- composition, camera, background, lighting และ material presets แบบ allowlist
- render profiles แบบ allowlist
- บันทึกฉาก `.blend` และ pack texture/resource ที่นำเข้าไว้ในไฟล์ เพื่อให้เปิดไฟล์ได้โดยไม่พึ่ง path ต้นฉบับ
- render PNG จาก `.blend` ที่เปิดใหม่ใน Blender process แยก
- QA manifest ที่บอกที่มา เวอร์ชัน ผลตรวจ และขนาดไฟล์

V1A ไม่ทำ:

- text-to-3D, image-to-3D, สร้าง geometry หรือ texture ใหม่ด้วย generative AI
- LLM planner, asset search, remote asset download หรือ API ภายนอก
- Blender add-on หรือ web UI; Blender UI ใช้เปิดและแก้ `.blend` ที่ส่งออกเท่านั้น
- หลาย asset ต่อ job, animation, rigging, physics, simulation หรือ batch render
- arbitrary Python, Blender scripts, shader graphs หรือ material node graph ที่ผู้ใช้ส่งมา
- งาน CAD ที่ต้องรักษามิติหรือ tolerance ทางวิศวกรรม

## 3. Product boundary และ ownership

ผลิตภัณฑ์จะอยู่ใน domain capability repository เดียวที่เสนอชื่อ `book-production-platform` ภายใต้ Book Dev portfolio boundary:

``

Repository นี้จะเป็นเจ้าของ SceneIR schemas, deterministic adapters, Blender runner, fixtures, tests, dependencies, local runtime rules และ product README รวมถึง adapters สำหรับ production domains อื่นที่ใช้ lifecycle และ contracts ร่วมกัน

Solo Empire เป็น control plane และ catalog: เก็บ design/plan, verified repository registration และ pilot evidence เท่านั้น ไม่เก็บ product runtime source หรือ generated customer artifacts ไว้ใน root repository ตอนเริ่ม implementation สร้าง local Git checkout สำหรับ `book-production-platform` ตาม path ข้างต้น; checkout นี้ไม่มี remote และยังไม่อยู่ใน external GitHub catalog เพราะ guard กำหนด URL และ commit SHA ที่ตรวจสอบได้ การสร้าง remote หรือประกาศว่ามี deployment ต้องทำแยกเมื่อได้รับอนุมัติและมีข้อมูลจริง

V1A เป็น local CLI ไม่มี server, database, job queue, authentication, hosted storage หรือ deployment target

## 4. Input contract: `scene-ir.v1`

ตัวอย่าง input ขั้นต่ำ:

    {
      "schema_version": "scene-ir.v1",
      "asset": {
        "path": "product.glb"
      },
      "recipe": "product-hero.studio.v1",
      "composition": "three-quarter.v1",
      "lighting": "softbox-three-point.v1",
      "background": "studio-neutral.v1",
      "material_preset": "preserve-source.v1",
      "render_profile": "square-1024.v1"
    }

ข้อกำหนด:

- `asset.path` เป็น relative path ใต้ input root ที่ผู้ใช้ระบุ; absolute path, URL, path traversal และ symlink ที่ใช้ในเส้นทาง asset ถูกปฏิเสธ
- ค่า recipe, composition, lighting, background, material และ render profile ต้องเป็น ID ใน registry แบบ allowlist และมีเวอร์ชัน
- ปฏิเสธ field ที่ไม่รู้จักและค่าตัวเลขที่อยู่นอกช่วง; ห้ามตีความ field ที่หายไปด้วย default ที่เปลี่ยนภาพสำคัญโดยเงียบ
- V1A ไม่รับ script, expression, external URI, texture link ภายนอก, animation, skin, physics หรือ glTF extension ที่ไม่อยู่ใน compatibility allowlist
- สร้าง schema แบบ strict และ versioned; การเปลี่ยน semantics ที่กระทบภาพหรือ replay ต้องเพิ่ม recipe/schema version
- Input ไม่รวม output path หรือ option ที่ทำให้เขียนทับไฟล์เดิม

## 5. Processing flow

    SceneIR + input root
      → strict schema / path / budget validation
      → inspect GLB container, embedded resources, extensions, and declared mesh budgets
      → canonicalize IR and hash source asset
      → replay lookup by content fingerprint
      → create isolated job staging directory
      → trusted Blender runner imports the GLB and applies versioned presets
      → pack imported resources and save scene.blend
      → start a fresh Blender process, reopen scene.blend, and render render.png
      → validate scene, render, and manifest
      → atomically publish the complete job directory

The Blender runner is trusted, versioned product code. It receives validated data and fixed preset identifiers; user input is never interpolated into Python or shell source. Invoke Blender with an argument array, a per-job deadline, a dedicated working directory, and captured bounded diagnostics. The standard path performs no network request or model inference.

The target runtime is Blender 5.2.2 LTS in background mode. The exact binary must be present and version-checked; V1A does not download or install Blender automatically. Before freezing the render profile, benchmark supported Blender render engines on the golden fixtures and select the lowest-cost profile that passes the visual and artifact gates. Record the selected engine, color-management settings, device policy and Blender build in the recipe version. Do not silently fall back to an unbenchmarked engine or device.

## 6. Pilot resource limits

These are conservative pilot caps; benchmark evidence may justify changing them before the V1A contract is frozen. They cannot be disabled by a job.

| Resource | Pilot limit |
|---|---:|
| GLB file size | 256 MiB |
| Imported object count | 100 |
| Total triangles | 1,000,000 |
| Texture dimension | 4,096 px maximum on either side |
| Total embedded texture bytes | 128 MiB |
| Render profiles | `square-1024.v1` or `landscape-1920x1080.v1` |
| Wall-clock deadline | 300 seconds per job |

Validation rejects an exceeded or unmeasurable limit safely. The runner terminates after the deadline and never publishes staged artifacts from a timed-out job. V1A records process timing and output sizes. Peak memory is recorded only when the host can measure it reliably; lack of a memory metric is reported as unknown rather than estimated.

The render-engine benchmark must use the same fixtures and selected render settings. Report accepted scenes, render latency, failures, output bytes and visual-review result by profile. Do not publish an unmeasured throughput or monetary-savings claim.

## 7. Security and local data boundary

- V1A is for trusted local GLB assets; it is not a hostile-file upload service or OS-level sandbox.
- Reject URL, absolute path, path traversal, symlinked asset paths, external GLTF resources, unknown extensions and script-bearing input.
- Parse and enforce container/file caps before handing data to Blender wherever the GLB structure makes them available; enforce mesh/texture limits again after import and stop before rendering if limits fail.
- Run each job in a unique staging directory; pass only required paths and safe environment settings to Blender; never use shell interpolation.
- Do not read credentials or unrelated workspace files. Do not store absolute local paths, raw asset payloads, or user prompts in QA telemetry.
- Keep diagnostic output bounded and sanitized. Stable failure codes may include safe stage names but not shell commands, environment variables or credential-shaped values.
- Do not process untrusted uploads until a separately reviewed OS-level sandbox denies network access and enforces CPU, memory, filesystem and process limits.

## 8. Artifact and replay contract

Every accepted job publishes exactly:

- `scene.blend` — editable, self-contained Blender scene with packed imported image resources.
- `render.png` — image rendered after reopening `scene.blend` in a fresh background process.
- `qa-manifest.json` — machine-readable lineage and QA record.

The manifest records the normalized SceneIR hash, source asset SHA-256, content fingerprint, schema and recipe versions, Blender version/build, render engine/profile, scene counts, QA checks, output hashes and dimensions, process result, wall time, bytes written, and `model_calls: 0`. It stores paths relative to the job directory. Monetary compute cost is `unknown` until a local resource rate card is configured.

The fingerprint is derived from canonical SceneIR, GLB content hash, composer/recipe version, exact Blender build, and render profile. A matching replay first verifies every published hash and required QA field, then reuses the existing artifacts. A corrupted or incomplete replay target fails closed; it is never overwritten automatically. Changed input bytes or settings create a different fingerprint and a separate output directory.

Write into staging on the same filesystem as the output root. Promote the complete directory only after all checks pass, using an atomic publication step. Output paths are content-addressed or otherwise unique; the product never overwrites an existing user artifact. A crash, timeout or failed QA leaves no accepted artifact reference.

## 9. Quality gates and acceptance

A job is `accepted` only when all automated checks pass:

1. SceneIR schema, allowlists, input-root containment and all measurable caps pass.
2. Blender exits successfully and saves a readable `.blend`.
3. A separate Blender process reopens the saved file without missing packed resources and finds an active camera, expected imported asset and expected render settings.
4. The reopened scene renders a valid PNG with the selected profile dimensions; file structure and pixel content checks reject missing, zero-byte or blank renders.
5. Manifest records matching hashes for the published `.blend` and PNG; the final directory is visible only after successful verification.
6. Replaying an identical fingerprint verifies and returns the same published artifact hashes without a second render.

The initial synthetic acceptance suite contains 20 cases: valid inputs, boundary values, malformed SceneIR/GLB, unsupported extensions, path escapes, symlink paths, exceeded budgets, timeout, corrupt replay artifacts and successful replay. The exact fixture mix is pinned in the implementation plan. At least five representative golden renders receive a human visual review before the V1A pilot is declared usable; automated pixel or histogram checks do not claim to judge composition quality. Invalid input and failed jobs must produce stable error codes and no accepted output.

## 10. AI and cost policy

The standard V1A path is deterministic and uses no LLM, vision model or generative model. `model_calls` is always zero. Geometry fitting, allowlist routing, schema validation, hashing, replay, rendering and artifact QA use ordinary code.

Measure wall time, Blender build/profile, process exit status, input/output bytes and replay status. Record actual CPU/GPU resource time when the host exposes a reliable measurement. Report monetary cost as unknown until a rate card exists; local rendering is not described as free. No model budget, provider key, hosted inference or cloud GPU is required for V1A.

A future prompt-to-SceneIR planner or generated-asset feature is a separately scoped capability. It must be opt-in, bounded by per-job and daily cost limits, pass strict schema validation, and be evaluated against the deterministic baseline before it can enter the default path.

## 11. Failure, rollback and rollout

Stable failure categories include invalid IR, unsafe/missing asset, unsupported GLB feature, resource cap exceeded, Blender unavailable/version mismatch, Blender failure, deadline exceeded, reopened scene invalid, render invalid, publication failure and replay integrity failure.

On failure, remove only the current job's staging files and retain sanitized diagnostic metadata. Never delete source assets or previously published artifacts. A user can retry with the same fingerprint after transient runtime failure; a successfully published replay is immutable. Roll back by disabling the CLI capability or pinning a prior recipe/Blender build. There is no database migration, remote side effect or deployment step in V1A.

Implementation evidence for the local synthetic pilot is recorded below. This confirms the V1A code and pilot acceptance gates; it does not establish broad customer-asset coverage or an upload-ready security boundary.

## 12. Local implementation evidence (2026-09-24)

- The implementation was reconstructed in the single standalone `book-production-platform` product repository on `main` at commits `14c087e`, `467ace0`, `cec19bf`, `780a049`, `5caf90a`, `b91fb90` and `0f0eb77`; no remote is configured, so no catalog URL or published release is claimed.
- Blender 5.2.2 LTS build `d13f752e3b9c` was installed locally after verifying the official archive SHA-256. The worker pins the exact build in its job fingerprint.
- The installed `book-production` CLI composed a local sample into exactly `scene.blend`, `render.png` and `qa-manifest.json`; a second invocation verified the same hashes and returned `replayed` with `model_calls=0`.
- Final synthetic acceptance: 20/20 cases matched expectation: 5 accepted golden renders, 5 verified exact replays, 10 stable rejections, 2 tampered-artifact QA failures and 0 unexpected cases. Both tampered replays returned `replay_integrity_error`.
- Five upright synthetic product renders were visually inspected for framing, orientation, clipping and readable shading. A fresh Blender process reopened the `.blend`, checked the imported product mesh, packed texture evidence and recipe settings, and rendered after the staging copy of the source GLB was removed.
- Selected default: EEVEE 64 samples. Final benchmark mean reopen-and-render time was 2.316 s for `square-1024.v1` and 3.300 s for `landscape-1920x1080.v1`. The Cycles CPU 32-sample candidate measured 10.356 s and 20.711 s respectively on this host. These measurements are local observations, not throughput guarantees.
- The final post-review package suite passed 49 tests; one symlink test was skipped because this Windows host denies symlink creation. Review findings were resolved: malformed accessor types and oversized JSON integers produce stable errors, deeply nested SceneIR/replay JSON is bounded by stable errors, and a receipt outside the three-artifact bundle pins all artifact hashes including the manifest so metadata-only tampering is rejected. The receipt patch was unit-tested; the Windows Store Blender package denied direct headless process access during this final rerun, so the earlier real Blender smoke and render benchmark remain the runtime evidence. LLM/model calls are zero. Monetary compute cost remains unknown without a local rate card; local rendering still consumes machine resources.

The pilot uses synthetic fixtures. Representative real product GLBs still need evaluation. V1A accepts trusted local files only and is not an OS-level sandbox for hostile uploads. A Blender add-on, web interface, published repository and production deployment are outside this local implementation.

## 13. References

- [Blender 5.2 LTS release](https://www.blender.org/releases/5-2/)
- [Blender command-line arguments and background mode](https://docs.blender.org/manual/en/5.2/advanced/command_line/arguments.html)
- [Blender glTF 2.0 import/export manual](https://docs.blender.org/manual/en/5.2/addons/scene_gltf2.html)
- Repository Boundaries & Scaling Strategy (Solo Empire `systems/architecture/repository-boundaries.md`)
- [Logic-first Production Platform design](2026-09-22-logic-first-production-platform-design.md)
- [Production product delivery order](../plans/2026-09-22-production-platform-next-products.md)
