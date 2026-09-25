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

The root `scripts/check.py` entrypoint runs the full repository test suite,
Ruff and strict mypy for the Graphics and Spreadsheet modules. These remain
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
