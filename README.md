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

### AI brief adapter (V1B)

`scene3d/provider.py` แปลง brief ข้อความอิสระเป็น `scene-ir.v1` ผ่าน Central
AI Gateway ด้วย `brief_adapter_kit` (`brief-adapter.v1`) โมเดลเลือกได้เฉพาะ
preset ที่ลงทะเบียนใน SceneIR (composition/มุมกล้อง, render profile, lighting,
background, material, recipe) และคัด path `.glb` ที่เขียนไว้ใน brief เท่านั้น
ไม่สร้าง geometry และ SceneIR v1 ไม่มีค่าตัวเลขอิสระ จึงปฏิเสธทุกค่าตัวเลข
หรือ key กล้อง/geometry (`numeric_value_not_allowed`,
`geometry_or_camera_forbidden`) preset นอก registry → `rejected`
(`preset_outside_registry`); มุมกล้องหรือขนาดภาพที่ brief ไม่ได้ระบุ, path ที่ไม่มี
ใน brief หรือ look ที่ไม่มี preset รองรับ → `needs_input` เนื้อหาแบบคำสั่ง,
path traversal หรือสคริปต์ `bpy` → `needs_input` โดยไม่เรียกโมเดล

preset ที่มีค่าลงทะเบียนเดียว (lighting, background, material, recipe) เป็นค่า
default ของ engine: ถ้า brief ไม่พูดถึงด้านนั้นเลย engine เติมให้เสมอ แม้โมเดล
จะใส่ไว้ใน `missing`; ถ้า brief พูดถึงด้านนั้น (`LOOK_TOPICS`) แต่ไม่ได้ระบุ look
ที่ลงทะเบียน (`DEFAULT_LOOK_EVIDENCE`) → `needs_input` (`look_not_registered`)
ไม่ว่าโมเดลตอบอะไร ส่วน composition/render profile ที่โมเดลบอกว่า `inferred`
ยังเป็น `needs_input` เสมอ prompt ปัจจุบันคือ `scene3d-brief.v2`
(`prompt_version` ใน config) ซึ่งแสดงคำที่ engine ใช้ตรวจหลักฐานมุมกล้องและขนาดภาพ
ให้โมเดลเห็นชุดเดียวกัน และนิยาม `provided`/`inferred`/`missing` ตามที่ engine อ่าน

config `scene3d-provider-config.v1` เริ่มที่ `mode: shadow`; ไม่มี config →
`provider_not_configured` คำสั่งอยู่ใน module ของ tool (ยังไม่ได้ลงทะเบียนใน
`book-production scene`):

```powershell
python -m book_production_platform.scene3d.brief_cli brief-shadow `
  --provider-config .\.runtime\scene3d-provider.json `
  --cases .\tests\fixtures\scene3d\eval_s3d_brief30.json `
  --split .\tests\fixtures\scene3d\eval_s3d_brief30_split.json `
  --output .\.runtime\scene3d-brief-shadow.json
python -m book_production_platform.scene3d.brief_cli brief --input .\brief.json `
  --output .\input\scene.json --job-id job-001 --provider-config .\.runtime\scene3d-provider.json
```

รายงาน shadow ตรวจเฉพาะระดับ IR (preset ถูก ≥ 95% บน clear, preset นอก
registry 0, ค่าตัวเลขอิสระ 0, `needs_input` recall 100%, adversarial ผ่านเงียบ 0,
structured 0 calls, ≤ 2 calls/job, canary 0) AC-S3D-01a ยังต้อง render จริงทุก
เคสใน Blender ที่ pin version ซึ่งยังไม่ได้รัน รายงานจึงบันทึก
`gate.pending: blender_render_all_cases` และ `gate.live_eligible: false` เสมอ
ชุด 30 เคส: structured 10 / clear 10 / ambiguous 5 / adversarial 5 ไทย/อังกฤษ
รวมอย่างละ 15 (หมวดที่มี 5 เคสต่างกันได้ 1)

### Job history, trace & manual steps

งาน `scene compose` ใหม่ทุกงาน (ผ่านหรือไม่ผ่าน) บันทึก `production-trace.v1`
ไว้ที่ `<output-root>/traces/<job_id>/` และเพิ่มหนึ่งบรรทัดใน
`<output-root>/history.jsonl` โฟลเดอร์ `<fingerprint>/` ยังมีไฟล์สามรายการเดิม
พอดี (replay ตรวจแบบนั้น) trace จึงอ้าง artifact ด้วย path สัมพัทธ์
`../../<fingerprint>/render.png` ส่วน replay ที่ตรงกันไม่เขียนอะไรเพิ่ม และ
output root ที่เป็น symlink จะไม่ถูกเขียน trace

| ไฟล์ | เนื้อหา |
|---|---|
| `traces/<job_id>/trace.json` | hash ของ SceneIR และ GLB, ขั้นตอน (parse, GLB preflight, Blender build/version, build scene พร้อมค่าไฟ/world/พื้น, reopen+render, verify PNG, publish), sha256 ของ `scene.blend`/`render.png`/`qa-manifest.json` |
| `traces/<job_id>/ir.json` | SceneIR ที่ engine ใช้จริง |
| `traces/<job_id>/MANUAL-STEPS.md` | ขั้นตอนทำเองใน Blender 5.2.2 LTS: import glTF, Product Root, World/พื้น, กล้อง (เลนส์ 55 mm, ตำแหน่ง/มุม หรือสูตร fit), ไฟ Area แต่ละดวง (Power/Size/Color/ตำแหน่ง/มุม), EEVEE 64 samples, AgX, ความละเอียด, PNG และ checks; ทางเลือก KeyShot |

```bash
python -m book_production_platform.production_trace history --root .runtime/jobs
python -m book_production_platform.production_trace manual  --root .runtime/jobs <job_id>
python -m book_production_platform.production_trace verify  --root .runtime/jobs <job_id>
```

`scene3d.brief_cli brief` เขียน `<scene>.proposal.json` (adapter, mode,
จำนวน model call, preset ที่เลือก — ไม่มีข้อความ brief) ข้างไฟล์ SceneIR และ
`scene compose` บันทึกเป็น AI proposal ของ trace ค่าที่ขึ้นกับขนาดสินค้า
(scale ของ Product Root และระยะกล้องของ composition แบบ `*-fit`) วัดใน Blender
คู่มือจึงให้สูตรและบอกให้คัดลอกจาก `scene.blend`

Preset ใหม่ (ใช้ได้ใน SceneIR แบบ structured แต่ V1B adapter ยังไม่เสนอ):
lighting `high-key-white.v1`, `dramatic-rim.v1`; background `studio-white.v1`,
`transparent.v1` (PNG RGBA ไม่มีพื้น); composition `orbit-fit-000.v1` …
`orbit-fit-315.v1` ทุก 45° สร้างชุด turntable ได้ด้วย
`python -m book_production_platform.scene3d.turntable --scene scene.json --output-dir turntable --steps 8`
(ได้ `frame-NNN.json` แล้ว compose ทีละไฟล์) preset ใหม่ทดสอบระดับ IR/spec
เท่านั้นเพราะยังไม่ได้ render ใน Blender จริง; ตาราง parity และ roadmap:
[`docs/parity/scene3d.md`](docs/parity/scene3d.md)

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
for layout or rendering.

### Templates are data: themes and motifs

Every template is a JSON theme in
`src/book_production_platform/graphics_production/themes/` (`graphic-theme.v1`),
compiled by `themes.py` from the reusable drawing motifs in `motifs.py`. A new
look is a new theme file, not new code; a new kind of drawing is one motif
function that every theme can then use.

- **Themes now:** `quote_card`, `product_card`, `announcement` (the original V1A
  templates, still compiled to the identical flat `graphic-ir.v1`; square,
  portrait, story) and `synthwave`, `bold_editorial`, `soft_gradient`
  (`graphic-ir.v2`, also `landscape` 1920×1080).
- **A theme declares** its sizes, text slots (required or optional, with the
  brief labels the AI adapter accepts), the brief words that name it, a palette
  (`#RRGGBB`, `brand.primary|accent|foreground|background`, or another palette
  entry), gradient paints, layout variables, and ordered layers of motif items.
- **Layout values** are numbers or safe arithmetic over `W`, `H`, `CX`, `CY`,
  `MIN`, `MAX`, and the theme's own variables (`min`, `max`, `round`, `abs`,
  `sqrt`, `sin`, `cos`; nothing else parses), with `by_variant`, `if_asset`,
  and `if_slot` selectors, so one theme fits every size.
- **Motifs:** `rect`, `ellipse`, `line`, `polygon`, `glow`, `text_block`,
  `display_text` (title with echo shadows), `image`, `starfield`,
  `perspective_grid`, `striped_sun`, `ridge`, `waves`, `blobs`, `halftone`,
  `rays`, `rings`, `stripes`, `grain`, `corner_frame`. Random motifs are
  seeded by theme, request id, and item id, so a job always replays exactly.
- **`graphic-ir.v2` output** has gradients, paths, styled text (weight, letter
  spacing, stroke, opacity), and one named `<g>` per theme layer, which
  Affinity, Figma, and Illustrator import as editable layers. Theme fonts are
  font stacks (`builtin:display` = Anton…, `builtin:serif` = Playfair
  Display…, `builtin:mono` = Space Mono…, `builtin:thai` = Sarabun…); install
  them for the intended look.

```powershell
book-production graphics themes                       # sizes, slots, brief words
book-production graphics make --theme synthwave --variant all `
  --set headline=SOLO --set headline2=EMPIRE --set "subhead=สร้างอาณาจักร · คนเดียว" `
  --output .\.runtime\graphics\themes --idempotency-key synthwave-1
```

Python 3.12 or newer and a local PNG rasterizer are required: a Microsoft Edge
or Google Chrome installation, or the optional `resvg-py` package
(`pip install .[preview]`), used automatically when no browser is found or
when `GRAPHICS_PRODUCTION_RASTERIZER=resvg`. Both shape Thai marks correctly;
assets never leave the machine. With resvg, `GRAPHICS_PRODUCTION_FONT_DIRS`
adds font folders and `GRAPHICS_PRODUCTION_FALLBACK_FONT` names the family
used when a stack has no installed font. Custom TTF/OTF files are embedded in
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

Artifacts are named after each item's `request_id` and written to a private
per-batch folder, `<output>/<batch_id>/`; manifests record them as
`<batch_id>/<name>.svg|png`. Batches therefore never share files, so a later
batch that reuses a `request_id` (or whose browser-rendered PNG bytes differ
run to run) cannot break an earlier manifest. An item whose name collides with
another item in the same batch is rejected with `artifact_name_collision`; a
batch folder left behind with different bytes (for example after its manifest
was deleted) is never overwritten and the item is rejected with
`artifact_exists_with_different_content`. Manifests written before per-batch
folders (bare file names) still export.

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

### AI brief adapter (V1B)

`graphics_production/provider.py` turns a free-text brief into a proposed
batch (the `{"items": [...]}` input of `graphics batch`) through the Central AI
Gateway, using the shared `brief_adapter_kit` (`brief-adapter.v1`). The model
only picks a template id from the registry (`quote_card`, `product_card`,
`announcement`), a variant, and a brand-kit ref, and copies slot text from the
brief; colors come from the brand kit, canvas sizes from the variant, and
layout from the template engine. Any other key (colors, images, fonts, sizes)
or an id outside the registries is `rejected`; text not copied from the brief,
a template or size the brief does not name, or anything the model marks
missing becomes `needs_input`. Code, not the model, also decides these: a slot
the model flags `inferred` passes only when its exact text follows that slot's
label in the brief (`Button:`/`ปุ่ม:` for `cta`); a placeholder text
(`TBD`, `ยังไม่สรุป`) or a price without a number is `needs_input`; brief text
that no slot copied asking for images, files, fonts, or colors is
`needs_input` (`style_or_asset_request`); and a `brand_ref` copied from the
brief's `ref:` job-reference line is treated as unstated (the single
registered kit applies; with several kits it is missing). Instruction-like,
path, or script content abstains with zero calls. Every item is compiled by
the engine before it is returned. If a slot overflows, the job's single repair
call (the second and last call) names each overflowing slot's JSON path, its
current length, and its line and character limit, all taken from the engine's
`LayoutError`, and asks the model to shorten only those slots using only words
from the brief. A reply that is not a JSON object gets the generic repair
message instead. If the text still overflows, the result is `needs_input` with
`text_overflow`, never a silent pass. The default `prompt_version` is
`graphics-brief.v2` (v2 added the targeted overflow repair message). The dev-only
overflow cases in `tests/graphics/fixtures/eval_gfx_dev_overflow.json` (each
marked `"split": "dev"`, never gated) can be added with a second `--cases` to
tune that message without touching held-out cases.

The provider config (`graphics-provider-config.v1`, starts in `mode: shadow`)
holds the gateway URL, the *name* of the token env var, rate card, caps, and
owner approval; without it the adapter fails closed (`provider_not_configured`).

```powershell
# shadow evaluation over EVAL-GFX-brief40 (metadata-only report, never renders)
book-production graphics brief-shadow --provider-config .\.runtime\graphics-provider.json `
  --cases .\tests\graphics\fixtures\eval_gfx_brief40.json `
  --split .\tests\graphics\fixtures\eval_gfx_brief40_split.json `
  --output .\.runtime\graphics\brief-shadow.json
# live proposal (refused with shadow_mode_only until the owner sets mode=live)
book-production graphics brief --input .\brief.json --output .\proposed-batch.json `
  --job-id job-001 --provider-config .\.runtime\graphics-provider.json
```

Gate (held-out cases): template correct on clear briefs ≥ 95%, uncaught
overflow 0, ids/colors outside the registries 0, `needs_input` recall on
ambiguous briefs 100%, adversarial silent passes 0, structured cases with 0
model calls, ≤ 2 calls per job, canary leaks 0. The 40-case suite is 14
structured / 14 clear / 6 ambiguous / 6 adversarial (40 is not divisible by 6,
so the ambiguous/adversarial minimum is floor(40/6) = 6), half Thai/half
English per category.

### Job history, trace & manual steps

Every fresh `graphics batch` (accepted, partial, or rejected, including an
unexpected error, which is still raised) writes into its batch folder and the
output root:

| File | What |
|---|---|
| `<batch_id>/trace.json` | batch input hash, asset hashes, per-item steps (compile template with node boxes/font sizes/line counts, render SVG, render PNG, QA with WCAG contrast, write), rejected items with their error code, artifact sha256 |
| `<batch_id>/ir.json` | `graphics-batch-ir.v1`: the exact `GraphicIR` of every accepted item |
| `<batch_id>/MANUAL-STEPS.md` | Figma steps per item: Frame W×H, every layer in paint order (rectangles with X/Y/W/H and fill hex, image slots, text boxes with font, size, line height = size × 1.2 px, colour, alignment, and the exact lines the engine wrapped, Thai included), PNG/SVG export; Canva and Photoshop equivalents; contrast checks |
| `history.jsonl` | one metadata line per batch |

`graphics export` traces the print export the same way in its output folder
(Figma PNG at `scale`x, then Photoshop Image Size at the DPI without resample
and Save a Copy as PDF); use one output folder per export. Exact replays write
nothing. Traces hold hashes, ids, sizes, and counts only, never content text.

```bash
python -m book_production_platform.production_trace history --root .runtime/graphics/output
python -m book_production_platform.production_trace manual  --root .runtime/graphics/output <batch_id>
python -m book_production_platform.production_trace verify  --root .runtime/graphics/output <batch_id>
```

`graphics brief` adds a metadata-only `proposal` block (adapter, mode, model
calls, chosen templates) to the proposed batch; `graphics batch` records it as
the trace's AI proposal. New in this pass: one item with
`"variants": ["square", "portrait", "story"]` expands to one item per size
(`<request_id>-<variant>`), and an advisory WCAG AA contrast report per text
layer. Feature parity and roadmap: [`docs/parity/graphics.md`](docs/parity/graphics.md).

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

### Building from a CSV or XLSX file

`spreadsheet from-file` reads a `.csv`, `.tsv`, `.xlsx`, or `.xlsm` file with
the vendored shared reader (`book_production_platform.file_intake`, identical to
the root `packages/file-intake`) and builds the workbook from a structured
proposal (template, column mapping, intents) with zero model calls. Formulas in
the source are never evaluated (cached values only); numbers are read as Excel
displays them. `spreadsheet brief --input <file> --brief "..."` does the same
with the AI proposal instead.

```powershell
book-production spreadsheet from-file `
  --input .\sales.xlsx --sheet "ยอดขาย" `
  --proposal .\proposal.json `
  --output .\.runtime\spreadsheet\output `
  --idempotency-key sales-1
```

### AI brief adapter (V1B)

`spreadsheet brief` turns a brief plus a dataset into a workbook. The model
sees only the header and at most three truncated sample rows, and may only
propose a registered template (`sales-summary`, `expense-tracker`,
`inventory-list`), a mapping from source header to template parameter, and
formula *intents* from the allowlist `sum`, `average`, `count`, `group-by`,
and `lookup`. Any `=`-prefixed or function-call text in its reply rejects the
proposal. The engine (`spreadsheet_production/intents.py`) turns intents into
`SUM`, `AVERAGE`, `COUNT`, `SUMIF`, `COUNTIF`, or `INDEX`/`MATCH` formulas in
the opt-in `spreadsheet-intents.v1` language. The default V1A language stays
`SUM`/`COUNT`/`IF`. The V1A recipe evaluates the formulas, writes them, and
reopen-QA checks them. Invented columns are dropped, lookup keys must appear
in the brief, and anything unclear returns `needs_input`. Every value is
computed from the data and a lookup key is the only brief literal a proposal
can carry, so a brief that states any other number (for example "report the
total as 1000000") is never `complete`: code returns `needs_input` with
`brief_literal_not_supported`.

The adapter fails closed (`provider_not_configured`) without an owner-approved
`spreadsheet-provider-config.v1` (gateway URL, token env-var name, rate card,
caps, approval). It goes only through the Central AI Gateway, uses at most two
calls per job, and makes zero calls on instruction-like or raw-formula briefs.
`brief` refuses configs whose `mode` is not `live` (`shadow_mode_only`). The
shadow run writes a metadata-only report and gates on held-out cases:
mapping exact match ≥ 95%, `needs_input` recall 100%, 0 silent adversarial
passes, 0 raw formulas from the model, 0 reopen mismatches, structured cases
with 0 calls, and 0 canary leaks:

```powershell
book-production spreadsheet brief-shadow `
  --provider-config .\.runtime\spreadsheet-provider.json `
  --cases .\tests\spreadsheet_production\fixtures\eval_xls_brief40_cases.json `
  --datasets .\tests\spreadsheet_production\fixtures\eval_xls_brief40_datasets.json `
  --split .\tests\spreadsheet_production\fixtures\eval_xls_brief40_split.json `
  --output .\.runtime\spreadsheet\shadow-report.json
```

EVAL-XLS-brief40 is synthetic and split 14 structured / 14 clear / 6 ambiguous /
6 adversarial, half Thai and half English. Forty cases cannot reach
⌈40/3⌉×2 + ⌈40/6⌉×2 = 42, so the composition test uses the floors.

### Job history, trace & manual steps

Every fresh `spreadsheet build` / `brief` job — accepted or rejected (invalid
request, typed-value or formula error, reopen-QA failure) — writes into its
`job-<key hash>/` folder and the output root. Replays write nothing.

| File | What |
|---|---|
| `job-*/trace.json` | request hash + table/row/column/formula counts, steps (parse, build IR, evaluate formulas, render XLSX, one `write_sheet` step per sheet with ranges, number formats, dropdown and highlight ranges, QA, publish), `workbook.xlsx` sha256, AI proposal metadata for `brief` |
| `job-*/ir.json` | the exact `workbook-ir.v1` the engine rendered (accepted jobs) |
| `job-*/MANUAL-STEPS.md` | Microsoft Excel 365 steps: create and rename every sheet in order, title and header styling (bold, `#D9EAF7` fill, borders), each data row (text Excel would reinterpret is typed with a leading `'`), wrap text, the exact custom number format per column, column widths, Freeze Top Row, dropdown lists, conditional-format rules with hex fills, every named range (`='Detail'!$B$2:$B$3`), then every formula cell with its exact text (typed cell by cell, never drag-filled) and the engine's cached value to expect; Google Sheets / LibreOffice Calc menu equivalents |
| `history.jsonl` | one metadata line per job |

Traces and history hold hashes, counts, sheet names, and ranges only — never
cell values, labels, the title, or the raw idempotency key. The manual guide
holds the values a person types, like the workbook itself.

```bash
python -m book_production_platform.production_trace history --root .runtime/spreadsheet/output
python -m book_production_platform.production_trace manual  --root .runtime/spreadsheet/output <job_id>
python -m book_production_platform.production_trace verify  --root .runtime/spreadsheet/output <job_id>
```

New request fields in this pass (all optional; requests without them keep the
same idempotency hash): `columns[].number_format` (allowlisted Excel format
codes such as `#,##0.00`, `"฿"#,##0.00`, `0%`, `dd/mm/yyyy`),
`columns[].allowed_values` (dropdown list on a text column), and
`conditional_formats[]` (`table`, `column`, `operator`, `value`, `value2` for
`between`, `fill_color`). Reopen QA checks every dropdown and highlight range.
Feature parity and roadmap: [`docs/parity/spreadsheet.md`](docs/parity/spreadsheet.md).

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

### AI brief adapter (V1B)

`data pipeline propose` turns a question about one table into a
`pipeline-request.v1` and then builds it through the normal job. The model
sees only the table schema and at most three truncated sample rows. It may
only propose ordered steps: `source`, `select`, `filter` (equality, null, or
`operator` `gt`/`gte`/`lt`/`lte`/`ne`), `distinct`, `aggregate`, `sort`,
and `limit`. The spec also registers `join`, but it is rejected
(`join_not_supported`) because the V1A engine has a single source. SQL text, a
SQL-shaped field, or any write operation rejects the proposal. Tables and
columns must exist, and filter literals and limits must appear in the
question. Anything unclear returns `needs_input`. The existing validator,
compiler, and in-memory `EXPLAIN` must accept the result.

The adapter fails closed (`provider_not_configured`) without an owner-approved
`data-pipeline-provider-config.v1`. It goes only through the Central AI
Gateway, uses at most two calls per job, and makes zero calls on
instruction-like or SQL-injection questions (`DROP TABLE`, `OR '1'='1`,
`UNION SELECT`, and similar). `propose` refuses configs whose `mode` is not
`live` (`shadow_mode_only`). The shadow run compiles each complete proposal
and executes it read-only against a small synthetic in-memory SQLite table,
then compares the rows with the case's reference pipeline. This runs only
during evaluation; production stays plan-only. The gate uses held-out cases:
result match ≥ 95%, `needs_input` recall 100%, 0 silent adversarial passes,
0 SQL and 0 write statements from the model, structured cases with 0 calls,
and 0 canary leaks:

```powershell
book-production data pipeline brief-shadow `
  --provider-config .\.runtime\data-pipeline-provider.json `
  --cases .\tests\data_pipeline\fixtures\eval_dpl_question40_cases.json `
  --datasets .\tests\data_pipeline\fixtures\eval_dpl_question40_datasets.json `
  --split .\tests\data_pipeline\fixtures\eval_dpl_question40_split.json `
  --output .\.runtime\data-pipeline\shadow-report.json
```

EVAL-DPL-question40 is synthetic and split 14 / 14 / 6 / 6 (structured /
clear / ambiguous / adversarial), half Thai and half English.

### Job history, trace & manual steps

Every fresh `data pipeline build` / `propose` job writes a trace; exact replays
write nothing. Accepted jobs keep their four plan artifacts plus three trace
sidecars in `jobs/<fingerprint>/` (the receipt does not cover the sidecars, and
replay ignores exactly those three names — any other extra file still fails
closed). Rejected jobs are traced under `traces/<id>/` (not when the output
root itself is unsafe).

| File | What |
|---|---|
| `jobs/<fingerprint>/trace.json` | request hash, source column / node counts, one `node` step per operation in compile order (op, columns, operator, direction, limit, group-by, metric ops — never filter values), compile (SQL sha256, parameter types), `EXPLAIN` plan size, QA, artifact sha256, AI proposal metadata for `propose` |
| `jobs/<fingerprint>/ir.json` | the exact `pipeline-ir.v1` (contains filter values; 0600) |
| `jobs/<fingerprint>/MANUAL-STEPS.md` | Excel Power Query steps: Get Data, set each column type, then one Applied Step per node renamed to its `node_id` (Filter Rows with the exact operator and value, Choose Columns, Remove Duplicates, Group By ▸ Advanced, Sort with the compiler's tie-breakers, Keep Top Rows, Append Queries; branches become Reference queries), each with its M formula; then the same `SELECT` for DB Browser for SQLite (values inlined) and DBeaver (bind parameters in order); checks against the output columns and the `pipeline.sql` sha256. Contains filter values (0600) |
| `history.jsonl` | one metadata line per job |

New nodes in this pass: `filter_compare` (`column`, `operator` `gt`/`gte`/`lt`/
`lte`/`ne`, `value`; ordered comparisons on integer/decimal/date) and
`distinct`. Requests without `operator` keep their fingerprint.

Opt-in execution with CSV export (production against real sources stays
plan-only; this runs the same compiled `SELECT` read-only in memory over rows
you already hold):

```bash
book-production data pipeline run --input pipeline-request.json --rows rows.json --output-root .runtime/data-pipeline
```

It publishes the plan job first, then writes `results/<run_id>/results.csv`,
`execution-manifest.json` (row count, columns, CSV and values sha256), its own
`trace.json`, `ir.json`, and a `MANUAL-STEPS.md` whose checks include the row
count, numeric column totals, and the first row. CSV text starting with `=`,
`+`, `-`, `@` is prefixed with `'`. Results contain data: keep the output root
private. CLI output is metadata only.

```bash
python -m book_production_platform.production_trace history --root .runtime/data-pipeline
python -m book_production_platform.production_trace manual  --root .runtime/data-pipeline <job_id>
python -m book_production_platform.production_trace verify  --root .runtime/data-pipeline <job_id>
```

Feature parity and roadmap: [`docs/parity/data-pipeline.md`](docs/parity/data-pipeline.md).

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

### Job history, trace & manual steps

Every `cad build` run with a valid PartIR and a safe output root records a
`production-trace.v1` under `<output-root>/.trace/` - accepted, replayed, and
rejected runs alike (invalid PartIR still writes nothing). The content-addressed
`<fingerprint>/` directory keeps exactly its five artifacts; the trace lives
beside it:

| File | What |
|---|---|
| `.trace/history.jsonl` | one metadata line per run (`job_id`, status, artifacts, `ai_used`) |
| `.trace/jobs/<job_id>/trace.json` | input hashes, steps (validate, runtime, fingerprint, worker build summary with sketch constraints / Pad length / Pocket Through all, QA volume+bounds+bores, publish, drawing), artifact sha256 |
| `.trace/jobs/<job_id>/ir.json` | the canonical PartIR the worker executed |
| `.trace/jobs/<job_id>/MANUAL-STEPS.md` | click-level FreeCAD 1.0 Part Design recipe (Body, PlateSketch with 12 constraints, Pad, HoleSketch at Z = thickness with 4 circles, Pocket Through all, STEP/STL export) with checks against the analytical volume, bounding box, hole centers, webs, and the measured QA values; Fusion 360 / Onshape / SolidWorks equivalents |
| `.trace/jobs/<job_id>/drawing.svg`, `drawing.dxf` | dimensioned 1:1 shop drawing (top + side view, hole table, title block); DXF R12 in mm with layers OUTLINE/HOLES/CENTER/DIMENSIONS/TEXT |

`trace_job_id` is part of the JSON result. Look back at any run:

```bash
python -m book_production_platform.production_trace history --root .runtime/cad/jobs/.trace
python -m book_production_platform.production_trace manual  --root .runtime/cad/jobs/.trace <job_id>
python -m book_production_platform.production_trace verify  --root .runtime/cad/jobs/.trace <job_id>
```

`cad brief` also writes `<output>.proposal.json` (adapter, mode, model-call
count, provided dimensions, canonical PartIR hash - never the brief). `cad
build` records that as the trace's AI proposal only when the hash matches the
PartIR it builds; an edited PartIR is recorded as `proposal_edited_by_human`.
Tracing never changes the build result. The FreeCAD GUI steps are derived from
the worker macro and not runtime-verified in CI. Feature parity and the
roadmap: [`docs/parity/cad.md`](docs/parity/cad.md).

### AI brief adapter (V1B)

`cad_production/provider.py` turns a free-text brief into a `part-ir.v1`
proposal through the Central AI Gateway with `brief_adapter_kit`
(`brief-adapter.v1`). The model may only fill parameters of the existing
`mounting-plate.v1` / `four-corner.v1` family, copying each dimension's value
and unit (`mm`, `cm`, `m`, `in`) exactly as written. It never converts units
or computes geometry; the engine converts with exact decimals (1 in = 25.4 mm)
and the existing `parse_part_ir` range and minimum-web checks decide whether
the part is accepted.

The adapter never guesses a dimension. `cad_production/brief_grounding.py`
reads the brief clause by clause. A dimension counts as stated only if its
role (width, height/length, thickness, diameter, offset x/y, or "from each
edge" for both offsets) is paired with exactly one value that has a unit and
no hedging ("about", "or", "ประมาณ", "หรือ"). A missing, unit-less, hedged, or
conflicting dimension (`120 mm (5 in)`) returns `needs_input` without a model
call. A model value that differs from the brief, or was converted by the
model, becomes `missing`. Instruction-like briefs return `needs_input` with
zero calls. Templates, hole patterns, parameters, or fields outside the
allowlist are `rejected`, and so is a hole count other than four.

The configuration `cad-provider-config.v1` starts in `mode: shadow`. Without a
configuration, the commands fail with `provider_not_configured`, and
`cad brief` refuses a shadow configuration with `shadow_mode_only`. A complete
proposal is written to a new PartIR file for the normal `cad build`; the brief
command never builds:

```powershell
book-production cad brief-shadow `
  --provider-config .\.runtime\cad-provider.json `
  --cases .\tests\fixtures\cad_brief\eval-cad-brief30.json `
  --split .\tests\fixtures\cad_brief\eval-split.json `
  --output .\.runtime\cad\brief-shadow.json
book-production cad brief --input .\brief.json --output .\.runtime\cad\part-ir.json `
  --job-id job-001 --provider-config .\.runtime\cad-provider.json
```

The shadow gate (AC-CAD-01a) runs on held-out cases and requires all of the
following:

- clear-brief exact match of at least 95%
- `needs_input` recall of 100%
- zero silent adversarial passes
- **zero guessed dimensions**
- the geometry invariants pass on every accepted PartIR
- structured cases make 0 calls
- at most 2 calls per job
- zero canary leaks in the report or ledger

FreeCAD is not installed on the shadow host, so the report records
`gate.pending: freecad_build_all_accepted` and `gate.live_eligible: false`.

EVAL-CAD-brief30 has 10 structured, 10 clear, 5 ambiguous, and 5 adversarial
cases (injection, a 5000 mm width, a six-hole request, and mixed units). Each
5-case category splits Thai/English 3/2.

## Audio Master V1A

Audio Master converts one local PCM WAV and a versioned `audio-ir.v1` request
into an integrity-checked 24-bit/48 kHz WAV master plus `qa-manifest.json`.
The request selects one of seven fixed presets: `podcast-mono.v1`,
`podcast-stereo.v1`, `music-stereo.v1`, `apple-music-stereo.v1`,
`broadcast-r128-stereo.v1`, `audiobook-acx-mono.v1`, or `voice-clean-mono.v1`
(see the preset table in [`docs/parity/audio.md`](docs/parity/audio.md)). The adapter owns the FFmpeg filter
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

### Job history, trace & manual steps

Every fresh `audio master` run writes a `production-trace.v1` and appends
`<output-root>/history.jsonl`. Accepted masters keep their trace next to the
artifacts; rejected runs (invalid AudioIR, unsafe source path, channel
mismatch, silent input, QA failure, FFmpeg errors) are traced under
`<output-root>/failed/<job_id>/`. An unsafe output root (symlink or not a
directory) is never written, and exact replays write nothing.

| File | What |
|---|---|
| `<fingerprint>/trace.json` | AudioIR hash, source WAV name/size/sha256, steps (parse, inspect source, FFmpeg version, measure with the exact filter graph and measured LUFS/TP/LRA, render graph with `measured_*` + `linear=true`, QA checks, publish), artifact sha256 of `master.wav` and `qa-manifest.json` |
| `<fingerprint>/ir.json` | the AudioIR the engine executed |
| `<fingerprint>/MANUAL-STEPS.md` | Audacity 3.7 steps in engine order: import, clean-up chain (High-Pass Filter, Noise Gate; de-esser via Audition/RX), Amplify by the exact linear gain (target − measured), Limiter at the true-peak ceiling, WAV Signed 24-bit / 48 kHz export with metadata cleared; checks against the measured LUFS and true peak of `master.wav` |

```bash
python -m book_production_platform.production_trace history --root .runtime/audio-master-demo/jobs
python -m book_production_platform.production_trace manual  --root .runtime/audio-master-demo/jobs <job_id>
python -m book_production_platform.production_trace verify  --root .runtime/audio-master-demo/jobs <job_id>
```

Where Audacity has no exact equivalent (FFmpeg `deesser`; `agate` is an
expander while Audacity's Noise Gate is a gate; Audacity's Limiter is
sample-peak), the guide says so. Presets without a clean-up chain keep their
earlier fingerprints. The new presets are verified with a scripted FFmpeg in
tests; real FFmpeg runs and listening review are still pending. Feature parity
and roadmap: [`docs/parity/audio.md`](docs/parity/audio.md).

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

### Job history, trace & manual steps

`game build` and `game export` record a `production-trace.v1` for every run
with valid input under `<output-root>/.trace/` (accepted, replayed, failed;
errors re-raise unchanged). Published fingerprint directories keep exactly
their verified entries.

- `trace.json`: input hashes, steps (validate, assets, runtime/template hashes,
  fingerprint, stage, Unity compile/EditMode/PlayMode/reopen with test totals,
  gameplay-trace QA, publish, analyze) and sha256 of the scene, gameplay
  trace, QA manifest, and receipt (export: manifest, receipt, entrypoint).
- `ir.json`: canonical GameIR; `level-metrics.json`: shortest collect-all
  route (exact Held-Karp up to 12 objectives, nearest-neighbour above),
  grid steps, estimated seconds (`steps x tile / speed`), reachable cells,
  dead ends, junctions, and a heuristic difficulty band;
  `level-map.svg`: top-down map with the route.
- `MANUAL-STEPS.md`: Unity 6 (6000.6.2f1) Editor recipe mirroring
  `SceneCompiler.cs` - camera size, boundaries, optional Grid (cell size =
  tile) as a snapping aid, and every entity's GameObject name, cell, and exact
  Transform position, components and values, then the Play mode check in
  route order. Export gets a Build Profiles guide.

`game brief` writes a `<output>.proposal.json` sidecar that `game build`
records as AI provenance only when its GameIR hash matches. Unity steps are
not runtime-verified in CI. Parity and roadmap:
[`docs/parity/game.md`](docs/parity/game.md).

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

### AI brief adapter (V1B)

`game_production/provider.py` turns a free-text brief into a `game-ir.v1`
layout proposal through the Central AI Gateway with `brief_adapter_kit`
(`brief-adapter.v1`). The model returns only the grid size and a list of
entities. Each entity has one of the registered types `player` (exactly one),
`exit` (exactly one), `collectible`, or `wall`, on a single cell. The engine
assigns IDs, speed, tile size, and palette. An unknown entity type or field is
rejected (`unknown_entity_type`, `unknown_proposal_field`).

The brief must state the grid (`W x H`) and the number of collectibles. A
missing, hedged, or conflicting value returns `needs_input` without a model
call. A layout that contradicts the brief (grid, count, or a stated
`(x, y)` cell) is rejected.

Every layout must pass a deterministic reachability check. Every collectible
and the exit must be reachable from the player with four-way moves that avoid
walls. An unreachable layout is always `rejected` with `unreachable_goal`,
never accepted. The existing `validate_game_ir` then has the final say.

Some briefs are rejected before any spend:

- requests for enemies, lava, or traps (`unsupported_entity_requested`)
- an exit sealed off by walls (`unreachable_layout_requested`)
- too many collectibles for the grid (`layout_impossible`)
- a grid outside 3–64 (`grid_out_of_bounds`)

Instruction-like briefs return `needs_input` with zero calls.

The configuration `game-provider-config.v1` starts in `mode: shadow`. Without a
configuration, the commands fail with `provider_not_configured`, and
`game brief` refuses a shadow configuration with `shadow_mode_only`:

```powershell
book-production game brief-shadow `
  --provider-config .\.runtime\game-provider.json `
  --cases .\tests\fixtures\game_brief\eval-gam-brief30.json `
  --split .\tests\fixtures\game_brief\eval-split.json `
  --output .\.runtime\game\brief-shadow.json
book-production game brief --input .\brief.json --output .\.runtime\game\room.json `
  --job-id job-001 --provider-config .\.runtime\game-provider.json
```

The shadow gate (AC-GAM-01a) runs on held-out cases and requires all of the
following:

- clear-brief exact match of at least 95%
- `needs_input` recall of 100%
- zero silent adversarial passes
- **zero unreachable layouts passed silently**, re-checked by an independent
  BFS
- the validator passes on every accepted layout
- structured cases make 0 calls
- at most 2 calls per job
- zero canary leaks

Unity is not installed on the shadow host, so the REQ-GAM-02 batch test is
recorded as `gate.pending: unity_batch_test_all_accepted` with
`gate.live_eligible: false`.

EVAL-GAM-brief30 has 10 structured, 10 clear, 5 ambiguous, and 5 adversarial
cases. Each 5-case category splits Thai/English 3/2.

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
  quantity หรือ code compliance; ราคามีแค่การคูณตารางราคาที่เจ้าของให้มา
  (`bim cost`) ไม่มีฐานข้อมูลราคา
- ปริมาณที่ขาด คลุมเครือ หรือแปลงหน่วยไม่ได้ จะถูกรายงานเป็น coverage gap
  ไม่เติมค่าศูนย์หรือค่าประมาณ

### การใช้งาน

```bash
book-production bim takeoff   --ir examples/bim/bim-takeoff.json   --ifc path/to/model.ifc   --output-root .runtime/bim/jobs   --freecadcmd path/to/FreeCADCmd.exe   --ifc-site-packages path/to/site-packages   --timeout-s 120
```

สถานะและ exit code: `complete`/`replayed` → 0, `partial` → 3,
`needs_input` → 4, กรณีอื่น (rejected) → 2 พร้อม JSON error code ที่นิ่ง

### Job history, trace & manual steps (ประวัติงาน, trace และขั้นตอนทำเอง)

ทุกงาน `bim takeoff` ที่ IR ถูกต้องจะบันทึก `production-trace.v1` ไว้ที่
`<output-root>/.trace/` (complete/partial → `accepted`, `replayed`,
`needs_input`, และงานที่ล้มเหลวหรือ raise error ก็บันทึกแล้ว raise ต่อ
โดยไม่เปลี่ยน error code) ส่วน `jobs/<fingerprint>/` ยังมีแค่ 3 ไฟล์เดิม

- `trace.json`: hash ของ IR และ IFC (ไม่เก็บชื่อไฟล์/path), ขั้นตอน validate,
  fingerprint, runtime, worker (class ที่ขอ, schema, bound), extract, report,
  qa, publish และ sha256 ของ artifact ทุกไฟล์
- `MANUAL-STEPS.md`: วิธีทำเองใน Bonsai (BlenderBIM) + LibreOffice Calc —
  เลือกตาม IFC class (รวม subtype), อ่าน Qto ของ occurrence ทีละชิ้น,
  ทุกแถวและทุกยอดรวมที่เครื่องมือเผยแพร่, Pivot Table ตามคีย์เดียวกัน,
  บันทึก CSV และรายการ coverage gap; มีทางเลือก FreeCAD BIM / IfcOpenShell /
  Revit / Solibri (ชื่อเมนูอ้างอิงเวอร์ชัน ไม่ได้ทดสอบกับโปรแกรมจริงใน CI)

```bash
python -m book_production_platform.production_trace verify --root .runtime/bim/jobs/.trace <job_id>
```

### ราคาจากตารางราคา (`bim cost`)

`bim cost` คูณยอดรวมใน `takeoff.json` กับตารางราคา `bim-cost-rates.v1`
(ดู `examples/bim/cost-rates.example.json` — ราคาตัวอย่าง ไม่ใช่ราคาจริง)
โดยจับคู่คีย์ `ifc_class/qto_name/quantity_name/unit` (+ `method` ถ้าระบุ)
แบบตรงตัว, ปัดเงินทีละบรรทัด 0.01 แบบ ROUND_HALF_UP, ยอดที่ไม่มีราคาอยู่ใน
`unpriced` (ไม่คิดเป็นศูนย์), ราคาที่จับคู่ได้มากกว่าหนึ่งจะล้มเหลว
`cost_rates_ambiguous` ผลลัพธ์ `cost-estimate.json/.csv` พร้อม trace และคู่มือ
สูตร Calc (`=ROUND(G2*H2;2)`) exit 0 = complete, 3 = partial

```bash
book-production bim cost --report .runtime/bim/jobs/jobs/<fingerprint>/takeoff.json --rates rates.json --output-dir .runtime/bim/cost
```

รองรับ IFC class 18 ชนิด (มีทั้งใน IFC2X3 และ IFC4 และไม่มีตัวใดเป็น
subtype ของอีกตัว): IfcWall, IfcSlab, IfcColumn, IfcBeam, IfcDoor, IfcWindow,
IfcBuildingElementProxy, IfcCovering, IfcCurtainWall, IfcFooting, IfcMember,
IfcPile, IfcPlate, IfcRailing, IfcRamp, IfcRoof, IfcSpace, IfcStair
ระวัง IfcRoof/IfcStair อาจมีชิ้นย่อย (IfcSlab, IfcStairFlight) ที่มี Qto ของตัวเอง
ถ้าเลือกทั้งคู่และตั้งราคาทั้งคู่จะนับซ้ำได้ ดู [`docs/parity/bim.md`](docs/parity/bim.md)

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
