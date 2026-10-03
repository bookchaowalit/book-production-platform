"""Timecode helpers for timeline-based manual guides (video, audio)."""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal


def seconds_to_timecode(seconds: float, *, milliseconds: bool = True) -> str:
    """``83.4`` → ``00:01:23.400`` (or ``00:01:23`` without milliseconds)."""

    total_ms = int((Decimal(str(seconds)) * 1000).to_integral_value(rounding=ROUND_HALF_UP))
    hours, rest = divmod(total_ms, 3_600_000)
    minutes, rest = divmod(rest, 60_000)
    secs, ms = divmod(rest, 1000)
    base = f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{base}.{ms:03d}" if milliseconds else base


def frames_to_timecode(seconds: float, fps: int) -> str:
    """SMPTE-style ``HH:MM:SS:FF`` for editors that type frame timecode (non-drop)."""

    total_frames = int((Decimal(str(seconds)) * fps).to_integral_value(rounding=ROUND_HALF_UP))
    hours, rest = divmod(total_frames, 3600 * fps)
    minutes, rest = divmod(rest, 60 * fps)
    secs, frames = divmod(rest, fps)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}:{frames:02d}"


__all__ = ["frames_to_timecode", "seconds_to_timecode"]
