"""Production trace and DaVinci Resolve manual guide for the overlay and replace-audio tools.

Each run writes ``trace.json`` (``production-trace.v1``: input hashes and probe
facts, every step with structural FFmpeg options, artifacts with sha256, QA)
and ``MANUAL-STEPS.md`` (how to make the same file by hand in DaVinci Resolve
19, with Premiere Pro / CapCut equivalents) into the output directory, and
appends ``history.jsonl`` there - on success and on failure.

``production_trace`` is a stdlib-only shared package. When it is not installed
in the interpreter running the tool (for example an older Production Hub
environment), the tools still work and skip the trace instead of failing the
media job.
"""

from __future__ import annotations

import math
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from typing import Any

try:  # pragma: no cover - exercised implicitly by both environments
    from production_trace import ManualGuide, TraceRecorder, frames_to_timecode, seconds_to_timecode
except ImportError:  # pragma: no cover
    ManualGuide = None  # type: ignore[assignment,misc]
    TraceRecorder = None  # type: ignore[assignment,misc]
    frames_to_timecode = None  # type: ignore[assignment]
    seconds_to_timecode = None  # type: ignore[assignment]

ENGINE_VERSION = "video-postpipeline 0.1.0"
_PATH_OPTIONS = frozenset({"-i"})


@dataclass
class _NullStep:
    outputs: list[str]
    params: dict[str, Any]


class NullRecorder:
    """Used when ``production_trace`` is unavailable: same calls, no files."""

    inputs: list[dict[str, Any]]

    def __init__(self) -> None:
        self.inputs = []

    @contextmanager
    def step(self, stage: str, operation: str, **params: Any) -> Iterator[_NullStep]:
        yield _NullStep(outputs=[], params=dict(params))

    def record_step(self, stage: str, operation: str, *, outputs: list[str] | None = None, note: str = "", **params: Any) -> _NullStep:
        return _NullStep(outputs=list(outputs or []), params=dict(params))

    def artifact(self, path: str | Path, *, media_type: str = "", role: str = "output") -> None:
        return None


def trace_available() -> bool:
    return TraceRecorder is not None


def new_recorder(tool_id: str, job_id: str, recipe: str) -> Any:
    if TraceRecorder is None:
        return NullRecorder()
    return TraceRecorder(tool_id=tool_id, job_id=job_id, engine_version=ENGINE_VERSION, recipe_version=recipe)


def summarize_argv(argv: list[str]) -> dict[str, Any]:
    """FFmpeg options without file paths (inputs are counted, the output is dropped)."""

    options: dict[str, Any] = {}
    maps: list[str] = []
    inputs = 0
    index = 1
    while index < len(argv):
        item = argv[index]
        following = argv[index + 1] if index + 1 < len(argv) else None
        if item in _PATH_OPTIONS:
            inputs += 1
            index += 2
        elif item == "-map" and following is not None:
            maps.append(following)
            index += 2
        elif item.startswith("-") and following is not None and not following.startswith("-"):
            options[item.lstrip("-")] = following
            index += 2
        elif item.startswith("-"):
            options[item.lstrip("-")] = True
            index += 1
        else:
            index += 1
    return {"executable": "ffmpeg", "inputs": inputs, "map": maps, "options": options}


@dataclass(frozen=True)
class VideoFacts:
    width: int
    height: int
    fps: float
    duration: float
    has_audio: bool
    bitrate: int | None = None

    @property
    def nominal_fps(self) -> int:
        return max(1, round(self.fps))

    def tc(self, seconds: float) -> str:
        if frames_to_timecode is None or seconds_to_timecode is None:  # pragma: no cover
            return f"{seconds:.3f}s"
        return f"{frames_to_timecode(seconds * self.nominal_fps / self.fps, self.nominal_fps)} (= {seconds_to_timecode(seconds)})"


def _rate(raw: Any) -> float | None:
    try:
        value = float(Fraction(str(raw)))
    except (ValueError, ZeroDivisionError):
        return None
    return value if math.isfinite(value) and value > 0 else None


def video_facts(probe: Mapping[str, Any]) -> VideoFacts:
    streams = list(probe.get("streams", []))
    video: Mapping[str, Any] = next((stream for stream in streams if stream.get("codec_type") == "video"), {})
    fps = _rate(video.get("avg_frame_rate")) or _rate(video.get("r_frame_rate")) or 30.0
    fmt = probe.get("format", {}) or {}
    try:
        duration = float(fmt.get("duration") or 0.0)
    except (TypeError, ValueError):
        duration = 0.0
    try:
        bitrate = int(video.get("bit_rate") or fmt.get("bit_rate") or 0) or None
    except (TypeError, ValueError):
        bitrate = None
    return VideoFacts(
        width=int(video.get("width") or 0),
        height=int(video.get("height") or 0),
        fps=fps,
        duration=duration,
        has_audio=any(stream.get("codec_type") == "audio" for stream in streams),
        bitrate=bitrate,
    )


def probe_params(facts: VideoFacts) -> dict[str, Any]:
    return {
        "width": facts.width,
        "height": facts.height,
        "fps": round(facts.fps, 6),
        "duration_s": round(facts.duration, 6),
        "has_audio": facts.has_audio,
        "bitrate": facts.bitrate,
    }


def _project_steps(guide: Any, facts: VideoFacts) -> None:
    section = "ตั้งค่าโปรเจกต์ (Project Settings)"
    guide.step("สร้างโปรเจกต์ใหม่", tool="Project Manager ▸ New Project", section=section)
    guide.step(
        "ตั้งความละเอียดและเฟรมเรตของไทม์ไลน์ให้เท่าวิดีโอต้นฉบับ",
        tool="File ▸ Project Settings… (Shift+9) ▸ Master Settings ▸ Timeline resolution ▸ Custom / Timeline frame rate",
        value=f"{facts.width} x {facts.height}; {round(facts.fps, 3):g} fps",
        note="ตั้งก่อนลากคลิปลงไทม์ไลน์",
        section=section,
    )
    guide.step(
        "สร้างไทม์ไลน์เริ่มที่ 00:00:00:00",
        tool="File ▸ New Timeline… (Ctrl+N) ▸ Start Timecode",
        value="00:00:00:00",
        section=section,
    )


def _deliver_steps(guide: Any, facts: VideoFacts, *, video_note: str, audio_value: str) -> None:
    section = "ส่งออก (Deliver page)"
    quality = f"Restrict to {round(facts.bitrate / 1000)} Kb/s" if facts.bitrate else "Automatic ▸ Best"
    guide.step(
        "ตั้งไฟล์ส่งออก",
        tool="Deliver page (Shift+8) ▸ Render Settings ▸ Custom Export ▸ Video",
        value=f"Format MP4; Codec H.264; {facts.width} x {facts.height}; {round(facts.fps, 3):g} fps; Quality {quality}",
        note=video_note,
        section=section,
    )
    guide.step("ตั้งเสียง", tool="Render Settings ▸ Audio", value=audio_value, section=section)
    guide.step("เพิ่มเข้าคิวและเรนเดอร์", tool="Add to Render Queue ▸ Render All", section=section)


def overlay_placement(
    facts: VideoFacts, image_size: tuple[int, int], *, target_width: int, position: str, margin: int
) -> dict[str, float]:
    """Mirror FFmpeg ``scale=W:-2`` + ``overlay=x:y`` (yuv420 even rounding) and convert to Resolve/Premiere values."""

    image_width, image_height = image_size
    target_height = max(2, round(target_width * image_height / (image_width * 2)) * 2)
    width, height = facts.width, facts.height
    x = {
        "top-left": margin,
        "bottom-left": margin,
        "top-right": width - target_width - margin,
        "bottom-right": width - target_width - margin,
        "bottom-center": (width - target_width) / 2,
    }[position]
    y = margin if position.startswith("top") else height - target_height - margin
    x, y = int(x) & ~1, int(y) & ~1
    fit = min(width / image_width, height / image_height)
    center_x, center_y = x + target_width / 2, y + target_height / 2
    return {
        "width": target_width,
        "height": target_height,
        "x": x,
        "y": y,
        "resolve_zoom": round(target_width / (image_width * fit), 3),
        "resolve_position_x": round(center_x - width / 2, 1),
        "resolve_position_y": round(height / 2 - center_y, 1),
        "premiere_scale_percent": round(target_width / image_width * 100, 1),
        "premiere_position_x": round(center_x, 1),
        "premiere_position_y": round(center_y, 1),
    }


def overlay_guide(
    *,
    facts: VideoFacts,
    video_name: str,
    image_name: str,
    image_size: tuple[int, int],
    placement: Mapping[str, float],
    position: str,
    output_name: str | None,
) -> Any:
    if ManualGuide is None:  # pragma: no cover
        return None
    guide = ManualGuide(
        program="DaVinci Resolve",
        program_version="19 (ฟรี)",
        alternatives=["Adobe Premiere Pro", "CapCut Desktop"],
        title=f"Overlay {image_name} บน {video_name}",
        summary="วางภาพกราฟิก (โลโก้/lower third) ทับวิดีโอทั้งคลิป ขนาดและตำแหน่งตรงกับที่เครื่องมือเผาลงภาพ",
        estimated_minutes=6,
        setup=[
            f"เตรียม `{video_name}` ({facts.width}x{facts.height}, {round(facts.fps, 3):g} fps, ยาว {facts.tc(facts.duration)}) และ `{image_name}` ({image_size[0]}x{image_size[1]} px)",
            *( [f"ไฟล์อ้างอิงของเครื่องมือ: `{output_name}`"] if output_name else []),
        ],
    )
    guide.layer("V1 (Video 1)", "Video track — วิดีโอต้นฉบับ", content=video_name, position="เต็มเฟรม (ไม่ย่อ/ไม่ครอป)")
    guide.layer(
        "V2 (Video 2)",
        "Video track — ภาพกราฟิก",
        content=image_name,
        position=(
            f"{position}: {placement['width']:g}x{placement['height']:g} px ที่ x={placement['x']:g}, y={placement['y']:g} "
            f"(Zoom {placement['resolve_zoom']:.3f}, Position {placement['resolve_position_x']:g}, {placement['resolve_position_y']:g})"
        ),
    )
    if facts.has_audio:
        guide.layer("A1 (Audio 1)", "Audio track", content="เสียงต้นฉบับ (copy ไม่แก้)", position="ลิงก์กับ V1")
    _project_steps(guide, facts)
    section = "วางวิดีโอและภาพ"
    guide.step("นำเข้าทั้งสองไฟล์", tool="File ▸ Import ▸ Media… (Ctrl+I)", value=f"{video_name}, {image_name}", section=section)
    guide.step("วางวิดีโอที่จุดเริ่ม", tool="เลือกวิดีโอใน Media Pool ▸ Edit ▸ Append at End (Shift+F12)", target="V1 + A1", value="00:00:00:00", section=section)
    guide.step("ลากภาพไปวางบนแทร็กเหนือวิดีโอที่จุดเริ่ม", tool="ลาก (drag) จาก Media Pool", target="V2", value="00:00:00:00", section=section)
    guide.step(
        "ลากขอบขวาของภาพให้ยาวถึงจบวิดีโอ",
        tool="Selection Mode (A) ▸ ลากขอบ Out ของคลิปภาพ",
        target="V2",
        value=facts.tc(facts.duration),
        note="เครื่องมือค้างภาพไว้จนวิดีโอจบ (eof_action=repeat)",
        section=section,
    )
    section = "ขนาดและตำแหน่งภาพ (Inspector)"
    guide.step("เลือกคลิปภาพบน V2", tool="Selection Mode (A)", target="V2", section=section)
    guide.step(
        "ตั้งขนาดภาพ",
        tool="Inspector ▸ Video ▸ Transform ▸ Zoom (X/Y ลิงก์กัน)",
        value=f"{placement['resolve_zoom']:.3f}",
        note=f"ภาพกว้าง {placement['width']:g} px (= กว้างวิดีโอ x width-ratio, ปัดลงเป็นเลขคู่)",
        section=section,
    )
    guide.step(
        "ตั้งตำแหน่งภาพ",
        tool="Inspector ▸ Video ▸ Transform ▸ Position",
        value=f"X {placement['resolve_position_x']:g}, Y {placement['resolve_position_y']:g}",
        note="Position ของ Resolve วัดจากกึ่งกลางเฟรม (Y บวก = ขึ้น)",
        section=section,
    )
    _deliver_steps(
        guide,
        facts,
        video_note="เครื่องมือ encode ใหม่ด้วย libx264 CRF 20 preset medium yuv420p (Resolve ไม่มี CRF)",
        audio_value="ถ้ามีเสียง: AAC ตามต้นฉบับ (เครื่องมือ copy เสียงเดิมไม่ encode ใหม่)",
    )
    guide.checks = [
        f"ภาพอยู่ที่ {position} ขนาด {placement['width']:g}x{placement['height']:g} px ตั้งแต่ 00:00:00:00 ถึงจบ",
        f"ไฟล์ได้ {facts.width}x{facts.height}, ยาวเท่าวิดีโอต้นฉบับ ({facts.duration:.3f} วินาที)",
    ]
    guide.glossary = {
        "Inspector ▸ Transform": (
            f"Premiere: Effect Controls ▸ Motion ▸ Scale {placement['premiere_scale_percent']:g}%, "
            f"Position {placement['premiere_position_x']:g}, {placement['premiere_position_y']:g} (วัดจากมุมซ้ายบน); "
            "CapCut: ลากภาพลงแทร็กบน (Overlay) แล้วตั้ง Basic ▸ Transform"
        ),
        "Deliver": "Premiere: File ▸ Export ▸ Media (Ctrl+M) H.264; CapCut: Export MP4",
    }
    return guide


def replace_audio_guide(
    *,
    facts: VideoFacts,
    audio_duration: float,
    video_name: str,
    audio_name: str,
    output_name: str | None,
) -> Any:
    if ManualGuide is None:  # pragma: no cover
        return None
    guide = ManualGuide(
        program="DaVinci Resolve",
        program_version="19 (ฟรี)",
        alternatives=["Adobe Premiere Pro", "CapCut Desktop"],
        title=f"แทนเสียง {video_name} ด้วย {audio_name}",
        summary="เอาเสียงเดิมออกแล้ววางเสียงที่ master แล้วแทน ภาพเหมือนเดิมทุกเฟรม ความยาวยึดตามวิดีโอ",
        estimated_minutes=5,
        setup=[
            f"เตรียม `{video_name}` ({facts.width}x{facts.height}, {round(facts.fps, 3):g} fps, ยาว {facts.tc(facts.duration)}) และ `{audio_name}` (ยาว {audio_duration:.3f} วินาที)",
            *( [f"ไฟล์อ้างอิงของเครื่องมือ: `{output_name}`"] if output_name else []),
        ],
    )
    guide.layer("V1 (Video 1)", "Video track — ภาพต้นฉบับ (ไม่แก้)", content=video_name, position="เต็มเฟรม")
    guide.layer("A1 (Audio 1)", "Audio track — เสียงที่ master แล้ว", content=audio_name, position="เริ่ม 00:00:00:00 จบพร้อมวิดีโอ")
    _project_steps(guide, facts)
    section = "วางภาพ และเอาเสียงเดิมออก"
    guide.step("นำเข้าทั้งสองไฟล์", tool="File ▸ Import ▸ Media… (Ctrl+I)", value=f"{video_name}, {audio_name}", section=section)
    guide.step("วางวิดีโอที่จุดเริ่ม", tool="เลือกวิดีโอใน Media Pool ▸ Edit ▸ Append at End (Shift+F12)", target="V1 + A1", value="00:00:00:00", section=section)
    if facts.has_audio:
        guide.step("เลิกลิงก์ภาพกับเสียงเดิม", tool="เลือกคลิป ▸ Clip ▸ Link Clips (Ctrl+Alt+L) ให้ปิด", target="V1/A1", section=section)
        guide.step("ลบเสียงเดิม", tool="คลิกคลิปเสียงบน A1 ▸ Delete (Backspace)", target="A1", section=section)
    section = "วางเสียงใหม่"
    guide.step("ลากเสียงที่ master แล้วไปวางที่จุดเริ่ม", tool="ลาก (drag) จาก Media Pool", target="A1", value="00:00:00:00", section=section)
    if audio_duration > facts.duration + 1e-3:
        guide.step(
            "ตัดเสียงส่วนที่ยาวเกินวิดีโอ",
            tool="พิมพ์ timecode ▸ Enter ▸ Timeline ▸ Razor (Ctrl+B) แล้วเลือกชิ้นท้าย ▸ Delete",
            target="A1",
            value=facts.tc(facts.duration),
            note="เครื่องมือยึดความยาววิดีโอ (-shortest)",
            section=section,
        )
    elif audio_duration < facts.duration - 1e-3:
        guide.step(
            "ปล่อยช่วงท้ายเงียบ (ไม่ต้องยืดเสียง)",
            target="A1",
            value=f"{facts.tc(audio_duration)} → {facts.tc(facts.duration)}",
            note="เครื่องมือเติมความเงียบ (apad)",
            section=section,
        )
    _deliver_steps(
        guide,
        facts,
        video_note="เครื่องมือ copy ภาพเดิมโดยไม่ encode ใหม่; Resolve ต้อง encode ใหม่ จึงตั้ง bitrate ให้เท่าต้นฉบับ",
        audio_value="AAC; 192 kb/s (เท่ากับเครื่องมือ)",
    )
    guide.checks = [
        "ไม่มีเสียงเดิมหลงเหลือ (ฟังช่วงต้นและท้าย)",
        f"ไฟล์ยาวเท่าวิดีโอต้นฉบับ ({facts.duration:.3f} วินาที) ภายใน 0.5 วินาที",
    ]
    guide.glossary = {
        "Link Clips / Delete": "Premiere: คลิกขวา ▸ Unlink แล้ว Delete เสียงเดิม; CapCut: คลิกขวา ▸ Extract audio / Mute clip",
        "Deliver": "Premiere: File ▸ Export ▸ Media (Ctrl+M) H.264, Audio AAC 192 kbps; CapCut: Export MP4",
    }
    return guide


def finish(trace: Any, *, status: str, out_dir: Path, guide: Any = None, note: str = "") -> None:
    """Persist the trace; never let tracing mask the media job's own outcome."""

    if TraceRecorder is None or not isinstance(trace, TraceRecorder):
        return
    try:
        if status != "accepted":
            trace.record_step("outcome", "failed", note=note)
        trace.finish(status=status, job_dir=out_dir, output_root=out_dir, guide=guide)
    except OSError:
        pass


__all__ = [
    "NullRecorder",
    "VideoFacts",
    "finish",
    "new_recorder",
    "overlay_guide",
    "overlay_placement",
    "probe_params",
    "replace_audio_guide",
    "summarize_argv",
    "trace_available",
    "video_facts",
]
