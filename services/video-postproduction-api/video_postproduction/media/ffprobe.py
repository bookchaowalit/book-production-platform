"""Safe, typed invocation and parsing of FFprobe metadata."""

from __future__ import annotations

import json
import math
import subprocess
from pathlib import Path
from typing import Any

from .models import AudioStreamMetadata, MediaMetadata, VideoStreamMetadata


class MediaProbeError(RuntimeError):
    """A safe, typed error returned when media inspection cannot complete."""

    def __init__(self, code: str, safe_message: str, diagnostics: str = "") -> None:
        self.code = code
        self.safe_message = safe_message
        self.diagnostics = diagnostics
        super().__init__(safe_message)


class FFprobeMetadataReader:
    """Read only the expected FFprobe JSON fields with no shell execution."""

    def __init__(self, executable: str = "ffprobe", timeout_seconds: int = 30) -> None:
        self.executable = executable
        self.timeout_seconds = timeout_seconds

    @staticmethod
    def _safe_diagnostics(stderr: str, media_path: Path, limit: int = 500) -> str:
        diagnostics = stderr or ""
        for path_text in {str(media_path), str(media_path.resolve(strict=False))}:
            diagnostics = diagnostics.replace(path_text, "<media>")
        diagnostics = diagnostics.replace("\\", "/")
        return diagnostics[:limit]

    @staticmethod
    def _number(value: Any, field_name: str, *, integer: bool = False) -> float | int | None:
        if value is None or value == "":
            return None
        try:
            parsed = float(value)
        except (TypeError, ValueError) as exc:
            raise MediaProbeError("media_probe_invalid_output", f"FFprobe returned invalid {field_name}") from exc
        if not math.isfinite(parsed):
            raise MediaProbeError("media_probe_invalid_output", f"FFprobe returned invalid {field_name}")
        return int(parsed) if integer else parsed

    @staticmethod
    def _fps(value: Any) -> float | None:
        if value in (None, "", "0/0"):
            return None
        if isinstance(value, str) and "/" in value:
            numerator, denominator = value.split("/", 1)
            try:
                denominator_value = float(denominator)
                if denominator_value == 0:
                    return None
                return float(numerator) / denominator_value
            except ValueError as exc:
                raise MediaProbeError("media_probe_invalid_output", "FFprobe returned invalid frame rate") from exc
        parsed = FFprobeMetadataReader._number(value, "frame rate")
        return float(parsed) if parsed is not None else None

    @staticmethod
    def _parse_payload(payload: Any) -> MediaMetadata:
        if not isinstance(payload, dict):
            raise MediaProbeError("media_probe_invalid_output", "FFprobe returned an invalid payload")
        format_data = payload.get("format")
        streams = payload.get("streams")
        if not isinstance(format_data, dict) or not isinstance(streams, list):
            raise MediaProbeError("media_probe_invalid_output", "FFprobe returned incomplete metadata")

        video_data = next((item for item in streams if isinstance(item, dict) and item.get("codec_type") == "video"), None)
        if video_data is None:
            raise MediaProbeError("media_probe_no_video", "The uploaded file does not contain a readable video stream")
        audio_data = next((item for item in streams if isinstance(item, dict) and item.get("codec_type") == "audio"), None)

        duration = FFprobeMetadataReader._number(format_data.get("duration"), "duration")
        if duration is None:
            raise MediaProbeError("media_probe_invalid_output", "FFprobe did not return a media duration")

        size = FFprobeMetadataReader._number(format_data.get("size"), "size", integer=True)
        bitrate = FFprobeMetadataReader._number(format_data.get("bit_rate"), "bit rate", integer=True)
        video = VideoStreamMetadata(
            codec_name=video_data.get("codec_name"),
            width=int(video_data.get("width") or 0),
            height=int(video_data.get("height") or 0),
            fps=FFprobeMetadataReader._fps(video_data.get("avg_frame_rate")),
            pix_fmt=video_data.get("pix_fmt"),
        )

        audio = None
        if isinstance(audio_data, dict):
            sample_rate = FFprobeMetadataReader._number(audio_data.get("sample_rate"), "sample rate", integer=True)
            channels = FFprobeMetadataReader._number(audio_data.get("channels"), "channels", integer=True)
            audio = AudioStreamMetadata(
                codec_name=audio_data.get("codec_name"),
                sample_rate=int(sample_rate) if sample_rate is not None else None,
                channels=int(channels) if channels is not None else None,
            )

        return MediaMetadata(
            duration=float(duration),
            format_name=format_data.get("format_name"),
            size_bytes=int(size) if size is not None else None,
            bitrate=int(bitrate) if bitrate is not None else None,
            video=video,
            audio=audio,
        )

    def read(self, media_path: str | Path) -> MediaMetadata:
        """Run FFprobe with a typed argv and parse a bounded safe result."""

        path = Path(media_path)
        argv = [
            self.executable,
            "-v",
            "error",
            "-print_format",
            "json",
            "-show_format",
            "-show_streams",
            str(path),
        ]
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
            raise MediaProbeError("ffprobe_unavailable", "Media inspection is unavailable") from exc
        except subprocess.TimeoutExpired as exc:
            raise MediaProbeError("media_probe_timeout", "Media inspection timed out") from exc
        except OSError as exc:
            raise MediaProbeError("media_probe_failed", "Media inspection failed") from exc

        if result.returncode != 0:
            diagnostics = self._safe_diagnostics(result.stderr, path)
            raise MediaProbeError("media_probe_failed", "Media inspection failed", diagnostics)
        try:
            payload = json.loads(result.stdout)
        except (TypeError, json.JSONDecodeError) as exc:
            raise MediaProbeError("media_probe_invalid_output", "Media inspection returned invalid JSON") from exc
        return self._parse_payload(payload)
