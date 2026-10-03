"""Approved, immutable mastering recipes for AudioIR V1."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class FilterStage:
    """One fixed FFmpeg filter that runs before loudness normalisation.

    ``params`` are literal FFmpeg option values; they are allowlisted here and
    never come from a request.
    """

    name: str
    params: tuple[tuple[str, str], ...]

    def graph(self) -> str:
        return self.name + "=" + ":".join(f"{key}={value}" for key, value in self.params)

    def to_dict(self) -> dict[str, object]:
        return {"filter": self.name, "params": dict(self.params)}


@dataclass(frozen=True)
class MasteringPreset:
    name: str
    channels: int
    integrated_lufs: float
    true_peak_dbtp: float
    loudness_range_lu: float
    dual_mono: bool
    # Filters applied in this order before ``loudnorm`` (empty = loudness only).
    chain: tuple[FilterStage, ...] = ()


# Speech clean-up chain: 80 Hz high-pass (12 dB/oct), a gentle downward
# expander as noise gate (threshold 0.01 = -40 dBFS, range 0.06 = -24.4 dB),
# and a moderate de-esser.
HIGH_PASS_80 = FilterStage("highpass", (("f", "80"), ("p", "2")))
VOICE_GATE = FilterStage("agate", (("threshold", "0.01"), ("ratio", "2"), ("attack", "10"), ("release", "250"), ("range", "0.06")))
VOICE_DEESSER = FilterStage("deesser", (("i", "0.4"), ("m", "0.5"), ("f", "0.5"), ("s", "o")))

PRESETS: dict[str, MasteringPreset] = {
    "podcast-mono.v1": MasteringPreset("podcast-mono.v1", 1, -19.0, -1.5, 11.0, True),
    "podcast-stereo.v1": MasteringPreset("podcast-stereo.v1", 2, -16.0, -1.5, 11.0, False),
    "music-stereo.v1": MasteringPreset("music-stereo.v1", 2, -14.0, -1.0, 11.0, False),
    "apple-music-stereo.v1": MasteringPreset("apple-music-stereo.v1", 2, -16.0, -1.0, 11.0, False),
    "broadcast-r128-stereo.v1": MasteringPreset("broadcast-r128-stereo.v1", 2, -23.0, -1.0, 20.0, False),
    "audiobook-acx-mono.v1": MasteringPreset("audiobook-acx-mono.v1", 1, -20.0, -3.0, 11.0, True),
    "voice-clean-mono.v1": MasteringPreset(
        "voice-clean-mono.v1", 1, -19.0, -1.5, 11.0, True, (HIGH_PASS_80, VOICE_GATE, VOICE_DEESSER)
    ),
}

# Which preset meets which delivery target (documentation and manual guide).
PLATFORM_TARGETS: dict[str, str] = {
    "Spotify / YouTube / Tidal / Amazon Music (-14 LUFS)": "music-stereo.v1",
    "Apple Music Sound Check (-16 LUFS)": "apple-music-stereo.v1",
    "Apple Podcasts / Spotify podcasts, stereo (-16 LUFS)": "podcast-stereo.v1",
    "Podcast, mono voice (-19 LUFS)": "podcast-mono.v1",
    "Podcast voice with clean-up (high-pass, gate, de-ess; -19 LUFS)": "voice-clean-mono.v1",
    "EBU R128 broadcast (-23 LUFS, -1 dBTP)": "broadcast-r128-stereo.v1",
    "Audible ACX audiobook (approximation: -20 LUFS, -3 dBTP)": "audiobook-acx-mono.v1",
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
