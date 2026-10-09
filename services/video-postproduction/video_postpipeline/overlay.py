"""Burn a graphic (logo, lower third, watermark) onto a video (graphic.png -> video).

The image is scaled to a fraction of the video width and placed in a corner
or across the bottom with a margin; the video is re-encoded (H.264, CRF 20)
because an overlay changes every frame, and the audio stream is copied. The
result is checked with ffprobe and described by a ``video-overlay.v1``
manifest with sha256 of the source video, the image, and the output.
"""

from __future__ import annotations

import json
import os
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import job_trace
from .audio_extract import ExtractionError, _probe, _run_tool, safe_pipeline_id
from .audio_replace import DURATION_TOLERANCE_SECONDS, _duration, _sha256, _streams

RECIPE = "overlay-graphic.v1"
TOOL_ID = "video-studio.overlay"
MANIFEST_NAME = "video-overlay.json"
POSITIONS = {
    "top-left": ("{m}", "{m}"),
    "top-right": ("main_w-overlay_w-{m}", "{m}"),
    "bottom-left": ("{m}", "main_h-overlay_h-{m}"),
    "bottom-right": ("main_w-overlay_w-{m}", "main_h-overlay_h-{m}"),
    "bottom-center": ("(main_w-overlay_w)/2", "main_h-overlay_h-{m}"),
}
_DEFAULT_TIMEOUT_SECONDS = 1800.0


@dataclass(frozen=True)
class OverlayResult:
    video_path: Path
    manifest_path: Path
    duration_seconds: float


def overlay_graphic(
    video: str | Path,
    image: str | Path,
    output_dir: str | Path,
    *,
    position: str = "bottom-right",
    width_ratio: float = 0.2,
    margin_px: int = 24,
    overlay_id: str | None = None,
    timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
) -> OverlayResult:
    """Burn the graphic and record ``trace.json`` + ``MANUAL-STEPS.md`` + ``history.jsonl`` in ``output_dir``."""

    video_path, out_dir = Path(video), Path(output_dir)
    identifier = safe_pipeline_id(overlay_id or f"{video_path.stem}-overlay")
    trace = job_trace.new_recorder(TOOL_ID, str(uuid.uuid4()), RECIPE)
    try:
        result, guide = _overlay(
            video_path, Path(image), out_dir, identifier, trace,
            position=position, width_ratio=width_ratio, margin_px=margin_px, timeout_seconds=timeout_seconds,
        )
    except ExtractionError as exc:
        out_dir.mkdir(parents=True, exist_ok=True)
        job_trace.finish(trace, status="failed", out_dir=out_dir, note=exc.code)
        raise
    job_trace.finish(trace, status="accepted", out_dir=out_dir, guide=guide)
    return result


def _overlay(
    video_path: Path,
    image_path: Path,
    out_dir: Path,
    identifier: str,
    trace: Any,
    *,
    position: str,
    width_ratio: float,
    margin_px: int,
    timeout_seconds: float,
) -> tuple[OverlayResult, Any]:
    trace.record_step(
        "validate", "check_placement", overlay_id=identifier, position=position, width_ratio=width_ratio, margin_px=margin_px
    )
    if position not in POSITIONS:
        raise ExtractionError("overlay_position_invalid", f"position must be one of {', '.join(POSITIONS)}")
    if not 0.05 <= width_ratio <= 1.0:
        raise ExtractionError("overlay_scale_invalid", "width ratio must be between 0.05 and 1.0")
    if not 0 <= margin_px <= 400:
        raise ExtractionError("overlay_margin_invalid", "margin must be between 0 and 400 px")
    for label, path in (("video", video_path), ("image", image_path)):
        if not path.is_file():
            raise ExtractionError("input_missing", f"{label} input not found: {path.name}")
    if job_trace.trace_available():
        trace.input_file("source_video", video_path)
        trace.input_file("graphic", image_path, role="graphic")
    with trace.step("probe", "ffprobe_inputs") as probe_step:
        probe = _probe(video_path, timeout_seconds)
        image_probe = _probe(image_path, timeout_seconds)
        facts = job_trace.video_facts(probe)
        image_facts = job_trace.video_facts(image_probe)
        probe_step.params = {**job_trace.probe_params(facts), "image_width": image_facts.width, "image_height": image_facts.height}
    video_streams = _streams(probe, "video")
    if not video_streams:
        raise ExtractionError("video_stream_missing", f"{video_path.name} has no video stream")
    width = int(video_streams[0].get("width") or 0)
    if width <= 0:
        raise ExtractionError("media_probe_failed", "video width is unavailable")
    target_width = max(2, int(width * width_ratio) // 2 * 2)
    x, y = (part.format(m=margin_px) for part in POSITIONS[position])
    # A still image is a one-frame stream; overlay repeats its last frame until
    # the main video ends (eof_action=repeat), so no -loop is needed and the
    # output length follows the video.
    filters = f"[1:v]scale={target_width}:-2[logo];[0:v][logo]overlay={x}:{y}:format=auto:eof_action=repeat[out]"

    out_dir.mkdir(parents=True, exist_ok=True)
    output = out_dir / f"{identifier}.mp4"
    partial = out_dir / f".{identifier}.partial.mp4"
    has_audio = bool(_streams(probe, "audio"))
    argv = [
        "ffmpeg", "-y", "-v", "error", "-i", str(video_path), "-i", str(image_path),
        "-filter_complex", filters, "-map", "[out]", *(["-map", "0:a:0", "-c:a", "copy"] if has_audio else []),
        "-c:v", "libx264", "-crf", "20", "-preset", "medium", "-pix_fmt", "yuv420p",
        "-movflags", "+faststart", str(partial),
    ]
    with trace.step("render", "ffmpeg_overlay", **job_trace.summarize_argv(argv)) as render_step:
        result = _run_tool(argv, timeout_seconds, code_prefix="overlay")
        if result.returncode != 0:
            partial.unlink(missing_ok=True)
            raise ExtractionError("overlay_failed", "ffmpeg could not overlay the graphic")
        render_step.outputs = [output.name]

    output_probe = _probe(partial, timeout_seconds)
    duration = _duration(output_probe)
    checks: dict[str, Any] = {
        "one_video_stream": len(_streams(output_probe, "video")) == 1,
        "audio_preserved": len(_streams(output_probe, "audio")) == (1 if has_audio else 0),
        "duration_matches_video": abs(duration - _duration(probe)) <= DURATION_TOLERANCE_SECONDS,
    }
    trace.record_step("qa", "probe_output", checks=checks, duration_s=round(duration, 6))
    if not all(checks.values()):
        partial.unlink(missing_ok=True)
        failed = ", ".join(name for name, passed in checks.items() if not passed)
        raise ExtractionError("overlay_qa_failed", f"overlaid output failed QA: {failed}")
    os.replace(partial, output)
    manifest = {
        "schema_version": "video-overlay.v1",
        "overlay_id": identifier,
        "status": "accepted",
        "recipe": RECIPE,
        "placement": {"position": position, "width_ratio": width_ratio, "margin_px": margin_px},
        "source_video": {"filename": video_path.name, "sha256": _sha256(video_path)},
        "source_image": {"filename": image_path.name, "sha256": _sha256(image_path)},
        "output": {
            "filename": output.name, "media_type": "video/mp4", "sha256": _sha256(output),
            "byte_size": output.stat().st_size, "duration_seconds": round(duration, 3),
        },
        "qa": {"passed": True, "checks": checks},
    }
    manifest_path = out_dir / MANIFEST_NAME
    staging = out_dir / f".{MANIFEST_NAME}.partial"
    staging.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(staging, manifest_path)
    trace.record_step("publish", "write_manifest", outputs=[output.name, MANIFEST_NAME])
    trace.artifact(output, media_type="video/mp4")
    trace.artifact(manifest_path, media_type="application/json", role="manifest")
    guide = job_trace.overlay_guide(
        facts=facts,
        video_name=video_path.name,
        image_name=image_path.name,
        image_size=(image_facts.width, image_facts.height),
        placement=job_trace.overlay_placement(
            facts, (image_facts.width, image_facts.height), target_width=target_width, position=position, margin=margin_px
        ),
        position=position,
        output_name=output.name,
    ) if image_facts.width and image_facts.height and facts.width and facts.height else None
    return OverlayResult(output, manifest_path, duration), guide
