"""Typed FFmpeg command building and output validation."""

from __future__ import annotations

import subprocess
from pathlib import Path

from ..edl import EDLCapabilityError, EDLDocument, validate_edl
from ..media.ffprobe import FFprobeMetadataReader, MediaProbeError
from ..timeline import TimelinePlanError, loudness_from_operations
from .base import (
    RenderArtifact,
    RenderCapabilityError,
    RenderOutputValidationError,
    RenderProcessError,
)


def _filter_path(path: Path) -> str:
    return str(path).replace("\\", "/").replace("\\", "\\\\").replace(":", r"\:").replace("'", r"\'")


def _seconds(value: float) -> str:
    return f"{value:.3f}"


def build_ffmpeg_argv(
    edl: EDLDocument,
    *,
    input_path: Path,
    output_path: Path,
    caption_path: Path | None = None,
    executable: str = "ffmpeg",
) -> list[str]:
    """Build an argv list; no shell syntax or interpolated command string is used."""

    try:
        validated = validate_edl(edl)
    except EDLCapabilityError as exc:
        raise RenderCapabilityError(str(exc)) from exc
    first = validated.timeline[0]
    if len(validated.timeline) != 1:
        raise RenderCapabilityError("V1B renderer supports one timeline segment")
    if first.visual.kind != "a_roll" or first.visual.asset_refs:
        raise RenderCapabilityError("V1B renderer supports only the source A-roll segment")
    if first.color.operations:
        raise RenderCapabilityError("V1B renderer does not support color operations yet")
    try:
        loudness = loudness_from_operations(list(first.audio.operations))
    except TimelinePlanError as exc:
        raise RenderCapabilityError(f"unsupported audio operation: {exc}") from exc
    width, height = validated.output.width, validated.output.height
    if validated.output.render_profile == "vertical_centered":
        if (
            first.visual.reframe is not None
            and (first.visual.reframe.mode != "center" or first.visual.reframe.aspect_ratio != "9:16")
        ):
            raise RenderCapabilityError("only center 9:16 reframe is supported in V1B")
        filters = ["crop=ih*9/16:ih:(iw-ih*9/16)/2:0", f"scale={width}:{height}"]
    elif first.visual.reframe is not None:
        raise RenderCapabilityError("source render profile cannot include a reframe instruction")
    else:
        # Keep the whole source frame: fit inside the output and letterbox instead of stretching.
        filters = [
            f"scale={width}:{height}:force_original_aspect_ratio=decrease",
            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2",
            "setsar=1",
        ]
    if caption_path is not None:
        caption_format = first.captions.format
        filters.append(f"subtitles='{_filter_path(caption_path)}'")
        if caption_format not in {"srt", "ass"}:
            raise RenderCapabilityError(f"unsupported caption format: {caption_format}")
    duration = first.source.end - first.source.start
    # Single-pass EBU R128 loudnorm upsamples internally, so resample back to 48 kHz.
    audio_filters = (
        ["-af", loudness.ffmpeg_filter(), "-ar", str(loudness.output_sample_rate)]
        if loudness is not None
        else []
    )
    return [
        executable,
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-ss",
        _seconds(first.source.start),
        "-i",
        str(input_path),
        "-t",
        _seconds(duration),
        "-map",
        "0:v:0",
        "-map",
        "0:a?",
        "-vf",
        ",".join(filters),
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        *audio_filters,
        "-c:a",
        "aac",
        "-movflags",
        "+faststart",
        str(output_path),
    ]


_FILTER_OPTIONS = frozenset({"-vf", "-af"})


def summarize_ffmpeg_argv(argv: list[str]) -> dict[str, object]:
    """Structural view of an FFmpeg argv for the trace: options and filters, never paths."""

    inputs = 0
    maps: list[str] = []
    options: dict[str, object] = {}
    position = 1
    while position < len(argv):
        item = argv[position]
        following = argv[position + 1] if position + 1 < len(argv) else None
        if item == "-i":
            inputs += 1
            position += 2
        elif item in _FILTER_OPTIONS and following is not None:
            options[item.lstrip("-")] = [
                "subtitles=<captions>" if part.startswith("subtitles=") else part
                for part in following.split(",")
            ]
            position += 2
        elif item == "-map" and following is not None:
            maps.append(following)
            position += 2
        elif item.startswith("-") and following is not None and not following.startswith("-"):
            options[item.lstrip("-")] = following
            position += 2
        elif item.startswith("-"):
            options[item.lstrip("-")] = True
            position += 1
        else:
            position += 1  # the output path
    return {"executable": "ffmpeg", "inputs": inputs, "map": maps, "options": options}


class FFmpegRenderEngine:
    """Run FFmpeg with timeout and require an MP4/video output contract."""

    def __init__(
        self,
        executable: str = "ffmpeg",
        probe_reader: FFprobeMetadataReader | None = None,
        timeout_seconds: int = 900,
    ) -> None:
        self.executable = executable
        self.probe_reader = probe_reader or FFprobeMetadataReader()
        self.timeout_seconds = timeout_seconds

    @staticmethod
    def _diagnostics(stderr: str, paths: tuple[Path, ...]) -> str:
        value = stderr or ""
        for path in paths:
            value = value.replace(str(path), "<media>")
        return value.replace("\\", "/")[:1000]

    def render(
        self,
        edl: EDLDocument,
        *,
        input_path: Path,
        output_path: Path,
        caption_path: Path | None = None,
    ) -> RenderArtifact:
        argv = build_ffmpeg_argv(
            edl,
            input_path=input_path,
            output_path=output_path,
            caption_path=caption_path,
            executable=self.executable,
        )
        try:
            result = subprocess.run(
                argv,
                capture_output=True,
                check=False,
                shell=False,
                text=True,
                timeout=self.timeout_seconds,
            )
        except FileNotFoundError as exc:
            raise RenderProcessError("ffmpeg_unavailable", "The render engine is unavailable") from exc
        except subprocess.TimeoutExpired as exc:
            raise RenderProcessError("ffmpeg_timeout", "The render operation timed out") from exc
        except OSError as exc:
            raise RenderProcessError("ffmpeg_failed", "The render operation failed") from exc
        if result.returncode != 0:
            raise RenderProcessError(
                "ffmpeg_failed",
                "The render operation failed",
                self._diagnostics(result.stderr, (input_path, output_path)),
            )
        if not output_path.is_file():
            raise RenderOutputValidationError("render_output_missing", "The render output was not created")
        try:
            metadata = self.probe_reader.read(output_path)
        except MediaProbeError as exc:
            raise RenderOutputValidationError("render_output_unreadable", "The render output is not readable", exc.diagnostics) from exc
        if not metadata.format_name or "mp4" not in metadata.format_name.split(","):
            raise RenderOutputValidationError("render_output_format_invalid", "The render output is not an MP4")
        if metadata.video.width != edl.output.width or metadata.video.height != edl.output.height:
            raise RenderOutputValidationError("render_output_dimensions_invalid", "The render output dimensions are invalid")
        return RenderArtifact(duration=metadata.duration, metadata=metadata)
