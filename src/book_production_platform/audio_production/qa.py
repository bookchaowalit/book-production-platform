"""WAV structural and post-master loudness QA."""

from __future__ import annotations

import json
import math
import re
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .errors import AudioProductionError
from .presets import (
    LOUDNESS_TOLERANCE_LU,
    MAX_AUDIO_SECONDS,
    MAX_SOURCE_BYTES,
    OUTPUT_SAMPLE_RATE,
    OUTPUT_SAMPLE_WIDTH_BYTES,
    TRUE_PEAK_TOLERANCE_DB,
    MasteringPreset,
)

_JSON_OBJECT = re.compile(r"\{[^{}]*\}", re.DOTALL)


@dataclass(frozen=True)
class WaveInfo:
    channels: int
    sample_width_bytes: int
    sample_rate: int
    frames: int
    duration_seconds: float
    byte_size: int


@dataclass(frozen=True)
class LoudnessStats:
    integrated_lufs: float
    true_peak_dbtp: float
    loudness_range_lu: float
    threshold_lufs: float
    normalization_type: str
    target_offset_db: float

    def to_dict(self) -> dict[str, float | str]:
        return {
            "integrated_lufs": self.integrated_lufs,
            "true_peak_dbtp": self.true_peak_dbtp,
            "loudness_range_lu": self.loudness_range_lu,
            "threshold_lufs": self.threshold_lufs,
            "normalization_type": self.normalization_type,
            "target_offset_db": self.target_offset_db,
        }


def inspect_pcm_wave(path: Path, *, output: bool = False) -> WaveInfo:
    """Read WAV metadata and reject unsupported or over-limit PCM sources."""
    code = "invalid_master" if output else "invalid_wav"
    if path.is_symlink() or not path.is_file():
        raise AudioProductionError(code, "WAV file is unavailable")
    try:
        size = path.stat().st_size
        if size < 44 or size > MAX_SOURCE_BYTES:
            raise AudioProductionError("audio_resource_cap_exceeded", "WAV size is outside the supported range")
        with wave.open(str(path), "rb") as source:
            channels = source.getnchannels()
            sample_width = source.getsampwidth()
            sample_rate = source.getframerate()
            frames = source.getnframes()
            compression = source.getcomptype()
    except AudioProductionError:
        raise
    except (OSError, EOFError, wave.Error) as error:
        raise AudioProductionError(code, "WAV header cannot be read") from error
    if compression != "NONE" or channels not in {1, 2} or sample_width not in {2, 3, 4}:
        raise AudioProductionError("unsupported_wav_format", "Only mono/stereo integer PCM WAV is supported")
    if not 8_000 <= sample_rate <= 192_000 or frames <= 0:
        raise AudioProductionError("invalid_wav", "WAV sample rate or frame count is invalid")
    duration = frames / sample_rate
    if duration > MAX_AUDIO_SECONDS:
        raise AudioProductionError("audio_resource_cap_exceeded", "WAV duration exceeds the two-hour limit")
    if output and (sample_width != OUTPUT_SAMPLE_WIDTH_BYTES or sample_rate != OUTPUT_SAMPLE_RATE):
        raise AudioProductionError("invalid_master", "Master WAV does not match the PCM24/48 kHz profile")
    return WaveInfo(channels, sample_width, sample_rate, frames, duration, size)


def parse_loudness_stats(stderr: bytes) -> LoudnessStats:
    """Parse the last complete loudnorm JSON report from bounded FFmpeg logs."""
    text = stderr.decode("utf-8", errors="replace")
    for match in reversed(list(_JSON_OBJECT.finditer(text))):
        try:
            payload: Any = json.loads(match.group(0))
        except json.JSONDecodeError:
            continue
        if not isinstance(payload, dict):
            continue
        integrated = payload.get("input_i")
        if isinstance(integrated, str) and integrated.strip().lower() in {"-inf", "-infinity"}:
            raise AudioProductionError("silent_audio", "Silent audio cannot be loudness mastered")
        values = [
            integrated,
            payload.get("input_tp"),
            payload.get("input_lra"),
            payload.get("input_thresh"),
            payload.get("target_offset"),
        ]
        numeric: list[float] = []
        for value in values:
            if isinstance(value, bool) or not isinstance(value, (str, int, float)):
                break
            try:
                parsed_value = float(value)
            except (ValueError, OverflowError):
                break
            if not math.isfinite(parsed_value):
                break
            numeric.append(parsed_value)
        if len(numeric) != 5:
            continue
        normalization_type = payload.get("normalization_type", "unknown")
        if not isinstance(normalization_type, str) or not normalization_type:
            normalization_type = "unknown"
        return LoudnessStats(numeric[0], numeric[1], numeric[2], numeric[3], normalization_type, numeric[4])
    raise AudioProductionError("measurement_unavailable", "FFmpeg did not return valid loudness measurements")


def master_qa(source: WaveInfo, output: WaveInfo, measured: LoudnessStats, preset: MasteringPreset) -> dict[str, bool]:
    """Verify output format, channels, duration, integrated loudness, and true peak."""
    duration_tolerance = max(1 / source.sample_rate, 1 / OUTPUT_SAMPLE_RATE)
    return {
        "pcm24_48khz": output.sample_width_bytes == OUTPUT_SAMPLE_WIDTH_BYTES and output.sample_rate == OUTPUT_SAMPLE_RATE,
        "channel_count_preserved": output.channels == source.channels == preset.channels,
        "duration_preserved": abs(output.duration_seconds - source.duration_seconds) <= duration_tolerance,
        "integrated_loudness_within_tolerance": abs(measured.integrated_lufs - preset.integrated_lufs) <= LOUDNESS_TOLERANCE_LU,
        "true_peak_below_ceiling": measured.true_peak_dbtp <= preset.true_peak_dbtp + TRUE_PEAK_TOLERANCE_DB,
    }
