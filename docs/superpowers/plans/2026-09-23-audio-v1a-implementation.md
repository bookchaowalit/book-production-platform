# Audio V1A — Loudness and Trim Batch Implementation Plan

**Goal:** ส่งมอบ CLI ที่รับ `audio-batch.v1` แล้วผลิตไฟล์เสียง lossless พร้อมผลวัดคุณภาพที่ตรวจสอบและ replay ได้ โดยไม่เรียก LLM

**Architecture:** JSON manifest → schema/track validation → safe local input resolution → FFprobe inspection → FFmpeg analysis/render → decoded-output QA → staged artifacts → atomic result manifest

**Tech Stack:** Python 3.12+, `argparse`, `dataclasses`, `psutil` สำหรับวัด CPU ของ FFmpeg process, `pytest`/`jsonschema` สำหรับ tests, FFmpeg/FFprobe ที่ติดตั้งในเครื่อง; ใช้ Python standard library สำหรับ JSON, hashing, subprocess, temporary files และ atomic rename

**Spec:** [Audio V1A design](../specs/2026-09-23-audio-v1a-design.md)

**สถานะ:** implementation Task 0–7 เสร็จและ commit ใน product repository; technical acceptance ผ่าน; รอ human listening gate ก่อนเรียก pilot-ready

## Global Constraints / ข้อกำหนดร่วม

- Product source, tests, fixtures, dependencies และ README อยู่ใน repository แยกที่ `bookchaowalit/book-audio-production`; Solo Empire เก็บ spec, plan และ catalog evidence เท่านั้น
- เริ่มจากตรวจ checkout/remote ที่มีอยู่ หากยังไม่มี ให้ตั้ง local repository บน branch `main` ตาม canonical path โดยไม่สร้าง GitHub repository หรือ push ระหว่างแผนนี้
- ไม่เพิ่ม API, web UI, database, queue, upload, publish, ad service หรือ network fetch; standard processing ต้องทำงานได้โดยไม่มี API key และ `model_calls=0`
- ใช้ขีดจำกัด pilot จาก spec เป็น defaults: 20 tracks/batch, 2 GiB/source, 2 ชั่วโมง/track และ 8 ชั่วโมงรวม; local config ปรับลงได้แต่ปรับเกิน hard cap หรือปิดขีดจำกัดไม่ได้
- ตั้ง hard caps ที่ implementation ก่อน render สำหรับจำนวน bytes ของ log, ระยะเวลา process และขนาด output; ทุกค่ามี default, มีเพดานสูงสุด และปรับผ่าน local config ได้โดยห้ามปิด
- Targets ใช้ช่วงที่ FFmpeg `loudnorm` รองรับ: integrated loudness `-70..-5 LUFS`, LRA `1..50 LU`, true peak `-9..0 dBTP`; เมื่อไม่ระบุ LRA ส่ง `50 LU` อย่างชัดเจนแทนการพึ่งค่า default ของ FFmpeg. อ้างอิง [FFmpeg loudnorm documentation](https://ffmpeg.org/ffmpeg-filters.html#loudnorm)
- รับ mono/stereo และคง sample rate กับ channel count; trim milliseconds แปลงเป็น sample index ด้วยกฎ `round(ms * sample_rate / 1000)` ก่อนส่ง filter เพื่อให้ขอบเขตชัดเจน
- ทั้ง `flac` และ `wav_pcm24` เขียนผลเป็น 24-bit PCM; strip metadata tags โดยปริยาย และบันทึกการตั้งค่านี้ในผลลัพธ์
- ห้ามเขียนทับ source หรือไฟล์ปลายทางที่มีอยู่; sanitize diagnostics, ไม่คืน absolute paths หรือ argv ของ FFmpeg ใน JSON; telemetry ไม่เก็บ path หรือเนื้อหาเสียง
- ค่า hard caps: 20 tracks, 2 GiB/source, 7,200 วินาที/track, 28,800 วินาทีรวม, output 8 GiB/track และ 40 GiB/batch; config ปรับลงได้แต่ห้ามปรับเกินค่านี้หรือปิดเพดาน
- wall deadline สูงสุด 21,600 วินาที/track และ 86,400 วินาที/batch; แต่ละ process ใช้เวลาที่เหลือของ deadline นั้น. stdout/stderr ที่เก็บเพื่อ parse จำกัด 1 MiB ต่อ invocation และระยะเวลาทำงานจริงวัดด้วย monotonic clock
- แบ่งผลระดับ track เป็น `accepted`/`rejected`; ระดับ batch เป็น `accepted`, `rejected`, `completed_with_errors`; manifest สุดท้ายเขียนแบบ atomic หลังตรวจ output
- Recipe เริ่มที่ `audio-v1a.1`; replay fingerprint รวม source SHA-256, trim, targets, format, recipe version, FFmpeg/FFprobe versions และ output profile
- เป้าหมาย QA คือ integrated loudness ±0.5 LU, true peak ไม่เกิน ceiling เกิน 0.1 dB, duration ตรง trim ภายในหนึ่ง decoded sample บวก container rounding; วัด sample peak แยกและออก `near_full_scale_peak` ที่ `>= -0.1 dBFS` โดยเป็น warning เท่านั้น
- ถ้าไม่มี rate card ให้ monetary cost เป็น `unknown`; บันทึก wall time, CPU time, bytes, replay และ `model_calls=0` โดยไม่สรุปว่า local CPU ไม่มีต้นทุน

## Review focus

1. **Architecture and ownership:** code, fixtures และ test data อยู่ใน nested product repo; ไม่มี product runtime code หรือ audio data ใน Solo Empire root
2. **Contract and consistency:** schema, dataclasses, output manifest, CLI flags, error codes และ recipe version ตรงกัน; เปลี่ยน field ใดต้องเพิ่ม schema version
3. **Security and privacy:** path traversal, symlink escape, shell injection, oversized input/log/output, metadata leakage และ absolute path leakage มี regression tests
4. **Failure and replay:** timeout, process crash, partial batch, duplicate concurrent run, stale lock, output collision และ tampered artifact ให้ผลที่ตรวจสอบได้และไม่รับ artifact ที่ไม่ผ่าน QA
5. **Quality, cost and operations:** metrics แยกกันครบ, tolerances ถูกใช้จริง, synthetic benchmark ไม่อ้าง intelligibility, manual speech review เป็น release gate, `model_calls=0`, cost unknown เมื่อไม่มี rate card

## สัญญา interface ระหว่าง tasks

ประกาศ dataclasses และ aliases เหล่านี้ใน `src/book_audio_production/models.py` เพื่อให้ validation, engine, reporting และ CLI ใช้ field names เดียวกัน; ประกาศ `AudioError` ใน `src/book_audio_production/errors.py`:

```python
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Literal, Mapping, Sequence

OutputFormat = Literal["flac", "wav_pcm24"]
BatchStatus = Literal["accepted", "rejected", "completed_with_errors"]
TrackStatus = Literal["accepted", "rejected"]
MeasurementStatus = Literal["measured", "unavailable"]
CommandRunner = Callable[[Sequence[str], float, int], "ProcessResult"]

@dataclass(frozen=True)
class AudioLimits:
    max_tracks: int = 20
    max_source_bytes: int = 2 * 1024**3
    max_track_seconds: int = 7200
    max_batch_seconds: int = 28800
    max_output_bytes_per_track: int = 8 * 1024**3
    max_output_bytes_per_batch: int = 40 * 1024**3
    track_deadline_seconds: int = 21600
    batch_deadline_seconds: int = 86400
    process_output_limit_bytes: int = 1024 * 1024

@dataclass(frozen=True)
class TrimRange:
    start_ms: int | None
    end_ms: int | None

@dataclass(frozen=True)
class LoudnessTargets:
    integrated_lufs: float
    true_peak_dbtp: float
    lra_lu: float | None

@dataclass(frozen=True)
class TrackRequest:
    track_id: str
    source: str
    trim: TrimRange | None
    targets: LoudnessTargets
    output_format: OutputFormat

@dataclass(frozen=True)
class AudioBatch:
    batch_id: str
    tracks: tuple[TrackRequest, ...]

@dataclass(frozen=True)
class ProbeInfo:
    codec_name: str
    channels: int
    sample_rate: int
    duration_seconds: float
    file_bytes: int

@dataclass(frozen=True)
class AudioMeasurement:
    integrated_lufs: float
    lra_lu: float | None
    sample_peak_dbfs: float
    true_peak_dbtp: float
    duration_seconds: float

@dataclass(frozen=True)
class LoudnessMeasurement:
    integrated_lufs: float
    lra_lu: float | None
    true_peak_dbtp: float
    threshold_lufs: float
    normalization_type: str

@dataclass(frozen=True)
class ValidatedTrack:
    request: TrackRequest
    source_path: Path
    source_sha256: str
    trim_start_sample: int
    trim_end_sample: int | None
    probe: ProbeInfo

@dataclass(frozen=True)
class ProcessResult:
    returncode: int
    stdout: bytes
    stderr: bytes
    elapsed_seconds: float
    cpu_seconds: float | None
    measurement_status: MeasurementStatus
    output_truncated: bool

@dataclass(frozen=True)
class ErrorInfo:
    code: str
    message: str
    retryable: bool

@dataclass(frozen=True)
class QAResult:
    passed: bool
    failures: tuple[str, ...]
    checks: Mapping[str, bool]

@dataclass(frozen=True)
class TrackResult:
    track_id: str
    status: TrackStatus
    source_relative: str | None
    source_sha256: str | None
    trim: TrimRange | None
    requested_targets: LoudnessTargets
    pre_measurement: AudioMeasurement | None
    post_measurement: AudioMeasurement | None
    output_format: OutputFormat
    output_relative_path: str | None
    output_sha256: str | None
    output_bytes: int
    normalization_type: str | None
    qa: QAResult
    wall_seconds: float
    cpu_seconds: float | None
    measurement_status: MeasurementStatus
    warnings: tuple[str, ...]
    error: ErrorInfo | None

@dataclass(frozen=True)
class AudioBatchResult:
    schema_version: Literal["audio-batch-result.v1"]
    batch_id: str
    status: BatchStatus
    recipe_version: str
    fingerprint: str
    ffmpeg_version: str | None
    ffprobe_version: str | None
    model_calls: Literal[0]
    accepted_tracks: int
    rejected_tracks: int
    wall_seconds: float
    cpu_seconds: float | None
    measurement_status: MeasurementStatus
    bytes_written: int
    monetary_cost: float | None
    cost_status: Literal["known", "unknown"]
    replay: bool
    tracks: tuple[TrackResult, ...]

@dataclass(frozen=True)
class VerificationResult:
    passed: bool
    error_code: str | None
    artifacts_checked: int
    bytes_checked: int

@dataclass(frozen=True)
class BenchmarkResult:
    accepted: int
    rejected: int
    partial_batches: int
    qa_failures: int
    replay_hits: int
    wall_seconds: float
    cpu_seconds: float | None
    bytes_written: int
    model_calls: Literal[0]
    monetary_cost: float | None
    cost_status: Literal["known", "unknown"]
```

```python
class AudioError(ValueError):
    def __init__(self, code: str, public_message: str, retryable: bool = False):
        super().__init__(public_message)
        self.code = code
        self.retryable = retryable
```

Function signatures: `parse_manifest(payload: bytes) -> AudioBatch`; `validate_track(track: TrackRequest, input_root: Path, limits: AudioLimits, runner: CommandRunner) -> ValidatedTrack`; `run_process(argv: Sequence[str], timeout_seconds: float, output_limit_bytes: int) -> ProcessResult`; `probe_audio(source: Path, runner: CommandRunner) -> ProbeInfo`; `milliseconds_to_samples(milliseconds: int, sample_rate: int) -> int`; `render_track(track: ValidatedTrack, output_path: Path, runner: CommandRunner) -> TrackResult`; `warnings_for_sample_peak(sample_peak_dbfs: float) -> tuple[str, ...]`; `process_batch(batch: AudioBatch, input_root: Path, output_root: Path, limits: AudioLimits) -> AudioBatchResult`; `verify_manifest(path: Path) -> VerificationResult`; `exit_code_for_status(status: BatchStatus, published: bool) -> int`; `run_benchmark(output: Path, samples: int, limits: AudioLimits) -> BenchmarkResult`; `main(argv: Sequence[str] | None = None) -> int`.

`TrackResult` and `AudioBatchResult` fields and types are defined in the code block above. Optional measurements, hashes, output fields, and error are `None` when the track did not reach that stage. `AudioBatchResult.monetary_cost` is `None` and `cost_status="unknown"` without a configured rate card. JSON Schema files define exact serialized names, required fields, and nullability. `CommandRunner` has the callable signature `Callable[[Sequence[str], float, int], ProcessResult]`.

## แผนทำงาน

ทำตามลำดับ เพราะ artifact และ replay พึ่งพา contract กับ processing result ที่คงที่ แต่ละ implementation task เขียน failing test ก่อน, เพิ่ม implementation ขั้นต่ำ, รัน tests ที่ระบุ, ตรวจ diff แล้ว commit เฉพาะ product repository นั้น

### Task 0 — ยืนยันขอบเขตและ baseline ของ repository

**ไฟล์/พื้นที่:** canonical product path ข้างต้น, product `.git`, `docs/engineering/baseline.md`, parent `repository-catalog/registries/projects.yaml` (เฉพาะหลังมี remote ที่ตรวจยืนยันแล้ว)

- **ก่อนเริ่ม:** ตรวจ root และ nested `git status`, branch, `origin`, ignore rules, Python และ FFmpeg/FFprobe เวอร์ชันด้วย `python --version`, `ffmpeg -version`, `ffprobe -version`; ถ้า FFmpeg ขาดให้บันทึกว่า integration/release gate ยังไม่ผ่านและไม่ติดตั้ง software อัตโนมัติ. ยืนยันว่าไม่มี audio source, credentials หรือ user files ใน fixtures.
- หาก nested repo ไม่มี ให้สร้าง local Git repository ด้วย `git init --initial-branch=main` ที่ canonical path และเตรียม scaffold; ห้ามสร้าง hosted repo, ตั้ง credential หรือ push ในขั้นนี้.
- ห้ามเพิ่ม entry ปลอมใน catalog; เพิ่ม registry entry และ refresh inventory เฉพาะเมื่อ checkout/remote มีอยู่จริงและตรวจ owner/path ตรงกัน.
- **ผ่านเมื่อ:** root working tree คงงานเดิม, nested boundary เป็นอิสระ, branch เป็น `main`, baseline เวอร์ชันหรือสถานะ missing ถูกบันทึกใน `docs/engineering/baseline.md` โดยไม่มี path ส่วนตัว.
- **Commit:** `git add docs/engineering/baseline.md` แล้ว `git commit -m "docs(audio): record local toolchain baseline"`.

### Task 1 — สร้าง package และตรึง manifest contract

**ไฟล์:** `pyproject.toml`, `README.md`, `schemas/audio-batch.v1.schema.json`, `schemas/audio-batch-result.v1.schema.json`, `src/book_audio_production/__init__.py`, `src/book_audio_production/models.py`, `src/book_audio_production/errors.py`, `src/book_audio_production/manifest.py`, `tests/test_manifest.py`, `tests/test_result_schema.py`

- **RED:** เพิ่ม tests ให้ valid manifest, malformed JSON, unknown `schema_version`, duplicate `track_id`, invalid format, non-finite numbers และ output status ที่ไม่รู้จักถูกปฏิเสธด้วย stable error codes.
- **GREEN:** ใช้ typed immutable dataclasses: `TrimRange(start_ms: int | None, end_ms: int | None)`, `LoudnessTargets(integrated_lufs: float, true_peak_dbtp: float, lra_lu: float | None)`, `TrackRequest(track_id: str, source: str, trim: TrimRange | None, targets: LoudnessTargets, output_format: OutputFormat)` และ `AudioBatch(batch_id: str, tracks: tuple[TrackRequest, ...])`.
- ยึด manifest shape `schema_version`, `batch_id`, `tracks`; แต่ละ track มี `track_id`, `source`, optional `trim`, `targets` และ `output_format`. เก็บ JSON Schema เป็น contract ที่เครื่องมืออื่นตรวจได้ และให้ parser ตรวจ semantic rules เพิ่ม. Result schema ต้องมี `schema_version`, `batch_id`, `status`, `recipe_version`, `fingerprint`, tool versions, `model_calls`, batch totals และ track results; แต่ละ track result ต้องบรรจุ input hash/relative ref, status/error, trim, requested+measured targets, sample/true peak, duration, output hash/bytes, processing mode, QA, wall/CPU time และ warnings.
- เพิ่ม `pyproject.toml` พร้อม package command `book-audio`, runtime dependency `psutil` แบบช่วงเวอร์ชันจำกัด, optional test dependencies `pytest` และ `jsonschema`; ไม่เพิ่ม SDK หรือ LLM package. ติดตั้งจาก nested repo ด้วย `python -m pip install -e ".[test]"` ก่อนเริ่มรันทดสอบ.
- **Interfaces:** สร้าง `AudioBatch`, `TrackRequest`, `TrimRange`, `LoudnessTargets`, `OutputFormat`, `AudioError` และ `parse_manifest(payload: bytes) -> AudioBatch`; Task 2 อ่าน models เหล่านี้โดยไม่เปลี่ยนชื่อ field.
- **Step 1 — เขียน failing tests:**

  ```python
  from book_audio_production.manifest import parse_manifest

  def test_parse_audio_batch_v1():
      payload = b'{"schema_version":"audio-batch.v1","batch_id":"pilot-1","tracks":[{"track_id":"intro","source":"intro.wav","targets":{"integrated_lufs":-16,"true_peak_dbtp":-1},"output_format":"flac"}]}'
      batch = parse_manifest(payload)
      assert batch.batch_id == "pilot-1"
      assert batch.tracks[0].targets.lra_lu is None
  ```

- **Step 2 — ยืนยันว่า test ล้ม:** `python -m pytest -q tests/test_manifest.py::test_parse_audio_batch_v1`; คาดว่า FAIL เพราะ package/parser ยังไม่มี.
- **Step 3 — เพิ่ม implementation ขั้นต่ำ:** decode UTF-8 JSON, require `schema_version == "audio-batch.v1"`, map fields เป็น dataclasses, reject unknown fields/version, duplicate IDs, non-finite targets และ format อื่นด้วย `AudioError(code, public_message)`; เขียน schemas ให้สะท้อน field/nullability เดียวกัน.
- **VERIFY:** `python -m pytest -q tests/test_manifest.py tests/test_result_schema.py` และตรวจ JSON Schema ด้วย test fixture ทั้ง positive/negative.
- **Step 4 — ยืนยันว่า tests ผ่าน:** รัน VERIFY ด้านบน; คาดว่า PASS.
- **Step 5 — Commit:** จาก nested product repo รัน `git add pyproject.toml README.md schemas src/book_audio_production tests/test_manifest.py tests/test_result_schema.py` แล้ว `git commit -m "feat(audio): define batch manifest contract"`.

### Task 2 — ตรวจ path, target และ resource limits ก่อนประมวลผล

**ไฟล์:** `src/book_audio_production/validation.py`, `src/book_audio_production/config.py`, `src/book_audio_production/limits.py`, `src/book_audio_production/errors.py`, `.gitignore`, `audio-production.toml` (local, ignored), `audio-production.toml.example`, `tests/test_validation.py`, `tests/test_limits.py`, `tests/test_config.py`

- **RED:** เพิ่ม tests สำหรับ absolute path, URL scheme, `..`, symlink escape, missing file, source ที่แก้ระหว่าง processing, ID ที่ใช้สร้าง path ไม่ได้, invalid trim (`end <= start`), targets นอกช่วง FFmpeg, multi-stream/unsupported channel, file/batch limits และผลลัพธ์ที่คาดว่าจะเกิน output cap.
- **GREEN:** parse JSON ทั้ง batch ก่อน; validate แต่ละ track แยกกันเพื่อให้ partial batch ทำงานได้. Resolve source ใต้ input root, ปฏิเสธ path escape, hash source ก่อน render และตรวจ hash ซ้ำหลัง render ใน `process_batch`; คืน error code เช่น `INVALID_MANIFEST`, `UNSAFE_PATH`, `SOURCE_NOT_FOUND`, `RESOURCE_LIMIT_EXCEEDED`, `INVALID_TRIM`, `INVALID_TARGET`, `UNSUPPORTED_STREAM`, `SOURCE_CHANGED`. `config.py` อ่าน TOML local ด้วย `tomllib`, ใช้ defaults ที่ตรงกับ hard caps และปฏิเสธค่าที่เกินเพดานหรือปิด limit.
- ยอมรับค่าทศนิยมเฉพาะ finite; `track_id` ใช้ตัวอักษร ASCII, ตัวเลข, `_`, `-` ความยาว 1–64 ตัว; source เป็น relative path เท่านั้น.
- `batch_id` ใช้ตัวอักษร ASCII, ตัวเลข, `_`, `-` ความยาว 1–64 ตัวเช่นกัน; สร้างโฟลเดอร์ output จาก batch ID และตั้งชื่อแต่ละไฟล์ด้วย track ID, ขีดกลาง, 12 ตัวแรกของ source SHA-256 และนามสกุลตาม format.
- **Interfaces:** ใช้ `AudioBatch`, `TrackRequest`, `AudioLimits`, `ProbeInfo`, `ValidatedTrack`, `AudioError`; ผลิต `validate_track(track, input_root, limits, runner) -> ValidatedTrack`.
- **Step 1 — เขียน failing test:**

  ```python
  import pytest
  from book_audio_production.errors import AudioError
  from book_audio_production.models import AudioLimits, LoudnessTargets, TrackRequest
  from book_audio_production.validation import validate_track

  def test_rejects_symlink_escape(tmp_path):
      outside = tmp_path.parent / "outside.wav"
      outside.write_bytes(b"audio")
      root = tmp_path / "input"
      root.mkdir()
      try:
          (root / "escape.wav").symlink_to(outside)
      except (OSError, NotImplementedError):
          pytest.skip("symlinks are unavailable on this host")
      request = TrackRequest("escape", "escape.wav", None, LoudnessTargets(-16, -1, None), "flac")
      def unreachable_runner(argv, timeout_seconds, output_limit_bytes):
          raise AssertionError("path validation must run before ffprobe")
      with pytest.raises(AudioError) as error:
          validate_track(request, root, AudioLimits(), unreachable_runner)
      assert error.value.code == "UNSAFE_PATH"
  ```

- **Step 2 — ยืนยันว่า test ล้ม:** `python -m pytest -q tests/test_validation.py::test_rejects_symlink_escape`; คาดว่า FAIL เพราะ path validation ยังไม่มี.
- **Step 3 — เพิ่ม implementation ขั้นต่ำ:** resolve source ด้วย `Path.resolve(strict=True)`; ปฏิเสธ path ที่ไม่อยู่ใต้ canonical input root และปฏิเสธเมื่อ input/output roots ซ้อนกัน; เช็ก file size ก่อน probe; validate targets, trim, format, channels และ cumulative caps; เก็บ source SHA-256 ก่อน render ให้ `process_batch` ตรวจซ้ำหลัง render. `config.py` อ่าน TOML แล้ว reject ค่าที่เกิน `AudioLimits` hard caps.
- **VERIFY:** `python -m pytest -q tests/test_validation.py tests/test_limits.py tests/test_config.py`; ตรวจด้วย temp directory ที่สร้าง symlink จริงถ้า OS อนุญาต.
- **Step 4 — ยืนยันว่า tests ผ่าน:** รัน VERIFY ด้านบน รวม path traversal, symlink cases เมื่อ OS รองรับ และ batch duration/bytes.
- **Step 5 — Commit:** `git add src/book_audio_production/validation.py src/book_audio_production/config.py src/book_audio_production/limits.py src/book_audio_production/errors.py .gitignore audio-production.toml.example tests/test_validation.py tests/test_limits.py tests/test_config.py` แล้ว `git commit -m "feat(audio): validate local batches and resource limits"`.

### Task 3 — หุ้ม FFprobe/FFmpeg เป็น process boundary ที่ปลอดภัย

**ไฟล์:** `toolchain.lock.json`, `src/book_audio_production/runner.py`, `src/book_audio_production/probe.py`, `src/book_audio_production/measurements.py`, `tests/test_runner.py`, `tests/test_probe.py`, `tests/test_measurements.py`, `tests/integration/test_ffmpeg_available.py`

- **RED:** ใช้ fake process runner ทดสอบ argv แบบ list, ไม่มี shell, timeout, exit code, stdout/stderr caps, sanitized error และ parsing ที่ทนต่อ FFmpeg log อื่นปน JSON; ทดสอบ NaN/ค่าขาดหายเป็น QA failure.
- **GREEN:** กำหนด interface:

  ```python
  run_process(argv: Sequence[str], timeout_seconds: float, output_limit_bytes: int) -> ProcessResult
  probe_audio(source: Path, runner: CommandRunner) -> ProbeInfo
  parse_loudnorm_measurement(stderr: str) -> LoudnessMeasurement
  parse_ebur128_measurement(stderr: str) -> AudioMeasurement
  ```

- ใช้ `ffprobe -of json`; เรียก executable ที่ค้นพบจาก PATH ด้วย argument array และ `-protocol_whitelist file,pipe`; จำกัดเวลาทุก invocation, drain output แบบ bounded, terminate process เมื่อ timeout, บันทึก version strings และซ่อน raw argv/path จาก public errors. `toolchain.lock.json` เก็บ full version string ที่ Task 0 ตรวจพบและ acceptance tests ใช้; binary ที่ไม่ตรง lock ปฏิเสธด้วย `TOOL_VERSION_MISMATCH`, ไม่มี binary คืน `TOOL_NOT_FOUND` และห้ามดาวน์โหลด/ติดตั้งเอง. Stable tool errors รวม `PROCESS_TIMEOUT`, `PROCESS_FAILED`.
- ให้ `psutil` วัด CPU time ของ FFmpeg child ขณะ process ทำงาน; ถ้าวัดไม่ได้ให้ `cpu_seconds=null` พร้อม `measurement_status=unavailable` แทนการคัดลอก wall time. ค่าที่จำเป็นต่อ target/QA ถ้าขาดต้อง fail ด้วย `MEASUREMENT_UNAVAILABLE`; LRA เป็น `None` ได้เมื่อ filter ไม่คืนค่าและ user ไม่กำหนด LRA target.
- **Step 1 — เขียน failing test:**

  ```python
  import sys
  import pytest
  from book_audio_production.errors import AudioError
  from book_audio_production.runner import run_process

  def test_process_timeout_is_stable_and_sanitized():
      command = [sys.executable, "-c", "import time; time.sleep(1)"]
      with pytest.raises(AudioError) as error:
          run_process(command, 0.01, 1024)
      assert error.value.code == "PROCESS_TIMEOUT"
      assert str(error.value) == "FFmpeg process exceeded its time limit"
  ```

- **Step 2 — ยืนยันว่า test ล้ม:** `python -m pytest -q tests/test_runner.py::test_process_timeout_is_stable_and_sanitized`; คาดว่า FAIL เพราะ runner ยังไม่มี.
- **Step 3 — เพิ่ม implementation ขั้นต่ำ:** เรียก `subprocess.Popen(argv, shell=False)`, drain stdout/stderr ด้วย bounded buffers, ใช้ monotonic deadline, kill+wait เมื่อ timeout และคืนเฉพาะ safe diagnostic; parse JSON block จาก loudnorm log โดยตรวจ finite values และ required fields.
- **Step 4 — ยืนยันว่า tests ผ่าน:** `python -m pytest -q tests/test_runner.py tests/test_probe.py tests/test_measurements.py`; integration probe test ตรวจ binary จริงและ skip เฉพาะเมื่อ executable ขาด.
- **Step 5 — Commit:** `git add toolchain.lock.json src/book_audio_production/runner.py src/book_audio_production/probe.py src/book_audio_production/measurements.py tests/test_runner.py tests/test_probe.py tests/test_measurements.py tests/integration/test_ffmpeg_available.py` แล้ว `git commit -m "feat(audio): add bounded ffmpeg process adapter"`.

### Task 4 — trim, two-pass loudness และ lossless output

**ไฟล์:** `src/book_audio_production/engine.py`, `src/book_audio_production/measurements.py`, `tests/__init__.py`, `tests/audio_fixtures.py`, `tests/test_engine.py`, `tests/integration/test_ffmpeg_pipeline.py`, `tests/fixtures/golden/README.md`

- **Interfaces:** รับ `ValidatedTrack`, คืน staged `TrackResult`; ใช้ `probe_audio` และ `run_process` จาก Task 3.
- **Step 1 — เขียน failing test:**

  ```python
  from book_audio_production.engine import milliseconds_to_samples

  def test_millisecond_trim_uses_sample_rounding():
      assert milliseconds_to_samples(11, 44100) == 485
      assert milliseconds_to_samples(1000, 44100) == 44100
  ```

- **Step 2 — ยืนยันว่า test ล้ม:** `python -m pytest -q tests/test_engine.py::test_millisecond_trim_uses_sample_rounding`; คาดว่า FAIL เพราะ helper ยังไม่มี.
- **Step 3 — เพิ่ม implementation ขั้นต่ำ:**

  ```python
  start_sample = round((trim.start_ms or 0) * probe.sample_rate / 1000)
  end_sample = None if trim.end_ms is None else round(trim.end_ms * probe.sample_rate / 1000)
  filter_prefix = f"atrim=start_sample={start_sample},asetpts=PTS-STARTPTS"
  ```

  วิเคราะห์ trimmed audio ด้วย loudnorm pass แรก; render pass ที่สองโดยส่ง `measured_I`, `measured_LRA`, `measured_TP`, `measured_thresh`, `I`, `TP` และ explicit `LRA` (`50` เมื่อไม่มี target); ใช้ `linear=true` แล้วบันทึก `normalization_type` ที่ FFmpeg รายงานจริง. ตั้ง codec ให้ได้ 24-bit, map เฉพาะ audio stream และ strip metadata.
- ใน `tests/audio_fixtures.py` กำหนด `create_synthetic_audio(path: Path) -> Path` ให้สร้าง tone/noise duration สั้นด้วย FFmpeg argument array; integration tests ใช้ helper นี้ ไม่เก็บ raw fixture binary ใน Git. `tests/fixtures/golden/README.md` ระบุว่า speech sample สำหรับ manual review อยู่ใน local temp/input และไม่ถูก commit.
- **Step 4 — ยืนยันว่า tests ผ่าน:** `python -m pytest -q tests/test_engine.py tests/integration/test_ffmpeg_pipeline.py`; integration suite สร้าง fixture sine/noise ด้วย FFmpeg, decode output กลับมาตรวจ duration/format/channels/loudness/true peak และไม่ skip บน build ที่ใช้ pilot.
- **Step 5 — Commit:** `git add src/book_audio_production/engine.py src/book_audio_production/measurements.py tests/__init__.py tests/audio_fixtures.py tests/test_engine.py tests/integration/test_ffmpeg_pipeline.py tests/fixtures/golden/README.md` แล้ว `git commit -m "feat(audio): render trimmed loudness-normalized masters"`.

### Task 5 — QA, result report และการเผยแพร่ artifact แบบ atomic

**ไฟล์:** `src/book_audio_production/qa.py`, `src/book_audio_production/artifacts.py`, `src/book_audio_production/report.py`, `tests/test_qa.py`, `tests/test_artifacts.py`, `tests/test_report.py`

- **Interfaces:** `render_track` คืน `TrackResult`; `process_batch` คืน `AudioBatchResult`; JSON schemas จาก Task 1 เป็น wire contract.
- **Step 1 — เขียน failing test:**

  ```python
  from book_audio_production.qa import warnings_for_sample_peak

  def test_sample_peak_warning_threshold():
      assert "near_full_scale_peak" in warnings_for_sample_peak(-0.09)
      assert "near_full_scale_peak" not in warnings_for_sample_peak(-0.11)
  ```

- **Step 2 — ยืนยันว่า test ล้ม:** `python -m pytest -q tests/test_qa.py::test_sample_peak_warning_threshold`; คาดว่า FAIL เพราะ QA rule ยังไม่มี.
- เพิ่ม tests ใน `tests/test_qa.py`, `tests/test_artifacts.py`, `tests/test_report.py` ให้ครอบคลุม accepted/rejected/partial status, loudness/true-peak tolerances, existing output collision, size caps, cleanup on QA failure, crash before manifest, hashes และการปฏิเสธ absolute paths/argv/audio payload.
- **Step 3 — เพิ่ม implementation ขั้นต่ำ:** render ไป temp file ใต้ `output_root/<batch_id>/`; หลัง output QA คำนวณ SHA-256 แล้ว `os.replace(temp_path, final_path)` ภายใต้ per-batch lock. หาก final path/manifest มีอยู่, ไม่เขียนทับ. เขียน JSON manifest ไป temp file แล้ว `os.replace(temp_manifest, manifest_path)` เป็น action สุดท้าย; diagnostics ที่ลง JSON เก็บเฉพาะ error code กับ public message.
- รายงาน input/output hash, relative paths, trim, requested+measured IL/LRA/sample peak/true peak, duration, format, bytes, tool/recipe versions, mode, QA, warnings และ wall/CPU time. ให้ `monetary_cost=null` และ `cost_status="unknown"` เมื่อไม่มี rate card.
- **Step 4 — ยืนยันว่า tests ผ่าน:** `python -m pytest -q tests/test_qa.py tests/test_artifacts.py tests/test_report.py`; ทดสอบ crash ก่อน manifest แล้ว `verify_manifest` ต้องไม่รับ orphan output เป็นสำเร็จ.
- **Step 5 — Commit:** `git add src/book_audio_production/qa.py src/book_audio_production/artifacts.py src/book_audio_production/report.py tests/test_qa.py tests/test_artifacts.py tests/test_report.py` แล้ว `git commit -m "feat(audio): verify and publish result manifests atomically"`.

### Task 6 — replay, collision handling และ concurrent duplicate requests

**ไฟล์:** `src/book_audio_production/replay.py`, `src/book_audio_production/locking.py`, `tests/test_replay.py`, `tests/test_locking.py`, `tests/test_concurrency.py`

- **Interfaces:** `process_batch` เรียก replay check ก่อน render; `verify_manifest(path) -> VerificationResult` ตรวจทุก relative output path ว่ายังอยู่ใน batch output root และ hash ตรง.
- **Step 1 — เขียน failing test:**

  ```python
  from concurrent.futures import ProcessPoolExecutor
  import pytest
  from book_audio_production.engine import process_batch
  from book_audio_production.models import AudioBatch, AudioLimits, LoudnessTargets, TrackRequest
  from tests.audio_fixtures import create_synthetic_audio

  def run_batch_in_child_process(arguments):
      return process_batch(*arguments)

  @pytest.fixture
  def valid_run_args(tmp_path):
      input_root = tmp_path / "input"
      output_root = tmp_path / "output"
      input_root.mkdir()
      create_synthetic_audio(input_root / "sample.wav")
      batch = AudioBatch("concurrent-1", (TrackRequest("sample", "sample.wav", None, LoudnessTargets(-16, -1, None), "flac"),))
      return batch, input_root, output_root, AudioLimits()

  def test_two_same_key_processes_publish_once(valid_run_args):
      with ProcessPoolExecutor(max_workers=2) as pool:
          results = list(pool.map(run_batch_in_child_process, [valid_run_args] * 2))
      assert sum(not result.replay for result in results) == 1
      assert sum(result.replay for result in results) == 1
  ```

- **Step 2 — ยืนยันว่า test ล้ม:** `python -m pytest -q tests/test_concurrency.py::test_two_same_key_processes_publish_once`; คาดว่า FAIL เพราะ locking/replay ยังไม่มี.
- เพิ่ม tests ใน `tests/test_replay.py` ให้ source/settings/tool version ที่เปลี่ยนคืน `REPLAY_CONFLICT`, manifest/output ที่ถูกแก้คืน `REPLAY_INTEGRITY_ERROR`, และ exact replay ไม่เรียก FFmpeg; เพิ่ม tests ใน `tests/test_locking.py` ให้ lock ที่มีอยู่คืน `LOCK_BUSY` โดยไม่ลบ lock.
- **Step 3 — เพิ่ม implementation ขั้นต่ำ:** fingerprint canonical JSON ของ source hash, trims, targets, format, recipe/tool versions; เช็ก existing manifest/hash ก่อน lock เพื่อให้ exact replay ผ่านได้, acquire lock ด้วย `os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)`, เช็ก manifest ซ้ำหลังได้ lock แล้วจึงประมวลผล.
- `valid_run_args` สร้าง input/output roots ใน `tmp_path`, เรียก `create_synthetic_audio` จาก Task 4 และคืน `(batch, input_root, output_root, AudioLimits())`; `run_batch_in_child_process(args)` เป็น top-level helper ที่คืน `process_batch(*args)` เพื่อให้ process pool เรียกได้.
- เมื่อ batch ID เดิมมี fingerprint ต่าง ให้ `REPLAY_CONFLICT`; manifest/artifact เสียให้ `REPLAY_INTEGRITY_ERROR`; lock ที่ค้างให้ `LOCK_BUSY` โดยไม่ลบอัตโนมัติ. `batch_id` เปลี่ยนเมื่อแก้ source หรือ settings; replay partial เป็น immutable.
- **Step 4 — ยืนยันว่า tests ผ่าน:** `python -m pytest -q tests/test_replay.py tests/test_locking.py tests/test_concurrency.py`; ใช้ processes จริงในการ concurrency test และยืนยันว่ามี artifact หนึ่งชุด.
- **Step 5 — Commit:** `git add src/book_audio_production/replay.py src/book_audio_production/locking.py tests/test_replay.py tests/test_locking.py tests/test_concurrency.py` แล้ว `git commit -m "feat(audio): make local batch runs replay-safe"`.

### Task 7 — CLI, benchmark, README และ pilot acceptance

**ไฟล์:** `src/book_audio_production/cli.py`, `src/book_audio_production/benchmark.py`, `tests/test_cli.py`, `tests/test_benchmark.py`, `README.md`, `.gitignore`

- **RED:** ทดสอบ CLI help/exit codes, malformed batch, accepted/rejected/partial output, verify manifest/hash และ benchmark counters.
- **GREEN:** เปิดคำสั่ง `book-audio run --manifest PATH --input-root DIR --output-root DIR [--config PATH] [--json]`, `book-audio verify MANIFEST`, และ `book-audio benchmark --output DIR --samples N`. stdout/stderr เป็น sanitized; exit code `0` = accepted/exact replay, `2` = malformed manifest หรือ rejected batch, `3` = `completed_with_errors`, `4` = fatal error ก่อน publish manifest.
- **Interfaces:** `main(argv: Sequence[str] | None = None) -> int`; `verify_manifest(path: Path) -> VerificationResult`; `run_benchmark(output: Path, samples: int, limits: AudioLimits) -> BenchmarkResult`.
- **Step 1 — เขียน failing test:**

  ```python
  from book_audio_production.cli import exit_code_for_status

  def test_status_exit_codes_are_stable():
      assert exit_code_for_status("accepted", published=True) == 0
      assert exit_code_for_status("rejected", published=True) == 2
      assert exit_code_for_status("completed_with_errors", published=True) == 3
      assert exit_code_for_status("rejected", published=False) == 4
  ```

- **Step 2 — ยืนยันว่า test ล้ม:** `python -m pytest -q tests/test_cli.py::test_status_exit_codes_are_stable`; คาดว่า FAIL เพราะ status mapping ยังไม่มี.
- **Step 3 — เพิ่ม implementation ขั้นต่ำ:** parse subcommands/flags ด้วย `argparse`; route `run` ไป `process_batch`, `verify` ไป `verify_manifest`, `benchmark` ไป `run_benchmark`; map statuses เป็น exit codes ข้างต้นและพิมพ์ JSON summary ที่ไม่มี absolute path.
- Benchmark ใช้ synthetic deterministic audio และ fixtures ที่สร้างใหม่ใน temp directory; สรุป accepted/rejected, partial batches, QA failures, replay hits, wall/CPU seconds, bytes, `model_calls=0`, และ monetary cost known/unknown.
- README ครอบคลุม install, ตรวจ FFmpeg/FFprobe, manifest ตัวอย่าง, caps, targets, reports, failure codes, replay, privacy, metadata stripping, manual review และ rollback. `.gitignore` กัน temp outputs, local config, generated audio และ benchmark artifacts.
- **Step 4 — ยืนยันว่า tests ผ่าน:** `python -m pytest -q`, `python -m book_audio_production.cli --help`, integration suite บน FFmpeg version ที่บันทึกไว้ และ benchmark อย่างน้อย 20 synthetic tracks; ฟัง paired source/output จาก consented local speech sample ที่ไม่ commit แล้วบันทึกผล manual QA โดยไม่เก็บเสียงหรือ path ใน telemetry.
- **Step 5 — Commit:** `git add src/book_audio_production/cli.py src/book_audio_production/benchmark.py tests/test_cli.py tests/test_benchmark.py README.md .gitignore` แล้ว `git commit -m "feat(audio): expose batch cli and pilot benchmark"`.

## Release gate และส่งมอบ

- ทุกข้อ acceptance ใน spec ต้องมี test หรือหลักฐาน manual ที่ระบุวิธีทำซ้ำ; numeric tests ไม่ใช้แทนการฟังตรวจ intelligibility.
- Full product checks: `python -m pytest -q`; tests ที่ต้องใช้ FFmpeg ต้องรันจริงก่อนประกาศ pilot-ready และห้ามนับ skipped integration suite เป็นผ่าน.
- ตรวจ output manifest ไม่มี absolute path, FFmpeg argv, raw audio, credentials หรือ user data; ตรวจ source hash ไม่เปลี่ยน; ตรวจ output files ไม่ทับของเดิม.
- สร้าง repository catalog entry เฉพาะเมื่อ remote มีอยู่จริงและ owner/path ยืนยันได้; จากนั้นรัน root `task docs:check`. ไม่ commit product implementation เข้า parent repository.
- ไม่ push, สร้าง hosted repository, เปิด paid provider, deploy หรือส่ง external write จาก implementation plan นี้. ไม่มี deployment target: `deployment_skipped_no_target`.
- เสร็จแล้วรายงาน commit ของ nested product repo, test evidence, FFmpeg build, benchmark counters, manual QA outcome, known gaps และสถานะ catalog/remote.

## หลักฐานการดำเนินงาน ณ 2026-09-24

- Product repository: `bookchaowalit/book-audio-production`, branch `main`, commit `f53440661389982392390a152f99a40e64ced812`; working tree สะอาดและไม่มี remote
- Environment: Python 3.12.10; FFmpeg/FFprobe `9.0.1-full_build-www.gyan.dev`, ตรงกับ `toolchain.lock.json`
- Verification: `.\.venv\Scripts\python.exe -B -m pytest -q` ได้ 142 passed, 1 skipped (host ไม่อนุญาตให้สร้าง symlink); `book-audio --help` ผ่าน
- Synthetic benchmark 20 samples: accepted 21, rejected 2, partial batch 1, QA failures 0, replay hit 1, output 633,297 bytes, wall 12.297s, CPU measurement unavailable, `model_calls=0`, monetary cost unknown
- `audio-v1a.2` synthetic speech source/output สำหรับ manual comparison ผ่าน automated QA 7 checks และ manifest/artifact verification; ยังไม่มี human listening result และยังไม่พิสูจน์ intelligibility ของเสียงพูดจริง
- Product code, test fixtures และ runtime outputs อยู่ใน product repository; ไม่เพิ่ม catalog entry เพราะไม่มี remote; ไม่มี deployment target (`deployment_skipped_no_target`)
- Root `task typecheck` ผ่าน; `task docs:catalog:check`, `task docs:numbers:check` และ `task docs:links:check` ผ่าน. `task test` รันไม่ได้เพราะ Bash/WSL shim ของ WindowsApps เรียก `/bin/bash` ไม่ได้. `task docs:check` ไปถึง `entity-graph:check` แล้วไม่ผ่านจาก spreadsheet `.runtime` manifests และ stale commit entities ที่อยู่นอก scope

## Self-review ก่อนเริ่ม implementation

- ครอบคลุม input contract, path/limits, ffprobe/ffmpeg, trim, two-pass normalization, outputs, measurements, warnings, partial batches, reports, privacy, concurrency, replay, benchmarking และ rollback ตาม spec.
- ทุก task ระบุ files, RED/GREEN, verification command และ commit; signatures ตรงกับ models ที่ระบุและ CLI/output schema versioned.
- ไม่มี placeholder path, unnamed error condition หรือ implicit cost/LLM assumption; ไม่มีงาน product code ใน Solo Empire control plane.
- ข้อมูล environment ที่ยังเปลี่ยนไปตามเครื่องมีเพียง FFmpeg/FFprobe build; บันทึกค่าที่พบใน Task 0 และใช้ build เดียวกันกับ acceptance suite ก่อนประกาศ pilot-ready.
