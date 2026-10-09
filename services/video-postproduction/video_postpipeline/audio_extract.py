"""FFmpeg-based video to audio extraction ready for the audio master pipeline.

House rules: ffmpeg/ffprobe run with typed argv, ``shell=False``, bounded
timeouts and stable error codes. When ``--format wav`` is used the extractor
writes 24-bit PCM WAV (``wav_pcm24``) and publishes an ``audio-batch.v1``
manifest validated against a fixture copy of the ``book-audio-production``
input contract (the nested tool owns the original; this service only
consumes it). ``--format mp3`` produces a convenience preview only: the audio
batch contract accepts ``flac`` and ``wav_pcm24`` exclusively, so no manifest
is published for MP3 output.

Loudness targets follow the audio production tool's documented example batch:
``-16.0 integrated_lufs`` and ``-1.0 true_peak_dbtp``; the master pipeline
performs the actual normalization.
"""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator
from pydantic import BaseModel, ConfigDict, Field

from .paths import SCHEMAS_DIR

AUDIO_BATCH_SCHEMA_PATH = SCHEMAS_DIR / "audio-batch.v1.schema.json"
DEFAULT_INTEGRATED_LUFS = -16.0
DEFAULT_TRUE_PEAK_DBTP = -1.0
_DEFAULT_TIMEOUT_SECONDS = 120.0
_SAFE_ID_PATTERN = re.compile(r"[^A-Za-z0-9_-]+")


class ExtractionError(RuntimeError):
    """Raised when extraction cannot produce a verified audio artifact."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code


class AudioBatchManifest(BaseModel):
    """Typed view of the consumed ``audio-batch.v1`` input contract."""

    model_config = ConfigDict(extra="forbid")

    schema_version: str
    batch_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")
    tracks: list[dict[str, Any]] = Field(min_length=1, max_length=20)


@dataclass(frozen=True)
class ExtractResult:
    audio_path: Path
    manifest_path: Path | None
    codec_name: str
    duration_seconds: float


def _run_tool(argv: list[str], timeout: float, *, code_prefix: str) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(argv, capture_output=True, check=False, shell=False, text=True, timeout=timeout)
    except FileNotFoundError as exc:
        raise ExtractionError(f"{code_prefix}_missing", f"tool not found on PATH: {argv[0]}") from exc
    except subprocess.TimeoutExpired as exc:
        raise ExtractionError(f"{code_prefix}_timeout", f"{argv[0]} exceeded {timeout} seconds") from exc
    except OSError as exc:
        raise ExtractionError(f"{code_prefix}_failed", f"{argv[0]} could not be executed") from exc


def _probe(path: Path, timeout: float) -> dict[str, Any]:
    argv = ["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)]
    result = _run_tool(argv, timeout, code_prefix="media_probe")
    if result.returncode != 0:
        raise ExtractionError("media_probe_failed", f"ffprobe could not inspect {path.name}")
    try:
        payload: Any = json.loads(result.stdout)
    except (TypeError, json.JSONDecodeError) as exc:
        raise ExtractionError("media_probe_invalid_output", "ffprobe returned invalid JSON") from exc
    if not isinstance(payload, dict):
        raise ExtractionError("media_probe_invalid_output", "ffprobe returned unexpected JSON")
    probe: dict[str, Any] = payload
    return probe


def _audio_stream(probe: dict[str, Any], *, label: str) -> dict[str, Any]:
    streams = [stream for stream in probe.get("streams", []) if stream.get("codec_type") == "audio"]
    if not streams:
        raise ExtractionError("no_audio_stream", f"{label} has no audio stream")
    stream: dict[str, Any] = streams[0]
    return stream


def _duration_seconds(probe: dict[str, Any]) -> float:
    raw = probe.get("format", {}).get("duration")
    try:
        return float(raw)
    except (TypeError, ValueError):
        return 0.0


def safe_pipeline_id(raw: str) -> str:
    """Derive a deterministic ``[A-Za-z0-9_-]{1,64}`` identifier from any stem."""

    cleaned = _SAFE_ID_PATTERN.sub("-", raw).strip("-") or "track"
    return cleaned[:64]


def _validate_audio_batch_payload(payload: dict[str, Any]) -> None:
    schema = json.loads(AUDIO_BATCH_SCHEMA_PATH.read_text(encoding="utf-8"))
    validator = Draft202012Validator(schema)
    errors = sorted(validator.iter_errors(payload), key=lambda error: str(error.absolute_path))
    if errors:
        first = errors[0]
        path = ".".join(str(part) for part in first.absolute_path) or "$"
        raise ExtractionError("audio_batch_manifest_invalid", f"{path}: {first.message}")


def build_audio_batch_manifest(*, batch_id: str, track_id: str, source_name: str) -> dict[str, Any]:
    """Build an ``audio-batch.v1`` payload for one extracted WAV track."""

    return {
        "schema_version": "audio-batch.v1",
        "batch_id": batch_id,
        "tracks": [
            {
                "track_id": track_id,
                "source": source_name,
                "targets": {
                    "integrated_lufs": DEFAULT_INTEGRATED_LUFS,
                    "true_peak_dbtp": DEFAULT_TRUE_PEAK_DBTP,
                    "lra_lu": None,
                },
                "output_format": "wav_pcm24",
            }
        ],
    }


def extract_audio(
    video_path: str | Path,
    output_dir: str | Path,
    *,
    audio_format: str = "wav",
    batch_id: str | None = None,
    track_id: str | None = None,
    timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
) -> ExtractResult:
    """Extract the first audio stream of ``video_path`` into ``output_dir``."""

    source = Path(video_path)
    if not source.is_file():
        raise ExtractionError("input_missing", f"input video does not exist: {source.name}")
    if audio_format not in {"wav", "mp3"}:
        raise ExtractionError("unsupported_format", f"unsupported audio format: {audio_format}")
    if timeout_seconds <= 0:
        raise ExtractionError("invalid_timeout", "timeout_seconds must be positive")

    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    stem = safe_pipeline_id(source.stem)
    track = safe_pipeline_id(track_id or stem)
    batch = safe_pipeline_id(batch_id or stem)
    extension = ".wav" if audio_format == "wav" else ".mp3"
    target = output_path / f"{track}{extension}"

    codec_args = ["-acodec", "pcm_s24le"] if audio_format == "wav" else ["-acodec", "libmp3lame", "-q:a", "2"]
    argv = ["ffmpeg", "-y", "-i", str(source), "-map", "0:a:0", "-vn", *codec_args, str(target)]
    result = _run_tool(argv, timeout_seconds, code_prefix="ffmpeg")
    if result.returncode != 0:
        raise ExtractionError("ffmpeg_failed", f"ffmpeg could not extract audio from {source.name}")
    if not target.is_file() or target.stat().st_size == 0:
        raise ExtractionError("ffmpeg_failed", "ffmpeg reported success but produced no audio file")

    probe = _probe(target, timeout_seconds)
    stream = _audio_stream(probe, label=target.name)
    codec_name = str(stream.get("codec_name", ""))
    expected_codec = "pcm_s24le" if audio_format == "wav" else "mp3"
    if codec_name != expected_codec:
        raise ExtractionError("codec_mismatch", f"expected {expected_codec}, found {codec_name}")

    manifest_path: Path | None = None
    if audio_format == "wav":
        manifest = build_audio_batch_manifest(batch_id=batch, track_id=track, source_name=target.name)
        _validate_audio_batch_payload(manifest)
        manifest_path = output_path / "audio-batch.v1.json"
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")

    return ExtractResult(audio_path=target, manifest_path=manifest_path, codec_name=codec_name, duration_seconds=_duration_seconds(probe))
