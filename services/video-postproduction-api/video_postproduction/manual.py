"""Manual guide: rebuild the engine's exact edit by hand in DaVinci Resolve 19 (free).

Every number comes from the same :class:`~video_postproduction.timeline.TimelinePlan`
the renderer used: timeline resolution and rate, every blade point at
``HH:MM:SS:FF`` (plus the engine's exact ``HH:MM:SS.mmm``), the ranges to
ripple delete, each clip's source and record in/out, the Inspector zoom that
matches the FFmpeg center crop, the caption file and style, the loudness
target, and the Deliver settings. Premiere Pro and CapCut equivalents are
listed in the glossary.

Human-facing text is Thai; program menus, tools, and shortcuts stay in English
exactly as the programs show them. Caption text appears here only (never in
``trace.json``), because a person needs it to retype a cue.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

from production_trace import ManualGuide

from .captions.cues import CaptionCue
from .interchange import CMX_FILENAME, FCPXML_FILENAME, OTIO_FILENAME
from .timeline import PlannedClip, TimelinePlan, frames_label, ms_label

PROGRAM = "DaVinci Resolve"
PROGRAM_VERSION = "19 (ฟรี)"
ALTERNATIVES = ["Adobe Premiere Pro", "CapCut Desktop"]
MAX_CUE_STEPS = 80
EDITOR_CAPTIONS = "captions.srt"


@dataclass(frozen=True, slots=True)
class GuideFiles:
    """Names of the job files the guide refers to (all in the job directory)."""

    video: str | None = None
    burned_captions: str | None = None
    editor_captions: str | None = None
    interchange: tuple[str, ...] = (CMX_FILENAME, FCPXML_FILENAME, OTIO_FILENAME)


@dataclass(frozen=True, slots=True)
class DeliveredOutput:
    """Facts measured on the engine's output (for matching Deliver settings)."""

    bitrate: int | None = None
    duration: float | None = None
    audio_sample_rate: int | None = None


@dataclass(frozen=True, slots=True)
class GuideContext:
    plan: TimelinePlan
    files: GuideFiles = field(default_factory=GuideFiles)
    cues: Sequence[CaptionCue] | None = None
    output: DeliveredOutput = field(default_factory=DeliveredOutput)
    engine_outcome: str = "accepted"
    job_kind: str = "render"


def tc(plan: TimelinePlan, frames: int) -> str:
    return frames_label(frames, plan.rate.nominal)


def tc_pair(plan: TimelinePlan, seconds: float, frames: int | None = None) -> str:
    """``HH:MM:SS:FF (= HH:MM:SS.mmm)`` at the timeline rate."""

    frame = plan.rate.frames(seconds) if frames is None else frames
    return f"{tc(plan, frame)} (= {ms_label(seconds)})"


def cut_points(plan: TimelinePlan) -> list[float]:
    """Source-time blade points for a sequential timeline (before any ripple delete)."""

    points: set[float] = set()
    end = plan.source.duration
    for clip in plan.clips:
        if clip.source_start > 0:
            points.add(clip.source_start)
        if end is None or clip.source_end < end - 1e-9:
            points.add(clip.source_end)
    return sorted(points)


def removed_ranges(plan: TimelinePlan) -> list[tuple[float, float | None]]:
    """Source ranges between kept clips; ``None`` end means "to the end of the file"."""

    ranges: list[tuple[float, float | None]] = []
    cursor = 0.0
    for clip in plan.clips:
        if clip.source_start > cursor + 1e-9:
            ranges.append((cursor, clip.source_start))
        cursor = max(cursor, clip.source_end)
    end = plan.source.duration
    if end is None:
        ranges.append((cursor, None))
    elif cursor < end - 1e-9:
        ranges.append((cursor, end))
    return ranges


def _clip_value(plan: TimelinePlan, clip: PlannedClip) -> str:
    return (
        f"Record IN {tc(plan, clip.record_in_frame)} → OUT {tc(plan, clip.record_out_frame)}"
        f" | Source IN {tc_pair(plan, clip.source_start, clip.source_in_frame)}"
        f" → OUT {tc_pair(plan, clip.source_end, clip.source_out_frame)}"
        f" | {clip.frame_length} เฟรม"
    )


def _reframe_text(plan: TimelinePlan) -> str:
    reframe = plan.reframe
    if reframe.profile == "vertical_centered":
        if reframe.resolve_zoom is None:
            return "ครอปกลางภาพเป็น 9:16 (ไม่ทราบขนาดต้นฉบับ: Zoom = (สูงไทม์ไลน์/สูงต้นฉบับ) ÷ min(กว้างไทม์ไลน์/กว้างต้นฉบับ, สูงไทม์ไลน์/สูงต้นฉบับ))"
        return f"ครอปกลางภาพ 9:16 แล้วขยายเต็มเฟรม: Zoom {reframe.resolve_zoom:.3f}, Position 0, 0"
    return "ย่อให้พอดีเฟรม (fit) เหลือแถบดำ: Zoom 1.000, Position 0, 0"


def _audio_text(plan: TimelinePlan) -> str:
    if plan.loudness is None:
        return "เสียงจากไฟล์ต้นฉบับ (ไม่ปรับระดับ)"
    loud = plan.loudness
    return f"เสียงจากไฟล์ต้นฉบับ + Normalize {loud.integrated_lufs:g} LUFS / True Peak {loud.true_peak_dbtp:g} dBTP"


def _setup(context: GuideContext) -> list[str]:
    plan = context.plan
    setup = [
        f"เตรียมไฟล์ต้นฉบับ `{plan.source.filename}`"
        + (f" (sha256 `{plan.source.sha256[:16]}…`)" if plan.source.sha256 else "")
        + " ไว้ในโฟลเดอร์เดียวกับไฟล์ในงานนี้",
        (
            f"ไทม์ไลน์ปลายทาง: {plan.width}x{plan.height} ({plan.aspect_label}) ที่ {plan.rate.label} fps, "
            f"ยาว {tc(plan, plan.record_frames)} ({plan.record_duration:.3f} วินาที), {len(plan.clips)} คลิป"
        ),
    ]
    if context.files.editor_captions:
        setup.append(
            f"ไฟล์ซับสำหรับโปรแกรมตัดต่อ: `{context.files.editor_captions}` (เวลาและการขึ้นบรรทัดเหมือนซับที่เครื่องมือเผาลงภาพ)"
        )
    if context.files.interchange:
        setup.append(
            "ทางลัด: ถ้าไม่ต้องการตัดเอง ให้นำเข้า "
            + ", ".join(f"`{name}`" for name in context.files.interchange)
            + " (ดูหัวข้อ \"ทางลัด: เปิดไทม์ไลน์สำเร็จรูป\" ท้ายขั้นตอน)"
        )
    if context.files.video:
        setup.append(f"ไฟล์อ้างอิงที่เครื่องมือ render ไว้: `{context.files.video}` ใช้เทียบผลตอนจบ")
    return setup


def _layers(guide: ManualGuide, context: GuideContext) -> None:
    plan = context.plan
    guide.layer(
        "V1 (Video 1)",
        "Video track — วิดีโอต้นฉบับ (A-roll)",
        content=f"{len(plan.clips)} คลิปจาก {plan.source.filename}",
        position=_reframe_text(plan),
    )
    if plan.captions is not None:
        cue_count = len(context.cues) if context.cues is not None else 0
        if plan.captions.format == "ass":
            position = (
                f"ล่างกลาง, เว้นขอบล่าง {plan.captions.margin_bottom_px} px, ซ้าย/ขวา "
                f"{plan.captions.margin_horizontal_px} px; {plan.captions.font} {plan.captions.font_size} px"
            )
        else:
            position = "ล่างกลาง (สไตล์ค่าเริ่มต้นของ libass)"
        guide.layer(
            "ST1 (Subtitle 1)",
            "Subtitle track — ซับไตเติล (เครื่องมือเผาลงภาพ)",
            content=f"{cue_count} cue จาก {context.files.editor_captions or EDITOR_CAPTIONS}",
            position=position,
        )
    if plan.source.has_audio is not False:
        guide.layer("A1 (Audio 1)", "Audio track — Stereo", content=_audio_text(plan), position="ลิงก์กับคลิปบน V1")


def _project_steps(guide: ManualGuide, plan: TimelinePlan) -> None:
    section = "ตั้งค่าโปรเจกต์ (Project Settings)"
    guide.step("สร้างโปรเจกต์ใหม่", tool="Project Manager ▸ New Project", value=plan.title, section=section)
    guide.step(
        "เปิดหน้าตั้งค่าโปรเจกต์",
        tool="File ▸ Project Settings… (Shift+9) ▸ Master Settings",
        note="ต้องตั้งก่อนลากคลิปลงไทม์ไลน์ เพราะ Timeline frame rate จะล็อกเมื่อมีคลิปแล้ว",
        section=section,
    )
    guide.step(
        "ตั้งความละเอียดไทม์ไลน์",
        tool="Timeline resolution ▸ Custom",
        target="ช่อง width x height",
        value=f"{plan.width} x {plan.height}",
        section=section,
    )
    guide.step("ตั้ง Timeline frame rate", tool="Timeline frame rate", value=plan.rate.label, section=section)
    guide.step("ตั้ง Playback frame rate", tool="Playback frame rate", value=plan.rate.label, section=section)
    guide.step(
        "ตั้งวิธีย่อภาพที่ขนาดไม่ตรง",
        tool="Image Scaling ▸ Input Scaling ▸ Mismatched resolution files",
        value="Scale entire image to fit",
        note="ค่า Zoom ในขั้น Reframe คำนวณจากค่านี้",
        section=section,
    )
    guide.step("บันทึกการตั้งค่า", tool="Save", section=section)


def _import_steps(guide: ManualGuide, plan: TimelinePlan, *, append_whole: bool) -> None:
    section = "นำเข้าไฟล์และสร้างไทม์ไลน์ (Media Pool / Edit page)"
    guide.step("ไปหน้า Edit", tool="Edit page (Shift+4)", section=section)
    guide.step(
        "นำเข้าไฟล์ต้นฉบับเข้า Media Pool",
        tool="File ▸ Import ▸ Media… (Ctrl+I)",
        value=plan.source.filename,
        note="ถ้าถามว่าจะเปลี่ยน frame rate ของโปรเจกต์ไหม ให้ตอบ Don't Change",
        section=section,
    )
    guide.step(
        "สร้างไทม์ไลน์เปล่า",
        tool="File ▸ New Timeline… (Ctrl+N)",
        target="Start Timecode / Timeline Name / Video Tracks / Audio Tracks",
        value=f"Start Timecode 00:00:00:00; Name {plan.title}; Video Tracks 1; Audio Tracks 1 (Stereo); เอาติ๊ก Empty Timeline ออก",
        note="Resolve ตั้งต้นที่ 01:00:00:00 — ต้องเปลี่ยนเป็น 00:00:00:00 เพื่อให้ timecode ตรงกับคู่มือนี้",
        section=section,
    )
    if not append_whole:
        return
    guide.step(
        "วางคลิปต้นฉบับทั้งไฟล์ลงไทม์ไลน์ที่ 00:00:00:00",
        tool="เลือกคลิปใน Media Pool ▸ Edit ▸ Append at End (Shift+F12)",
        target="V1 + A1",
        value=tc(plan, 0),
        section=section,
    )


def _blade_steps(guide: ManualGuide, plan: TimelinePlan) -> None:
    section = "ตัดคลิป (Blade) ที่ timecode ต้นฉบับ"
    points = cut_points(plan)
    guide.step(
        "ตัดทุกจุดก่อน แล้วค่อยลบ (ยังไม่ลบระหว่างตัด เพื่อให้ timecode ตรงกับต้นฉบับ)",
        tool="Selection Mode (A)",
        note=f"มีทั้งหมด {len(points)} จุดตัด; ไทม์ไลน์ตอนนี้ยังตรงกับ timecode ของไฟล์ต้นฉบับ",
        section=section,
    )
    for number, point in enumerate(points, start=1):
        guide.step(
            f"จุดตัดที่ {number}: เลื่อน playhead แล้วตัดคลิป",
            tool="คลิก Timeline Viewer ▸ พิมพ์ timecode ▸ Enter ▸ Timeline ▸ Razor (Ctrl+B)",
            target="V1 + A1 (คลิปลิงก์กัน ตัดพร้อมกัน)",
            value=tc_pair(plan, point),
            note="หรือใช้ Blade Edit Mode (B) แล้วคลิกบนเส้น playhead",
            section=section,
        )
    section = "ลบช่วงที่ไม่ใช้ (Ripple Delete) — เริ่มจากท้ายไปต้น"
    ranges = removed_ranges(plan)
    for number, (start, end) in enumerate(reversed(ranges), start=1):
        end_text = tc_pair(plan, end) if end is not None else "จบไฟล์"
        guide.step(
            f"ลบชิ้นที่ {number}",
            tool="Selection Mode (A) คลิกเลือกชิ้น ▸ Edit ▸ Ripple Delete (Shift+Backspace)",
            target="ชิ้นบน V1 + A1",
            value=f"{tc_pair(plan, start)} → {end_text}",
            note="ลบจากท้ายไปต้นเพื่อให้ตำแหน่งของชิ้นที่ยังไม่ลบไม่เลื่อน",
            section=section,
        )
    if not ranges:
        guide.step("ไม่มีช่วงที่ต้องลบ (ใช้ทั้งไฟล์)", section=section)


def _append_steps(guide: ManualGuide, plan: TimelinePlan) -> None:
    section = "วางคลิปตามลำดับ (Source Viewer In/Out ▸ Append)"
    guide.step(
        "คลิปในงานนี้ไม่เรียงตามเวลาต้นฉบับหรือซ้อนกัน จึงใช้ In/Out ทีละคลิปแทนการ Blade",
        note="ไทม์ไลน์ยังว่างอยู่; คลิปแต่ละชิ้นจะต่อท้ายกันตามลำดับด้านล่าง",
        section=section,
    )
    for clip in plan.clips:
        guide.step(
            f"คลิปที่ {clip.index}: เปิดต้นฉบับใน Source Viewer แล้วตั้ง In",
            tool="ดับเบิลคลิกคลิปใน Media Pool ▸ พิมพ์ timecode ▸ Enter ▸ Mark In (I)",
            value=tc_pair(plan, clip.source_start, clip.source_in_frame),
            section=section,
        )
        guide.step(
            f"คลิปที่ {clip.index}: ตั้ง Out ที่เฟรมสุดท้ายที่ใช้",
            tool="พิมพ์ timecode ▸ Enter ▸ Mark Out (O)",
            value=tc(plan, clip.source_out_frame - 1),
            note=f"Out ของ Resolve รวมเฟรมที่มาร์ก จึงมาร์กก่อน {tc(plan, clip.source_out_frame)} หนึ่งเฟรม",
            section=section,
        )
        guide.step(
            f"คลิปที่ {clip.index}: ต่อท้ายไทม์ไลน์",
            tool="Edit ▸ Append at End (Shift+F12)",
            target="V1 + A1",
            value=f"Record IN {tc(plan, clip.record_in_frame)}",
            section=section,
        )


def _result_steps(guide: ManualGuide, plan: TimelinePlan) -> None:
    section = "ลำดับคลิปที่ต้องได้ (ตรวจกับ timecode)"
    for clip in plan.clips:
        guide.step(
            f"คลิปที่ {clip.index} ({clip.segment_id}) ต้องอยู่ตรงนี้",
            tool="เลือกคลิป ▸ ดูช่วงใน Timeline / Inspector ▸ File",
            target="V1",
            value=_clip_value(plan, clip),
            section=section,
        )


def _reframe_steps(guide: ManualGuide, plan: TimelinePlan) -> None:
    section = f"จัดเฟรม ({plan.aspect_label}) ให้ตรงกับเครื่องมือ"
    reframe = plan.reframe
    guide.step("เลือกทุกคลิปบน V1", tool="Timeline ▸ Select All (Ctrl+A)", target="V1", section=section)
    if reframe.profile == "vertical_centered":
        if reframe.resolve_zoom is not None:
            guide.step(
                "ขยายภาพให้เต็มเฟรมแนวตั้ง (center crop)",
                tool="Inspector ▸ Video ▸ Transform ▸ Zoom (X/Y ลิงก์กัน)",
                value=f"{reframe.resolve_zoom:.3f}",
                note=(
                    f"เครื่องมือครอป {reframe.crop_width}x{reframe.crop_height} px ที่ x={reframe.crop_x} "
                    f"จากต้นฉบับ {plan.source.width}x{plan.source.height} แล้วย่อ/ขยายเป็น {plan.width}x{plan.height} "
                    "(FFmpeg ปัดความกว้างลงเป็นเลขคู่ ต่างกันไม่เกิน ~1 px)"
                ),
                section=section,
            )
        else:
            guide.step(
                "ขยายภาพให้เต็มเฟรมแนวตั้ง (center crop)",
                tool="Inspector ▸ Video ▸ Transform ▸ Zoom",
                value="(สูงไทม์ไลน์ ÷ สูงต้นฉบับ) ÷ min(กว้างไทม์ไลน์ ÷ กว้างต้นฉบับ, สูงไทม์ไลน์ ÷ สูงต้นฉบับ)",
                note="ไม่ทราบขนาดต้นฉบับจาก probe; ดูขนาดที่ Media Pool ▸ Clip Attributes",
                section=section,
            )
        guide.step(
            "จัดกึ่งกลาง",
            tool="Inspector ▸ Video ▸ Transform ▸ Position",
            value="X 0.000, Y 0.000",
            note="ทางเลือก: Project Settings ▸ Image Scaling ▸ Mismatched resolution files = Scale full frame with crop แล้วคง Zoom 1.000",
            section=section,
        )
    else:
        guide.step(
            "คงภาพเต็มไม่ครอป (มีแถบดำ)",
            tool="Inspector ▸ Video ▸ Transform ▸ Zoom",
            value="1.000",
            note="ตรงกับ FFmpeg scale (force_original_aspect_ratio=decrease) + pad กึ่งกลาง",
            section=section,
        )


def _caption_steps(guide: ManualGuide, context: GuideContext) -> None:
    plan = context.plan
    captions = plan.captions
    if captions is None:
        return
    section = "ซับไตเติล (Subtitle track ST1)"
    srt = context.files.editor_captions or EDITOR_CAPTIONS
    guide.step("นำเข้าไฟล์ซับเข้า Media Pool", tool="File ▸ Import ▸ Subtitle…", value=srt, section=section)
    guide.step(
        "ลากไฟล์ซับจาก Media Pool ลงไทม์ไลน์ที่จุดเริ่ม",
        tool="ลาก (drag) ไปวางเหนือ V1",
        target="ST1 ที่ 00:00:00:00",
        value=tc(plan, 0),
        note="Resolve สร้าง Subtitle track ให้อัตโนมัติ; เวลาใน .srt เป็นเวลาบนไทม์ไลน์ที่ตัดแล้ว",
        section=section,
    )
    if captions.format == "ass":
        guide.step(
            "ตั้งสไตล์ทั้งแทร็ก",
            tool="คลิกหัวแทร็ก ST1 ▸ Inspector ▸ Track ▸ Style",
            value=f"Font {captions.font}; Size {captions.font_size}; Color ขาว #FFFFFF",
            note=f"ไฟล์ ASS ที่เครื่องมือเผาใช้ {captions.font_size} px บนเฟรมสูง {captions.play_res[1]} px; ปรับ Size ให้ความสูงตัวอักษรเท่าภาพอ้างอิง",
            section=section,
        )
        guide.step(
            "ตั้งขอบตัวอักษรและเงา",
            tool="Inspector ▸ Track ▸ Stroke / Drop Shadow",
            value=f"Stroke ดำ #000000 ขนาด {captions.outline_px}; Drop Shadow ดำ 50% ระยะ {captions.shadow_px}",
            section=section,
        )
        guide.step(
            "ตั้งตำแหน่ง",
            tool="Inspector ▸ Track ▸ Position",
            value=f"ล่างกลาง; ระยะจากขอบล่าง {captions.margin_bottom_px} px; ขอบซ้าย/ขวา {captions.margin_horizontal_px} px",
            section=section,
        )
        guide.step(
            "ห้ามตัดบรรทัดใหม่เอง",
            value=f"สูงสุด {captions.wrap_columns} ตัวอักษรต่อบรรทัด (ไม่นับสระบน/ล่างและวรรณยุกต์)",
            note="ไฟล์ .srt ขึ้นบรรทัดไว้แล้วตามที่เครื่องมือตัดคำไทยด้วย PyThaiNLP newmm",
            section=section,
        )
    else:
        guide.step(
            "ใช้สไตล์ค่าเริ่มต้น",
            note="เครื่องมือเผา SRT ด้วยสไตล์ค่าเริ่มต้นของ libass (ไม่ได้ตั้งฟอนต์/ขนาดเอง)",
            section=section,
        )
    cues = list(context.cues or [])
    if cues and len(cues) <= MAX_CUE_STEPS:
        cue_section = "รายการซับ (ถ้าจะพิมพ์เองทีละ cue)"
        for cue in cues:
            guide.step(
                f"Cue {cue.index}",
                tool="Toolbar ▸ Subtitle ▸ Add Subtitle (หรือดับเบิลคลิก cue ใน ST1)",
                target=f"ST1 {tc_pair(plan, cue.start)} → {tc_pair(plan, cue.end)}",
                value=" / ".join(cue.lines),
                section=cue_section,
            )
    elif cues:
        guide.step(
            f"มี {len(cues)} cue — ใช้ไฟล์ {srt} แทนการพิมพ์เอง",
            section=section,
        )


def _audio_steps(guide: ManualGuide, plan: TimelinePlan) -> None:
    section = "เสียง (A1)"
    if plan.source.has_audio is False:
        guide.step("ไฟล์ต้นฉบับไม่มีเสียง — ไม่มีแทร็กเสียง", section=section)
        return
    guide.step(
        "ใช้เสียงจากต้นฉบับที่ลิงก์กับภาพ (ไม่ต้องแก้)",
        target="A1",
        note="เครื่องมือไม่ได้แทนเสียง ไม่ได้ใส่เพลง และไม่ได้ทำ ducking ในงานนี้",
        section=section,
    )
    if plan.loudness is not None:
        loud = plan.loudness
        guide.step(
            "ตั้ง normalize ความดังตอน Deliver",
            tool="Deliver page ▸ Render Settings ▸ Audio ▸ Normalize Audio ✓",
            value=f"Normalization Mode ITU-R BS.1770-4; Target Loudness {loud.integrated_lufs:g} LKFS; True Peak {loud.true_peak_dbtp:g} dBTP",
            note=(
                f"เครื่องมือใช้ FFmpeg loudnorm แบบ single-pass (I={loud.integrated_lufs:g}, TP={loud.true_peak_dbtp:g}, "
                f"LRA={loud.loudness_range_lu:g}) และ resample เป็น {loud.output_sample_rate} Hz; ค่าที่วัดได้อาจต่างกัน ±1 LU"
            ),
            section=section,
        )


def _deliver_steps(guide: ManualGuide, context: GuideContext) -> None:
    plan = context.plan
    section = "ส่งออก (Deliver page)"
    guide.step("ไปหน้า Deliver", tool="Deliver page (Shift+8) ▸ Render Settings ▸ Custom Export", section=section)
    guide.step("ตั้งรูปแบบไฟล์", tool="Video ▸ Format / Codec", value="Format MP4; Codec H.264", section=section)
    guide.step(
        "ตั้งความละเอียดและเฟรมเรต",
        tool="Video ▸ Resolution / Frame rate",
        value=f"{plan.width} x {plan.height}; {plan.rate.label} fps",
        section=section,
    )
    if context.output.bitrate:
        kbps = round(context.output.bitrate / 1000)
        quality = f"Restrict to {kbps} Kb/s"
        note = "เครื่องมือใช้ libx264 CRF 23 (ค่าเริ่มต้น) preset medium yuv420p; Resolve ไม่มี CRF จึงจำกัด bitrate ให้เท่าไฟล์ของเครื่องมือ"
    else:
        quality = "Automatic ▸ Best"
        note = "เครื่องมือใช้ libx264 CRF 23 (ค่าเริ่มต้น) preset medium yuv420p; Resolve ไม่มี CRF"
    guide.step("ตั้งคุณภาพ", tool="Video ▸ Quality", value=quality, note=note, section=section)
    if plan.source.has_audio is not False:
        rate = context.output.audio_sample_rate or (plan.loudness.output_sample_rate if plan.loudness else plan.source.sample_rate)
        guide.step(
            "ตั้งเสียง",
            tool="Audio ▸ Codec / Bit Rate / Sample Rate",
            value=f"AAC; 192 kb/s; {rate or 'ตามต้นฉบับ'} Hz",
            note="เครื่องมือใช้ AAC ของ FFmpeg โดยไม่กำหนด bitrate (ค่าเริ่มต้นของ encoder)",
            section=section,
        )
    if plan.captions is not None:
        guide.step(
            "เผาซับลงภาพเหมือนเครื่องมือ",
            tool="Subtitle Settings ▸ Export Subtitle ✓ ▸ Format",
            value="Burn into video",
            section=section,
        )
    guide.step("เพิ่มเข้าคิวและเรนเดอร์", tool="Add to Render Queue ▸ Render All", section=section)


def _shortcut_steps(guide: ManualGuide, context: GuideContext) -> None:
    plan = context.plan
    if not context.files.interchange:
        return
    section = "ทางลัด: เปิดไทม์ไลน์สำเร็จรูป (FCPXML / EDL / OTIO)"
    if FCPXML_FILENAME in context.files.interchange:
        guide.step(
            "นำเข้าไทม์ไลน์ FCPXML (มีจุดตัด + Zoom ครบ)",
            tool="File ▸ Import ▸ Timeline… (Ctrl+Shift+I)",
            value=FCPXML_FILENAME,
            note="ในหน้าต่าง Load XML ให้เอาติ๊ก Automatically import source clips into media pool ออก ถ้านำเข้าต้นฉบับไว้แล้ว",
            section=section,
        )
    if CMX_FILENAME in context.files.interchange:
        guide.step(
            "หรือนำเข้า CMX3600 EDL (มีเฉพาะจุดตัด)",
            tool="File ▸ Import ▸ Timeline… ▸ เลือก .edl",
            value=f"{CMX_FILENAME}; frame rate {plan.rate.label}",
            note="EDL จับคู่คลิปด้วยชื่อไฟล์ (FROM CLIP NAME) และไม่พา Zoom/ซับ/เสียงมาด้วย — ทำขั้น Reframe, ซับ, Deliver ต่อ",
            section=section,
        )
    if OTIO_FILENAME in context.files.interchange:
        guide.step(
            "หรือนำเข้า OpenTimelineIO",
            tool="File ▸ Import ▸ Timeline… ▸ เลือก .otio",
            value=OTIO_FILENAME,
            section=section,
        )
    guide.step(
        "ถ้าคลิปขึ้นสีแดง (offline) ให้ชี้ไฟล์ต้นฉบับ",
        tool="Media Pool ▸ คลิกขวาที่คลิป ▸ Relink Selected Clips…",
        value=plan.source.filename,
        section=section,
    )
    guide.step(
        "นำเข้าซับและตั้ง Deliver ตามหัวข้อด้านบน",
        note="ไฟล์ interchange ไม่ได้พาซับมาด้วย",
        section=section,
    )


def _checks(context: GuideContext) -> list[str]:
    plan = context.plan
    checks = [
        f"ไทม์ไลน์ยาว {tc(plan, plan.record_frames)} ({plan.record_frames} เฟรม ที่ {plan.rate.label} fps)",
        f"ไฟล์ที่ได้มีขนาด {plan.width}x{plan.height} และ {plan.rate.label} fps",
        f"มี {len(plan.clips)} คลิปบน V1 ตามลำดับและ timecode ในหัวข้อ \"ลำดับคลิปที่ต้องได้\"",
    ]
    if context.output.duration is not None:
        checks.append(f"ความยาวไฟล์ใกล้เคียงไฟล์ของเครื่องมือ ({context.output.duration:.3f} วินาที)")
    if plan.captions is not None:
        checks.append("ซับขึ้น/หายตรงเวลาใน captions.srt และขึ้นบรรทัดตรงกับภาพอ้างอิง")
    if plan.loudness is not None:
        checks.append(f"ความดังรวมประมาณ {plan.loudness.integrated_lufs:g} LUFS (Fairlight ▸ Loudness meter)")
    if context.files.video:
        checks.append(f"เปิดเทียบกับ `{context.files.video}` ที่เฟรมแรกและเฟรมสุดท้ายของทุกคลิป")
    return checks


def _glossary(plan: TimelinePlan) -> dict[str, str]:
    reframe = plan.reframe
    premiere_scale = f"{reframe.premiere_scale_percent:g}%" if reframe.premiere_scale_percent is not None else "ตามสูตร"
    capcut_scale = f"{reframe.resolve_zoom * 100:.0f}%" if reframe.resolve_zoom is not None else "ตามสูตร"
    return {
        "Timeline resolution / frame rate": f"Premiere: Sequence ▸ Sequence Settings… (Frame Size {plan.width}x{plan.height}, Timebase {plan.rate.label}); CapCut: Ratio ▸ {plan.aspect_label} และ Frame rate ตอน Export",
        "Razor (Ctrl+B) / Blade (B)": "Premiere: Razor Tool (C) หรือ Add Edit (Ctrl+K) ที่ playhead; CapCut: Split (Ctrl+B)",
        "Ripple Delete (Shift+Backspace)": "Premiere: Ripple Delete (Shift+Delete); CapCut: Delete โดยเปิด Main track magnet ให้คลิปชิดกัน",
        "Mark In/Out (I/O) + Append at End": "Premiere: Source Monitor I/O + Overwrite/Insert (. / ,) ; CapCut: ลากคลิปแล้วลากขอบ trim",
        "Inspector ▸ Transform ▸ Zoom": f"Premiere: Effect Controls ▸ Motion ▸ Scale {premiere_scale} (ปิด Set to Frame Size); CapCut: Video ▸ Basic ▸ Scale {capcut_scale}",
        "File ▸ Import ▸ Subtitle": "Premiere: File ▸ Import… (.srt) แล้วลากลง Captions track; CapCut: Captions ▸ Local captions ▸ Import (ชื่อเมนูอาจต่างตามเวอร์ชัน)",
        "Normalize Audio": "Premiere: Clip ▸ Audio Gain ▸ Normalize / Essential Sound ▸ Loudness ▸ Auto-Match; CapCut: Audio ▸ Basic ▸ Normalize loudness",
        "Deliver ▸ H.264 MP4": "Premiere: File ▸ Export ▸ Media (Ctrl+M) ▸ H.264, Target Bitrate; CapCut: Export ▸ Format MP4, Codec H.264",
        "File ▸ Import ▸ Timeline (FCPXML/EDL/OTIO)": "Premiere: File ▸ Import… เลือก timeline.edl (CMX3600); FCPXML ต้องแปลงเป็น FCP 7 XML ก่อน (เช่น XtoCC); CapCut: นำเข้าไม่ได้ — ทำตามขั้นตอนด้วยมือ",
    }


def build_resolve_guide(context: GuideContext) -> ManualGuide:
    """Translate one job's timeline plan into numbered DaVinci Resolve steps."""

    plan = context.plan
    if context.engine_outcome == "accepted":
        summary = (
            "ทำตามขั้นตอนนี้ใน DaVinci Resolve จะได้คลิปเดียวกับที่เครื่องมือ render "
            "(จุดตัด, ลำดับคลิป, การครอป, ซับ, เสียง, การส่งออก); timecode ทุกค่ามาจาก EDL ชุดเดียวกับที่ engine ใช้"
        )
    else:
        summary = (
            f"เครื่องมือทำงานนี้ไม่สำเร็จ (`{context.engine_outcome}`) แต่ไทม์ไลน์ด้านล่างมาจาก EDL เดียวกัน "
            "จึงทำต่อเองใน DaVinci Resolve ได้"
        )
    cuts = len(cut_points(plan)) if plan.is_sequential else len(plan.clips) * 3
    cue_count = len(context.cues or [])
    guide = ManualGuide(
        program=PROGRAM,
        program_version=PROGRAM_VERSION,
        alternatives=list(ALTERNATIVES),
        title=plan.title,
        summary=summary,
        estimated_minutes=round(10 + cuts * 0.5 + cue_count * 0.2 + (3 if plan.loudness else 0), 1),
        setup=_setup(context),
    )
    _layers(guide, context)
    _project_steps(guide, plan)
    _import_steps(guide, plan, append_whole=plan.is_sequential)
    if plan.is_sequential:
        _blade_steps(guide, plan)
    else:
        _append_steps(guide, plan)
    _result_steps(guide, plan)
    _reframe_steps(guide, plan)
    _caption_steps(guide, context)
    _audio_steps(guide, plan)
    _deliver_steps(guide, context)
    _shortcut_steps(guide, context)
    guide.checks = _checks(context)
    guide.glossary = _glossary(plan)
    return guide


__all__ = [
    "ALTERNATIVES",
    "PROGRAM",
    "DeliveredOutput",
    "GuideContext",
    "GuideFiles",
    "build_resolve_guide",
    "cut_points",
    "removed_ranges",
    "tc",
    "tc_pair",
]
