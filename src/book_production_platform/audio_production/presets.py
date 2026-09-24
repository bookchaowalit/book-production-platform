"""Approved, immutable mastering recipes for AudioIR V1."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class MasteringPreset:
    name: str
    channels: int
    integrated_lufs: float
    true_peak_dbtp: float
    loudness_range_lu: float
    dual_mono: bool


PRESETS: dict[str, MasteringPreset] = {
    "podcast-mono.v1": MasteringPreset("podcast-mono.v1", 1, -19.0, -1.5, 11.0, True),
    "podcast-stereo.v1": MasteringPreset("podcast-stereo.v1", 2, -16.0, -1.5, 11.0, False),
    "music-stereo.v1": MasteringPreset("music-stereo.v1", 2, -14.0, -1.0, 11.0, False),
}

OUTPUT_SAMPLE_RATE = 48_000
OUTPUT_SAMPLE_WIDTH_BYTES = 3
OUTPUT_CODEC = "pcm_s24le"
MASTERING_RECIPE_VERSION = "audio-master.v1a.1"
LOUDNESS_TOLERANCE_LU = 0.5
TRUE_PEAK_TOLERANCE_DB = 0.1
MAX_SOURCE_BYTES = 2 * 1024**3
MAX_AUDIO_SECONDS = 2 * 60 * 60
MAX_PROCESS_OUTPUT_BYTES = 1024 * 1024
MAX_PROCESS_TIMEOUT_SECONDS = 1800
MAX_LOCK_WAIT_SECONDS = 30
