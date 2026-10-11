---
title: CAD Parametric Part Generator V1A — Design
tags: [design, cad, freecad, part-ir, production-platform]
created: 2026-09-25
updated: 2026-09-25
status: approved-written-spec
description: Deterministic local mounting-plate generation from a bounded PartIR, with an editable FreeCAD document, neutral exports, preview, and QA evidence.
---

# CAD Parametric Part Generator V1A

## 1. เป้าหมายและผู้ใช้

สร้าง mounting plate แบบพาราเมตริกจากข้อมูลที่มีโครงสร้างชัดเจน ให้ผู้ใช้ได้โมเดล native ที่เปิดแก้ไขต่อใน FreeCAD และไฟล์กลางสำหรับส่งต่อ โดยทุกมิติและ feature มาจาก `PartIR` ที่ผ่านกฎตรวจสอบ ไม่ให้โมเดลเดาหรือเติมขนาดเอง

ผู้ใช้เป้าหมายคือผู้ทำแบบหรือผู้ทำต้นแบบที่ต้องการสร้างแผ่นยึดรูปแบบซ้ำ ๆ จากขนาดและระยะรูที่ระบุไว้แล้ว ความสำเร็จของ V1A คือ input ที่ valid สร้างไฟล์ครบชุดที่เปิดตรวจได้, geometry ผ่าน invariant, replay ตรวจ hash ได้, input ที่ไม่ผ่านถูกปฏิเสธก่อนเผยแพร่ output และเส้นทางปกติมี `model_calls=0`.

แนวทางที่ผู้ใช้อนุมัติในขั้นออกแบบ:

- ใช้ FreeCAD เพื่อส่งไฟล์ native `.FCStd` ที่แก้ไขต่อได้
- รับ `PartIR` สำหรับ mounting plate ที่จำกัด feature และค่าพารามิเตอร์
- ส่งออก `.FCStd`, STEP, STL, preview และ QA manifest
- ใช้การประมวลผล deterministic และไม่เรียก LLM ใน V1A
- เก็บ adapter ใน `book-production-platform` ซึ่งเป็น production-platform repo กลางที่มีอยู่ ไม่สร้าง repo หรือ agent runtime ใหม่

ผู้ใช้อนุมัติ spec นี้เมื่อ 2026-09-25 จึงเริ่มเขียน implementation plan ได้ การอนุมัตินี้ยังไม่ใช่การอนุมัติให้ลงมือเขียน product code หรือดาวน์โหลด/ติดตั้ง CAD runtime; ต้องรีวิวแผนและเลือกวิธีดำเนินงานก่อน

## 2. ขอบเขต V1A

V1A มี local CLI สำหรับสร้างชิ้นงานหนึ่งชิ้นต่อ job จาก `PartIR` หนึ่งชุด ใช้ template เดียวคือ `mounting-plate.v1` และรูทะลุสี่รูแบบสมมาตร `four-corner.v1`.

รองรับ:

- แผ่นสี่เหลี่ยมมุมฉากหนึ่ง body
- ระบุความกว้าง ความสูง ความหนา เส้นผ่านศูนย์กลางรู และระยะศูนย์กลางรูจากขอบด้าน X/Y
- สร้าง feature tree ที่แก้ไขต่อใน FreeCAD ได้ โดยใช้ sketch, pad และ pocket ที่สร้างจากค่าพารามิเตอร์
- ส่งออก native document, STEP, STL และภาพ preview แบบ isometric ที่สร้างจาก geometry เดียวกัน
- ตรวจ geometry หลัง recompute และเปิดไฟล์ที่บันทึกใหม่ใน FreeCAD process แยก
- สร้าง manifest, content fingerprint, artifact hashes และ replay receipt

ไม่รวม:

- การสร้างหรือปรับมิติจากข้อความ/ภาพ, LLM, image generation หรือการค้นหา component
- arbitrary Python, macro, expression, user-supplied sketch หรือ feature ที่ไม่อยู่ใน allowlist
- profile นอกเหนือจากแผ่นสี่เหลี่ยม, รูแบบอื่น, เกลียว, countersink, fillet/chamfer, หลาย body หรือ assembly
- drawing, dimension annotations, material selection, FEA, DFM, CAM, toolpath หรือการสั่งเครื่องผลิต
- การรับไฟล์ CAD จากผู้ใช้ภายนอก, server/API, database, cloud storage หรือ deployment
- การรับรองว่าเหมาะกับ load, safety-critical use หรือการผลิตจริง

## 3. ขอบเขต repository และเจ้าของ

Implementation เป็น domain adapter ใน repository ที่มีอยู่:

``

Repository นี้เป็นเจ้าของ CAD IR, schema/validation, FreeCAD worker, exporter, preview generation, CLI, fixtures, tests, runtime pin และ product README. `solo-empire` เป็นเจ้าของ spec, implementation plan, roadmap/catalog updates และ pilot evidence เท่านั้น ไม่เก็บ runtime source หรือ job artifacts ไว้ใน root repository.

V1A เป็น local CLI ไม่มี service, authentication, remote side effect หรือ production deployment target.

## 4. Input contract: `part-ir.v1`

ตัวอย่าง:

```json
{
  "schema_version": "part-ir.v1",
  "part_id": "mounting-plate-demo",
  "template": "mounting-plate.v1",
  "units": "mm",
  "parameters": {
    "width_mm": "120.000",
    "height_mm": "80.000",
    "thickness_mm": "6.000",
    "hole_diameter_mm": "6.000",
    "hole_offset_x_mm": "12.000",
    "hole_offset_y_mm": "12.000"
  },
  "hole_pattern": "four-corner.v1"
}
```

สัญญา input:

- ใช้ schema version แบบ strict; ปฏิเสธ field ที่ไม่รู้จัก, field ซ้ำ, ตัวเลขที่ไม่ finite และค่าที่เกินความละเอียด 0.001 mm. มิติเป็น decimal string แบบ `^(0|[1-9][0-9]{0,2})(\.[0-9]{1,3})?$`; ไม่รับ sign, exponent, leading zero หรือ JSON boolean/number แทน string.
- `part_id` ต้องตรง `^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$`; ไม่มีชื่ออิสระหรือ prompt ที่ต้องตีความ.
- `units` ใน V1A รับเฉพาะ `mm`; ไม่แปลงหน่วยโดยเงียบ.
- ช่วงค่า: `width_mm` และ `height_mm` 20–500 mm; `thickness_mm` 1–50 mm; `hole_diameter_mm` 2–25 mm; `hole_offset_x_mm` และ `hole_offset_y_mm` 5–200 mm.
- ค่า offset วัดจากขอบนอกของแผ่นถึงศูนย์กลางรู; รูสี่รูวางสมมาตรจากมุมทั้งสี่และทะลุความหนาทั้งหมด.
- ระยะเนื้อขั้นต่ำรอบรูและระหว่างรูคือ 2 mm: `offset - diameter/2 >= 2`, `width - 2*offset_x - diameter >= 2`, และ `height - 2*offset_y - diameter >= 2`.
- ความกว้าง/สูงต้องมากกว่าศูนย์หลังหัก offset ทั้งสองด้าน; schema ต้องปฏิเสธค่าติดลบ, NaN, infinity และเลขทศนิยมที่ยาวเกินกำหนด.
- IR ไม่มี path, URL, script, output destination, FreeCAD command หรือ user-defined formula.

ระบบเก็บค่ามิติเป็น decimal string แบบ canonical ใน fingerprint และแปลงเป็นหน่วย FreeCAD หลัง validation แล้วเท่านั้น หากเปลี่ยนความหมายหรือกฎมิติ ต้องเพิ่ม schema/template version.

## 5. Processing flow และ engine

```text
PartIR
  → strict schema / range / minimum-web validation
  → canonicalize decimal values and calculate input fingerprint
  → verify replay receipt and all existing artifact hashes
  → create unique staging directory under output root
  → trusted FreeCAD worker builds sketch → pad → hole sketch → pocket
  → recompute, save .FCStd, export STEP and STL
  → generate isometric PNG preview from the resulting geometry
  → reopen .FCStd and STEP in a fresh FreeCAD process; verify geometry and bounds
  → validate STL mesh, preview pixels, manifest, and artifact hashes
  → atomically publish all outputs and a separate immutable replay receipt
```

FreeCAD is selected because its official feature documentation describes native parametric objects and the `.FCStd` format alongside STEP/STL export. CadQuery can produce parametric Python models and neutral exports, but its own documentation identifies its Python source as its fully parametric format; it does not satisfy the selected native FreeCAD-document handoff by itself.

The runtime build must be pinned by exact version/build identity and executable hash in the implementation. The official download page currently lists FreeCAD 1.1.3 as stable, but the local host has no FreeCAD installation and its supported non-interactive launch mode has not yet been verified; the implementation plan must include a local runtime probe before accepting jobs. If the pinned build cannot create, save, reopen and export a document without user interaction, do not silently switch engines: return to design review.

The worker is trusted versioned product code. It receives only the normalized fixed-shape IR, runs with a per-job 120-second deadline, uses no network, and never evaluates user-provided code. The preview profile is fixed at `isometric-1024x768.v1`; a CPU rasterizer converts the resulting FreeCAD tessellation into a PNG with a depth buffer, so preview generation does not require a GUI or a second geometry source. V1A is a trusted-local pilot, not an OS-level sandbox for hostile files or public uploads.

## 6. Outputs, QA manifest, and replay

An accepted job publishes exactly:

- `part.FCStd` — self-contained editable FreeCAD document with named, inspectable parameter-driven feature history.
- `part.step` — solid geometry for precise neutral CAD exchange.
- `part.stl` — triangulated mesh for downstream preview/printing workflows; it does not preserve parametric history.
- `preview.png` — deterministic isometric visual check from the accepted geometry; it is not a dimensioned technical drawing.
- `qa-manifest.json` — lineage, engine identity, QA results, measured geometry, output hashes, timings, byte counts, replay status and `model_calls: 0`.

The manifest may contain the normalized dimensions and measured bounding box needed to audit the output; these are local project data. No prompt, arbitrary path, environment variable, or credential is recorded. Logs and benchmark summaries contain only stable error codes, aggregate counts, timings, byte counts, model calls and fingerprints; they do not include raw `PartIR` values. Monetary compute cost is `unknown` until a host resource rate card is measured and approved.

The fingerprint covers canonical `PartIR`, schema/template version, worker recipe version, exact FreeCAD build and preview profile. Geometry values and preview semantics are deterministic; replay returns already-published byte-identical artifacts after verification, while independently regenerated native containers are not promised to be byte-identical across FreeCAD builds. A matching replay succeeds only after an external receipt and every artifact hash/size verify. A corrupt, incomplete or tampered replay target fails closed and is never overwritten. A changed input or engine build writes to a new content-addressed job directory. Publish the complete artifact set atomically only after QA passes; a failed job leaves no accepted artifact reference and never deletes prior output or source files.

## 7. Deterministic validation and failure contract

All control flow uses ordinary code. The schema validator checks exact fields, numeric bounds, units, hole count/layout, edge clearance and inter-hole web before starting FreeCAD. Geometry QA then independently checks:

1. Recompute completed and the native document opens in a fresh process.
2. Exactly one body and one valid non-null solid exist; shape validity passes the kernel check and volume is positive.
3. The feature history contains the expected sketch, pad, hole sketch and pocket with no failed feature.
4. The solid bounding box matches requested width, height and thickness within 0.01 mm.
5. Four through-holes exist at the expected symmetric centers and diameter within 0.01 mm; their bores pass through the complete thickness.
6. Reopened STEP has one valid solid in millimeters, matching bounds within 0.01 mm and relative volume error no greater than 0.1%.
7. STL has 1–100,000 triangles, a valid closed mesh for this supported shape, matching bounds within 0.01 mm, positive volume and no non-finite coordinates.
8. Preview is a 1024×768 PNG, is non-blank, and is generated from the same fingerprinted FreeCAD tessellation.
9. Manifest and receipt hashes match all five published artifacts; replay verifies them before returning `replayed`.

Stable rejection/error codes include `invalid_part_ir`, `unsupported_template`, `unsupported_units`, `dimension_out_of_range`, `feature_constraint_violation`, `freecad_unavailable`, `unsupported_freecad_build`, `worker_deadline_exceeded`, `native_document_invalid`, `geometry_invalid`, `step_export_invalid`, `stl_export_invalid`, `preview_invalid`, `publication_failed`, and `replay_integrity_error`. Error output never echoes full input dimensions or paths.

## 8. Acceptance and evaluation

The synthetic acceptance suite has 20 cases: five valid parameter combinations, five exact replay cases, eight malformed/boundary/constraint rejections, and two tampered-artifact cases. The implementation plan pins exact fixtures and expected stable codes. The pilot passes only if expected accepted/replayed/rejected totals match, every output reopens and satisfies geometry QA, both tamper cases fail closed, and total `model_calls` is zero.

At least five distinct accepted previews and their `.FCStd` feature trees receive manual visual/openability review in FreeCAD before V1A is called ready for local use. Automated pixel checks do not claim design quality. Record actual build and platform, fresh/replay wall time, bytes written, model calls, QA failures and monetary cost as known/unknown; do not claim manufacturing accuracy, safety, or general CAD coverage from this template pilot.

## 9. Cost, privacy, and trust boundaries

- Default path is `PartIR → deterministic validator → FreeCAD worker → QA`, with `model_calls=0`; no model provider, prompt, API key or cloud compute is required.
- Dimensions and output models are local and potentially confidential design data. They stay in the caller-selected local output root and are not uploaded, sent to telemetry, or copied into the Solo Empire control-plane repo.
- Input is JSON only; no input CAD file, expression, macro, path or network reference is executed or dereferenced.
- Output creation is scoped to a new job directory below the chosen output root. Reject path traversal, symlink escapes and overwrite attempts; stage and publish on the same filesystem.
- Worker diagnostics are bounded and sanitized. Failed jobs expose stable codes without echoing the request.
- Geometry and output checks verify structural correctness only. A qualified human must review engineering intent, material, loads, tolerance stack-up, legal compliance and manufacturing process for real-world use.
- A future language-to-PartIR adapter is a separate product capability. It must be optional, budgeted, evaluated, able to abstain, and subject to the same strict validator before it can invoke this worker.

## 10. Rollback and rollout

V1A is opt-in through a local CLI command. Rollback means disabling the CAD command or pinning the prior worker/template/runtime version; it does not delete published jobs. There is no API rollout, database migration, remote write, deployment or destructive action. Do not promote this pilot to public uploads until a separate security review proves OS-level isolation and resource controls.

## 11. References

- [FreeCAD feature documentation](https://www.freecad.org/features.php) — native parametric objects, `.FCStd`, and STEP/STL formats.
- [FreeCAD downloads](https://www.freecad.org/downloads) — current stable build; confirm again before pinning the implementation runtime.
- [CadQuery import/export documentation](https://cadquery.readthedocs.io/en/latest/importexport.html) — parametric source and neutral format behavior used in engine comparison.
- Repository Boundaries & Scaling Strategy (Solo Empire `systems/architecture/repository-boundaries.md`)
- [Logic-first Production Platform design](2026-09-22-logic-first-production-platform-design.md)
- [Production product delivery order](../plans/2026-09-22-production-platform-next-products.md)
