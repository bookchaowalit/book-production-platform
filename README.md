# Book Production Platform

แพลตฟอร์ม local สำหรับแปลงข้อมูลโดเมนที่มีโครงสร้างให้เป็นผลผลิตจริงผ่าน
adapter และ engine ที่ตรวจสอบซ้ำได้ ใช้กฎ โปรแกรม และ API กับงานที่ทำซ้ำได้
แล้วเรียกโมเดลเฉพาะขั้นที่ต้องใช้ความสามารถเชิงกำเนิดจริง ๆ

## Doctor — ตรวจ runtime และความพร้อมของแต่ละ product

`book-production doctor` ตรวจ runtime ในเครื่องแบบ offline (python,
freecadcmd, ffmpeg, blender, unity) แล้วสรุปว่า product บน dispatcher
(scene, graphics, game, spreadsheet, audio, cad, data, bim) ตัวใดพร้อมรัน
จาก runtime ที่จำเป็นเท่านั้น ไม่มีการเข้าถึงเครือข่ายและไม่รัน pipeline

```powershell
book-production doctor                 # ตารางแบบคนอ่าน (ค่าเริ่มต้น)
book-production doctor --format json   # ผลลัพธ์ JSON นิ่งสำหรับเครื่องอ่าน
```

- การค้นหา binary: ใช้ `BLENDER_BIN` และ `FFMPEG_BIN` เดิม และรับ
  `FREECARDCMD_BIN` กับ `UNITY_BIN` เพิ่มสำหรับ doctor แล้วต่อด้วย `PATH`
  ถ้ายังไม่พบ freecadcmd จะลองตำแหน่ง pinned runtime ตาม lock ที่
  `.runtime/cad/freecad-<version>/extracted/...` อีกขั้น
- เวอร์ชัน: freecadcmd อ่านจาก runtime lock (`FreeCAD 1.1.3`) โดยไม่ spawn,
  ffmpeg และ blender ถูก probe แบบ bounded (timeout 15 วินาที) เสมอ,
  unity ไม่ถูก spawn เพราะ `-version` ต้องใช้ project; ธง `--probe` จะ spawn
  `FreeCADCmd --version` เพิ่มและปฏิเสธถ้าเวอร์ชันไม่ตรง lock
- exit code: `0` เมื่อคำสั่งตรวจสำเร็จแม้ runtime ตัวเลือกบางตัวจะหาย;
  `2` เมื่อพบตัว freecadcmd แต่ runtime lock ใช้ไม่ได้ (เช่น
  `unsupported_ifcopenshell_runtime`, `unsupported_freecad_build`) หรือ
  `cli_io_error` พร้อม JSON error code ที่นิ่ง
- doctor เป็นรายงานเท่านั้น คำสั่ง production ยังต้องส่ง `--blender`,
  `--unity` หรือ `--freecadcmd` ของตัวเองต่อตามปกติ

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
manifest หรือ artifact ที่ถูกแก้ไข และจะไม่เขียนทับงานเดิม

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

## Pipeline: Scene-to-Graphics (3D Scene → PNG → Graphics Template Batch)

`book-production pipeline scene-to-graphics` เชื่อมสอง product ที่มีอยู่เข้าด้วยกันแบบ
thin orchestration โดยไม่ย้าย ownership ของ code: stage แรกเรียก scene composer
เดิม (`compose_scene`) เพื่อ render ฉากสินค้าด้วย Blender จริง แล้ว stage ที่สองเรียก
graphics batch เดิม (`run_batch`) เพื่อทำภาพ SVG/PNG หลายขนาดโดยใช้ `render.png`
จากฉากเป็น `product_image` เส้นทางปกติ deterministic, offline และรายงาน
`model_calls: 0`

การส่งต่อระหว่างสอง stage ผ่าน **versioned artifact contract `scene-render-batch.v1`**
(schema อยู่ที่ `pipeline/scene-render-batch.v1.schema.json`) ทุก path ใน artifact
ที่เผยแพร่เป็น relative POSIX path เท่านั้น: `scene.artifact_dir` อ้างจาก pipeline
output root และ path ภายใน (เช่น `render.png`) อ้างจาก artifact_dir ห้าม absolute
path, backslash หรือ traversal segment

### การใช้งาน

เตรียม `scene.json` (SceneIR v1) + `.glb` ใน input root และไฟล์ spec
(`scene-graphics-spec.v1` — ครึ่ง graphics ของ contract: `template_id`,
`variants` สูงสุด 3 จาก square/portrait/story, `brand`, `content`) ตัวอย่างที่
`examples/pipeline/scene-graphics-spec.json`:

```powershell
book-production pipeline scene-to-graphics `
  --scene .\input\scene.json `
  --input-root .\input `
  --spec .\examples\pipeline\scene-graphics-spec.json `
  --output-root .\.runtime\pipeline `
  --blender "$env:BLENDER_BIN" `
  --timeout-s 240
```

ผลลัพธ์ภายใต้ `<output-root>`:

- `<scene-fingerprint>/` — งาน scene เดิม (`scene.blend`, `render.png`,
  `qa-manifest.json`) เผยแพร่โดย scene composer เอง พร้อม replay ตามปกติ
- `.pipeline/<pipeline-id>/scene-render-batch.json` — contract ที่ผ่านการตรวจแล้ว
  (scene half แปลงจาก QA manifest ของงาน scene, graphics half มาจาก spec)
- `.pipeline/<pipeline-id>/graphics/` — ผลลัพธ์ graphics batch เดิมต่อหนึ่งขนาด
  (SVG + PNG) พร้อม manifest ของ graphics เอง
- `.pipeline/<pipeline-id>/pipeline-manifest.json` — manifest ที่โยง identity ทั้งสอง
  stage: scene fingerprint, contract hash, graphics `batch_id`, idempotency key,
  hash และขนาดของทุก artifact โดยไม่มี timing หรือ absolute path

`pipeline_id` คือ SHA-256 ของ contract ทั้งก้อน (recipe + ผล scene + spec) ดังนั้น
การรันซ้ำด้วย input เดิมได้ผลลัพธ์เดิม: scene stage จะ replay จาก hash, ถ้า
pipeline manifest มีอยู่แล้วและตรงกันระบบจะตอบ `replayed` โดยไม่รัน graphics ซ้ำ
และไม่เขียนทับ artifact เดิมเลย ถ้า contract หรือ manifest ที่เผยแพร่ไว้ถูกแก้ไข
จะปฏิเสธด้วย `pipeline_conflict`

### ขอบเขตและ error

- pipeline ไม่ render เองและไม่คำนวณ layout เอง — ทุกงานผลิตยังอยู่กับ scene3d และ
  graphics เดิม; contract ผ่าน `parse_scene_render_batch` ตรวจได้ทุกเมื่อ
- stage ล้มเหลว = pipeline ล้มเหลว: scene error code ของ scene3d ส่งต่อตรง
  (เช่น `blender_unavailable`, `deadline_exceeded`) ถ้า graphics batch ไม่รับทุกขนาด
  จะปฏิเสธด้วย `graphics_batch_failed` และไม่มีการเขียน pipeline manifest
- stable error codes ของ pipeline: `invalid_graphics_spec`, `invalid_timeout`,
  `contract_validation_failed`, `pipeline_conflict`, `graphics_batch_failed`,
  `publication_failed`, `unsafe_output_path`, `cli_io_error` (exit code `2`)
- ขีดจำกัด spec 64 KiB, variants 1–3 แบบไม่ซ้ำ; graphics stage ยังใช้ขอบเขต
  เดิมของ Graphics V1A รวมถึง browser ในเครื่องสำหรับ rasterize PNG
- ไม่มีการเรียกโมเดลหรือเครือข่ายในทุก stage; ต้นทุนค่าคอมพิวต์รายงานเป็น
  `unknown` ตามเดิม

## Graphics V1A Template Batch

Graphics V1A turns validated JSON and brand tokens into editable SVG and PNG
artifacts using `GraphicIR`, fixed templates, replay checks, and local QA. Its
standard path is deterministic and reports `model_calls=0`; no model is used
for layout or rendering. The three templates are `quote_card`, `product_card`,
and `announcement`, with square, portrait, and story sizes.

Python 3.12 or newer and a local Microsoft Edge or Google Chrome installation
are required. A headless local browser rasterizes SVG so Thai marks are shaped
correctly; assets never leave the machine. Custom TTF/OTF files are embedded in
SVG outputs under a content-derived family name. Configure
`GRAPHICS_PRODUCTION_BROWSER` to select a browser executable, or
`GRAPHICS_PRODUCTION_BROWSER_PROFILE` to relocate its isolated local profile.

Run a batch and the 30-output synthetic benchmark:

```powershell
book-production graphics batch `
  --input .\examples\graphics\batch.json `
  --output .\.runtime\graphics\output `
  --idempotency-key graphics-demo-1

python -m book_production_platform.graphics_production.scripts.benchmark `
  --fixture .\tests\graphics\fixtures\batch_30.json `
  --output-dir .\.runtime\graphics\benchmark
```

Artifacts are named after each item's `request_id` and live directly in the
output directory next to every batch manifest that hashes them. An item whose
name collides with another item in the same batch is rejected with
`artifact_name_collision`; an item that would replace a file another batch
already wrote with different bytes is rejected with
`artifact_exists_with_different_content` (identical bytes are reused). Use a
new output directory or `request_id` for a changed render.

This repository is the owner of graphics production. `book-graphics-production`
is an older frozen fork kept only until it is archived; safety fixes are
mirrored there, features are not. `python scripts/graphics_fork_parity.py
--fork ..\book-graphics-production` reports which modules have drifted and
fails if the fork has a module this repository lacks.

The root `scripts/check.py` entrypoint runs the full repository test suite,
Ruff and strict mypy (pinned to the Windows platform in `pyproject.toml`) for
every product module listed in the script, and GitHub Actions runs the same
script on Ubuntu (`.github/workflows/ci.yml`). Tests that need Blender, the
pinned FreeCAD runtime, FFmpeg or a Chromium-family browser skip with a reason
when that runtime is absent. These remain
domain adapters inside this shared product repository and do not create another
agent or model runtime.

## Spreadsheet V1A Workbook Builder

Spreadsheet V1A converts explicit typed tables into editable XLSX workbooks
with `Summary` and `Detail` sheets. Its `WorkbookIR` uses typed values,
allowlisted `SUM`, `COUNT`, and `IF` formulas, named ranges, and source
lineage. A deterministic evaluator computes cached values before XlsxWriter
creates the file; ZIP/XML reopen QA checks the result. Text beginning with
`=`, `+`, `-`, or `@` stays text. The standard path reports `model_calls=0`.

Build a workbook and run the 20-case synthetic benchmark:

```powershell
book-production spreadsheet build `
  --input .\examples\spreadsheet\workbook.json `
  --output .\.runtime\spreadsheet\output `
  --idempotency-key spreadsheet-demo-1

book-production spreadsheet benchmark `
  --fixture .\tests\spreadsheet_production\fixtures\workbooks_20.json `
  --output-dir .\.runtime\spreadsheet\benchmark
```

The HTTP interface is available from
`book_production_platform.spreadsheet_production.main:app` for local use. It
does not call an LLM; future semantic column mapping or anomaly explanations
remain optional adapters and cannot replace formula evaluation.

## Data SQL/Pipeline Builder V1A

Data Pipeline Builder converts a typed `pipeline-request.v1` operation graph
into parameterized SQL and a lineage/query-plan report. It validates schema,
graph structure, operation types, and deterministic ordering before publishing
content-addressed local artifacts: `pipeline.sql`, `parameters.json`,
`report.json`, and `qa-manifest.json`, with a separate integrity receipt.
`parameters.json` contains filter values, so keep the output root in a
user-private local location and do not share it. CLI/API responses contain job
metadata only; benchmark summaries contain aggregate counts only. The normal
path reports `model_calls: 0`.

This V1A compiles and validates a plan; it does not connect to or read from a
source database and does not execute the generated `SELECT`. Query-plan
verification uses an in-memory SQLite schema stub and `EXPLAIN QUERY PLAN`.
The source reference and typed schema describe the future source contract, not
an active data connection.

Build from one standalone request JSON:

```powershell
book-production data pipeline build `
  --input .\pipeline-request.json `
  --output-root .\.runtime\data-pipeline
```

The optional FastAPI endpoint is
`book_production_platform.data_pipeline.main:app`. The project does not add an
ASGI server dependency; when using a locally installed ASGI server such as
Uvicorn, keep it bound to loopback:

```powershell
$env:DATA_PIPELINE_OUTPUT_ROOT = ".\.runtime\data-pipeline\api"
uvicorn book_production_platform.data_pipeline.main:app --host 127.0.0.1 --port 8765
```

Do not bind this local pilot to a LAN or public interface. The endpoint is
`POST /v1/data/pipelines` and stores artifacts under
`DATA_PIPELINE_OUTPUT_ROOT` (default `.runtime/data-pipeline`).

Run the synthetic acceptance benchmark into a new, empty output directory:

```powershell
python -m book_production_platform.data_pipeline.scripts.pipeline_benchmark `
  --fixtures .\tests\data_pipeline\fixtures\pipelines_20.json `
  --output-root .\.runtime\data-pipeline\benchmark-new-run
```

Measured locally on 2026-09-25: 20 cases, 12 accepted, 8 expected rejections,
0 unexpected outcomes, 0 query-plan failures, 0 lineage failures, 12 exact
replay hits, 0 model calls, and 32,566 artifact bytes written. This is a
synthetic plan-only gate, not a measurement of execution against a real source
database. Monetary compute cost remains `unknown` without an approved rate
card; there is no deployment target.

## CAD Parametric Part Generator V1A

CAD V1A turns one bounded `part-ir.v1` JSON request into an editable native
FreeCAD mounting plate and neutral STEP/STL exports. The first template is
intentionally narrow: one rectangular plate with four through-bores. The
adapter builds a fixed FreeCAD feature tree, checks the reopened native model,
STEP units and volume, STL geometry, bore locations, and a deterministic PNG
preview before publishing. The production path is local and deterministic;
it uses no LLM, image model, or remote service and reports `model_calls: 0`.

### Runtime and build

- Python 3.12 or newer
- Windows x86-64 and the pinned portable FreeCADCmd `1.1.3`
- Runtime archive SHA-256:
  `9c6959dc9c4dba64dd818a62447e3dfedb4221d776fb044b239d462f150bcec4`
- FreeCADCmd executable SHA-256:
  `8feb06052673cdcd56df60d856cc5e6fbf8cf862d56f1419e80a558f505a7b23`

The package checks the executable version, build, platform, and hash against
`cad_production/freecad-runtime.v1.json` before a job. Runtime files belong in
the ignored `.runtime/cad/` directory; the portable runtime is not installed
system-wide or committed. Use the included request at
`examples/cad/part-ir.json`:

```powershell
$freecadcmd = ".\.runtime\cad\freecad-1.1.3\extracted\FreeCAD_1.1.3-Windows-x86_64-py311\FreeCADCmd.exe"
book-production cad build `
  --input .\examples\cad\part-ir.json `
  --output-root .\.runtime\cad\jobs `
  --freecadcmd $freecadcmd `
  --timeout-s 120
```

An accepted fingerprint directory contains exactly `part.FCStd`, `part.step`,
`part.stl`, `preview.png`, and `qa-manifest.json`. The `.FCStd` file keeps the
editable Body, sketch, pad, hole sketch, and pocket feature history. Replay
receipts are stored separately under `.receipts/`; an exact replay validates
all five artifacts and skips the FreeCAD build. Existing artifacts are never
overwritten.

Run the synthetic acceptance suite once into a new report location:

```powershell
book-production cad benchmark `
  --fixtures .\tests\fixtures\cad `
  --output .\.runtime\cad\pilot\benchmark-cad-v1a.json `
  --freecadcmd $freecadcmd
```

The benchmark creates `benchmark-cad-v1a.jobs` beside the report and retains its
five accepted native projects and previews for local review. It checks five
fresh builds, five exact replays, eight malformed/boundary requests, and two
tampered-artifact replays. The JSON report includes case IDs, counts, timings,
runtime identity, and byte totals; it contains neither PartIR values nor local
paths. Output and job locations must be new and are not overwritten.

### V1A boundaries and errors

PartIR is limited to 64 KiB, decimal strings with at most three fractional
digits, and millimeter units. Width and height range from 20–500 mm, thickness
from 1–50 mm, bore diameter from 2–25 mm, and offsets from 5–200 mm. Each bore
must leave at least a 2 mm edge or inter-bore web. Only `mounting-plate.v1` and
`four-corner.v1` are accepted. Requests cannot supply scripts, expressions,
macros, paths, URLs, arbitrary feature types, or source CAD files.

Rejected CLI requests print one stable JSON object and exit with status 2;
benchmark outcome mismatches produce a failed report and exit with status 3.
Stable codes include `invalid_part_ir`, `unsupported_template`,
`unsupported_units`, `dimension_out_of_range`,
`feature_constraint_violation`, `freecad_unavailable`,
`unsupported_freecad_build`, `worker_deadline_exceeded`,
`native_document_invalid`, `step_export_invalid`, `stl_export_invalid`,
`preview_invalid`, `replay_integrity_error`, and `publication_failed`.
Input and generated artifacts stay on the local machine. This pilot is not an
OS sandbox for untrusted files and its geometric checks are not engineering,
structural, safety, manufacturing, or regulatory certification. Monetary cost
is `unknown`; the standard build path makes zero model calls.

## Audio Master V1A

Audio Master converts one local PCM WAV and a versioned `audio-ir.v1` request
into an integrity-checked 24-bit/48 kHz WAV master plus `qa-manifest.json`.
The request selects one of three fixed presets: `podcast-mono.v1`,
`podcast-stereo.v1`, or `music-stereo.v1`. The adapter owns the FFmpeg filter
graph; arbitrary filter strings and direct numeric overrides are rejected. The
normal path uses FFmpeg loudness analysis, double-pass `loudnorm`, and a second
measurement of the output. Python validates the IR, paths, WAV format, channel
count, duration, loudness, true peak, and replay receipt. It reports
`model_calls: 0`; there is no LLM, speech recognition, audio generation, or
network access in the mastering path.
The filter behavior follows the [FFmpeg `loudnorm` documentation](https://ffmpeg.org/ffmpeg-filters.html#loudnorm).

Generate a synthetic modulated-tone fixture and master it locally:

```powershell
py -3.12 .\examples\audio\generate_fixture.py `
  --output .\.runtime\audio-master-demo\input\synthetic-voice.wav

book-production audio master `
  --audio-ir .\examples\audio\audio-ir.json `
  --input-root .\.runtime\audio-master-demo\input `
  --output-root .\.runtime\audio-master-demo\jobs
```

The example intentionally uses synthesized tones rather than a recording of a
person. Accepted output is published under a content fingerprint with
`master.wav`, `qa-manifest.json`, and `.receipt.json`; an exact replay verifies
the stored hashes and skips analysis/rendering. Changing the source, AudioIR,
preset, recipe, or FFmpeg build generates a separate fingerprint. Source audio
is never modified. The local pilot accepts mono or stereo PCM WAV up to 2 GiB
and two hours, checks that the selected preset matches the channel count, strips
container metadata, rejects silent input, and never overwrites an existing
artifact. It is not an OS sandbox for files from untrusted users.

Initial preset targets are fixed in the adapter: mono podcast at -19 LUFS / -1.5
dBTP, stereo podcast at -16 LUFS / -1.5 dBTP, and stereo music at -14 LUFS / -1.0
dBTP. These are pilot recipes, not a claim that one mastering target fits every
platform or creative brief. Numerical QA does not prove speech intelligibility
or subjective quality; human listening review remains a separate product gate.
Without an approved local rate card, monetary cost is reported as `unknown`.

Audio ownership: Audio Master is not a copy of `book-audio-production`. This
repository owns the `audio-ir.v1` contract (one WAV, fixed presets);
`book-audio-production` owns `audio-batch.v1` (batch trim and per-track
loudness targets) and its pinned FFmpeg build. Keep new audio features on the
side that owns their contract. `python scripts/audio_boundary_check.py
--sibling ..\book-audio-production` fails if either side starts handling the
other's contract or importing the other's package, and prints the sibling's
FFmpeg pin for comparison with the `ffmpeg_version` in Audio Master manifests.

## Game Scene Prototype Builder V1A

Game V1A converts a validated `game-ir.v1` into a native, editable Unity 2D
project. The current collection template creates a player, walls, collectibles,
an exit, and a deterministic gameplay trace. Python checks the IR and grid
logic before Unity runs; Unity composes the scene and proves the collection loop
through EditMode, PlayMode, and a fresh-process reopen check. The standard path
uses no LLM, image model, remote service, or generated asset and reports
`model_calls: 0`.

### Runtime and build

- Python 3.12 or newer
- Unity Editor `6000.6.2f1` with its bundled Test Framework
- The Unity executable is supplied with `--unity`; other Editor versions are
  rejected for this pilot

Install the package and test tools in the repository virtual environment:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[test]"
```

Build the included room:

```powershell
$gameOutput = Join-Path $env:LOCALAPPDATA 'BookProduction\Game'
book-production game build `
  --game .\examples\game\collection-room.json `
  --output-root $gameOutput `
  --unity "C:\Program Files\Unity\Hub\Editor\6000.6.2f1\Editor\Unity.exe" `
  --timeout-s 120
```

### Audio from local files (`game-ir.v2`)

`game-ir.v2` is `game-ir.v1` plus optional `assets` and `audio`. Each asset is a
WAV or Ogg file named in `assets[].filename`, pinned by `sha256`, and read from
the folder passed with `--asset-root`. `audio.music` loops for the whole scene
and `audio.collect_sfx` plays once per collected objective:

```json
"assets": [{"asset_id": "theme", "kind": "audio", "filename": "theme.wav", "sha256": "…"}],
"audio": {"music": "theme", "collect_sfx": null}
```

Before Unity starts, every file must sit directly in the asset root, match its
hash, be at most 20 MB and 10 minutes, and probe as WAV/Ogg with `ffprobe`
(`FFPROBE_BIN` or `PATH`). Files are copied to `Assets/BookProduction/Audio/`
with deterministic `.meta` GUIDs; the audio slots are part of the composer
template, so the scene stays byte-identical across rebuilds. The QA manifest
lists each asset and the worker report checks the music source and collect
sound. A `game-ir.v1` input produces the same canonical IR and worker input as
before; the template gained the built-in `com.unity.modules.audio` module (no
download), so v1 builds get new fingerprints.

On Windows, keep the output root short because Unity creates its `Library`
database below the fingerprinted project path. A deeply nested repository
output root can exceed the host path limit when opening the project in Unity.

The accepted artifact is published atomically under its content fingerprint:

- `project/` — native Unity project; open it in Unity and edit
  `Assets/Scenes/collection-room.unity`
- `gameplay-trace.json` — verified blocked-exit, movement, collection, and win
  events
- `qa-manifest.json` — source and template hashes, pinned Unity build, test
  results, output hashes and sizes, timing, and `model_calls: 0`
- `.receipt.json` — pins the project, scene, trace, and QA manifest hashes for
  exact replay

An exact repeat verifies the receipt and returns `replayed` without starting
Unity. A changed GameIR, Unity executable, or template receives a new
fingerprint. Existing output is never overwritten. Benchmark the accepted,
replayed, and rejected fixture set after setup:

```powershell
book-production game benchmark `
  --fixtures .\tests\fixtures\game_production `
  --output .\.runtime\game-production\benchmark.json `
  --unity "C:\Program Files\Unity\Hub\Editor\6000.6.2f1\Editor\Unity.exe"
```

Latest local benchmark (2026-09-24): 5 builds and 5 exact replays passed; all
9 invalid fixtures and the tampered-artifact replay were rejected, with zero
unexpected outcomes. Unity `6000.6.2f1` passed EditMode 10/10 and PlayMode 5/5;
the executable SHA-256 was
`6ffd6192620a1b6c82d78b24c8881041c2b22dafbee4ee404a6156c3bb7946b2`. Fresh
build latency was 39.7–56.4 s (median 50.6 s, p95 56.4 s); exact replay was
155–364 ms (median 252 ms, p95 364 ms). Published Unity projects were 1.12–1.20 MB.
`model_calls: 0`; monetary cost is unknown. The report is saved locally at
`.runtime/game-production/benchmark-final.json`. On 2026-09-25, an agent visual
review passed for the accepted `larger-valid-room` fixture: the Unity Editor
camera render shows the player, 14 walls, four objectives, and exit inside the
frame. The fixture's verified PlayMode trace records movement, all four
collections, and victory. Review files are
`.runtime/game-production/manual-scene-review-20260925.png` and
`.runtime/game-production/manual-scene-review-20260925.json`; the benchmark
report now records `manual_scene_review.status=passed`. This visual review uses
an Editor camera capture; gameplay is verified by the automated PlayMode trace,
not manual keyboard input.

### V1A limits and error codes

GameIR input is limited to 256 KiB, worlds to 64 × 64 cells, walls to 192
cells, and objectives to 32. The Unity job has one 120-second deadline, an
8 MiB log cap, a 1 GiB staging cap, and a 64 MiB published-project cap. Only
the registered collection template and palette are accepted. Users cannot
provide scripts, assemblies, paths, URLs, or Unity component types through the
IR. This local pilot is not an OS sandbox for opening projects or assets from
untrusted sources.

Rejections emit one JSON object with a stable code and exit status 2. Codes
include `invalid_game_ir`, `resource_cap_exceeded`, `unsafe_input_path`,
`unsafe_output_path`, `invalid_timeout`, `unity_unavailable`,
`unsupported_unity_version`, `deadline_exceeded`,
`unity_log_cap_exceeded`, `scene_qa_failed`, `unity_test_failed`,
`gameplay_qa_failed`, `publication_failed`, `publication_conflict`, and
`replay_integrity_error`.

### Export playable players (`game export`)

`game export` turns an accepted `game build` artifact into a playable build for
one target. It first verifies the source artifact's receipt, copies its project
into staging, adds the packaged `PlayerExporter.cs` editor script to that copy
only, and runs Unity with `-buildTarget`. The published editable project is
never modified.

| Target | Unity build target | Output entrypoint | Install needed |
|---|---|---|---|
| `web` | WebGL (Gzip with decompression fallback) | `player/index.html` | WebGL Build Support |
| `windows` | StandaloneWindows64 (Mono) | `player/<scene_id>.exe` | Windows Build Support |

```powershell
$exportOutput = Join-Path $env:LOCALAPPDATA 'BookProduction\GameExport'
book-production game export `
  --artifact <accepted game build artifact dir> `
  --target web `
  --output-root $exportOutput `
  --unity "C:\Program Files\Unity\Hub\Editor\6000.6.2f1\Editor\Unity.exe" `
  --timeout-s 900
```

The web build writes `Build/player.{loader.js,data,framework.js,wasm}` (the
last three as `.unityweb`); Unity names them after the output folder. It loads
from any static host, including servers that do not send `Content-Encoding`
headers, because the loader decompresses in JavaScript when needed. Serve the
`player/` folder over HTTP; opening `index.html` from disk does not work in
browsers.

Local verification (2026-09-30, `pod-launch-arcade`, Unity `6000.6.2f1`): the
web export took 120 s and produced a 4.4 MiB player that loaded in Chromium over
`python -m http.server` with no console errors and moved the player with the
arrow keys; the Windows export took 37 s, produced a 91 MiB player, and ran for
8 s on Direct3D 12 with no errors in its log. Repeating both exports returned
`replayed` without starting Unity. The host CPU was near 90% busy during the
web run, so treat these timings as upper bounds.

The export is published atomically under a fingerprint of the exporter version,
source artifact fingerprint and project hash, target, and pinned Unity
executable. Each export contains:

- `player/` — the playable build; Unity's `*_DoNotShip` folders are removed
- `export-manifest.json` — source fingerprint, target, Unity build result,
  every player file with its SHA-256 and size, timing, and `model_calls: 0`
- `.receipt.json` — pins the player tree and manifest hashes for exact replay

An exact repeat verifies the receipt and returns `replayed` without starting
Unity. Unity player builds are not byte-reproducible, so replay checks the
published files instead of rebuilding them. Limits: timeout up to 1800 s
(default 900 s), 64 MiB log cap, 512 MiB player cap. Export-specific error codes
are `unsupported_export_target`, `export_target_not_installed`,
`unity_export_failed`, and `player_incomplete`, in addition to the shared codes
above. Android and iOS are not supported yet: Android needs the Android Build
Support module, and iOS needs a macOS host with Xcode.

## BIM Quantity Takeoff V1A

รุ่นทดลองนี้อ่านโมเดล IFC2X3/IFC4 ไฟล์เดียวในเครื่อง (สูงสุด 64 MiB และไม่เกิน
250,000 IFC instances) พร้อม `BimTakeoffIR` แบบ strict แล้วดึงเฉพาะ
occurrence-level `IfcElementQuantity` (Length/Area/Volume/Count) แปลงหน่วยเป็น
SI และเผยแพร่ `takeoff.json`, `takeoff.csv`, `qa-manifest.json`
อย่าง content-addressed พร้อม replay ที่ตรวจ hash ทุกไบต์

### ข้อจำกัดที่สำคัญ

- รองรับเฉพาะ Windows x86-64 กับ runtime เดิมที่ pin ไว้ (FreeCAD 1.1.3 /
  Python 3.11 / IfcOpenShell 0.8.4) ผ่าน Windows short path เท่านั้น
  ไม่มีการติดตั้งหรือคัดลอก runtime เพิ่ม
- ไม่รองรับ IFCZIP, IFCXML, IFC4X3, geometry-derived quantity, type-level
  quantity, pricing หรือ code compliance
- ปริมาณที่ขาด คลุมเครือ หรือแปลงหน่วยไม่ได้ จะถูกรายงานเป็น coverage gap
  ไม่เติมค่าศูนย์หรือค่าประมาณ

### การใช้งาน

```bash
book-production bim takeoff   --ir examples/bim/bim-takeoff.json   --ifc path/to/model.ifc   --output-root .runtime/bim/jobs   --freecadcmd path/to/FreeCADCmd.exe   --ifc-site-packages path/to/site-packages   --timeout-s 120
```

สถานะและ exit code: `complete`/`replayed` → 0, `partial` → 3,
`needs_input` → 4, กรณีอื่น (rejected) → 2 พร้อม JSON error code ที่นิ่ง

### Benchmark 20 กรณี

```bash
book-production bim benchmark   --fixtures tests/fixtures/bim/cases.json   --output .runtime/bim/benchmarks/latest.json   --freecadcmd path/to/FreeCADCmd.exe   --ifc-site-packages path/to/site-packages   --fixture-macro tests/bim_production/fixture_worker.FCMacro   --timeout-s 120
```

### ความเป็นส่วนตัว

ทุกอย่างทำงานในเครื่อง ไม่มีการอัปโหลดโมเดลหรือเรียก parser ภายนอก
เส้นทางปกติ `model_calls: 0` และ `monetary_cost: "unknown"`
source IFC path จะไม่ปรากฏใน report, manifest, benchmark summary หรือ logs

### การตรวจสอบแบบ offline

```bash
py -3.12 -m pytest -q tests/bim_ir tests/bim_production
py -3.12 -m ruff check src/book_production_platform/bim_ir src/book_production_platform/bim_production tests/bim_ir tests/bim_production
py -3.12 -m mypy --strict src/book_production_platform/bim_ir src/book_production_platform/bim_production
py -3.12 scripts/check.py
```
