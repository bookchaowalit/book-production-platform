"""The versioned ``transcript-artifact.v1`` contract and its validator.

The published contract is the JSON Schema at
``schemas/transcript-artifact.v1.schema.json`` next to the service root. This
module validates payloads against that schema file (authoritative structure)
and mirrors the same constraints in typed pydantic models for deterministic
programmatic access. A test keeps the two representations aligned on a matrix
of valid and invalid cases.

Published artifacts never contain absolute local paths; ``source_name`` is
validated as a bare file name.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Literal

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError as _JsonSchemaError
from jsonschema.exceptions import ValidationError as _JsonSchemaValidationError
from pydantic import BaseModel, ConfigDict, Field, model_validator

from .paths import SCHEMAS_DIR

TRANSCRIPT_ARTIFACT_SCHEMA_VERSION = "transcript-artifact.v1"
TRANSCRIPT_ARTIFACT_SCHEMA_ID = "urn:solo-empire:video-postproduction:transcript-artifact.v1"
_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_BARE_NAME_PATTERN = re.compile(r"^[^/\\:]*$")

SCHEMA_PATH = SCHEMAS_DIR / "transcript-artifact.v1.schema.json"


class ValidationError(ValueError):
    """Raised when a payload is not a valid ``transcript-artifact.v1``."""


class TranscriptSegment(BaseModel):
    """One verbatim cue: ``start_seconds < end_seconds`` and non-empty text."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    start_seconds: float = Field(ge=0)
    end_seconds: float = Field(gt=0)
    text: str = Field(min_length=1, max_length=2000)
    speaker: str | None = Field(default=None, min_length=1, max_length=120)

    @model_validator(mode="after")
    def _check_order(self) -> TranscriptSegment:
        if self.end_seconds <= self.start_seconds:
            raise ValueError("end_seconds must be greater than start_seconds")
        return self


class TranscriptArtifact(BaseModel):
    """Typed view of the ``transcript-artifact.v1`` contract."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    schema_version: Literal["transcript-artifact.v1"]
    transcript_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")
    segments: list[TranscriptSegment] = Field(min_length=1, max_length=20000)
    language: str | None = Field(default=None, min_length=2, max_length=35)
    duration_seconds: float | None = Field(default=None, ge=0)
    source_tool: str | None = Field(default=None, min_length=1, max_length=120)
    source_name: str | None = Field(default=None, min_length=1, max_length=255)

    @model_validator(mode="after")
    def _check_source_name(self) -> TranscriptArtifact:
        if self.source_name is not None and not _BARE_NAME_PATTERN.fullmatch(self.source_name):
            raise ValueError("source_name must be a bare file name without path separators")
        return self


def load_schema() -> dict[str, Any]:
    """Load the published JSON Schema contract from disk."""

    schema: dict[str, Any] = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    return schema


def schema_issues(payload: Any) -> list[str]:
    """Return sorted JSON Schema error messages for the payload."""

    schema = load_schema()
    validator = Draft202012Validator(schema)
    messages: list[str] = []
    for error in validator.iter_errors(payload):
        path = ".".join(str(part) for part in error.absolute_path) or "$"
        messages.append(f"{path}: {error.message}")
    return sorted(messages)


def validate_transcript_artifact_payload(payload: Any) -> list[str]:
    """Validate a raw payload against the published contract.

    Returns the sorted list of contract violations; an empty list means the
    payload conforms to ``transcript-artifact.v1``.
    """

    try:
        return schema_issues(payload)
    except _JsonSchemaValidationError as exc:  # pragma: no cover - defensive
        raise ValidationError(f"schema check failed: {exc.message}") from exc
    except _JsonSchemaError as exc:  # pragma: no cover - defensive
        raise ValidationError(f"contract schema is invalid: {exc.message}") from exc


def parse_transcript_artifact(payload: Any) -> TranscriptArtifact:
    """Parse and fully validate a payload, raising :class:`ValidationError`."""

    issues = validate_transcript_artifact_payload(payload)
    if issues:
        raise ValidationError("transcript artifact failed contract validation: " + "; ".join(issues[:5]))
    try:
        return TranscriptArtifact.model_validate(payload)
    except Exception as exc:  # pragma: no cover - schema/pydantic drift guard
        raise ValidationError(f"transcript artifact is not a valid contract instance: {exc}") from exc


def load_transcript_artifact(path: str | Path) -> TranscriptArtifact:
    """Read a JSON file and parse it as a ``transcript-artifact.v1``."""

    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except OSError as exc:
        raise ValidationError(f"cannot read transcript artifact: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise ValidationError(f"transcript artifact is not valid JSON: {exc}") from exc
    return parse_transcript_artifact(payload)
