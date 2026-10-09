"""Export the engine's timeline to files real editors open: CMX3600, FCPXML 1.10, OTIO.

All three are written from the same :class:`~video_postproduction.timeline.TimelinePlan`
the renderer used, so the cuts land on the same frames:

* ``timeline.edl`` - CMX3600 cut list (non-drop frame), one ``AA/V`` event per
  clip with ``* FROM CLIP NAME`` so DaVinci Resolve, Premiere Pro, and Avid can
  match the source clip by name. Source timecode assumes the file starts at
  ``00:00:00:00`` (true for most phone and screen recordings).
* ``timeline.fcpxml`` - FCPXML 1.10 project with the output format, the source
  asset, one ``asset-clip`` per clip on the spine, ``adjust-conform fit`` plus
  ``adjust-transform`` matching the 9:16 center crop, and a marker naming each
  EDL segment id. DaVinci Resolve imports it via ``File > Import > Timeline``.
* ``timeline.otio`` - OpenTimelineIO JSON (``Timeline.1`` / ``Clip.2``) with a
  video and an audio track; Resolve 18.5+ and other OTIO-aware tools read it.

Captions travel separately as ``captions.srt`` (same cues and line breaks as the
burned-in captions); none of these formats carries them reliably across editors.
The files contain the source file name and timings only, no transcript text.
"""

from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from fractions import Fraction
from typing import Any
from urllib.parse import quote

from .timeline import TimelinePlan, frames_label

CMX_FILENAME = "timeline.edl"
FCPXML_FILENAME = "timeline.fcpxml"
OTIO_FILENAME = "timeline.otio"
FCPXML_VERSION = "1.10"
_REEL = "AX"


def _ascii_title(title: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9 _.\-]+", "_", title).strip() or "VIDEO STUDIO"
    return cleaned[:70]


def _has_audio(plan: TimelinePlan) -> bool:
    return plan.source.has_audio is not False


def to_cmx3600(plan: TimelinePlan) -> str:
    """CMX3600 cut list; every event is a straight cut (``C``) at the plan's frames."""

    nominal = plan.rate.nominal
    track = "AA/V" if _has_audio(plan) else "V"
    lines = [f"TITLE: {_ascii_title(plan.title)}", "FCM: NON-DROP FRAME", ""]
    for clip in plan.clips:
        lines.append(
            f"{clip.index:03d}  {_REEL:<8} {track:<5} {'C':<8} "
            f"{frames_label(clip.source_in_frame, nominal)} {frames_label(clip.source_out_frame, nominal)} "
            f"{frames_label(clip.record_in_frame, nominal)} {frames_label(clip.record_out_frame, nominal)}"
        )
        lines.append(f"* FROM CLIP NAME: {plan.source.filename}")
        lines.append(f"* SEGMENT ID: {clip.segment_id}")
        lines.append("")
    return "\r\n".join(lines) + "\r\n"


def _time(frames: int, plan: TimelinePlan) -> str:
    value = Fraction(frames) / plan.rate.exact
    if value.denominator == 1:
        return f"{value.numerator}s"
    return f"{value.numerator}/{value.denominator}s"


def _frame_duration(plan: TimelinePlan) -> str:
    value = 1 / plan.rate.exact
    return f"{value.numerator}/{value.denominator}s"


def _format_number(value: float) -> str:
    return f"{value:.6f}".rstrip("0").rstrip(".")


def to_fcpxml(plan: TimelinePlan, *, media_url: str | None = None) -> str:
    """FCPXML 1.10 project; ``media_url`` defaults to the bare source file name (relink in the editor)."""

    root = ET.Element("fcpxml", version=FCPXML_VERSION)
    resources = ET.SubElement(root, "resources")
    ET.SubElement(
        resources,
        "format",
        id="r1",
        name=f"Video Studio {plan.width}x{plan.height}",
        frameDuration=_frame_duration(plan),
        width=str(plan.width),
        height=str(plan.height),
        colorSpace="1-1-1 (Rec. 709)",
    )
    source_width = plan.source.width or plan.width
    source_height = plan.source.height or plan.height
    ET.SubElement(
        resources,
        "format",
        id="r2",
        name=f"Source {source_width}x{source_height}",
        frameDuration=_frame_duration(plan),
        width=str(source_width),
        height=str(source_height),
        colorSpace="1-1-1 (Rec. 709)",
    )
    source_frames = (
        plan.rate.frames(plan.source.duration)
        if plan.source.duration is not None
        else max(clip.source_out_frame for clip in plan.clips)
    )
    asset_attributes = {
        "id": "r3",
        "name": plan.source.filename,
        "start": "0s",
        "duration": _time(source_frames, plan),
        "hasVideo": "1",
        "format": "r2",
        "videoSources": "1",
    }
    if _has_audio(plan):
        asset_attributes |= {
            "hasAudio": "1",
            "audioSources": "1",
            "audioChannels": str(plan.source.channels or 2),
            "audioRate": str(plan.source.sample_rate or 48000),
        }
    asset = ET.SubElement(resources, "asset", asset_attributes)
    ET.SubElement(
        asset,
        "media-rep",
        kind="original-media",
        src=media_url or quote(plan.source.filename),
    )
    library = ET.SubElement(root, "library")
    event = ET.SubElement(library, "event", name="Solo Empire Video Studio")
    project = ET.SubElement(event, "project", name=plan.title)
    sequence = ET.SubElement(
        project,
        "sequence",
        format="r1",
        duration=_time(plan.record_frames, plan),
        tcStart="0s",
        tcFormat="NDF",
        audioLayout="stereo",
        audioRate="48k",
    )
    spine = ET.SubElement(sequence, "spine")
    zoom = plan.reframe.resolve_zoom
    for clip in plan.clips:
        element = ET.SubElement(
            spine,
            "asset-clip",
            ref="r3",
            offset=_time(clip.record_in_frame, plan),
            name=f"{clip.index:02d} {clip.segment_id}",
            start=_time(clip.source_in_frame, plan),
            duration=_time(clip.frame_length, plan),
            format="r2",
            tcFormat="NDF",
        )
        if _has_audio(plan):
            element.set("audioRole", "dialogue")
        ET.SubElement(element, "adjust-conform", type="fit")
        if zoom is not None and abs(zoom - 1.0) > 1e-9:
            ET.SubElement(element, "adjust-transform", position="0 0", scale=f"{_format_number(zoom)} {_format_number(zoom)}")
        ET.SubElement(
            element,
            "marker",
            start=_time(clip.source_in_frame, plan),
            duration=_frame_duration(plan),
            value=clip.segment_id,
        )
    ET.indent(root, space="  ")
    body = ET.tostring(root, encoding="unicode")
    return '<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE fcpxml>\n' + body + "\n"


def _rational(frames: float, rate: float) -> dict[str, Any]:
    return {"OTIO_SCHEMA": "RationalTime.1", "rate": rate, "value": float(frames)}


def _range(start: float, duration: float, rate: float) -> dict[str, Any]:
    return {"OTIO_SCHEMA": "TimeRange.1", "start_time": _rational(start, rate), "duration": _rational(duration, rate)}


def _otio_clip(plan: TimelinePlan, clip_index: int, *, media_url: str) -> dict[str, Any]:
    clip = plan.clips[clip_index]
    rate = float(plan.rate.exact)
    available = (
        _range(0, plan.rate.frames(plan.source.duration), rate) if plan.source.duration is not None else None
    )
    return {
        "OTIO_SCHEMA": "Clip.2",
        "name": f"{clip.index:02d} {clip.segment_id}",
        "source_range": _range(clip.source_in_frame, clip.frame_length, rate),
        "effects": [],
        "markers": [],
        "enabled": True,
        "metadata": {"solo_empire": {"segment_id": clip.segment_id, "source_in_s": clip.source_start, "source_out_s": clip.source_end}},
        "media_references": {
            "DEFAULT_MEDIA": {
                "OTIO_SCHEMA": "ExternalReference.1",
                "name": plan.source.filename,
                "target_url": media_url,
                "available_range": available,
                "available_image_bounds": None,
                "metadata": {},
            }
        },
        "active_media_reference_key": "DEFAULT_MEDIA",
    }


def to_otio(plan: TimelinePlan, *, media_url: str | None = None) -> str:
    """OpenTimelineIO JSON with ``V1`` (+ ``A1`` when the source has audio)."""

    url = media_url or quote(plan.source.filename)
    rate = float(plan.rate.exact)
    tracks: list[dict[str, Any]] = []
    kinds = ["Video", "Audio"] if _has_audio(plan) else ["Video"]
    for kind in kinds:
        tracks.append(
            {
                "OTIO_SCHEMA": "Track.1",
                "name": "V1" if kind == "Video" else "A1",
                "kind": kind,
                "source_range": None,
                "effects": [],
                "markers": [],
                "enabled": True,
                "metadata": {},
                "children": [_otio_clip(plan, index, media_url=url) for index in range(len(plan.clips))],
            }
        )
    document = {
        "OTIO_SCHEMA": "Timeline.1",
        "name": plan.title,
        "global_start_time": _rational(0, rate),
        "metadata": {
            "solo_empire": {
                "width": plan.width,
                "height": plan.height,
                "fps": rate,
                "render_profile": plan.render_profile,
                "reframe_filters": list(plan.reframe.filters),
                "resolve_zoom": plan.reframe.resolve_zoom,
                "loudness": None
                if plan.loudness is None
                else {"integrated_lufs": plan.loudness.integrated_lufs, "true_peak_dbtp": plan.loudness.true_peak_dbtp},
                "captions_file": "captions.srt" if plan.captions is not None else None,
            }
        },
        "tracks": {
            "OTIO_SCHEMA": "Stack.1",
            "name": "tracks",
            "source_range": None,
            "effects": [],
            "markers": [],
            "enabled": True,
            "metadata": {},
            "children": tracks,
        },
    }
    return json.dumps(document, ensure_ascii=False, indent=2) + "\n"


def write_interchange(plan: TimelinePlan) -> dict[str, str]:
    """Return ``{filename: content}`` for all interchange formats."""

    return {
        CMX_FILENAME: to_cmx3600(plan),
        FCPXML_FILENAME: to_fcpxml(plan),
        OTIO_FILENAME: to_otio(plan),
    }


__all__ = [
    "CMX_FILENAME",
    "FCPXML_FILENAME",
    "FCPXML_VERSION",
    "OTIO_FILENAME",
    "to_cmx3600",
    "to_fcpxml",
    "to_otio",
    "write_interchange",
]
