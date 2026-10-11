---
title: AI Post-Production Engine — V1 Design
aliases: [AI Video Post-Production, Video Editing Engine, AI Director]
tags: [design, video, ai, ffmpeg, edl, post-production]
created: 2026-09-22
updated: 2026-09-22
status: approved-conversational-design
description: Design for a local-first AI post-production engine with a thin control UI and an extensible EDL contract.
---

# AI Post-Production Engine — V1 Design

## 1. Decision summary

สร้างระบบนี้เป็น **AI Post-Production Engine** ไม่ใช่ UI clone ของ OpusClip หรือ
Premiere ระบบจะรับ raw footage และคำสั่งของผู้ใช้ แล้วค่อย ๆ สร้างผลลัพธ์ผ่าน
แผนที่ตรวจสอบได้ (Edit Decision List หรือ EDL) โดยให้ AI ทำหน้าที่
`understand → reason → decide` และให้ deterministic/open-source engines ทำหน้าที่
`process → render`.

แนวทางหลักคือ local-first และ provider-optional:

- FFmpeg/FFprobe เป็น video engine หลักและไม่มี paid rendering API เป็น dependency
  ของระบบ
- faster-whisper เป็น transcription provider เริ่มต้น
- OpenAI-compatible transcription/LLM providers เป็น adapter ที่เลือกเปิดใช้ได้
- AI Director ไม่ได้รับอนุญาตให้เขียนไฟล์วิดีโอหรือประกอบคำสั่ง shell เอง
- ทุกการตัด, crop, caption, audio, color และ asset placement ต้องปรากฏใน EDL
  และย้อนกลับไปยัง source range ได้
- UI เป็น control surface สำหรับ ingest, ตรวจ progress, อ่าน transcript,
  ตรวจแผน และ review output; ไม่สร้าง full nonlinear editor ใน V1

การทำงานจะเริ่มด้วย V1A ที่พิสูจน์ ingest และ transcript จากนั้นจึงทำ V1B ที่
เพิ่ม deterministic cut, captions, 9:16 reframe และ MP4 render. ความสามารถของ
audio post, color grade, asset search, graphic generation และ AI Director เต็มรูปแบบ
จะเพิ่มตามลำดับโดยไม่เปลี่ยนแกน EDL.

## 2. Intent, users, and success criteria

### Intent

เจ้าของระบบต้องการส่งวิดีโอยาวเข้าเครื่องหรือ self-hosted runtime แล้วได้ผลลัพธ์
first cut ที่ inspectable และแก้ไขต่อได้ โดยมีต้นทุน AI เฉพาะงานที่ต้องใช้
ความเข้าใจเนื้อหา และใช้ local compute สำหรับงาน media processing ที่ทำซ้ำได้.

### Primary user

ผู้ใช้คนเดียวใน local development หรือ self-hosted environment ซึ่งต้องการผลิต
short-form และ post-production draft จาก raw footage โดยไม่ผูกกับ subscription
ของ video editor รายหนึ่ง.

### V1 success criteria

ระบบถือว่า V1 สำเร็จเมื่อผู้ใช้สามารถ:

1. สร้าง project และ upload ไฟล์วิดีโอที่ผ่านการตรวจชนิด/ขนาด
2. เห็น job ID และสถานะของแต่ละ processing stage โดย request upload ไม่ค้างรอ
3. อ่าน metadata ที่มาจาก FFprobe เช่น duration, dimensions, fps, codecs และ audio
4. ได้ normalized transcript ที่มี segment timestamps และ word timestamps เมื่อ
   provider รองรับ
5. เปิดดู transcript และ source video จาก Project Editor ได้
6. ตรวจ EDL ตั้งต้นที่ผูกกับ source asset ได้ โดยไม่มีการแก้ source เดิม
7. retry เฉพาะ stage ที่ล้มเหลวได้ และเห็น error ที่ปลอดภัยต่อการเปิดเผย
8. สลับ transcription provider ผ่าน configuration ได้โดยไม่แก้ downstream code

V1A ไม่อ้างว่าทำ automatic highlight selection หรือ AI Director ได้แล้ว. V1B
จึงค่อยเพิ่มการ render จาก source ranges ที่เลือกได้และ caption/reframe ที่
deterministic. AI clip discovery เป็น Phase 2 ของระบบ.

## 3. Scope and non-goals

### In scope for the approved implementation sequence

#### V1A — ingest and transcript

- project creation และ project listing แบบ local single-user
- immutable original video storage บน local filesystem
- file validation, content hash และ metadata extraction ด้วย FFprobe
- asynchronous job lifecycle ผ่าน Redis และ worker process
- transcription provider interface
- faster-whisper local provider
- OpenAI-compatible transcription provider
- normalized transcript persistence
- initial EDL v1 skeleton
- Project Editor ที่แสดง source metadata, job progress และ transcript
- structured logs, retryable stages และ safe error responses
- Docker Compose สำหรับ PostgreSQL, Redis, API, worker และ web ใน development

#### V1B — deterministic first cut

- explicit source-range selection จาก transcript/editor controls
- FFmpeg cut และ output validation ด้วย FFprobe
- SRT/ASS caption generation จาก normalized transcript
- 9:16 crop/reframe แบบ center crop เป็น baseline
- RenderJob และ RenderOutput lifecycle
- preview/download ของ output ที่ render สำเร็จ

### Deliberately deferred

- LLM highlight ranking, hook generation และ natural-language-to-EDL
- PySceneDetect scene analysis
- active-speaker detection และ dynamic face tracking
- silence/filler removal
- audio denoise, dereverb, EQ, compression, ducking, music และ SFX
- exposure correction, white balance, shot matching และ creative grade
- B-roll search, asset embeddings, stock providers และ license automation
- diagram/graphic generation
- image/video generation
- publishing integrations
- multi-user authentication, billing และ hosted multi-tenant operation
- full Premiere-style timeline

การ deferred ไม่ได้ทำให้ EDL ล็อกอยู่กับ clip cutter. EDL v1 ต้องรองรับ field
และ discriminated types สำหรับความสามารถเหล่านี้ตั้งแต่ต้น แต่ renderer ต้อง
ประกาศ capability และ fail explicitly เมื่อได้รับ operation ที่ยังไม่รองรับ.

## 4. Repository boundary and runtime shape

การทำ V1 เป็น extraction pilot ใน Solo Empire ตาม boundary ที่ repository นี้ใช้
อยู่แล้ว:

```text
apps/video-postproduction-web/
  Next.js control surface

services/video-postproduction-api/
  FastAPI application
  SQLAlchemy models and repositories
  provider interfaces and local implementations
  worker entrypoint (same codebase, separate process)
  tests and contract fixtures

packages/video-postproduction-edl/
  versioned EDL JSON Schema
  schema examples and compatibility notes

services/video-postproduction/
  Docker Compose and local runtime documentation
```

API และ worker ใช้ package/domain code ชุดเดียวกันเพื่อหลีกเลี่ยงการ duplicate
model และ contract แต่รันเป็นคนละ process:

- `web`: รับคำสั่งจากผู้ใช้และอ่าน versioned REST contract
- `api`: validate request, persist state, enqueue jobs และ stream files ที่อนุญาต
- `worker`: consume jobs, เรียก media/provider engines และบันทึก stage events
- `postgres`: durable operational state
- `redis`: queue และ transient progress signalling
- `local storage`: original/intermediate/transcript/render artifacts ผ่าน
  `StorageProvider`

ไม่ให้ browser อ่าน SQLite หรือ Markdown ของ Solo Empire โดยตรง และไม่ให้
video engine เขียนข้อมูลลง control-plane database ของ Solo Empire. หาก extraction
กลายเป็น product แยกในอนาคต สามารถย้ายทั้งชุดตาม contract นี้ได้.

## 5. Layered architecture

### 5.1 Ingest and storage layer

`StorageProvider` เป็น interface ที่มี operation อย่างน้อย:

```text
put_atomic(temp_path, storage_key) -> StoredObject
open(storage_key) -> binary stream
local_path(storage_key) -> Path
delete(storage_key) -> None
exists(storage_key) -> bool
```

V1 implement เฉพาะ `LocalFilesystemStorage`. Storage key สร้างจาก generated
project/asset IDs และ artifact kind; ห้ามใช้ชื่อไฟล์จากผู้ใช้เป็น path โดยตรง.
ไฟล์ original จะถูกเขียนเป็น temp file, hash/validate, แล้ว atomic rename เข้า
พื้นที่ที่กำหนด. Original ไม่ถูก overwrite.

### 5.2 Media analysis layer

`FFprobeMetadataReader` เรียก executable ด้วย argument array ที่สร้างจาก typed
values เท่านั้น ไม่ใช้ shell string. Output JSON ของ FFprobe จะถูก parse ผ่าน
Pydantic model ที่เลือก field ที่ต้องใช้ และเก็บ raw diagnostic แยกจาก public API.

V1 metadata ได้แก่:

- duration และ time base
- width, height, aspect ratio และ fps
- video/audio codec
- sample rate และ channel count เมื่อมี audio
- container format และ file size

### 5.3 Transcription layer

Downstream code ใช้ contract เดียว:

```python
class TranscriptionProvider(Protocol):
    def transcribe(
        self,
        audio_path: Path,
        *,
        language: str | None,
        word_timestamps: bool,
    ) -> NormalizedTranscript: ...
```

Provider implementations:

- `FasterWhisperProvider`: local model, CPU fallback และ optional GPU config
- `OpenAICompatibleTranscriptionProvider`: configurable base URL, model และ
  API key จาก environment; ไม่เก็บ key ใน database, logs หรือ artifact metadata

Normalized format:

```json
{
  "language": "th",
  "duration": 14.72,
  "segments": [
    {
      "id": "segment-0001",
      "start": 10.25,
      "end": 14.72,
      "text": "ตัวอย่างข้อความ",
      "words": [
        {"start": 10.25, "end": 10.64, "text": "ตัวอย่าง", "confidence": 0.98}
      ]
    }
  ]
}
```

ทุก timestamp ต้องเป็น finite number, `0 <= start < end <= duration` เมื่อ
duration พร้อมใช้งาน และต้องรักษาลำดับของ segments/words. Provider ที่ไม่มี
word timestamps คืน `words: []` และระบุ capability ใน metadata แทนการสร้างเวลา
ปลอม.

### 5.4 Director and plan layer

V1A ไม่ต้องเรียก LLM เพื่อให้ระบบพื้นฐานรันได้. `LLMProvider` จะถูกประกาศเป็น
future boundary ตั้งแต่แรก โดยรับ transcript, media metadata, analysis signals
และ user instruction แล้วคืน `PostProductionPlan` ที่ validate ด้วย Pydantic และ
แปลงเป็น EDL.

ข้อบังคับของ boundary:

- LLM ห้ามคืน shell command, filter graph ดิบ หรือ file path ที่ให้ execute ได้
- LLM ไม่ได้รับ full video โดย default; ส่งเฉพาะ transcript/metadata/signals
- invalid structured output ต้อง retry ด้วย correction instruction ที่จำกัดจำนวน
- retry และ provider/model ต้องบันทึกเป็น metadata ที่ไม่เปิดเผย secret
- plan ที่ validate แล้วเท่านั้นจึงถูกส่งต่อไป renderer

### 5.5 EDL layer

EDL เป็น source of truth ของ edit intent ไม่ใช่ LLM response ดิบ. JSON Schema
อยู่ที่ `packages/video-postproduction-edl/edl.schema.json` และ API ใช้ Pydantic
model ที่สอดคล้องกับ schema.

โครงสร้างเชิงแนวคิดของ EDL v1:

```json
{
  "version": 1,
  "project_id": "project_123",
  "source_assets": [
    {
      "asset_id": "asset_123",
      "storage_key": "projects/project_123/original/asset_123.mp4",
      "duration": 3600.0
    }
  ],
  "timeline": [
    {
      "id": "segment_0001",
      "source": {
        "asset_id": "asset_123",
        "start": 82.4,
        "end": 127.8
      },
      "visual": {
        "kind": "a_roll",
        "reframe": {"mode": "center", "aspect_ratio": "9:16"},
        "asset_refs": []
      },
      "captions": {
        "enabled": true,
        "source": "transcript",
        "format": "ass",
        "preset": "minimal"
      },
      "audio": {"operations": []},
      "color": {"operations": []},
      "provenance": {"reason": "explicit user-selected source range"}
    }
  ],
  "assets": [],
  "output": {"width": 1080, "height": 1920, "fps": 30, "format": "mp4"},
  "capabilities": {"required": ["cut", "captions", "center_reframe"]}
}
```

Schema design requirements:

- source ranges เป็น canonical timestamps และแก้ไขได้โดยไม่ทำลาย source
- visual kinds รองรับ `a_roll`, `stock`, `asset`, `graphic` และ
  `generated_video` โดยแต่ละ asset reference ต้องมี provenance
- audio รองรับ voice processing, music และ SFX operation types ในอนาคต
- color รองรับ correction และ grade operations แยกกัน
- captions แยกจาก transcription และเลือก preset/style ได้
- unsupported capability เป็น validation/render error ที่บอกชื่อ operation
- EDL มี version และ migration policy; breaking change ต้องเพิ่ม schema version

### 5.6 Render and artifact layer

`RenderEngine` รับ EDL ที่ validate แล้ว ไม่รับ user prompt โดยตรง. V1B จะมี
`FFmpegRenderEngine` ซึ่ง:

1. ตรวจ capability และ source ranges
2. สร้าง typed FFmpeg argv/filter graph จาก EDL
3. render ลง temp output
4. ตรวจ output ด้วย FFprobe และ duration/stream expectations
5. ย้าย output เข้า final storage แบบ atomic
6. บันทึก command-safe diagnostics, elapsed time และ output metadata

ห้ามสร้าง FFmpeg command จาก string ที่ผู้ใช้หรือ LLM ส่งมาโดยตรง.

## 6. Processing workflow and state model

### V1A stage flow

```text
create project
  → upload asset
  → validate + hash + store original
  → enqueue probe stage
  → persist metadata
  → enqueue transcribe stage
  → persist normalized transcript
  → create initial EDL
  → mark project ready_for_edit
```

Job และ stage statuses:

```text
queued → running → succeeded
queued → running → failed → retry_queued
queued/running → cancelled
```

แต่ละ stage มี `attempt`, `started_at`, `finished_at`, `error_code`, safe
`error_message`, `metadata` และ provider/model fingerprint. Stage จะ idempotent
ด้วย key ที่ประกอบจาก asset content hash, stage name, provider, model และ options.
การ retry ใช้ผลลัพธ์ที่สำเร็จแล้วซ้ำได้ และไม่เริ่ม stage ก่อนหน้าที่สำเร็จใหม่.

Cancellation ทำได้ที่ stage boundary และต้อง cleanup temp files ใน `finally`.
ถ้า process ถูกหยุดกลาง stage ให้ recovery command เปลี่ยน stale `running` jobs
เป็น retryable failure หลัง lease timeout.

### V1B stage flow

```text
validated EDL
  → generate caption artifact
  → render FFmpeg output
  → validate output with FFprobe
  → persist RenderOutput
  → expose preview/download
```

## 7. Database model

Production runtime ใช้ PostgreSQL ผ่าน SQLAlchemy และ Alembic migration. Unit
tests ใช้ isolated database ได้ แต่ contract และ transaction behavior ต้องตรงกับ
PostgreSQL.

V1 tables:

- `projects`: id, name, instructions, status, created/updated timestamps
- `media_assets`: id, project_id, storage_key, original_filename, media_type,
  size_bytes, sha256, metadata JSON, status, created_at
- `processing_jobs`: id, project_id, asset_id, kind, status, progress, error,
  idempotency_key, timestamps
- `processing_stages`: id, job_id, name, status, attempt, metadata JSON, timestamps
- `transcripts`: id, project_id, asset_id, provider, model, language, duration,
  normalized JSON, content_fingerprint, timestamps
- `transcript_segments`: id, transcript_id, ordinal, start, end, text, words JSON
- `edls`: id, project_id, source_asset_id, version, schema_version, document JSON,
  status, created/updated timestamps

V1B เพิ่ม:

- `render_outputs`: id, project_id, edl_id, job_id, storage_key, status,
  metadata JSON, error, timestamps

Future asset-library tables จะถูกเพิ่มเมื่อมี search/reuse flow จริง ไม่สร้าง
ตารางที่ยังไม่มี behavior เพื่อหลีกเลี่ยง schema ที่หลอกว่ารองรับความสามารถนั้น.

## 8. REST API contract

ทุก endpoint อยู่ใต้ `/api/v1` และ error response ใช้รูปแบบเดียว:

```json
{
  "error": {
    "code": "asset_type_not_supported",
    "message": "The uploaded file type is not supported.",
    "request_id": "req_123"
  }
}
```

V1A endpoints:

```text
GET    /healthz
GET    /api/v1/projects
POST   /api/v1/projects
GET    /api/v1/projects/{project_id}
POST   /api/v1/projects/{project_id}/assets
GET    /api/v1/projects/{project_id}/assets/{asset_id}
GET    /api/v1/projects/{project_id}/transcript
GET    /api/v1/jobs/{job_id}
POST   /api/v1/jobs/{job_id}/retry
GET    /api/v1/projects/{project_id}/edl
GET    /api/v1/projects/{project_id}/assets/{asset_id}/stream
```

Rules:

- upload response คืน `asset_id`, `job_id`, `status` และ `poll_url`
- API ไม่รอ transcription ให้เสร็จใน upload request
- pagination/limits ใช้กับ list endpoints
- streaming ต้อง resolve storage key ฝั่ง server และป้องกัน path traversal
- API เปิด local single-user mode เป็นค่าเริ่มต้น; token guard เปิดได้ผ่าน env
- ไม่มี endpoint ที่รับ arbitrary FFmpeg args, executable path หรือ shell command

V1B เพิ่ม endpoint สำหรับแก้ source ranges, caption options, render และ output
download หลังจาก EDL edit contract ผ่านการทดสอบ.

## 9. Web control surface

Frontend ใช้ Next.js App Router, TypeScript, Tailwind CSS และ shadcn/ui-style
components โดยเน้นความชัดเจนของ state มากกว่าการเลียนแบบ desktop editor.

V1A screens:

- **Projects**: รายการ project, processing state และปุ่มสร้างใหม่
- **New Project**: ชื่อ, instruction note และ upload dropzone พร้อม validation
- **Project Editor**: video metadata, job progress, transcript และ EDL summary

V1B เพิ่ม preview rendered output และ render controls. Render Queue และ Settings
จะเพิ่มเมื่อ job/provider configuration มี contract ที่เสถียร; ไม่สร้างหน้า
placeholder ที่อ้างว่าทำงานได้ทั้งที่ backend ยังไม่มี behavior.

Frontend อ่าน API ผ่าน versioned client ที่มี typed response และ polling backoff.
เมื่อ job failed ต้องแสดง stage และ safe error; เมื่อไม่มี transcript ต้องแสดง
state ที่ชัดเจน ไม่ใช่จอว่างหรือ fake sample data.

## 10. Cost, provider, and data policy

ค่าใช้จ่ายและ data boundary เป็น design constraint:

- local FFmpeg, FFprobe, captions, crop, reframe และ filesystem ไม่ต้องใช้ paid API
- faster-whisper เป็น default provider เมื่อเครื่องรองรับ
- OpenAI-compatible provider เปิดผ่าน environment/configuration เท่านั้น
- ไม่ upload full video ให้ LLM โดย default; LLM เห็น transcript/metadata/signals
- transcript cache key ใช้ source fingerprint + provider + model + language/options
- เปลี่ยน LLM ไม่ทำให้ต้อง transcribe ใหม่
- เปลี่ยน caption preset ไม่ทำให้ต้อง transcribe หรือ rerun analysis
- provider pricing/cost metadata เก็บแบบ optional และไม่ทำให้ pipeline ล้มถ้าไม่มีราคา
- API keys อ่านจาก environment/secret manager และไม่ถูกเก็บใน DB, logs, EDL หรือ
  object metadata
- original media และ derived artifacts มี provenance และลบตาม lifecycle policy

ไม่มี Shotstack, Creatomate หรือ paid video rendering service เป็น core dependency.
สามารถเพิ่มเป็น `RenderEngine` implementation ภายหลังได้ถ้าค่าใช้จ่ายและ
operational trade-off คุ้มกว่า self-hosted CPU/GPU.

## 11. Safety, reliability, and observability

- allowlist video MIME/container extensions และ validate ด้วย FFprobe หลัง upload
- จำกัด body/file size และจำนวน concurrent jobs ผ่าน configuration
- ใช้ generated IDs และ normalized storage keys; ห้าม trust filename/path จากผู้ใช้
- เรียก subprocess ด้วย argv, timeout, cancellation และ cleanup ที่ชัดเจน
- เก็บ original media แบบ immutable และเขียน derived artifact แบบ atomic
- structured logs ทุก stage: request/job/project/asset IDs, timing, status และ safe metadata
- ไม่ log transcript เต็ม, API key, cookies, raw provider payload หรือ private prompt
- metrics ขั้นต่ำ: job counts by status, stage duration, failure codes และ bytes processed
- retry เฉพาะ transient failures; validation/security errors ไม่ retry อัตโนมัติ
- worker ไม่กลืน stderr ของ FFmpeg/provider; บันทึก diagnostic ที่จำกัดขนาดและ scrub secret
- database transaction ครอบคลุม state transition และ artifact registration

## 12. Verification strategy

การทดสอบต้องแบ่งชั้นและไม่ประกาศว่า phase ผ่านจาก unit tests อย่างเดียว:

### Contract/unit tests

- EDL schema/Pydantic validation และ reject invalid ranges/capabilities
- transcript normalization, ordering, missing word timestamps และ boundary precision
- storage key/path safety และ atomic write behavior
- FFprobe argument construction และ safe parsing
- job/stage state transitions, idempotency key และ retry policy
- API request validation/error envelope

### Integration tests

- API + isolated PostgreSQL/Redis หรือ equivalent test runtime
- upload วิดีโอ fixture ขนาดเล็ก → asset/job records → worker stage persistence
- provider injection ใช้ deterministic test provider เพื่อทดสอบ orchestration โดยไม่
  ทำให้ production configuration มี mock provider
- fixture EDL → FFmpeg engine → output FFprobe validation ใน V1B

### Real-provider smoke tests

- opt-in local faster-whisper test เมื่อ model cache และ runtime พร้อม
- opt-in OpenAI-compatible provider test เมื่อผู้ใช้จัดเตรียม endpoint/key เอง
- external tests ต้อง redact credentials และไม่นำ private media ไปเก็บใน repository

### Browser/end-to-end acceptance

- start Compose stack
- สร้าง project ผ่าน UI
- upload small sample video
- observe progress ผ่าน polling
- open transcript and metadata
- verify failed stage can retry
- V1B: select range, render, preview/download MP4 และตรวจ stream/duration

ถ้า Docker, FFmpeg หรือ model runtime ไม่พร้อมใน environment ให้รายงานเป็น
unavailable check แยกจาก code failure และห้ามใช้ผล unit tests แทน real E2E.

## 13. Delivery sequence

1. **Design and contracts**: EDL schema, API error envelope, DB model, provider
   protocols และ Compose boundaries
2. **V1A backend**: storage, project/asset/job persistence, FFprobe, queue,
   transcription providers และ retryable stages
3. **V1A web**: Projects, New Project, Project Editor และ transcript state UI
4. **V1A verification**: unit/integration/E2E with a small sample video
5. **V1B render**: explicit source ranges, captions, center reframe, FFmpeg output
6. **V1B verification**: output validation, preview/download และ cancellation cleanup
7. **Phase 2**: candidate segment discovery, LLMProvider, structured plan retry,
   clip ranking และ EDL generation from natural-language instructions
8. **Later phases**: audio, color, asset library, visuals, generated media และ
   full AI Director ตามลำดับ โดยทุก phase ต้องมี contract และ E2E evidence

## 14. Risks and explicit trade-offs

| Risk | Decision |
| --- | --- |
| UI ดูเหมือน dashboard แต่ engine ยังไม่ทำงาน | ส่งมอบ V1A/V1B เป็น vertical slices ที่มี real worker/output evidence |
| faster-whisper ใช้ทรัพยากรสูง | local provider configurable และมี OpenAI-compatible adapter โดยไม่ผูก downstream |
| EDL ใหญ่เกินจน implement ไม่จบ | schema extensible แต่เปิดใช้ capability ทีละ phase |
| provider output มี timestamp ผิด | normalize + validate + reject/repair ที่ boundary ก่อน persist |
| FFmpeg security/quoting bug | typed argv/filter builder และห้าม arbitrary shell input |
| external API cost บาน | local-first, cache fingerprints, transcript-only LLM context |
| fork open-source app ล็อก architecture | reuse libraries/algorithms เป็นส่วนประกอบ ไม่ fork UI ที่ไม่ตรง contract |
| nested product โตจนควรแยก repo | รักษา versioned app/service/EDL boundaries เพื่อย้ายออกได้ภายหลัง |

## 15. Definition of done for the design

เอกสารนี้ถือว่าเป็น design ที่พร้อมส่งต่อเมื่อ:

- ขอบเขต V1A/V1B และ non-goals ระบุชัด
- EDL รองรับ future audio/color/assets โดยไม่อ้างว่า renderer รองรับแล้ว
- API, database, provider, storage และ worker boundaries ใช้ชื่อสอดคล้องกัน
- มี failure/retry/security/cost behavior ที่ตรวจสอบได้
- มี unit, integration, real-provider และ browser E2E acceptance paths
- ไม่มี requirement ที่บังคับใช้ paid video rendering API หรือส่ง raw video ให้ LLM

