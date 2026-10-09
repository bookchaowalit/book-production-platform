"""Replace a video's audio with a mastered track (audio.master -> video).

The video stream is copied unchanged and its length is authoritative: the
mastered WAV is encoded to AAC, padded with silence when it is shorter and cut
when it is longer (stream-copied video can only be cut at keyframes, so the
video is never trimmed). The result is verified with ffprobe (one video
stream, one AAC audio stream, duration equal to the source video) and
described by a ``video-remux.v1`` manifest next to the output so downstream
tools such as the Production Hub can ingest it with sha256 lineage.

House rules match ``audio_extract``: typed argv, ``shell=False``, bounded
timeouts, stable error codes, and atomic publication of the manifest.
"""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import job_trace
from .audio_extract import ExtractionError, _probe, _run_tool, safe_pipeline_id

RECIPE = "replace-audio.v1"
TOOL_ID = "video-studio.replace-audio"
MANIFEST_NAME = "video-remux.json"
DURATION_TOLERANCE_SECONDS = 0.5
_DEFAULT_TIMEOUT_SECONDS = 600.0


@dataclass(frozen=True)
class ReplaceResult:
    video_path: Path
    manifest_path: Path
    duration_seconds: float


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _streams(probe: dict[str, Any], codec_type: str) -> list[dict[str, Any]]:
    return [stream for stream in probe.get("streams", []) if stream.get("codec_type") == codec_type]


def _duration(probe: dict[str, Any]) -> float:
    try:
        return float(probe["format"]["duration"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ExtractionError("media_probe_failed", "media duration is unavailable") from exc


def replace_audio(
    video: str | Path,
    audio: str | Path,
    output_dir: str | Path,
    *,
    remux_id: str | None = None,
    timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
) -> ReplaceResult:
    """Remux and record ``trace.json`` + ``MANUAL-STEPS.md`` + ``history.jsonl`` in ``output_dir``."""

    video_path, out_dir = Path(video), Path(output_dir)
    identifier = safe_pipeline_id(remux_id or f"{video_path.stem}-remastered")
    trace = job_trace.new_recorder(TOOL_ID, str(uuid.uuid4()), RECIPE)
    try:
        result, guide = _replace(video_path, Path(audio), out_dir, identifier, trace, timeout_seconds=timeout_seconds)
    except ExtractionError as exc:
        out_dir.mkdir(parents=True, exist_ok=True)
        job_trace.finish(trace, status="failed", out_dir=out_dir, note=exc.code)
        raise
    job_trace.finish(trace, status="accepted", out_dir=out_dir, guide=guide)
    return result


def _replace(
    video_path: Path,
    audio_path: Path,
    out_dir: Path,
    identifier: str,
    trace: Any,
    *,
    timeout_seconds: float,
) -> tuple[ReplaceResult, Any]:
    trace.record_step("validate", "check_inputs", remux_id=identifier)
    for label, path in (("video", video_path), ("audio", audio_path)):
        if not path.is_file():
            raise ExtractionError("input_missing", f"{label} input not found: {path.name}")
    if job_trace.trace_available():
        trace.input_file("source_video", video_path)
        trace.input_file("mastered_audio", audio_path, role="audio")
    with trace.step("probe", "ffprobe_inputs") as probe_step:
        video_probe = _probe(video_path, timeout_seconds)
        audio_probe = _probe(audio_path, timeout_seconds)
        facts = job_trace.video_facts(video_probe)
        audio_seconds = job_trace.video_facts(audio_probe).duration
        probe_step.params = {**job_trace.probe_params(facts), "audio_duration_s": round(audio_seconds, 6)}
    if not _streams(video_probe, "video"):
        raise ExtractionError("video_stream_missing", f"{video_path.name} has no video stream")
    if not _streams(audio_probe, "audio"):
        raise ExtractionError("audio_stream_missing", f"{audio_path.name} has no audio stream")
    expected = _duration(video_probe)

    out_dir.mkdir(parents=True, exist_ok=True)
    output = out_dir / f"{identifier}.mp4"
    partial = out_dir / f".{identifier}.partial.mp4"
    argv = [
        "ffmpeg", "-y", "-v", "error", "-i", str(video_path), "-i", str(audio_path),
        "-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy", "-af", "apad", "-c:a", "aac", "-b:a", "192k",
        "-shortest", "-movflags", "+faststart", str(partial),
    ]
    with trace.step("render", "ffmpeg_remux", **job_trace.summarize_argv(argv)) as render_step:
        result = _run_tool(argv, timeout_seconds, code_prefix="audio_replace")
        if result.returncode != 0:
            partial.unlink(missing_ok=True)
            raise ExtractionError("audio_replace_failed", "ffmpeg could not remux the mastered audio")
        render_step.outputs = [output.name]

    output_probe = _probe(partial, timeout_seconds)
    audio_streams = _streams(output_probe, "audio")
    duration = _duration(output_probe)
    checks = {
        "one_video_stream": len(_streams(output_probe, "video")) == 1,
        "one_aac_audio_stream": len(audio_streams) == 1 and audio_streams[0].get("codec_name") == "aac",
        "duration_matches_video": abs(duration - expected) <= DURATION_TOLERANCE_SECONDS,
    }
    trace.record_step("qa", "probe_output", checks=checks, duration_s=round(duration, 6))
    if not all(checks.values()):
        partial.unlink(missing_ok=True)
        failed = ", ".join(name for name, passed in checks.items() if not passed)
        raise ExtractionError("audio_replace_qa_failed", f"remuxed output failed QA: {failed}")
    os.replace(partial, output)

    manifest = {
        "schema_version": "video-remux.v1",
        "remux_id": identifier,
        "status": "accepted",
        "recipe": RECIPE,
        "source_video": {"filename": video_path.name, "sha256": _sha256(video_path)},
        "source_audio": {"filename": audio_path.name, "sha256": _sha256(audio_path)},
        "output": {
            "filename": output.name,
            "media_type": "video/mp4",
            "sha256": _sha256(output),
            "byte_size": output.stat().st_size,
            "duration_seconds": round(duration, 3),
        },
        "qa": {"passed": True, "checks": checks, "expected_duration_seconds": round(expected, 3)},
    }
    manifest_path = out_dir / MANIFEST_NAME
    staging = out_dir / f".{MANIFEST_NAME}.partial"
    staging.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(staging, manifest_path)
    trace.record_step("publish", "write_manifest", outputs=[output.name, MANIFEST_NAME])
    trace.artifact(output, media_type="video/mp4")
    trace.artifact(manifest_path, media_type="application/json", role="manifest")
    guide = job_trace.replace_audio_guide(
        facts=facts,
        audio_duration=audio_seconds,
        video_name=video_path.name,
        audio_name=audio_path.name,
        output_name=output.name,
    ) if facts.width and facts.height else None
    return ReplaceResult(output, manifest_path, duration), guide
