"""Validated transcript models shared by every transcription provider."""

from __future__ import annotations

import math

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class TranscriptValidationError(ValueError):
    """Raised when provider output cannot be represented safely."""


def _finite(value: float) -> float:
    if not math.isfinite(value):
        raise ValueError("timestamp must be finite")
    return value


class Word(BaseModel):
    model_config = ConfigDict(extra="ignore")

    text: str
    start: float = Field(ge=0)
    end: float = Field(ge=0)
    confidence: float | None = Field(default=None, ge=0, le=1)

    _finite_start = field_validator("start")(_finite)
    _finite_end = field_validator("end")(_finite)

    @model_validator(mode="after")
    def validate_range(self) -> Word:
        if self.start >= self.end:
            raise ValueError("word start must be before end")
        return self


class TranscriptSegment(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    start: float = Field(ge=0)
    end: float = Field(ge=0)
    text: str
    words: list[Word] = Field(default_factory=list)

    _finite_start = field_validator("start")(_finite)
    _finite_end = field_validator("end")(_finite)

    @model_validator(mode="after")
    def validate_range(self) -> TranscriptSegment:
        if self.start >= self.end:
            raise ValueError("segment start must be before end")
        return self


class NormalizedTranscript(BaseModel):
    model_config = ConfigDict(extra="ignore")

    schema_version: str = "transcript.v1"
    provider: str
    model: str
    language: str | None = None
    duration: float = Field(ge=0)
    segments: list[TranscriptSegment] = Field(default_factory=list)

    _finite_duration = field_validator("duration")(_finite)
