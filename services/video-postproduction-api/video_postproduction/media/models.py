"""Typed metadata returned by media inspection."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class VideoStreamMetadata:
    codec_name: str | None
    width: int
    height: int
    fps: float | None = None
    pix_fmt: str | None = None


@dataclass(frozen=True, slots=True)
class AudioStreamMetadata:
    codec_name: str | None
    sample_rate: int | None = None
    channels: int | None = None


@dataclass(frozen=True, slots=True)
class MediaMetadata:
    duration: float
    format_name: str | None
    size_bytes: int | None
    bitrate: int | None
    video: VideoStreamMetadata
    audio: AudioStreamMetadata | None = None
