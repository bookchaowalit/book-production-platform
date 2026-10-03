"""Typed, allowlisted input contract for Audio Master V1A."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class AudioIR(BaseModel):
    """A single local WAV source and a named mastering preset."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    schema_version: Literal["audio-ir.v1"]
    audio_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
    source_path: str = Field(min_length=5, max_length=240)
    preset: Literal[
        "podcast-mono.v1",
        "podcast-stereo.v1",
        "music-stereo.v1",
        "apple-music-stereo.v1",
        "broadcast-r128-stereo.v1",
        "audiobook-acx-mono.v1",
        "voice-clean-mono.v1",
    ]

    @field_validator("source_path")
    @classmethod
    def wav_only(cls, value: str) -> str:
        if not value.lower().endswith((".wav", ".wave")):
            raise ValueError("source_path must name a WAV file")
        if "\x00" in value:
            raise ValueError("source_path must not contain NUL")
        return value
