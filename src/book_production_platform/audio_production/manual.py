"""Manual-reproduction guide for Audio Master: the same master in Audacity.

The steps follow the engine's FFmpeg graph in order: the preset's fixed
clean-up chain (if any), then ``loudnorm`` two-pass linear normalisation to the
preset's integrated loudness under its true-peak ceiling, then 48 kHz / 24-bit
PCM WAV export with metadata stripped. The linear gain is copied from the
engine's first-pass measurement (``target − measured``) rather than estimated,
and the checks compare against the measured loudness of ``master.wav``.

Where Audacity has no equivalent control (FFmpeg ``deesser``) or a different
algorithm (``agate`` is a downward expander, Audacity's Noise Gate is a gate;
Audacity's Limiter works on sample peaks, not true peak), the guide says so and
names the closest tool in Adobe Audition or iZotope RX.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any

from book_production_platform.audio_ir import AudioIR
from book_production_platform.production_trace import ManualGuide, seconds_to_timecode

from .presets import (
    LOUDNESS_TOLERANCE_LU,
    OUTPUT_SAMPLE_RATE,
    TRUE_PEAK_TOLERANCE_DB,
    FilterStage,
    MasteringPreset,
)

AUDACITY_VERSION = "3.7"


def _db(linear: str) -> float:
    return round(20 * math.log10(float(linear)), 1)


def linear_gain_db(preset: MasteringPreset, before: Mapping[str, Any]) -> float:
    """The gain loudnorm applies in linear mode: target minus the first-pass measurement."""

    return round(preset.integrated_lufs - float(before["integrated_lufs"]), 2)


def _filter_steps(guide: ManualGuide, stage: FilterStage) -> None:
    params = dict(stage.params)
    section = "ลบเสียงรบกวนและแต่งเสียงพูด (ตามลำดับของเครื่องมือ)"
    if stage.name == "highpass":
        roll_off = int(params.get("p", "2")) * 6
        guide.step(
            "ตัดความถี่ต่ำ (เสียงฮัม ลม เสียงสั่นไมค์)",
            tool="Effect ▸ EQ and Filters ▸ High-Pass Filter…",
            value=f"Frequency (Hz) = {params['f']}, Roll-off = {roll_off} dB per octave",
            note=f"FFmpeg: `{stage.graph()}`",
            section=section,
        )
    elif stage.name == "agate":
        guide.step(
            "ลดเสียงพื้นช่วงที่ไม่มีเสียงพูด",
            tool="Effect ▸ Noise Removal and Repair ▸ Noise Gate…",
            value=(
                f"Gate threshold (dB) = {_db(params['threshold'])}, Level reduction (dB) = {_db(params['range'])}, "
                f"Attack (ms) = {params['attack']}, Hold (ms) = 0, Decay (ms) = {params['release']}"
            ),
            note=(
                f"FFmpeg ใช้ downward expander ratio {params['ratio']}:1 (`{stage.graph()}`) ส่วน Noise Gate ของ Audacity "
                "เป็น gate จึงใกล้เคียงแต่ไม่เท่ากันทุกตัวอย่าง; Audition: `Effects ▸ Amplitude and Compression ▸ Dynamics` (Expander)"
            ),
            section=section,
        )
    elif stage.name == "deesser":
        guide.step(
            "ลดเสียง ส/ซ/ช ที่แหลมเกิน (de-ess)",
            tool="Adobe Audition: Effects ▸ Amplitude and Compression ▸ DeEsser… (Audacity ไม่มีในตัว)",
            value=f"intensity {params['i']}, max reduction {params['m']}, keep {params['f']} of original, mode {params['s']}",
            note=(
                f"FFmpeg: `{stage.graph()}`; ค่าของ FFmpeg ไม่ใช่หน่วย dB/Hz แบบ Audition จึงเป็นการเทียบโดยประมาณ; "
                "iZotope RX: `De-ess` (Classic, Sensitivity ปานกลาง)"
            ),
            section=section,
        )
    else:  # pragma: no cover - every registered stage is mapped above
        guide.step("ใช้ filter ตามค่าของเครื่องมือ", value=stage.graph(), section=section)


def build_audacity_guide(ir: AudioIR, preset: MasteringPreset, manifest: Mapping[str, Any]) -> ManualGuide:
    """Translate one accepted master (IR + preset + measured QA) into numbered Audacity steps."""

    source = manifest["source"]
    output = manifest["output"]
    before = manifest["measurements"]["before"]
    after = manifest["measurements"]["after"]
    channels = "Mono" if preset.channels == 1 else "Stereo"
    duration = seconds_to_timecode(float(source["duration_seconds"]))
    source_name = ir.source_path.split("/")[-1]
    gain = linear_gain_db(preset, before)
    linear = str(manifest.get("render_normalization_type", "")).lower() == "linear"

    guide = ManualGuide(
        program="Audacity",
        program_version=AUDACITY_VERSION,
        alternatives=["Adobe Audition", "iZotope RX / Ozone"],
        title=f"Audio master `{ir.audio_id}` · preset `{preset.name}`",
        summary=(
            f"ทำตามขั้นตอนนี้ใน Audacity จะได้ `master.wav` {channels} {OUTPUT_SAMPLE_RATE} Hz 24-bit ที่ดัง "
            f"{preset.integrated_lufs:g} LUFS (±{LOUDNESS_TOLERANCE_LU:g} LU) และ true peak ไม่เกิน {preset.true_peak_dbtp:g} dBTP "
            "เหมือนไฟล์ของเครื่องมือ; ค่าตัวเลขคัดลอกจากการวัดของเครื่องมือ อย่าประเมินเอง"
        ),
        estimated_minutes=round(5 + 2 * len(preset.chain) + float(source["duration_seconds"]) / 60, 1),
        setup=[
            f"เปิด Audacity {AUDACITY_VERSION} ▸ `Audio Setup ▸ Audio Settings… ▸ Project Sample Rate` = {OUTPUT_SAMPLE_RATE} Hz",
            "`Edit ▸ Preferences ▸ Quality ▸ Default Sample Format` = 32-bit float (ประมวลผลแบบ float เหมือน FFmpeg)",
            (
                f"เตรียมไฟล์ต้นฉบับ `{source_name}` ({source['channels']} ch, {source['sample_rate']} Hz, "
                f"{int(source['sample_width_bytes']) * 8}-bit, ยาว {duration})"
            ),
        ],
        glossary={
            "LUFS": "ความดังที่หูรับรู้แบบ integrated (ITU-R BS.1770)",
            "dBTP": "true peak — ยอดคลื่นจริงหลัง oversampling ใช้กันเสียงแตกตอนแปลงเป็น MP3/AAC",
            "linear normalization": "ปรับ gain คงที่ทั้งไฟล์ ไม่บีบ dynamic",
        },
    )
    guide.layer("Track 1", f"{channels} audio track", content=source_name, position=f"00:00:00.000 – {duration}")
    for stage in preset.chain:
        guide.layer(stage.name, "Effect (destructive)", content=stage.graph(), position="ทั้งแทร็ก")
    guide.layer("loudnorm", "Effect (destructive)", content=f"I={preset.integrated_lufs:g} TP={preset.true_peak_dbtp:g} LRA={preset.loudness_range_lu:g}", position="ทั้งแทร็ก")
    guide.layer("Export", "WAV (Microsoft) Signed 24-bit PCM", content="master.wav", position=f"{OUTPUT_SAMPLE_RATE} Hz, {channels}")

    guide.step("นำเข้าไฟล์ต้นฉบับ", tool="File ▸ Import ▸ Audio… (Ctrl+Shift+I)", value=source_name, section="นำเข้า")
    guide.step("ตรวจหัวแทร็กว่าเป็นจำนวนช่องเสียงถูกต้อง", target="Track Control Panel (ซ้ายของแทร็ก)", value=channels, section="นำเข้า")
    guide.step("เลือกทั้งแทร็ก", tool="Select ▸ All (Ctrl+A)", target=f"00:00:00.000 – {duration}", section="นำเข้า")

    for stage in preset.chain:
        _filter_steps(guide, stage)

    section = "ปรับความดัง (loudnorm)"
    if linear:
        guide.step(
            "เพิ่ม/ลด gain คงที่ทั้งไฟล์ตามที่เครื่องมือวัดได้ (ตรงกับ loudnorm linear mode)",
            tool="Effect ▸ Volume and Compression ▸ Amplify…",
            value=f"Amplification (dB) = {gain:+.2f}, Allow clipping = off",
            note=(
                f"วัดรอบแรกได้ {float(before['integrated_lufs']):g} LUFS, true peak {float(before['true_peak_dbtp']):g} dBTP; "
                f"{preset.integrated_lufs:g} − ({float(before['integrated_lufs']):g}) = {gain:+.2f} dB"
            ),
            section=section,
        )
    else:
        guide.step(
            "ปรับความดังแบบ perceived loudness",
            tool="Effect ▸ Volume and Compression ▸ Loudness Normalization…",
            value=f"Normalize perceived loudness to {preset.integrated_lufs:g} LUFS, Treat mono as stereo = {'on' if preset.dual_mono and preset.channels == 1 else 'off'}",
            note=(
                f"เครื่องมือใช้ loudnorm โหมด `{manifest.get('render_normalization_type', 'unknown')}` (ปรับ gain ตามเวลา) "
                "ซึ่ง Audacity ทำซ้ำแบบเป๊ะไม่ได้; ผลจะใกล้เคียงในช่วง tolerance"
            ),
            section=section,
        )
    guide.step(
        "กันยอดคลื่นเกินเพดาน",
        tool="Effect ▸ Volume and Compression ▸ Limiter…",
        value=f"Threshold (dB) = {preset.true_peak_dbtp:g}, Make-up target (dB) = {preset.true_peak_dbtp:g}, Lookahead (ms) = 1",
        note=(
            f"Audacity ก่อน 3.4: `Limit to (dB) = {preset.true_peak_dbtp:g}`, `Apply Make-up Gain = No`; "
            "Limiter ของ Audacity วัด sample peak ไม่ใช่ true peak — ถ้ามิเตอร์ true peak ยังเกิน ให้ลดเพดานลงทีละ 0.1 dB "
            "(Audition: `Effects ▸ Amplitude and Compression ▸ Hard Limiter`)"
        ),
        section=section,
    )
    if preset.dual_mono and preset.channels == 1:
        guide.step(
            "ถ้าใช้ Loudness Normalization แทน Amplify ให้เปิดตัวเลือกนี้ (เครื่องมือวัด mono แบบ dual mono)",
            tool="Loudness Normalization… ▸ Treat mono as stereo",
            value="on",
            section=section,
        )

    section = "ส่งออก"
    guide.step("ล้าง metadata (เครื่องมือตัด metadata ทั้งหมด)", tool="Edit ▸ Metadata Editor…", value="Clear", section=section)
    guide.step("ส่งออกเป็น WAV", tool="File ▸ Export Audio… (Ctrl+Shift+E)", value="Format = WAV (Microsoft)", section=section)
    guide.step(
        "ตั้งค่าไฟล์",
        target="Export Audio dialog",
        value=f"Channels = {channels}, Sample Rate = {OUTPUT_SAMPLE_RATE} Hz, Encoding = Signed 24-bit PCM",
        section=section,
    )
    guide.step("ตั้งชื่อไฟล์แล้วกด Export", target="File Name", value="master.wav", section=section)

    guide.checks = [
        f"ความดัง integrated {preset.integrated_lufs:g} LUFS ±{LOUDNESS_TOLERANCE_LU:g} LU — เครื่องมือวัด `master.wav` ได้ {float(after['integrated_lufs']):g} LUFS",
        f"true peak ≤ {preset.true_peak_dbtp + TRUE_PEAK_TOLERANCE_DB:g} dBTP — เครื่องมือวัดได้ {float(after['true_peak_dbtp']):g} dBTP",
        f"loudness range ของเครื่องมือ {float(after['loudness_range_lu']):g} LU (เป้า ≤ {preset.loudness_range_lu:g} LU ถ้าเป็น linear)",
        f"{output['channels']} ช่องเสียง, {output['sample_rate']} Hz, {int(output['sample_width_bytes']) * 8}-bit PCM, ยาว {seconds_to_timecode(float(output['duration_seconds']))}",
        "วัดด้วยมิเตอร์ LUFS/true peak (เช่น Youlean Loudness Meter ใน Audacity `Effect ▸ Add / Remove Plugins…`, หรือ Audition `Window ▸ Amplitude Statistics`) แล้วเทียบกับตัวเลขด้านบน",
    ]
    if preset.name == "audiobook-acx-mono.v1":
        guide.checks.append("ACX วัด RMS -18 ถึง -23 dB และ noise floor ≤ -60 dB ซึ่งเครื่องมือไม่ได้ตรวจ — ใช้ `Analyze ▸ ACX Check` ของ Audacity (เปิดใน `Tools ▸ Plugin Manager` ถ้ายังไม่เห็น) ก่อนส่ง")
    return guide


__all__ = ["AUDACITY_VERSION", "build_audacity_guide", "linear_gain_db"]
