# CAD Parametric Part Generator V1A — Implementation Plan

> **For agentic workers:** ใช้ superpowers:executing-plans หลังผู้ใช้อนุมัติแผนและเลือกวิธีดำเนินงาน ทำทีละ task ตามลำดับและทำเครื่องหมาย checkbox เมื่อมีหลักฐานผ่าน

**Goal:** เพิ่ม local CAD adapter ใน book-production-platform ให้สร้าง mounting plate จาก PartIR ที่ตรวจเข้มงวด ได้ไฟล์ FreeCAD ที่แก้ไขต่อได้, STEP, STL, preview และ QA manifest ที่ replay ตรวจ hash ได้ โดยงานปกติมี model_calls=0

**Architecture:** Parent process ตรวจ PartIR, ขอบเขตไฟล์, runtime identity และ replay ก่อนเริ่ม engine จากนั้นส่งเฉพาะ normalized JSON ไปยัง FreeCADCmd worker ที่เป็น product code ซึ่งสร้าง feature tree แบบ Sketch → Pad → Hole Sketch → Pocket ใน staging แยกต่างหาก การตรวจ native document และ STEP ทำใน FreeCADCmd process ใหม่ ส่วน parent ตรวจ STL, สร้าง preview จาก tessellation ที่ worker ส่งออก, ตรวจ manifest/receipt และ publish job แบบ content-addressed หลัง QA ผ่านครบ

**Tech Stack:** Python 3.12, Pydantic 2.x, Pillow 12.x, pytest 9.x, Ruff, mypy และ FreeCAD 1.1.3 FreeCADCmd แบบ portable ที่ผ่าน runtime probe ก่อนใช้งาน ไม่เพิ่ม CAD dependency ใน Python; ใช้ FreeCAD API ภายใน worker เท่านั้น

**Spec:** [CAD Parametric Part Generator V1A design](../specs/2026-09-25-cad-parametric-part-generator-design.md)

## Global Constraints

- Product source, tests และ README อยู่ใน nested repository  บน branch main; ไม่สร้าง repository หรือ AI runtime ใหม่
- ก่อนแก้ product source ต้องพิสูจน์ว่า FreeCADCmd 1.1.3 แบบ console สร้าง, บันทึก, เปิดซ้ำ และ export FCStd/STEP/STL ได้โดยไม่มี GUI; ถ้าไม่ผ่านให้หยุดและกลับมาให้ผู้ใช้ทบทวน design
- ดาวน์โหลดเฉพาะ portable package จากหน้า FreeCAD ทางการ เก็บไว้ใน ignored .runtime ของ product repo ไม่ติดตั้งทั่วเครื่องและไม่ commit binary; บันทึก URL ต้นทาง, version/build identity และ SHA-256 ของ executable ที่ตรวจจริง ห้ามเดาค่า hash
- PartIR รับเฉพาะ JSON แบบ bounded และ schema part-ir.v1; ห้าม prompt, path, URL, macro, expression, script, custom feature หรือ input CAD file
- ใช้ Decimal หลังรับ decimal string ที่ผ่าน regex; มิติเป็น mm เท่านั้น; canonical fingerprint ครอบคลุม PartIR, schema/template/recipe version, exact FreeCAD identity และ preview profile
- เส้นทางปกติใช้ deterministic code และ FreeCAD local เท่านั้น; ห้ามเรียก LLM, image generation, cloud API, network fetch หรือ fallback ไป GUI; manifest และ benchmark ต้องรายงาน model_calls=0 และ monetary cost=unknown
- จำกัด FreeCAD worker ไม่เกิน 120 วินาที ใช้ shell=False, stdin ที่ปิด, working directory ชั่วคราว และ diagnostic buffer ที่มีเพดาน; log คืนเฉพาะ stable error code โดยไม่ echo มิติหรือ path เต็ม
- ผลงานที่ยอมรับมีไฟล์ห้ารายการเท่านั้น: part.FCStd, part.step, part.stl, preview.png และ qa-manifest.json; receipt แยกจาก artifact set
- ปฏิเสธ path traversal, symlink/junction escape, output ที่มีอยู่และเขียนทับ, replay ที่ไม่มี receipt หรือ hash/ขนาดไม่ตรง; publish จาก staging ใน volume เดียวกันหลัง QA ผ่าน
- CAD runtime เป็น trusted-local pilot ไม่ใช่ OS sandbox สำหรับไฟล์หรือผู้ใช้ที่ไม่น่าเชื่อถือ และ geometry QA ไม่ใช่การรับรองด้านวิศวกรรมหรือการผลิต
- ไม่มี service, API, database migration, cloud job หรือ deployment; rollback คือปิดคำสั่ง CAD หรือ pin adapter/runtime รุ่นก่อน โดยไม่ลบ job ที่ publish ไปแล้ว
- root Solo Empire เก็บเฉพาะ spec, plan, product roadmap/catalog และ pilot evidence แบบสรุป; ห้าม copy source, CAD output, dimensions หรือ private artifact จาก product repo มายัง root

## Review Focus

- Decimal input ที่เป็น JSON number/boolean, exponent, sign, leading zero, NaN/infinity, ค่าทศนิยมเกินสามตำแหน่ง, duplicate key, field เกิน/ขาด ต้องถูกปฏิเสธด้วย stable code ก่อนเรียก FreeCAD; ครอบคลุมใน Task 1
- มิติที่ขอบช่วงและเงื่อนไขเนื้อขั้นต่ำ 2 mm ทั้งกรณีเท่าขอบและต่ำกว่าขอบต้องให้ผลแน่นอน; ครอบคลุมใน Task 1
- offset ที่ทำให้รูแตะขอบ, web ระหว่างรูต่ำกว่า 2 mm, X/Y ไม่สมมาตร และการตรวจตำแหน่ง/เส้นผ่านศูนย์กลางรูหลังเปิดไฟล์ใหม่ต้องไม่สร้าง BREP ที่ผิด; ครอบคลุมใน Task 2 และ Task 3
- FreeCADCmd หายไป, version/build/hash ไม่ตรง, worker ล้มเหลวหรือ timeout ต้องไม่ publish artifact หรือ receipt และห้ามสลับไปใช้ GUI; ครอบคลุมใน Task 0 และ Task 4
- replay ที่แก้ manifest, STEP/STL/PNG, receipt, เพิ่ม/ลบไฟล์ หรือเปลี่ยน PartIR/runtime ต้องไม่ยอมรับซ้ำและห้าม overwrite งานเดิม; ครอบคลุมใน Task 4

---

### Task 0: พิสูจน์ FreeCADCmd แบบไม่ใช้ GUI และ pin runtime

**Files and local runtime:**
- สร้าง probe scripts เฉพาะใน ignored .runtime/cad/probe/
- เก็บ portable runtime เฉพาะเครื่องใน ignored .runtime/cad/freecad-1.1.3/
- เมื่อ probe ผ่าน ให้เพิ่ม runtime identity lock ที่มี version/build/hash จริงใน src/book_production_platform/cad_production/freecad-runtime.v1.json

- [x] **Step 1: ดาวน์โหลดและตรวจ portable runtime**

ใช้เฉพาะ Windows x86_64 portable 1.1.3 จากหน้า download ทางการ https://www.freecad.org/downloads; ตรวจ source และ checksum/signature ที่ผู้เผยแพร่ให้ไว้ก่อนแตกไฟล์ลง .runtime หากไม่มี checksum ที่ผู้เผยแพร่ลงนาม ให้เก็บ archive SHA-256 ที่คำนวณได้พร้อม URL/version แต่ห้ามอ้างว่าเป็นการยืนยันลายเซ็น ดาวน์โหลดหรือแตกไฟล์ล้มเหลวให้หยุดก่อนเปลี่ยน product source

- [x] **Step 2: ตรวจคำสั่งและ identity จาก executable ที่ resolve ได้**

บันทึกผล --version และ --help; เลือก FreeCADCmd.exe เท่านั้น แล้วบันทึก full version/build string, platform และ SHA-256 ของ executable ลง probe evidence ใช้ argument vector โดยตรง ไม่มี shell invocation

- [x] **Step 3: ทดสอบ native parametric build**

สร้าง probe macro จาก trusted test code เพื่อสร้าง rectangular sketch, pad, hole sketch สี่วง และ pocket; สร้าง FCStd, export STEP/STL และเขียนผลตรวจแบบ JSON ที่ไม่มี dimensions ลง log ทดสอบว่า process ปิดเองและจบภายใน 120 วินาที

- [x] **Step 4: เปิดตรวจใน process ใหม่**

เปิด FCStd และ STEP ด้วย FreeCADCmd process แยก ตรวจว่ามี solid เดียว, shape valid, volume เป็นบวก, bounding box สอดคล้องกับ probe geometry, STEP/STL เปิดได้ และไม่มีการเรียก FreeCADGui; ตรวจ executable, version หรือผลใดไม่ตรงให้หยุดโดยไม่เริ่ม Task 1

- [x] **Step 5: สร้าง runtime lock จากค่าที่วัดได้**

เพิ่ม freecad-runtime.v1.json ที่มี schema_version, source_url, archive_sha256, version, build, platform, executable_sha256, worker_protocol และ preview_profile; ใส่เฉพาะ identity ที่ probe ผ่านจริง ห้าม commit archive, executable, machine path หรือ probe artifacts

**ผ่านเมื่อ:** FreeCADCmd 1.1.3 ทำ build/save/reopen/STEP/STL ใน headless process แยกได้, identity ถูกบันทึกจาก executable จริง และ probe artifacts อยู่ใต้ .runtime ที่ ignore

### Task 1: เพิ่ม PartIR schema และ deterministic validation

**Files:**
- สร้าง src/book_production_platform/cad_ir/__init__.py
- สร้าง src/book_production_platform/cad_ir/models.py
- สร้าง src/book_production_platform/cad_ir/part-ir.v1.schema.json
- สร้าง tests/cad_ir/__init__.py, tests/cad_ir/conftest.py, tests/cad_ir/test_models.py, tests/cad_ir/test_validation.py
- สร้าง examples/cad/part-ir.json
- แก้ pyproject.toml เพื่อรวม schema JSON ใน package data

**Interfaces:**
- PartIR และ PartParameters เป็น frozen Pydantic models แบบ strict และปฏิเสธ extra fields
- PartIRError(code: str) เป็น validation error ที่เก็บ stable code เท่านั้น
- parse_part_ir(value: object) -> PartIR ตรวจ schema/template/units, field, decimal string, range และ web constraints; ใช้ invalid_part_ir, unsupported_template, unsupported_units, dimension_out_of_range และ feature_constraint_violation ตามสาเหตุ
- load_part_ir(path: Path) -> PartIR จำกัด input 64 KiB, ปฏิเสธ symlink, UTF-8/JSON ผิด และ duplicate object key
- canonical_part_ir(part: PartIR) -> bytes คืน canonical JSON ที่ sort keys และคง canonical decimal string
- part_dimensions(part: PartIR) -> PartDimensions แปลงเป็น Decimal หลัง validation ผ่านเท่านั้น

- [x] **Step 1: เขียน contract tests ให้ fail ก่อน**

เขียน fixture valid หนึ่งรายการและทดสอบ accepted input, exact schema version, template, units, part_id และ decimal strings; เพิ่ม rejection cases สำหรับ field เกิน/ขาด, wrong type, malformed JSON, duplicate key และ unsafe input path

```python
def test_parse_accepts_valid_mounting_plate(valid_part_ir):
    part = parse_part_ir(valid_part_ir)
    assert part.schema_version == "part-ir.v1"
    assert part.parameters.width_mm == "120.000"

def test_parse_rejects_json_number_instead_of_decimal_string(valid_part_ir):
    valid_part_ir["parameters"]["width_mm"] = 120
    with pytest.raises(PartIRError) as error:
        parse_part_ir(valid_part_ir)
    assert error.value.code == "invalid_part_ir"
```

- [x] **Step 2: รัน contract tests ก่อน implementation**

รัน py -3.12 -m pytest -q tests/cad_ir/test_models.py tests/cad_ir/test_validation.py; คาดว่า fail จาก module/interface ที่ยังไม่มี

- [x] **Step 3: implement strict schema และ numeric rules**

เพิ่ม Literal schema/template/pattern, extra="forbid", strict=True, regex decimal ^(0|[1-9][0-9]{0,2})(\.[0-9]{1,3})?$, part_id regex ตาม spec และ json.loads object-pairs hook ที่ reject duplicate keys; error object เก็บ stable code เท่านั้น

- [x] **Step 4: เพิ่ม range และ web constraints ด้วย Decimal**

บังคับ width/height 20–500 mm, thickness 1–50 mm, hole diameter 2–25 mm และ offsets 5–200 mm; เช็ก offset minus radius ≥2 mm และ X/Y inner web ≥2 mm โดย equality ผ่านและค่าที่ต่ำกว่าแม้ 0.001 mm ต้องไม่ผ่าน

- [x] **Step 5: ตรวจ schema tests และ static checks**

รัน CAD IR tests; เพิ่ม test ที่ assert canonical JSON ให้ fingerprint คงเดิมเมื่อ key ordering ต่างกัน และคงค่ามิติสามตำแหน่ง; รัน py -3.12 -m ruff check src/book_production_platform/cad_ir tests/cad_ir และ py -3.12 -m mypy --strict src/book_production_platform/cad_ir

### Task 2: สร้าง FreeCAD worker และ parametric mounting plate

**Files:**
- สร้าง src/book_production_platform/cad_production/__init__.py
- สร้าง src/book_production_platform/cad_production/errors.py
- สร้าง src/book_production_platform/cad_production/runtime.py
- สร้าง src/book_production_platform/cad_production/freecad_worker.FCMacro
- สร้าง tests/cad_production/__init__.py, tests/cad_production/conftest.py, tests/cad_production/test_runtime.py, tests/cad_production/test_freecad_worker.py
- เพิ่ม cad_production/*.FCMacro ใน package data ของ pyproject.toml

**Interfaces:**
- CadProductionError(code: str) เป็น error boundary สำหรับ CLI/jobs โดยไม่เก็บ raw dimensions หรือ path
- FreeCADIdentity เก็บ version, build, platform และ executable SHA-256 จาก runtime lock
- inspect_freecad_runtime(freecadcmd: Path) -> FreeCADIdentity ปฏิเสธ symlink, executable ที่หาไม่พบ, version/build/hash mismatch และ runtime ที่ไม่ตรง lock
- run_freecad_worker(freecadcmd: Path, mode: str, request_path: Path, result_root: Path, timeout_s: int) -> WorkerResult ใช้ shell=False, จำกัดเวลา 1–120 วินาทีและจำกัด diagnostic bytes
- Worker รับ mode ที่ allowlist เท่านั้น: build, verify-native, verify-step; ใช้ JSON ที่ parent สร้าง ไม่มีการอ่าน macro, expression หรือ command จาก input
- WorkerResult มี exit_code, elapsed_seconds, diagnostics_bytes, error_code และ bounded result_json; timeout/process failure ถูกแปลงเป็น stable error โดยไม่แนบ raw input

- [x] **Step 1: เขียน runtime identity และ worker behavior tests**

ทดสอบ missing binary → freecad_unavailable, version/build/hash mismatch → unsupported_freecad_build, malformed worker response/nonzero exit → stable worker code และ timeout → worker_deadline_exceeded; ตรวจว่า stdout/stderr ถูกจำกัดและไม่มี artifact final

```python
def test_worker_timeout_returns_stable_code(fake_freecadcmd, tmp_path):
    result = run_freecad_worker(
        fake_freecadcmd,
        "build",
        tmp_path / "request.json",
        tmp_path,
        timeout_s=1,
    )
    assert result.error_code == "worker_deadline_exceeded"
    assert result.diagnostics_bytes <= MAX_DIAGNOSTIC_BYTES
```

- [x] **Step 2: รัน runtime tests ให้แสดง failure ที่คาด**

รัน py -3.12 -m pytest -q tests/cad_production/test_runtime.py tests/cad_production/test_freecad_worker.py; ใช้ monkeypatched process adapter สำหรับ failure-path tests จึงตรวจ behavior ได้โดยไม่ต้องติดตั้ง FreeCAD เพิ่ม

- [x] **Step 3: implement runtime inspection และ bounded process runner**

อ่าน lock จาก package resource, hash executable เป็น chunk, ตรวจ version/build ผ่าน subprocess และส่งเฉพาะ environment ที่จำเป็นกับ FreeCAD; ตั้ง stdin เป็น DEVNULL, shell=False, cwd เป็น worker temp directory, รวบ diagnostics แบบ capped และ kill worker เมื่อครบ deadline

- [x] **Step 4: เขียน FreeCAD macro จาก normalized PartIR**

สร้างหนึ่ง Body, rectangle Sketch พร้อม dimensional constraints, Pad ตาม thickness, hole Sketch ที่มี 4 circles ณตำแหน่งสมมาตรจาก offsets และ Pocket ทะลุ body; ตั้งชื่อ object คงที่, recompute, ตรวจ feature status, saveAs FCStd และ export shape เดียวกันเป็น STEP/STL ห้าม GUI API และห้ามรับ Python expression จาก input

- [x] **Step 5: ทดสอบ feature tree ด้วย FreeCAD integration**

ใช้ runtime จาก Task 0 สร้าง plate fixture ขนาด asymmetric หนึ่งกรณี; assert native file เปิดใหม่ใน process แยก, มี Body/Sketch/Pad/HoleSketch/Pocket ครบ, one solid valid, volume positive และ exports มีขนาดไม่เป็นศูนย์

- [x] **Step 6: รัน worker tests และ lint/typecheck**

รัน runtime/worker tests แล้ว py -3.12 -m ruff check src/book_production_platform/cad_production tests/cad_production และ py -3.12 -m mypy --strict src/book_production_platform/cad_production

### Task 3: ตรวจ geometry, export และสร้าง preview จาก tessellation เดียวกัน

**Files:**
- สร้าง src/book_production_platform/cad_production/geometry.py
- สร้าง src/book_production_platform/cad_production/preview.py
- สร้าง src/book_production_platform/cad_production/qa.py
- เพิ่ม tests/cad_production/test_geometry.py, test_preview.py, test_qa.py

**Interfaces:**
- Worker mode verify-native เปิด FCStd ใหม่และคืน bounded JSON ที่มี feature names, solid count, validity, bounds, volume และ cylindrical bore measurements
- Worker mode verify-step เปิด STEP ใหม่และคืน solid count, validity, bounds, volume และ unit metadata
- read_stl_mesh(path: Path) -> TriangleMesh ตรวจ binary/ASCII STL, file length, 1–100,000 triangles, finite coordinates, closed edge counts, bounds และ positive volume
- render_isometric_preview(mesh: TriangleMesh, path: Path) -> None วาด CPU orthographic isometric ด้วย depth buffer, fixed lighting/palette/camera ที่ขนาด 1024×768
- verify_preview(path: Path) -> None ตรวจ PNG format, 1024×768, RGB/RGBA, non-blank และ pixel bounds ไม่เกิน canvas
- verify_cad_outputs(part: PartIR, artifact_root: Path, freecadcmd: Path, timeout_s: int) -> CadQAResult เปิด native และ STEP ด้วย worker process ใหม่ แล้วตรวจ output ครบ

- [x] **Step 1: เขียน geometry/mesh/preview QA tests**

ทดสอบ bbox tolerance 0.01 mm, four cylindrical through-holes ที่ radius/center ถูกต้อง, STEP หนึ่ง solid และ volume relative error ≤0.1%; ทดสอบ STL triangle cap, open edge, non-finite coordinate, zero-volume mesh และ preview ขาวล้วน/ผิดขนาด โดย assert stable codes native_document_invalid, geometry_invalid, step_export_invalid, stl_export_invalid และ preview_invalid ตาม output ที่ผิด

```python
def test_preview_is_deterministic_for_same_mesh(valid_triangle_mesh, tmp_path):
    first = tmp_path / "first.png"
    second = tmp_path / "second.png"
    render_isometric_preview(valid_triangle_mesh, first)
    render_isometric_preview(valid_triangle_mesh, second)
    assert first.read_bytes() == second.read_bytes()
    verify_preview(first)
```

- [x] **Step 2: รัน QA tests ก่อน implementation**

รัน py -3.12 -m pytest -q tests/cad_production/test_geometry.py tests/cad_production/test_preview.py tests/cad_production/test_qa.py; คาดว่า tests fail จนกว่าจะมี mesh parser, renderer และ reopen verifier

- [x] **Step 3: ส่ง tessellation จาก BREP หลังตรวจด้วย FreeCAD**

ให้ trusted worker export vertices/faces จาก shape ที่ recompute แล้วลง intermediate JSON แบบ bounded; parent ตรวจโครงสร้าง, index, finite coordinates และ triangle cap ก่อนสร้าง preview; ไม่ใช้ภาพจาก CAD GUI หรือ geometry คนละชุด

- [x] **Step 4: implement STL parser, watertight check และ fixed renderer**

รองรับ binary และ ASCII STL โดยตรวจขนาดไฟล์/รูปแบบก่อน allocate; quantize จุดตาม tolerance ของ exporter สำหรับ edge incidence; project mesh ด้วย camera profile ที่ versioned, z-buffer และ deterministic Pillow PNG writer

- [x] **Step 5: implement independent native/STEP QA**

เปิดไฟล์ที่บันทึกแล้วด้วย FreeCADCmd process ใหม่; ตรวจ feature tree, valid non-null one-solid BREP, positive volume, exact requested bounds ±0.01 mm, 4 bore centers/radii ±0.01 mm และ through-thickness; ตรวจ STEP units เป็น mm, bounds ±0.01 mm และ relative volume error ≤0.1%

- [x] **Step 6: รัน QA integration และตรวจ preview จริง**

รัน CAD integration tests ด้วย runtime ที่ pin แล้ว; สร้างห้า fixture, เปิด PNG และ FCStd ตรวจด้วยคน, บันทึกผล review ลง ignored .runtime/cad/pilot/visual-review.json โดยเก็บเฉพาะ fixture id, pass/fail และ note สั้น ไม่มี private dimension หรือ full path

### Task 4: เพิ่ม content-addressed jobs, replay receipt และ fail-closed publication

**Files:**
- สร้าง src/book_production_platform/cad_production/jobs.py
- เพิ่ม tests/cad_production/test_jobs.py

**Interfaces:**
- CadJobResult มี fingerprint, status, artifact_dir, artifact_hashes, qa, replay, bytes_written, model_calls และ error_code
- build_cad_job(input_path: Path, output_root: Path, freecadcmd: Path, timeout_s: int = 120) -> CadJobResult validate ก่อนเริ่ม engine, ตรวจ replay, สร้าง staging, build/export/reopen/QA, เขียน manifest/receipt และ publish
- Fingerprint ใช้ canonical PartIR, schema/template/recipe versions, exact FreeCAD build และ preview profile
- Job directory คือ output_root/<fingerprint>/; receipt คือ output_root/.receipts/<fingerprint>.json; accepted job directory มี artifact ห้ารายการพอดี
- Manifest มี fingerprint, IR hash, exact runtime identity, QA measurements, hash/bytes ของสี่ output ที่ไม่ใช่ manifest, timings, original build status, model_calls: 0 และ monetary cost unknown; receipt pin hash/size ของ output ทั้งสี่และ qa-manifest.json เพื่อหลีกเลี่ยง self-referential hash และตรวจ replay โดยไม่แก้ manifest

- [x] **Step 1: เขียน lifecycle tests กับ fake worker**

ทดสอบ first build, exact replay ที่ไม่เรียก worker, input/runtime เปลี่ยนแล้ว fingerprint เปลี่ยน, tamper manifest/STEP/PNG/receipt, file เกิน/ขาด, output symlink/junction, lock contention, timeout และ publication error; ทุก rejection ต้องไม่เขียนทับ final job หรือสร้าง receipt ที่ยอมรับได้

```python
def test_exact_replay_reuses_verified_files_without_worker(valid_job, monkeypatch):
    first = build_cad_job(**valid_job)
    monkeypatch.setattr(
        "book_production_platform.cad_production.jobs.run_freecad_worker",
        lambda *args, **kwargs: pytest.fail("replay must not start FreeCAD"),
    )
    replay = build_cad_job(**valid_job)
    assert replay.replay is True
    assert replay.artifact_hashes == first.artifact_hashes
    assert replay.model_calls == 0
```

- [x] **Step 2: รัน lifecycle tests ก่อน implementation**

รัน py -3.12 -m pytest -q tests/cad_production/test_jobs.py; คาดว่า fail จาก API และ receipt protocol ที่ยังไม่มี

- [x] **Step 3: implement bounded input, fingerprint และ path safety**

อ่าน input สูงสุด 64 KiB, ปฏิเสธ symlink/junction และ output root ที่พ้นจาก root ที่ resolve แล้ว; สร้าง canonical SHA-256 ก่อนเริ่ม engine, ใช้ lock ต่อ fingerprint และ staging directory ใน filesystem เดียวกัน

- [x] **Step 4: implement receipt verification และ no-overwrite replay**

receipt schema ต้องระบุ fingerprint/runtime/artifact hashes+sizes ครบ; replay ตรวจชื่อไฟล์ชุดที่อนุญาต, file count, manifest hash และ hash/size ของทุก artifact; ถ้า target มีอยู่แต่ receipt ขาด/ผิด ให้คืน replay_integrity_error โดยห้ามซ่อมหรือเขียนทับ

- [x] **Step 5: implement staged build, QA manifest และ atomic publish**

สร้าง native/export/tessellation ใน staging, ตรวจทุก output, เขียน preview และ manifest, คำนวณ hashes, publish directory ใหม่ด้วย atomic rename แล้วเขียน receipt ด้วย temp file + atomic replace; บนความล้มเหลว cleanup ได้เฉพาะ staging ของ job นี้ ห้ามลบ final artifact ที่มีอยู่ก่อน

- [x] **Step 6: รันทดสอบ replay, tamper, timeout และ concurrent job**

รัน py -3.12 -m pytest -q tests/cad_production/test_jobs.py tests/cad_production/test_qa.py; ตรวจ exact replay ไม่เริ่ม FreeCAD, fail-closed replay ไม่แก้ hash เดิม และ worker crash ทิ้งแต่ staging ที่ลบได้

### Task 5: เพิ่ม CLI, 20-case benchmark, package checks และเอกสารผู้ใช้

**Files:**
- สร้าง src/book_production_platform/cad_production/benchmark.py
- สร้าง src/book_production_platform/cad_production/cli.py
- สร้าง tests/cad_production/test_cli.py, tests/cad_production/test_benchmark.py
- สร้าง tests/fixtures/cad/cases.json พร้อม input fixtures 13 รายการ: 5 accepted, 8 rejected; benchmark เรียก 5 accepted ซ้ำเพื่อ replay และทำ 2 tamper cases
- สร้าง examples/cad/part-ir.json
- แก้ src/book_production_platform/cli.py, scripts/check.py, pyproject.toml และ README.md

**Interfaces:**
- book-production cad build --input PATH --output-root PATH --freecadcmd PATH --timeout-s 120 คืน JSON stable status/error; exit 0 เฉพาะ accepted/replayed, exit 2 เมื่อ reject
- book-production cad benchmark --fixtures PATH --output PATH --freecadcmd PATH รัน 20 กรณีและรายงาน fresh_accepted=5, replayed=5, input_rejected=8, tamper_rejected=2, qa_failures, timings, bytes, model_calls และ monetary_cost
- Top-level CLI ส่ง cad arguments ให้ cad_production.cli.main() แบบเดียวกับ audio/data family ปัจจุบัน; ห้ามสร้าง CAD parser ซ้ำใน platform CLI

- [x] **Step 1: เขียน CLI และ benchmark tests**

ทดสอบ help, missing input, stable error JSON, unsupported runtime, accepted build output, 20-case aggregate counts และ model_calls=0; benchmark unit tests ใช้ fake runner ส่วน acceptance run ใช้ FreeCAD จริง

```python
def test_top_level_cad_cli_delegates_arguments(monkeypatch):
    monkeypatch.setattr("book_production_platform.cli.cad_main", lambda args: 7)
    assert main(["cad", "build", "--input", "part.json"]) == 7
```

- [x] **Step 2: รัน CLI/benchmark tests ให้ fail ก่อน wiring**

รัน py -3.12 -m pytest -q tests/cad_production/test_cli.py tests/cad_production/test_benchmark.py; ตรวจว่า failure ชี้ไปที่ command/API ที่ยังไม่มี

- [x] **Step 3: implement domain CLI และ top-level dispatch**

CLI อ่าน JSON แบบ bounded, เรียก jobs API, serialize JSON แบบ sort keys/Thai UTF-8 และคืน stable exit codes; เพิ่ม cad remainder subcommand ใน top-level CLI และเพิ่ม cad source/tests ลง Ruff, mypy targets ใน scripts/check.py

- [x] **Step 4: implement 20-case benchmark**

ระบุ fixture id และ expected error code ครบทุกกรณี; ทำ 5 fresh builds, exact replay 5 รอบ, 8 malformed/boundary rejects, และ 2 tampered artifact replays; หยุดเมื่อผลจริงต่างจาก expected, รวม model calls ต้องเป็น 0 และข้อมูลรายงานต้องไม่มี raw PartIR/path

- [x] **Step 5: เขียน product README และรัน acceptance จริง**

อธิบาย runtime portable/version/hash check, ตัวอย่าง PartIR, ขอบเขต template, คำสั่ง build/benchmark, outputs, error codes, replay, local privacy และข้อจำกัดที่ไม่ใช่ engineering certification; รัน benchmark แล้วเก็บ report กับห้า preview/FCStd ใน ignored .runtime/cad/pilot/

- [x] **Step 6: รัน package tests และ static checks**

รัน py -3.12 -m pytest -q, py -3.12 -m ruff check บน CAD, CLI และ test targets, py -3.12 -m mypy --strict บน CAD modules และ py -3.12 scripts/check.py; แก้เฉพาะ failure ที่เกิดจาก CAD patch

### Task 6: ปิด pilot evidence และตรวจขอบเขต repository

**Files:**
- แก้ root docs/superpowers/plans/2026-09-22-production-platform-next-products.md เพื่ออ้างถึง CAD pilot เมื่อ evidence ผ่าน
- เพิ่ม root docs/superpowers/reviews/2026-09-25-cad-parametric-part-generator-v1a-evaluation.md
- รัน catalog/link/number/docs guards; ไม่คัดลอก source หรือ artifact จาก nested repository

- [x] **Step 1: ตรวจ 20-case evidence และ golden review**

ยืนยันจำนวน accepted/replayed/rejected ตรงที่กำหนด, ทุก output ผ่าน reopen QA, tamper สองกรณี fail closed, model_calls=0; เปิด preview และ FCStd ห้าชุดใน FreeCAD GUI เพื่อตรวจว่ามองเห็นชิ้นงานและ feature tree แก้ไขต่อได้ บันทึก platform, exact runtime identity, timing, bytes และข้อจำกัดใน pilot evaluation

- [x] **Step 2: commit product adapter ที่ตรวจผ่านแล้ว**

หลัง golden review ผ่านและ nested package checks ผ่าน ให้ตรวจ diff ของ nested repo, stage เฉพาะ CAD source/tests/docs, commit ใน branch main ด้วยข้อความ feat(cad): add verified parametric mounting plate V1A; push เฉพาะเมื่อมี upstream ที่ตั้งค่าไว้ ห้ามสร้าง remote

- [x] **Step 3: อัปเดต control-plane roadmap และ catalog จากหลักฐานจริง**

เพิ่ม pilot evaluation และปรับ product order หลังตรวจ nested commit SHA จริงแล้ว; เชื่อมกับ local path/commit ที่มีอยู่เท่านั้น หากไม่มี remote ให้ระบุ local-only และห้ามสร้าง GitHub URL หรือ registry metadata ขึ้นเอง

- [x] **Step 4: รัน root documentation checks**

รัน task docs:catalog, task docs:catalog:check, task docs:links:check, task docs:numbers:check และ task docs:check; หากพบ gap-review registry drift แบบ baseline missing=3 ให้บันทึกตามจริงและอย่าแก้ database หรือ review rows ที่ไม่เกี่ยวกับ CAD

- [x] **Step 5: ตรวจ diff และปิดงานอย่าง scoped**

ตรวจ git diff --check และ git status --short ใน root และ nested repo; stage เฉพาะ root spec/plan/evidence/catalog ที่ task นี้สร้างหรือแก้ ห้ามใช้ git add .; commit root docs ใน main ต่อเมื่อ required root checks ผ่านและไม่มี approval gate อื่น หาก docs:check ยังติด baseline gap-review registry drift ให้คง root docs เป็น reviewable working-tree changes แล้วรายงานผล ห้ามแก้ database ที่ไม่เกี่ยวข้อง

**Task 6 execution result (2026-09-25):** Benchmark และ visual review ผ่าน; product commit `d0d9ac9ddfea030efeb6f141f0e673a2f2d4db9d` อยู่บน nested `main` แบบ local-only เพราะไม่มี remote/upstream. Catalog, link และ number checks ผ่าน; `task docs:check` หยุดที่ baseline `entity-graph:check missing=3, stale=0`. ตาม gate จึงคง root CAD plan/spec/evaluation/roadmap/catalog edits ไว้ใน working tree เพื่อ review และไม่แก้ gap rows หรือ database ที่ไม่เกี่ยวข้อง.

**Completion gate:** FreeCADCmd runtime ที่ pin แล้วสร้างและเปิดซ้ำ FCStd/STEP/STL ได้, package checks ผ่าน, benchmark 20 กรณีตรง expected, มี human review 5 ชุด, root evidence เชื่อมกับ commit ที่มีจริง, model_calls=0; หาก runtime probe หรือ required verification ล้มเหลว ให้รายงาน blocker ที่ตรวจพบและหยุดก่อน commit/push

## References

- [FreeCAD downloads](https://www.freecad.org/downloads) — official Windows portable package and current stable release
- [FreeCAD command-line and headless documentation](https://github.com/FreeCAD/FreeCAD-documentation/blob/main/wiki/Start_up_and_Configuration.md) — FreeCADCmd console mode, command-line behavior, and script execution
