"""One timeline plan, derived from a validated EDL, shared by every consumer.

The FFmpeg renderer, the manual guide (``MANUAL-STEPS.md``), and the
interchange exports (CMX3600, FCPXML, OpenTimelineIO) all read positions from
:class:`TimelinePlan`, so a person following the guide or opening the export
in a real editor sees exactly the cuts, record positions, reframe, captions,
and loudness the engine used.

Timecode rules (match what an NLE shows):

* ``HH:MM:SS.mmm`` is the EDL's exact seconds.
* ``HH:MM:SS:FF`` is frame-based at the timeline rate: source in/out frames
  are ``round(seconds x fps)`` (half up) and record positions are the running
  sum of clip frame lengths, as an editor lays clips end to end.
* Fractional NTSC rates (23.976, 29.97, 59.94) are labelled with their
  nominal integer rate, non-drop-frame.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from decimal import ROUND_HALF_UP, Decimal
from fractions import Fraction
from typing import Any

from .edl import EDLDocument

LOUDNESS_OPERATION = "loudness_normalize"
LOUDNESS_LIMITS = {
    "integrated_lufs": (-31.0, -5.0),
    "true_peak_dbtp": (-9.0, 0.0),
    "loudness_range_lu": (1.0, 20.0),
}
LOUDNESS_DEFAULTS = {"integrated_lufs": -14.0, "true_peak_dbtp": -1.0, "loudness_range_lu": 11.0}
_NTSC_RATES = {24: Fraction(24000, 1001), 30: Fraction(30000, 1001), 60: Fraction(60000, 1001)}


class TimelinePlanError(ValueError):
    """The EDL cannot be expressed as a timeline plan (structure, not content)."""


@dataclass(frozen=True, slots=True)
class SourceVideo:
    """Probe facts about the source (from the ingest probe; never transcript text)."""

    filename: str
    width: int | None = None
    height: int | None = None
    fps: float | None = None
    duration: float | None = None
    video_codec: str | None = None
    pix_fmt: str | None = None
    has_audio: bool | None = None
    audio_codec: str | None = None
    sample_rate: int | None = None
    channels: int | None = None
    size_bytes: int | None = None
    sha256: str | None = None

    @classmethod
    def from_metadata(
        cls,
        filename: str,
        metadata: Mapping[str, Any] | None,
        *,
        size_bytes: int | None = None,
        sha256: str | None = None,
    ) -> SourceVideo:
        raw = dict(metadata or {})
        video = raw.get("video") if isinstance(raw.get("video"), Mapping) else {}
        audio = raw.get("audio") if isinstance(raw.get("audio"), Mapping) else None
        assert isinstance(video, Mapping)

        def _int(value: Any) -> int | None:
            try:
                number = int(value)
            except (TypeError, ValueError):
                return None
            return number if number > 0 else None

        def _float(value: Any) -> float | None:
            try:
                number = float(value)
            except (TypeError, ValueError):
                return None
            return number if math.isfinite(number) and number > 0 else None

        has_audio: bool | None
        if "audio" in raw:
            has_audio = audio is not None
        else:
            has_audio = None
        return cls(
            filename=filename,
            width=_int(video.get("width")),
            height=_int(video.get("height")),
            fps=_float(video.get("fps")),
            duration=_float(raw.get("duration")),
            video_codec=video.get("codec_name") if isinstance(video.get("codec_name"), str) else None,
            pix_fmt=video.get("pix_fmt") if isinstance(video.get("pix_fmt"), str) else None,
            has_audio=has_audio,
            audio_codec=audio.get("codec_name") if audio and isinstance(audio.get("codec_name"), str) else None,
            sample_rate=_int(audio.get("sample_rate")) if audio else None,
            channels=_int(audio.get("channels")) if audio else None,
            size_bytes=size_bytes if size_bytes is None else int(size_bytes),
            sha256=sha256,
        )

    def structural(self) -> dict[str, Any]:
        """Metadata-only view for the trace (no paths, no content)."""

        return {
            "file": self.filename,
            "bytes": self.size_bytes,
            "sha256": self.sha256,
            "duration_s": self.duration,
            "fps": self.fps,
            "width": self.width,
            "height": self.height,
            "video_codec": self.video_codec,
            "pix_fmt": self.pix_fmt,
            "has_audio": self.has_audio,
            "audio_codec": self.audio_codec,
            "sample_rate": self.sample_rate,
            "channels": self.channels,
        }


@dataclass(frozen=True, slots=True)
class FrameRate:
    """Timeline rate: exact rational plus the integer label rate used for timecode."""

    exact: Fraction
    nominal: int

    @classmethod
    def from_fps(cls, fps: float) -> FrameRate:
        if not math.isfinite(fps) or fps <= 0:
            raise TimelinePlanError("frame rate must be positive")
        nominal = max(1, round(fps))
        ntsc = _NTSC_RATES.get(nominal)
        if ntsc is not None and abs(fps - float(ntsc)) < 0.005:
            return cls(exact=ntsc, nominal=nominal)
        if abs(fps - nominal) < 0.005:
            return cls(exact=Fraction(nominal), nominal=nominal)
        return cls(exact=Fraction(fps).limit_denominator(1001), nominal=nominal)

    @property
    def is_integer(self) -> bool:
        return self.exact.denominator == 1

    @property
    def label(self) -> str:
        value = float(self.exact)
        return f"{self.nominal}" if self.is_integer else f"{value:.3f}".rstrip("0").rstrip(".")

    def frames(self, seconds: float) -> int:
        """``round(seconds x fps)`` half up, matching ``production_trace.frames_to_timecode``."""

        exact = Decimal(str(seconds)) * Decimal(self.exact.numerator) / Decimal(self.exact.denominator)
        return int(exact.to_integral_value(rounding=ROUND_HALF_UP))

    def frame_seconds(self, frames: int) -> float:
        return float(Fraction(frames) / self.exact)


def frames_label(frames: int, nominal: int) -> str:
    """``HH:MM:SS:FF`` non-drop label for a frame count at the nominal rate."""

    hours, rest = divmod(frames, 3600 * nominal)
    minutes, rest = divmod(rest, 60 * nominal)
    seconds, frame = divmod(rest, nominal)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}:{frame:02d}"


def ms_label(seconds: float) -> str:
    """``HH:MM:SS.mmm`` (half-up milliseconds, same as ``production_trace.seconds_to_timecode``)."""

    total_ms = int((Decimal(str(seconds)) * 1000).to_integral_value(rounding=ROUND_HALF_UP))
    hours, rest = divmod(total_ms, 3_600_000)
    minutes, rest = divmod(rest, 60_000)
    secs, ms = divmod(rest, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}.{ms:03d}"


@dataclass(frozen=True, slots=True)
class PlannedClip:
    index: int
    segment_id: str
    source_start: float
    source_end: float
    record_start: float
    record_end: float
    source_in_frame: int
    source_out_frame: int
    record_in_frame: int
    record_out_frame: int
    provenance_reason: str = ""

    @property
    def duration(self) -> float:
        return self.source_end - self.source_start

    @property
    def frame_length(self) -> int:
        return self.source_out_frame - self.source_in_frame


@dataclass(frozen=True, slots=True)
class ReframePlan:
    """How the source frame maps onto the output frame (mirrors the FFmpeg filters)."""

    profile: str
    output_width: int
    output_height: int
    filters: tuple[str, ...]
    crop_width: int | None = None
    crop_height: int | None = None
    crop_x: int | None = None
    crop_y: int | None = None
    scale_x: float | None = None
    scale_y: float | None = None
    fit_scale: float | None = None
    resolve_zoom: float | None = None
    premiere_scale_percent: float | None = None
    aspect_matches: bool = True


@dataclass(frozen=True, slots=True)
class CaptionPlan:
    enabled: bool
    source: str
    format: str
    preset: str
    font: str
    font_size: int
    outline_px: int
    shadow_px: int
    margin_horizontal_px: int
    margin_bottom_px: int
    alignment: str
    wrap_columns: int | None
    play_res: tuple[int, int]


@dataclass(frozen=True, slots=True)
class LoudnessPlan:
    integrated_lufs: float
    true_peak_dbtp: float
    loudness_range_lu: float
    output_sample_rate: int = 48000

    def ffmpeg_filter(self) -> str:
        return (
            f"loudnorm=I={self.integrated_lufs:g}:TP={self.true_peak_dbtp:g}"
            f":LRA={self.loudness_range_lu:g}"
        )


@dataclass(frozen=True, slots=True)
class TimelinePlan:
    title: str
    width: int
    height: int
    rate: FrameRate
    rate_source: str
    clips: tuple[PlannedClip, ...]
    reframe: ReframePlan
    captions: CaptionPlan | None
    loudness: LoudnessPlan | None
    source: SourceVideo
    render_profile: str
    warnings: tuple[str, ...] = field(default_factory=tuple)

    @property
    def record_duration(self) -> float:
        return sum(clip.duration for clip in self.clips)

    @property
    def record_frames(self) -> int:
        return self.clips[-1].record_out_frame if self.clips else 0

    @property
    def is_sequential(self) -> bool:
        """Clips run forward through the source without overlap (blade + ripple delete works)."""

        return all(
            later.source_start >= earlier.source_end - 1e-9
            for earlier, later in zip(self.clips, self.clips[1:], strict=False)
        )

    @property
    def aspect_label(self) -> str:
        divisor = math.gcd(self.width, self.height)
        return f"{self.width // divisor}:{self.height // divisor}"

    def structural(self) -> dict[str, Any]:
        """Metadata-only summary for the trace (no caption text)."""

        return {
            "width": self.width,
            "height": self.height,
            "fps": float(self.rate.exact),
            "fps_label": self.rate.label,
            "fps_source": self.rate_source,
            "render_profile": self.render_profile,
            "clips": [
                {
                    "segment_id": clip.segment_id,
                    "source_in": clip.source_start,
                    "source_out": clip.source_end,
                    "record_in": clip.record_start,
                    "record_out": clip.record_end,
                    "source_in_frame": clip.source_in_frame,
                    "source_out_frame": clip.source_out_frame,
                    "record_in_frame": clip.record_in_frame,
                    "record_out_frame": clip.record_out_frame,
                }
                for clip in self.clips
            ],
            "record_duration_s": round(self.record_duration, 6),
            "record_frames": self.record_frames,
            "reframe": {
                "profile": self.reframe.profile,
                "filters": list(self.reframe.filters),
                "crop": [self.reframe.crop_width, self.reframe.crop_height, self.reframe.crop_x, self.reframe.crop_y],
                "resolve_zoom": self.reframe.resolve_zoom,
            },
            "captions": None
            if self.captions is None
            else {
                "format": self.captions.format,
                "preset": self.captions.preset,
                "font": self.captions.font,
                "font_size": self.captions.font_size,
                "wrap_columns": self.captions.wrap_columns,
            },
            "loudness": None
            if self.loudness is None
            else {
                "integrated_lufs": self.loudness.integrated_lufs,
                "true_peak_dbtp": self.loudness.true_peak_dbtp,
                "loudness_range_lu": self.loudness.loudness_range_lu,
            },
            "warnings": list(self.warnings),
        }


def loudness_from_operations(operations: list[dict[str, Any]]) -> LoudnessPlan | None:
    """Accept exactly one ``loudness_normalize`` audio operation; reject anything else."""

    if not operations:
        return None
    if len(operations) != 1 or operations[0].get("type") != LOUDNESS_OPERATION:
        raise TimelinePlanError("only one loudness_normalize audio operation is supported")
    operation = operations[0]
    unknown = sorted(set(operation) - {"type", *LOUDNESS_LIMITS})
    if unknown:
        raise TimelinePlanError(f"unknown loudness_normalize fields: {', '.join(unknown)}")
    values: dict[str, float] = {}
    for key, (low, high) in LOUDNESS_LIMITS.items():
        raw = operation.get(key, LOUDNESS_DEFAULTS[key])
        if isinstance(raw, bool) or not isinstance(raw, int | float) or not math.isfinite(raw):
            raise TimelinePlanError(f"loudness_normalize {key} must be a number")
        if not low <= float(raw) <= high:
            raise TimelinePlanError(f"loudness_normalize {key} must be between {low:g} and {high:g}")
        values[key] = float(raw)
    return LoudnessPlan(
        integrated_lufs=values["integrated_lufs"],
        true_peak_dbtp=values["true_peak_dbtp"],
        loudness_range_lu=values["loudness_range_lu"],
    )


def _even_floor(value: float) -> int:
    return math.floor(value) // 2 * 2


def _reframe(edl: EDLDocument, source: SourceVideo) -> ReframePlan:
    width, height = edl.output.width, edl.output.height
    profile = edl.output.render_profile
    if profile == "vertical_centered":
        filters: tuple[str, ...] = ("crop=ih*9/16:ih:(iw-ih*9/16)/2:0", f"scale={width}:{height}")
        if source.width is None or source.height is None:
            return ReframePlan(profile, width, height, filters, aspect_matches=width * 16 == height * 9)
        iw, ih = source.width, source.height
        exact_crop = ih * 9 / 16
        # FFmpeg's crop truncates the expression and rounds width/x down to the
        # 4:2:0 chroma grid (multiples of 2).
        crop_w = _even_floor(exact_crop)
        crop_x = _even_floor((iw - exact_crop) / 2)
        fit = min(width / iw, height / ih)
        fill = height / ih
        return ReframePlan(
            profile,
            width,
            height,
            filters,
            crop_width=crop_w,
            crop_height=ih,
            crop_x=crop_x,
            crop_y=0,
            scale_x=width / crop_w if crop_w else None,
            scale_y=height / ih,
            fit_scale=fit,
            resolve_zoom=round(fill / fit, 3),
            premiere_scale_percent=round(fill * 100, 1),
            aspect_matches=width * 16 == height * 9,
        )
    filters = (
        f"scale={width}:{height}:force_original_aspect_ratio=decrease",
        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2",
        "setsar=1",
    )
    if source.width is None or source.height is None:
        return ReframePlan(profile, width, height, filters)
    fit = min(width / source.width, height / source.height)
    return ReframePlan(
        profile,
        width,
        height,
        filters,
        fit_scale=fit,
        resolve_zoom=1.0,
        premiere_scale_percent=round(fit * 100, 1),
    )


def _captions(edl: EDLDocument) -> CaptionPlan | None:
    # Imported lazily: the ASS module pulls in PyThaiNLP.
    from .captions.ass import (
        _FONT,
        _HORIZONTAL_MARGIN,
        PRESETS,
        line_columns_for_width,
    )

    settings = edl.timeline[0].captions
    if not settings.enabled:
        return None
    width, height = edl.output.width, edl.output.height
    font, size = PRESETS.get(settings.preset, PRESETS["minimal"])
    if settings.format == "ass":
        return CaptionPlan(
            enabled=True,
            source=settings.source,
            format="ass",
            preset=settings.preset,
            font=font,
            font_size=int(size),
            outline_px=4,
            shadow_px=1,
            margin_horizontal_px=_HORIZONTAL_MARGIN,
            margin_bottom_px=round(120 * height / 1920),
            alignment="bottom-center",
            wrap_columns=line_columns_for_width(width),
            play_res=(width, height),
        )
    return CaptionPlan(
        enabled=True,
        source=settings.source,
        format="srt",
        preset=settings.preset,
        font=_FONT,
        font_size=0,
        outline_px=0,
        shadow_px=0,
        margin_horizontal_px=0,
        margin_bottom_px=0,
        alignment="bottom-center",
        wrap_columns=None,
        play_res=(width, height),
    )


def build_timeline_plan(edl: EDLDocument, source: SourceVideo, *, title: str = "") -> TimelinePlan:
    """Compute the exact timeline the engine renders (or a person rebuilds) from an EDL."""

    warnings: list[str] = []
    asset_ids = {segment.source.asset_id for segment in edl.timeline}
    if len(asset_ids) != 1:
        raise TimelinePlanError("a timeline plan needs exactly one source asset")
    if source.duration is None:
        asset = next((item for item in edl.source_assets if item.asset_id in asset_ids), None)
        if asset is not None and asset.duration > 0:
            source = replace(source, duration=float(asset.duration))
    if source.fps is not None:
        rate = FrameRate.from_fps(source.fps)
        rate_source = "source_probe"
        if abs(float(edl.output.fps) - source.fps) > 0.01:
            # The renderer does not resample (no -r), so the output keeps the source rate.
            warnings.append("edl_output_fps_differs_from_source")
    else:
        rate = FrameRate.from_fps(float(edl.output.fps))
        rate_source = "edl_output"
    settings = [(segment.captions, segment.audio.operations) for segment in edl.timeline]
    if any(captions != settings[0][0] for captions, _ in settings):
        raise TimelinePlanError("all timeline segments must share caption settings")
    if any(operations != settings[0][1] for _, operations in settings):
        raise TimelinePlanError("all timeline segments must share audio operations")
    loudness = loudness_from_operations(list(edl.timeline[0].audio.operations))
    if loudness is not None and source.has_audio is False:
        raise TimelinePlanError("loudness normalization needs a source audio stream")

    clips: list[PlannedClip] = []
    record_seconds = 0.0
    record_frame = 0
    for index, segment in enumerate(edl.timeline, start=1):
        start, end = float(segment.source.start), float(segment.source.end)
        in_frame, out_frame = rate.frames(start), rate.frames(end)
        if out_frame <= in_frame:
            raise TimelinePlanError(f"segment {segment.id} is shorter than one frame")
        length = out_frame - in_frame
        clips.append(
            PlannedClip(
                index=index,
                segment_id=segment.id,
                source_start=start,
                source_end=end,
                record_start=record_seconds,
                record_end=record_seconds + (end - start),
                source_in_frame=in_frame,
                source_out_frame=out_frame,
                record_in_frame=record_frame,
                record_out_frame=record_frame + length,
                provenance_reason=str(segment.provenance.get("reason", ""))[:80],
            )
        )
        record_seconds += end - start
        record_frame += length
    reframe = _reframe(edl, source)
    if not reframe.aspect_matches:
        warnings.append("vertical_crop_output_not_9x16")
    if (
        reframe.profile == "vertical_centered"
        and source.width is not None
        and source.height is not None
        and source.width < source.height * 9 / 16
    ):
        # FFmpeg's crop cannot be wider than the input; the render will fail.
        warnings.append("source_narrower_than_9x16_crop")
    return TimelinePlan(
        title=title or f"Video Studio {edl.project_id}",
        width=edl.output.width,
        height=edl.output.height,
        rate=rate,
        rate_source=rate_source,
        clips=tuple(clips),
        reframe=reframe,
        captions=_captions(edl),
        loudness=loudness,
        source=source,
        render_profile=edl.output.render_profile,
        warnings=tuple(warnings),
    )


__all__ = [
    "LOUDNESS_DEFAULTS",
    "LOUDNESS_OPERATION",
    "CaptionPlan",
    "FrameRate",
    "LoudnessPlan",
    "PlannedClip",
    "ReframePlan",
    "SourceVideo",
    "TimelinePlan",
    "TimelinePlanError",
    "build_timeline_plan",
    "frames_label",
    "loudness_from_operations",
    "ms_label",
]
