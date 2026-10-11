# AI Post-Production Engine V1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** สร้าง V1 ของ AI Post-Production Engine ที่รับวิดีโอ, อ่าน metadata, ถอดเสียงแบบ asynchronous, แสดง transcript และ render first cut แบบ deterministic จาก EDL ที่ตรวจสอบได้

**Architecture:** ใช้ `apps/video-postproduction-web` เป็น thin control surface และ `services/video-postproduction-api` เป็น FastAPI codebase ที่รันทั้ง API และ worker คนละ process. `packages/video-postproduction-edl` เป็น JSON Schema กลาง; PostgreSQL เก็บ state, Redis จัดคิว, local filesystem เก็บ artifacts, และ FFmpeg/FFprobe ทำ media processing โดยไม่รับ shell string จากผู้ใช้หรือ LLM.

**Tech Stack:** Python 3.11+, FastAPI, Pydantic v2, SQLAlchemy 2, Alembic, PostgreSQL, Redis, Celery, FFmpeg/FFprobe, faster-whisper, httpx, Next.js 16, TypeScript, Tailwind CSS, shadcn/ui-style components, Vitest, Playwright.

**Spec:** `docs/superpowers/specs/2026-09-22-ai-post-production-engine-design.md`

## Global Constraints

- FFmpeg/FFprobe เป็น video engine หลักและไม่มี paid rendering API เป็น dependency ของระบบ
- local-first และ provider-optional; faster-whisper เป็น transcription provider เริ่มต้น
- AI Director ไม่ได้รับอนุญาตให้เขียนไฟล์วิดีโอหรือประกอบคำสั่ง shell เอง
- ทุกการตัด, crop, caption, audio, color และ asset placement ต้องปรากฏใน EDL และย้อนกลับไปยัง source range ได้
- API และ worker ใช้ codebase เดียวกันแต่รันเป็นคนละ process
- original media ต้อง immutable; derived artifacts ต้องเขียนแบบ atomic
- ห้าม upload full video ให้ LLM โดย default และห้ามเก็บ API keys ใน DB, logs, EDL หรือ object metadata
- ห้ามรับหรือ execute arbitrary FFmpeg args, executable path หรือ shell command
- V1A ไม่ทำ automatic highlight selection หรือ AI Director; V1B render เฉพาะ source ranges ที่เลือกได้
- unsupported EDL capability ต้อง fail explicitly และห้าม silently ignore
- ทุก task ต้องรักษาไฟล์งานค้างอื่นใน working tree และใช้ `git add -- <scoped paths>` เท่านั้น

## Review Focus

- ชื่อไฟล์หรือ storage key ที่พยายาม path traversal ต้องถูกปฏิเสธและไม่สามารถเขียนนอก storage root ได้ — pinned by Task 3 `test_storage_rejects_path_traversal`.
- transcript ที่มี timestamp ย้อนกลับ, เกิน duration หรือไม่มี word timestamps ต้องถูก normalize/reject อย่างชัดเจนโดยไม่สร้างเวลาเท็จ — pinned by Task 4 `test_normalizer_rejects_invalid_ranges` and `test_provider_preserves_missing_word_timestamps`.
- การ retry หรือการส่ง job ซ้ำต้องไม่สร้าง transcript/EDL ซ้ำหรือ render ซ้ำโดยไม่จำเป็น — pinned by Task 5 `test_pipeline_is_idempotent_for_same_fingerprint`.
- FFprobe/FFmpeg/provider failure ต้องทำ cleanup, เก็บ safe error และเปิด retry ได้โดยไม่กลืน diagnostic — pinned by Tasks 3, 5, and 9 failure-path tests.
- EDL ที่ร้องขอ capability ซึ่ง renderer ยังไม่รองรับต้องคืน capability error ไม่ใช่ผลิตไฟล์บางส่วน — pinned by Task 1 `test_edl_rejects_unsupported_required_capability` and Task 9 `test_renderer_fails_before_process_for_unknown_capability`.

## Planned file map

### Shared contract

- Create: `packages/video-postproduction-edl/edl.schema.json` — versioned JSON Schema for EDL v1.
- Create: `packages/video-postproduction-edl/examples/initial-edl.json` — smallest valid initial EDL.
- Create: `packages/video-postproduction-edl/README.md` — schema versioning and capability rules.
- Create: `packages/video-postproduction-edl/tests/test_schema.py` — contract tests.

### Backend and worker

- Create: `services/video-postproduction-api/pyproject.toml` — runtime/dev dependencies and tool configuration.
- Create: `services/video-postproduction-api/video_postproduction/` — application package, configuration, database, EDL models, storage, media, transcription, jobs, API routes, and rendering.
- Create: `services/video-postproduction-api/alembic.ini` and `services/video-postproduction-api/migrations/` — PostgreSQL migrations.
- Create: `services/video-postproduction-api/tests/` — unit, integration, and API tests.
- Create: `services/video-postproduction-api/.env.example` — non-secret configuration contract.
- Create: `services/video-postproduction-api/Dockerfile` — API/worker image.
- Create: `services/video-postproduction/tests/test_compose.py` — Compose structure check.

### Web control surface

- Create: `apps/video-postproduction-web/` — Next.js app, typed API client, UI components, pages, Vitest tests, and Playwright smoke test.

### Local runtime and documentation

- Create: `services/video-postproduction/docker-compose.yml` — PostgreSQL, Redis, API, worker, and web services.
- Create: `services/video-postproduction/README.md` — local startup, fixture test, and troubleshooting instructions.
- Modify: `apps/README.md` — register the new frontend boundary.
- Modify: `services/README.md` — register the new API/worker boundary.

## Interfaces locked by this plan

The following names and shapes are intentionally stable across tasks:

```python
class StorageProvider(Protocol):
    def put_atomic(self, temp_path: Path, storage_key: str) -> StoredObject: ...
    def open(self, storage_key: str) -> BinaryIO: ...
    def local_path(self, storage_key: str) -> Path: ...
    def exists(self, storage_key: str) -> bool: ...
    def delete(self, storage_key: str) -> None: ...

class FFprobeMetadataReader:
    def read(self, media_path: Path) -> MediaMetadata: ...

class TranscriptionProvider(Protocol):
    def transcribe(
        self,
        audio_path: Path,
        *,
        language: str | None,
        word_timestamps: bool,
    ) -> NormalizedTranscript: ...

class JobQueue(Protocol):
    def enqueue(self, job_id: UUID, stage_name: str) -> str: ...

class RenderEngine(Protocol):
    def render(self, edl: EDLDocument, *, output_key: str) -> RenderArtifact: ...
```

Every task below must preserve these signatures or update this section and all
downstream tasks before continuing.

---

### Task 1: EDL v1 contract and validation boundary

**Files:**
- Create: `packages/video-postproduction-edl/edl.schema.json`
- Create: `packages/video-postproduction-edl/examples/initial-edl.json`
- Create: `packages/video-postproduction-edl/README.md`
- Create: `packages/video-postproduction-edl/tests/test_schema.py`
- Create: `services/video-postproduction-api/video_postproduction/edl.py`
- Create: `services/video-postproduction-api/tests/test_edl_contract.py`
- Modify: `services/video-postproduction-api/pyproject.toml`

**Interfaces:**
- Consumes: no earlier implementation; use the approved design's EDL v1 example and capability list.
- Produces: `SourceRange`, `TimelineSegment`, `EDLDocument`, `EDLCapabilityError`, and `validate_edl(document: Mapping[str, Any]) -> EDLDocument` for database, API, and renderer tasks.

- [x] **Step 1: Write the failing schema tests**

Add tests that load `initial-edl.json` and assert that the following behavior
does not exist yet:

```python
def test_initial_edl_is_valid() -> None:
    assert validate_edl(load_initial_example()).version == 1


def test_edl_rejects_end_before_start() -> None:
    with pytest.raises(ValidationError):
        validate_edl(document_with_range(start=12.0, end=4.0))


def test_edl_rejects_unsupported_required_capability() -> None:
    with pytest.raises(EDLCapabilityError, match="active_speaker"):
        validate_edl(document_with_capabilities(["active_speaker"]))
```

- [x] **Step 2: Run the tests to verify the contract fails correctly**

Run:

```text
python -m pytest services/video-postproduction-api/tests/test_edl_contract.py packages/video-postproduction-edl/tests/test_schema.py -q
```

Expected: FAIL because the schema loader, Pydantic models, and validator do not
exist yet; there must be no import typo or fixture error.

- [x] **Step 3: Implement the minimal EDL model and JSON Schema**

Define the Pydantic v2 models with these exact concepts:

```python
class SourceRange(BaseModel):
    asset_id: UUID
    start: NonNegativeFloat
    end: PositiveFloat

    @model_validator(mode="after")
    def end_after_start(self) -> "SourceRange": ...


class EDLDocument(BaseModel):
    version: Literal[1]
    project_id: UUID
    source_assets: list[SourceAsset]
    timeline: list[TimelineSegment]
    assets: list[EDLAssetReference]
    output: OutputSettings
    capabilities: RequiredCapabilities
```

Keep the schema extensible for `a_roll`, `asset`, `stock`, `graphic`, and
`generated_video`, but permit only `cut`, `captions`, and `center_reframe` in
the V1 capability allowlist. Validate every source range against the known
asset duration when `validate_edl` receives the metadata map.

- [x] **Step 4: Run the focused contract tests to verify they pass**

Run the same pytest command. Expected: PASS, including JSON Schema validation,
Pydantic validation, finite/ordered timestamps, and explicit capability errors.

- [x] **Step 5: Add a schema synchronization test**

Export `EDLDocument.model_json_schema()` during the test and assert the required
top-level properties, `version: 1`, and the discriminated visual kinds match
`edl.schema.json`. This prevents API models from silently drifting from the
shared contract.

- [x] **Step 6: Commit the contract only**

```text
git add -- packages/video-postproduction-edl services/video-postproduction-api/pyproject.toml services/video-postproduction-api/video_postproduction/edl.py services/video-postproduction-api/tests/test_edl_contract.py
git commit -m "feat: add video post-production EDL v1 contract"
```

### Task 2: Backend foundation, configuration, and database migrations

**Files:**
- Modify: `services/video-postproduction-api/pyproject.toml`
- Create: `services/video-postproduction-api/video_postproduction/config.py`
- Create: `services/video-postproduction-api/video_postproduction/db.py`
- Create: `services/video-postproduction-api/video_postproduction/models.py`
- Create: `services/video-postproduction-api/video_postproduction/schemas.py`
- Create: `services/video-postproduction-api/video_postproduction/main.py`
- Create: `services/video-postproduction-api/alembic.ini`
- Create: `services/video-postproduction-api/migrations/env.py`
- Create: `services/video-postproduction-api/migrations/versions/0001_v1_core.py`
- Create: `services/video-postproduction-api/tests/conftest.py`
- Create: `services/video-postproduction-api/tests/test_database.py`
- Create: `services/video-postproduction-api/tests/test_health.py`

**Interfaces:**
- Consumes: `EDLDocument` and `validate_edl` from Task 1.
- Produces: `Settings`, `get_session()`, SQLAlchemy models for `Project`, `MediaAsset`, `ProcessingJob`, `ProcessingStage`, `Transcript`, `TranscriptSegment`, `EDL`, and `RenderOutput`, plus `GET /healthz`.

- [x] **Step 1: Write failing database and health tests**

Use an isolated test database and assert:

```python
def test_project_round_trips_with_initial_edl(session):
    project = Project(name="demo", status="created")
    session.add(project)
    session.flush()
    edl = EDL(project_id=project.id, schema_version="edl.v1", document=valid_initial_edl(project.id))
    session.add(edl)
    session.commit()
    assert session.get(Project, project.id).name == "demo"
    assert session.get(EDL, edl.id).document["version"] == 1


def test_health_reports_service_contract(client):
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json()["service"] == "video-postproduction-api"
```

- [x] **Step 2: Run tests and verify the missing application fails**

Run:

```text
python -m pytest services/video-postproduction-api/tests/test_database.py services/video-postproduction-api/tests/test_health.py -q
```

Expected: FAIL because the service package, models, and FastAPI application do
not exist yet.

- [x] **Step 3: Add runtime dependencies and settings**

Extend `pyproject.toml` with runtime dependencies for FastAPI, Uvicorn,
Pydantic Settings, SQLAlchemy, Psycopg, Alembic, Celery, Redis, httpx,
jsonschema, and FFmpeg/transcription adapters. Add dev dependencies for
pytest, pytest-cov, ruff, mypy, PyYAML, and `testcontainers` only if the local
Docker verification uses it.

`Settings` must read `DATABASE_URL`, `REDIS_URL`, `STORAGE_ROOT`,
`MAX_UPLOAD_BYTES`, `ALLOWED_VIDEO_MIME_TYPES`, `TRANSCRIPTION_PROVIDER`,
`WHISPER_MODEL`, `WHISPER_DEVICE`, `OPENAI_COMPATIBLE_BASE_URL`,
`OPENAI_COMPATIBLE_TRANSCRIPTION_MODEL`, and `VIDEO_EDITOR_API_TOKEN` without
printing secret values.

- [x] **Step 4: Implement SQLAlchemy models and session lifecycle**

Use UUID primary keys, UTC timestamps, JSON columns for provider/media metadata,
foreign keys with explicit delete behavior, and status values constrained in
application validation. `get_session()` must commit only in the route/service
boundary and rollback on exception.

- [x] **Step 5: Create the Alembic initial migration and FastAPI app**

Expose `app = FastAPI(title="AI Post-Production API", version="1")`, register
`GET /healthz`, and make startup verify configuration without eagerly loading a
Whisper model. The migration must create all V1A tables and indexes for
`project_id`, `asset_id`, `job_id`, `status`, and transcript ordering.

- [x] **Step 6: Run focused tests, migration upgrade, and type checks**

Run:

```text
python -m pytest services/video-postproduction-api/tests/test_database.py services/video-postproduction-api/tests/test_health.py -q
alembic -c services/video-postproduction-api/alembic.ini upgrade head
ruff check services/video-postproduction-api/video_postproduction services/video-postproduction-api/tests
mypy services/video-postproduction-api/video_postproduction
```

Expected: tests PASS, migration reaches `head`, and lint/typecheck have no new
errors.

- [x] **Step 7: Commit the backend foundation**

```text
git add -- services/video-postproduction-api
git commit -m "feat: add video post-production API foundation"
```

### Task 3: Safe local storage and FFprobe metadata extraction

**Files:**
- Create: `services/video-postproduction-api/video_postproduction/storage/base.py`
- Create: `services/video-postproduction-api/video_postproduction/storage/local.py`
- Create: `services/video-postproduction-api/video_postproduction/media/validation.py`
- Create: `services/video-postproduction-api/video_postproduction/media/ffprobe.py`
- Create: `services/video-postproduction-api/video_postproduction/media/models.py`
- Create: `services/video-postproduction-api/tests/test_storage.py`
- Create: `services/video-postproduction-api/tests/test_media_validation.py`
- Create: `services/video-postproduction-api/tests/test_ffprobe.py`

**Interfaces:**
- Consumes: `Settings`, `MediaAsset`, and database session from Task 2.
- Produces: `LocalFilesystemStorage`, `validate_upload_filename`, `validate_media_type`, `MediaMetadata`, and `FFprobeMetadataReader.read(path) -> MediaMetadata`.

- [x] **Step 1: Write failing path-safety and metadata tests**

Cover the required dangerous inputs:

```python
def test_storage_rejects_path_traversal(tmp_path):
    storage = LocalFilesystemStorage(tmp_path)
    with pytest.raises(StoragePathError):
        storage.local_path("../../outside.mp4")


def test_upload_validator_rejects_unknown_type():
    with pytest.raises(UploadValidationError, match="not supported"):
        validate_media_type("application/x-executable", "video.mp4")


def test_ffprobe_reader_parses_duration_and_streams(monkeypatch, sample_video):
    reader = FFprobeMetadataReader(executable="ffprobe")
    metadata = reader.read(sample_video)
    assert metadata.duration > 0
    assert metadata.video.width > 0
```

- [x] **Step 2: Run the tests to observe the expected missing-module failures**

Run:

```text
python -m pytest services/video-postproduction-api/tests/test_storage.py services/video-postproduction-api/tests/test_media_validation.py services/video-postproduction-api/tests/test_ffprobe.py -q
```

Expected: FAIL because storage, validators, metadata models, and FFprobe reader
are not implemented. If FFprobe is unavailable, keep the pure path-safety tests
failing for the missing implementation and mark the real binary test as an
environment-gated integration test, not as a false code failure.

- [x] **Step 3: Implement storage with atomic writes and root confinement**

Resolve every storage key under the configured root, reject absolute paths and
`..` segments, create parent directories, write to a same-directory temp file,
fsync when practical, and atomically rename. `open()` and `delete()` must use
the same confinement check. Return `StoredObject(storage_key, size_bytes,
sha256, content_type)` from `put_atomic`.

- [x] **Step 4: Implement upload validation**

Stream the upload into a temp file with a byte counter, enforce
`MAX_UPLOAD_BYTES`, reject unsupported suffix/MIME combinations, and use
FFprobe after storage to reject files that are not readable video. Do not trust
the browser-provided MIME value as the only check.

- [x] **Step 5: Implement typed FFprobe invocation and parsing**

Build the exact argv:

```python
[executable, "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(media_path)]
```

Call `subprocess.run(..., shell=False, check=False, timeout=...)`, parse only
expected fields, and raise `MediaProbeError(code, safe_message, diagnostics)`
with truncated stderr. Never expose the full local path in the API error.

- [x] **Step 6: Run focused tests and the real FFprobe fixture test**

Run:

```text
python -m pytest services/video-postproduction-api/tests/test_storage.py services/video-postproduction-api/tests/test_media_validation.py services/video-postproduction-api/tests/test_ffprobe.py -q
```

Expected: all pure tests PASS; the fixture test PASS when FFprobe is installed,
otherwise report the external binary as unavailable rather than replacing it
with a production mock.

- [x] **Step 7: Commit storage and probing**

```text
git add -- services/video-postproduction-api/video_postproduction/storage services/video-postproduction-api/video_postproduction/media services/video-postproduction-api/tests/test_storage.py services/video-postproduction-api/tests/test_media_validation.py services/video-postproduction-api/tests/test_ffprobe.py
git commit -m "feat: add safe media storage and ffprobe inspection"
```

### Task 4: Transcript normalization and provider adapters

**Files:**
- Create: `services/video-postproduction-api/video_postproduction/transcription/models.py`
- Create: `services/video-postproduction-api/video_postproduction/transcription/base.py`
- Create: `services/video-postproduction-api/video_postproduction/transcription/normalize.py`
- Create: `services/video-postproduction-api/video_postproduction/transcription/faster_whisper.py`
- Create: `services/video-postproduction-api/video_postproduction/transcription/openai_compatible.py`
- Create: `services/video-postproduction-api/tests/test_transcript_normalization.py`
- Create: `services/video-postproduction-api/tests/test_transcription_providers.py`

**Interfaces:**
- Consumes: `Settings` and `MediaMetadata` from Tasks 2–3.
- Produces: `Word`, `TranscriptSegment`, `NormalizedTranscript`, `TranscriptionProvider`, `FasterWhisperProvider`, `OpenAICompatibleTranscriptionProvider`, and `normalize_provider_result(...)`.

- [x] **Step 1: Write failing normalization tests**

Pin ordering, bounds, and missing-word behavior:

```python
def test_normalizer_rejects_invalid_ranges():
    with pytest.raises(TranscriptValidationError):
        normalize_provider_result({"segments": [{"start": 3, "end": 2, "text": "bad"}]}, duration=10)


def test_provider_preserves_missing_word_timestamps():
    result = normalize_provider_result({"segments": [{"start": 0, "end": 2, "text": "hello"}]}, duration=2)
    assert result.segments[0].words == []


def test_normalizer_assigns_stable_segment_ids_and_orders_words():
    result = normalize_provider_result(raw_segments_in_reverse_order(), duration=10)
    assert [segment.id for segment in result.segments] == ["segment-0001", "segment-0002"]
    assert result.segments[0].words[0].start <= result.segments[0].words[1].start
```

- [x] **Step 2: Run the tests and confirm the normalizer is absent**

Run:

```text
python -m pytest services/video-postproduction-api/tests/test_transcript_normalization.py services/video-postproduction-api/tests/test_transcription_providers.py -q
```

Expected: FAIL because the models and provider adapters do not exist.

- [x] **Step 3: Implement normalized Pydantic models and validation**

Use finite floats, enforce `0 <= start < end`, clamp only harmless floating
point overshoot within a small tolerance, reject material overshoot, preserve
provider language/duration metadata, and return `words: []` when the provider
does not supply word timestamps.

- [x] **Step 4: Implement the OpenAI-compatible adapter with explicit transport**

Send only extracted audio to the configured transcription endpoint, pass model,
language, and timestamp options, parse provider JSON into the normalized model,
and turn non-2xx/invalid JSON into `TranscriptionProviderError` without logging
the authorization header or response body in full. Use `httpx.Client` injected
into the adapter so tests can use `httpx.MockTransport` without changing
production behavior.

- [x] **Step 5: Implement lazy faster-whisper integration**

Load the model only inside `transcribe()`, map segments and optional words into
the normalized model, honor `WHISPER_MODEL` and `WHISPER_DEVICE`, and raise a
clear configuration error when the package/model is unavailable. Do not load a
large model during API startup.

- [x] **Step 6: Run provider and normalization tests plus static checks**

Run:

```text
python -m pytest services/video-postproduction-api/tests/test_transcript_normalization.py services/video-postproduction-api/tests/test_transcription_providers.py -q
ruff check services/video-postproduction-api/video_postproduction/transcription services/video-postproduction-api/tests
mypy services/video-postproduction-api/video_postproduction/transcription
```

Expected: PASS; the real faster-whisper model smoke test remains opt-in until
the runtime image/model cache is available.

- [x] **Step 7: Commit transcript contracts and adapters**

```text
git add -- services/video-postproduction-api/video_postproduction/transcription services/video-postproduction-api/tests/test_transcript_normalization.py services/video-postproduction-api/tests/test_transcription_providers.py
git commit -m "feat: add normalized transcription providers"
```

### Task 5: Asynchronous job queue, stage runner, and idempotency

**Files:**
- Create: `services/video-postproduction-api/video_postproduction/jobs/queue.py`
- Create: `services/video-postproduction-api/video_postproduction/jobs/stages.py`
- Create: `services/video-postproduction-api/video_postproduction/jobs/tasks.py`
- Create: `services/video-postproduction-api/video_postproduction/jobs/recovery.py`
- Create: `services/video-postproduction-api/video_postproduction/observability.py`
- Create: `services/video-postproduction-api/tests/test_job_queue.py`
- Create: `services/video-postproduction-api/tests/test_stage_runner.py`
- Modify: `services/video-postproduction-api/video_postproduction/models.py`
- Modify: `services/video-postproduction-api/video_postproduction/config.py`

**Interfaces:**
- Consumes: storage/probe/transcription providers and database models from Tasks 2–4.
- Produces: `CeleryJobQueue`, `InlineJobQueue`, `StageRunner.run_probe(job_id)`, `StageRunner.run_transcribe(job_id)`, `enqueue_asset_pipeline(project_id, asset_id)`, and `recover_stale_jobs()`.

- [x] **Step 1: Write failing idempotency and retry tests**

Use a deterministic test provider only through dependency injection:

```python
def test_pipeline_is_idempotent_for_same_fingerprint(session, inline_queue, asset):
    first = enqueue_asset_pipeline(session, inline_queue, asset.id)
    second = enqueue_asset_pipeline(session, inline_queue, asset.id)
    assert first.job_id == second.job_id
    assert session.query(ProcessingJob).count() == 1


def test_failed_stage_records_safe_error_and_can_retry(session, runner, job):
    runner.run_transcribe(job.id)
    failed = session.get(ProcessingStage, stage_id(job, "transcribe"))
    assert failed.status == "failed"
    assert failed.error_code == "transcription_provider_error"
    retry_job = runner.retry_stage(job.id, "transcribe")
    assert retry_job.status == "queued"
```

- [x] **Step 2: Run the tests and verify missing queue behavior**

Run:

```text
python -m pytest services/video-postproduction-api/tests/test_job_queue.py services/video-postproduction-api/tests/test_stage_runner.py -q
```

Expected: FAIL because queue adapters, stage runner, and idempotency service do
not exist.

- [x] **Step 3: Implement queue adapters and Celery configuration**

`CeleryJobQueue.enqueue(job_id, stage_name)` must send a task name plus IDs,
never a serialized shell command or local path. `InlineJobQueue` executes the
same stage function synchronously for tests. Celery tasks must open a fresh DB
session and close it after every task.

- [x] **Step 4: Implement stage runner and state transitions**

`run_probe` reads the immutable source through `StorageProvider`, writes media
metadata, and enqueues `transcribe` only after a committed probe success.
`run_transcribe` selects the configured provider, persists one transcript per
fingerprint, creates the initial EDL, and marks the project `ready_for_edit`.
Both stages write structured start/end/failure events and clean temporary audio
files in `finally`.

- [x] **Step 5: Add stale-job recovery and bounded retry**

Implement `recover_stale_jobs()` to mark jobs whose lease exceeded the configured
timeout as retryable failures. Validation/security errors are terminal; provider
and transient process errors may be retried up to `MAX_STAGE_ATTEMPTS`.

- [x] **Step 6: Run focused tests and Celery import checks**

Run:

```text
python -m pytest services/video-postproduction-api/tests/test_job_queue.py services/video-postproduction-api/tests/test_stage_runner.py -q
python -c "from video_postproduction.jobs.tasks import celery_app; print(celery_app.main)"
ruff check services/video-postproduction-api/video_postproduction/jobs services/video-postproduction-api/tests
```

Expected: tests PASS and Celery imports without connecting to Redis during
module import.

- [x] **Step 7: Commit the asynchronous pipeline**

```text
git add -- services/video-postproduction-api/video_postproduction/jobs services/video-postproduction-api/video_postproduction/observability.py services/video-postproduction-api/video_postproduction/models.py services/video-postproduction-api/video_postproduction/config.py services/video-postproduction-api/tests/test_job_queue.py services/video-postproduction-api/tests/test_stage_runner.py
git commit -m "feat: add retryable asynchronous media stages"
```

### Task 6: FastAPI project, upload, transcript, job, and streaming endpoints

**Files:**
- Create: `services/video-postproduction-api/video_postproduction/api/dependencies.py`
- Create: `services/video-postproduction-api/video_postproduction/api/routes.py`
- Create: `services/video-postproduction-api/video_postproduction/api/errors.py`
- Create: `services/video-postproduction-api/video_postproduction/api/services.py`
- Create: `services/video-postproduction-api/tests/test_api_projects.py`
- Create: `services/video-postproduction-api/tests/test_api_upload.py`
- Create: `services/video-postproduction-api/tests/test_api_transcript.py`
- Modify: `services/video-postproduction-api/video_postproduction/main.py`
- Modify: `services/video-postproduction-api/video_postproduction/schemas.py`

**Interfaces:**
- Consumes: `StorageProvider`, `FFprobeMetadataReader`, `JobQueue`, `StageRunner`, and database models from Tasks 2–5.
- Produces: the V1A REST contract: `GET /healthz`, project CRUD/list, multipart upload, asset status, job polling/retry, transcript retrieval, EDL retrieval, and safe asset streaming.

- [x] **Step 1: Write failing API tests for the public contract**

Cover the exact endpoints and response shapes:

```python
def test_create_project_returns_project_id(client):
    response = client.post("/api/v1/projects", json={"name": "Demo", "instructions": "Thai subtitles"})
    assert response.status_code == 201
    assert response.json()["status"] == "created"


def test_upload_returns_job_without_waiting_for_transcription(client, sample_video):
    response = client.post(
        "/api/v1/projects/{project_id}/assets".format(project_id=project_id),
        files={"file": ("sample.mp4", sample_video.read_bytes(), "video/mp4")},
    )
    assert response.status_code == 202
    assert response.json()["job_id"]
    assert response.json()["poll_url"].startswith("/api/v1/jobs/")


def test_transcript_not_ready_is_explicit(client, project_id):
    response = client.get(f"/api/v1/projects/{project_id}/transcript")
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "transcript_not_ready"
```

- [x] **Step 2: Run API tests and confirm routes are missing**

Run:

```text
python -m pytest services/video-postproduction-api/tests/test_api_projects.py services/video-postproduction-api/tests/test_api_upload.py services/video-postproduction-api/tests/test_api_transcript.py -q
```

Expected: FAIL because routers and service dependencies are not registered.

- [x] **Step 3: Implement project and asset request/response schemas**

Define typed models for project creation, project summaries, upload response,
job status, transcript response, EDL response, and the shared error envelope.
Keep internal filesystem paths and provider diagnostics out of response models.

- [x] **Step 4: Implement project and upload services**

Stream the upload to a temp file, validate it, create `MediaAsset` and
`ProcessingJob` in one transaction, commit before enqueueing, and return `202`.
If enqueueing fails after commit, mark the job failed with a safe queue error so
the asset record remains inspectable and retryable.

- [x] **Step 5: Implement job/transcript/EDL/stream routes and error mapping**

Register routes under `/api/v1`, map domain exceptions to the exact error
envelope, enforce optional bearer token for writes, and resolve streaming paths
only through `StorageProvider.local_path`. Do not expose raw exception strings.

- [x] **Step 6: Run API tests, OpenAPI generation, and static checks**

Run:

```text
python -m pytest services/video-postproduction-api/tests/test_api_projects.py services/video-postproduction-api/tests/test_api_upload.py services/video-postproduction-api/tests/test_api_transcript.py -q
python -c "from video_postproduction.main import app; assert '/api/v1/projects' in app.openapi()['paths']"
ruff check services/video-postproduction-api/video_postproduction/api services/video-postproduction-api/tests
mypy services/video-postproduction-api/video_postproduction/api
```

Expected: tests PASS and the OpenAPI document contains all V1A routes.

- [x] **Step 7: Commit the V1A API contract**

```text
git add -- services/video-postproduction-api/video_postproduction/api services/video-postproduction-api/video_postproduction/main.py services/video-postproduction-api/video_postproduction/schemas.py services/video-postproduction-api/tests/test_api_projects.py services/video-postproduction-api/tests/test_api_upload.py services/video-postproduction-api/tests/test_api_transcript.py
git commit -m "feat: expose video post-production V1A API"
```

### Task 7: Next.js control surface for projects, upload, and transcript

**Files:**
- Create: `apps/video-postproduction-web/package.json`
- Create: `apps/video-postproduction-web/tsconfig.json`
- Create: `apps/video-postproduction-web/next.config.ts`
- Create: `apps/video-postproduction-web/postcss.config.mjs`
- Create: `apps/video-postproduction-web/app/layout.tsx`
- Create: `apps/video-postproduction-web/app/page.tsx`
- Create: `apps/video-postproduction-web/app/new/page.tsx`
- Create: `apps/video-postproduction-web/app/projects/[projectId]/page.tsx`
- Create: `apps/video-postproduction-web/app/globals.css`
- Create: `apps/video-postproduction-web/components/ui/button.tsx`
- Create: `apps/video-postproduction-web/components/ui/card.tsx`
- Create: `apps/video-postproduction-web/components/project-list.tsx`
- Create: `apps/video-postproduction-web/components/project-create-form.tsx`
- Create: `apps/video-postproduction-web/components/project-editor.tsx`
- Create: `apps/video-postproduction-web/components/job-progress.tsx`
- Create: `apps/video-postproduction-web/components/transcript-panel.tsx`
- Create: `apps/video-postproduction-web/lib/api.ts`
- Create: `apps/video-postproduction-web/lib/api.test.ts`
- Create: `apps/video-postproduction-web/tests/browser-smoke.mjs`

**Interfaces:**
- Consumes: REST responses from Task 6; use `NEXT_PUBLIC_VIDEO_POSTPRODUCTION_API_ORIGIN` or a same-origin proxy.
- Produces: typed client functions `listProjects`, `createProject`, `uploadAsset`, `getProject`, `getJob`, and `getTranscript`, plus accessible Projects/New Project/Project Editor screens.

- [x] **Step 1: Write failing typed-client tests**

Add Vitest tests for response parsing and error handling:

```typescript
it('parses an accepted upload and exposes the poll URL', async () => {
  const result = await uploadAsset('project-1', fileFixture(), fetcherReturning(uploadResponse));
  expect(result.status).toBe('queued');
  expect(result.poll_url).toBe('/api/v1/jobs/job-1');
});

it('turns the API error envelope into a user-safe Error', async () => {
  await expect(getTranscript('project-1', fetcherReturning(apiError(409, 'transcript_not_ready'))))
    .rejects.toThrow('transcript_not_ready');
});
```

- [x] **Step 2: Run the web test and typecheck to observe the missing app**

Run from `apps/video-postproduction-web`:

```text
npm test -- --run
npm run typecheck
```

Expected: FAIL because the Next app, API client, and test configuration do not
exist yet.

- [x] **Step 3: Scaffold the Next.js app and typed API client**

Configure scripts `dev`, `build`, `start`, `typecheck`, `test`, and
`test:browser`. Implement `lib/api.ts` with typed JSON parsing, multipart
upload, polling-compatible job responses, and an error parser that never
renders raw stack traces.

- [x] **Step 4: Implement the three V1A screens**

Projects lists real API data and states; New Project creates a project and
uploads a selected video; Project Editor polls job status, embeds the allowed
asset stream, shows metadata, and renders transcript segments/words when ready.
Use dark-mode-first Tailwind classes and small shadcn/ui-style primitives. Do
not add fake clips, fake scores, or a timeline that the API cannot persist.

- [x] **Step 5: Add loading, empty, error, and retry states**

The UI must distinguish queued/running/succeeded/failed, show stage names and
safe error messages, disable duplicate upload while busy, and provide a retry
button that calls the job retry endpoint. The transcript panel must show an
explicit not-ready state rather than blank content.

- [x] **Step 6: Run web tests, typecheck, and production build**

Run:

```text
npm test -- --run
npm run typecheck
npm run build
```

Expected: PASS with no TypeScript errors and a successful production build.

- [x] **Step 7: Commit the V1A control surface**

```text
git add -- apps/video-postproduction-web
git commit -m "feat: add video post-production V1A control surface"
```

### Task 8: Docker Compose runtime and repository documentation

**Files:**
- Create: `services/video-postproduction-api/.env.example`
- Create: `services/video-postproduction-api/Dockerfile`
- Create: `services/video-postproduction/Dockerfile.web`
- Create: `services/video-postproduction/docker-compose.yml`
- Create: `services/video-postproduction/README.md`
- Modify: `apps/README.md`
- Modify: `services/README.md`

**Interfaces:**
- Consumes: API/worker/web commands and environment names from Tasks 2–7.
- Produces: one documented local command, `docker compose -f services/video-postproduction/docker-compose.yml up --build`, with healthchecked `postgres`, `redis`, `api`, `worker`, and `web` services.

- [x] **Step 1: Write the Compose validation check**

Add a small test script at `services/video-postproduction/tests/test_compose.py`
that loads the Compose YAML and asserts service names, healthchecks, the shared
storage volume, and that API/worker use the same image. The test must fail
before the Compose file exists.

- [x] **Step 2: Run the check and verify the runtime definition is missing**

Run:

```text
python -m pytest services/video-postproduction/tests/test_compose.py -q
```

Expected: FAIL because the Compose file and test directory do not exist.

- [x] **Step 3: Add Dockerfiles and Compose services**

The Python image installs the API package and system FFmpeg dependencies. API
starts Uvicorn; worker starts the Celery worker; web builds/runs Next.js. Add
PostgreSQL and Redis healthchecks, wait for health before application startup,
mount the local storage volume, expose only development ports, and pass all
configuration through environment variables.

- [x] **Step 4: Document startup and provider configuration**

Document the local command, migration behavior, sample upload flow, optional
OpenAI-compatible settings, faster-whisper model download/cache behavior,
storage location, port map, and the fact that secrets must remain outside Git.
Add links from the existing apps/services README files without rewriting their
unrelated content.

- [x] **Step 5: Run Compose validation and configuration checks**

Run:

```text
python -m pytest services/video-postproduction/tests/test_compose.py -q
docker compose -f services/video-postproduction/docker-compose.yml config
```

Expected: YAML/config validation PASS. If Docker is unavailable, report the
unavailable external check; do not claim the runtime started.

- [x] **Step 6: Commit the runtime and docs**

```text
git add -- services/video-postproduction-api/.env.example services/video-postproduction-api/Dockerfile services/video-postproduction services/video-postproduction/tests apps/README.md services/README.md
git commit -m "chore: add local video post-production runtime"
```

### Task 9: V1B captions, deterministic render, and output validation

**Files:**
- Create: `services/video-postproduction-api/video_postproduction/captions/srt.py`
- Create: `services/video-postproduction-api/video_postproduction/captions/ass.py`
- Create: `services/video-postproduction-api/video_postproduction/render/base.py`
- Create: `services/video-postproduction-api/video_postproduction/render/ffmpeg.py`
- Create: `services/video-postproduction-api/video_postproduction/render/service.py`
- Create: `services/video-postproduction-api/tests/test_captions.py`
- Create: `services/video-postproduction-api/tests/test_render_commands.py`
- Create: `services/video-postproduction-api/tests/test_render_service.py`
- Modify: `packages/video-postproduction-edl/edl.schema.json`
- Modify: `services/video-postproduction-api/video_postproduction/edl.py`
- Modify: `services/video-postproduction-api/video_postproduction/api/routes.py`
- Modify: `services/video-postproduction-api/video_postproduction/models.py`
- Modify: `services/video-postproduction-api/migrations/versions/0002_render_outputs.py`
- Modify: `apps/video-postproduction-web/components/project-editor.tsx`

**Interfaces:**
- Consumes: validated `EDLDocument`, transcript words, `StorageProvider`, and media metadata from Tasks 1–8.
- Produces: `CaptionRenderer.render_srt`, `CaptionRenderer.render_ass`, `FFmpegRenderEngine.render`, `RenderArtifact`, and V1B endpoints for EDL update, render enqueue, output status, preview, and download.

- [x] **Step 1: Write failing caption and renderer tests**

Pin deterministic subtitle and command behavior:

```python
def test_srt_uses_word_or_segment_boundaries_without_invalid_timecodes():
    srt = render_srt(normalized_transcript)
    assert "00:00:10,250 --> 00:00:14,720" in srt
    assert "[object Object]" not in srt


def test_renderer_fails_before_process_for_unknown_capability(edl, runner):
    edl.capabilities.required.append("active_speaker")
    with pytest.raises(RenderCapabilityError, match="active_speaker"):
        runner.render(edl, output_key="projects/p1/renders/r1.mp4")


def test_ffmpeg_builder_returns_argv_without_shell_interpolation(edl):
    argv = build_ffmpeg_argv(edl, input_path=Path("source.mp4"), output_path=Path("out.mp4"))
    assert argv[0] == "ffmpeg"
    assert all(";" not in item for item in argv)
```

- [x] **Step 2: Run the focused tests and observe the missing renderer**

Run:

```text
python -m pytest services/video-postproduction-api/tests/test_captions.py services/video-postproduction-api/tests/test_render_commands.py services/video-postproduction-api/tests/test_render_service.py -q
```

Expected: FAIL because caption renderers, capability checks, and FFmpeg engine
do not exist.

- [x] **Step 3: Implement SRT/ASS caption renderers**

Use normalized transcript timestamps, deterministic line grouping, escaped ASS
text, configurable preset values, and no user-controlled raw ASS event lines.
Keep caption generation independent from transcription provider selection.

- [x] **Step 4: Implement typed FFmpeg command/filter builders**

Support only the V1B capability set: source range cut, center 9:16 crop,
resize, audio passthrough/normalization option, and SRT/ASS subtitle burn-in.
Build filter arguments from typed model values, use `shell=False`, apply a
timeout, and capture bounded stderr for diagnostics.

- [x] **Step 5: Implement render service and output validation**

Check EDL capability requirements before spawning FFmpeg, render to a temp
output, run FFprobe on the result, require an MP4 container plus one video
stream and expected dimensions, atomically store the output, and persist
`RenderOutput`. On any failure remove only the temp artifact and preserve the
source and EDL.

- [x] **Step 6: Add V1B API and editor controls**

Add typed endpoint contracts for `PATCH /api/v1/projects/{id}/edl`,
`POST /api/v1/projects/{id}/renders`, `GET /api/v1/renders/{id}`, and output
stream/download. The editor must allow explicit start/end range edits, caption
preset selection, and center-crop output; it must not expose unsupported active
speaker, color, or B-roll controls as if they worked.

- [x] **Step 7: Run render tests and a small real FFmpeg fixture**

Run:

```text
python -m pytest services/video-postproduction-api/tests/test_captions.py services/video-postproduction-api/tests/test_render_commands.py services/video-postproduction-api/tests/test_render_service.py -q
```

Then run the real fixture test with FFmpeg/FFprobe installed. Expected: output
passes container, stream, duration, and 1080x1920 checks. If the binaries are
missing, record the external check as unavailable instead of claiming V1B works.

- [x] **Step 8: Run web verification and commit V1B**

```text
cd apps/video-postproduction-web
npm test -- --run
npm run typecheck
npm run build
cd ../..
git add -- packages/video-postproduction-edl services/video-postproduction-api apps/video-postproduction-web
git commit -m "feat: add deterministic captions and V1B rendering"
```

### Task 10: End-to-end evidence and final V1 verification

**Files:**
- Create: `services/video-postproduction-api/tests/e2e/test_upload_to_transcript.py`
- Create: `services/video-postproduction-api/tests/e2e/test_render_output.py`
- Create: `services/video-postproduction-api/tests/fixtures/generate_sample_video.py`
- Modify: `apps/video-postproduction-web/tests/browser-smoke.mjs`
- Modify: `services/video-postproduction/README.md`
- Modify: `docs/superpowers/plans/2026-09-22-ai-post-production-engine-v1.md`

**Interfaces:**
- Consumes: the complete V1A/V1B contracts and Compose runtime from Tasks 1–9.
- Produces: repeatable evidence that upload → metadata → transcription → transcript UI and explicit EDL range → captions → 9:16 MP4 output work end to end.

- [x] **Step 1: Write the failing end-to-end tests**

The tests must generate a short fixture through FFmpeg argv, create a project,
upload it through the API, run the worker with the deterministic test provider,
poll until the transcript is ready, and assert persisted metadata/EDL. The V1B
test must enqueue a render and assert the output dimensions and duration with
FFprobe.

- [x] **Step 2: Run the E2E tests before wiring the full runtime**

Run:

```text
python -m pytest services/video-postproduction-api/tests/e2e -q
```

Expected: FAIL only for missing runtime wiring or implementation defects, not
because the fixture script contains a syntax or path error.

- [x] **Step 3: Implement fixture generation and API/worker E2E harness**

Use a temporary directory, generated testsrc video plus sine audio, isolated
database/storage, and the explicit test provider injection. Never commit the
generated media binary, provider credentials, or local model cache.

- [x] **Step 4: Add browser smoke coverage**

Start the web/API runtime, open Projects, create a project, upload the sample,
assert that progress changes from queued/running to ready, open the transcript,
and verify a retry/error state can be represented. Use selectors based on
accessible labels and roles rather than CSS implementation details.

- [x] **Step 5: Run the complete local verification gate**

Run the smallest available equivalent of:

```text
python -m pytest services/video-postproduction-api/tests -q
ruff check services/video-postproduction-api/video_postproduction services/video-postproduction-api/tests
mypy services/video-postproduction-api/video_postproduction
cd apps/video-postproduction-web
npm test -- --run
npm run typecheck
npm run build
npm run test:browser
cd ../..
docker compose -f services/video-postproduction/docker-compose.yml config
```

Then run repository checks relevant to the changed boundaries:

```text
task test
task typecheck
task docs:check
```

If a repository command depends on unavailable `/bin/bash`, Docker, FFmpeg,
browser runtime, or model cache, record the exact unavailable check and run the
closest direct equivalent; do not hide the limitation.

- [x] **Step 6: Update evidence and commit the final V1 verification record**

Add the commands, pass/fail results, unavailable dependencies, and verified
runtime URLs to `services/video-postproduction/README.md`. Update this plan's
checkboxes only for work actually completed, then commit only the V1 files:

```text
git add -- services/video-postproduction-api/tests/e2e services/video-postproduction-api/tests/fixtures apps/video-postproduction-web/tests/browser-smoke.mjs services/video-postproduction/README.md docs/superpowers/plans/2026-09-22-ai-post-production-engine-v1.md
git commit -m "test: verify video post-production V1 end to end"
```

## Execution notes

- Execute tasks in order because later tasks consume exact interfaces from earlier tasks.
- Keep V1A and V1B commits separate so a failed render capability does not obscure a working ingest/transcription slice.
- Do not start Phase 2 LLM clip discovery, B-roll, asset search, audio post, color grade, or generated media in this plan.
- Before claiming completion, inspect `git status --short` and confirm unrelated pre-existing changes remain unstaged and unmodified.
