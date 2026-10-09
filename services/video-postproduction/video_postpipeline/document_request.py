"""Convert a transcript artifact into a Documents service render request.

The emitted payload conforms to the ``transcript-document-request.v1``
contract owned by ``services/document-production-api``; this service embeds a
byte-identical schema copy under ``schemas/`` and validates every emitted
payload against it before writing. The resulting document is a deterministic,
verbatim, timestamped transcript — semantic summarization requires AI and is
deferred to T8 / Phase 4 by roadmap decision.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any, Literal

from jsonschema import Draft202012Validator
from pydantic import BaseModel, ConfigDict, Field, model_validator

from .contract import TranscriptArtifact
from .paths import SCHEMAS_DIR

TRANSCRIPT_DOCUMENT_REQUEST_SCHEMA_VERSION = "transcript-document-request.v1"
DOCUMENT_REQUEST_SCHEMA_PATH = SCHEMAS_DIR / "transcript-document-request.v1.schema.json"
_DEFAULT_LOCALE = "th-TH"
_LANGUAGE_LOCALES = {"th": "th-TH", "en": "en-US"}


class DocumentRequestError(ValueError):
    """Raised when a transcript artifact cannot become a document request."""


class RequestSegment(BaseModel):
    """Typed view of one segment in the emitted document request."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    start_seconds: float = Field(ge=0)
    end_seconds: float = Field(gt=0)
    text: str = Field(min_length=1, max_length=2000)
    speaker: str | None = Field(default=None, min_length=1, max_length=120)

    @model_validator(mode="after")
    def _check_order(self) -> RequestSegment:
        if self.end_seconds <= self.start_seconds:
            raise DocumentRequestError("end_seconds must be greater than start_seconds")
        return self


class TranscriptDocumentRequest(BaseModel):
    """Typed view of the consumed Documents service request contract."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    schema_version: Literal["transcript-document-request.v1"]
    document_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")
    title: str = Field(min_length=1, max_length=200)
    locale: str = Field(default=_DEFAULT_LOCALE, min_length=2, max_length=35)
    issue_date: date
    source_name: str = Field(min_length=1, max_length=200)
    language: str | None = Field(default=None, min_length=2, max_length=35)
    source_tool: str | None = Field(default=None, min_length=1, max_length=120)
    segments: list[RequestSegment] = Field(min_length=1, max_length=20000)


def resolve_locale(language: str | None) -> str:
    """Map a transcript language hint to the deterministic document locale."""

    if not language:
        return _DEFAULT_LOCALE
    return _LANGUAGE_LOCALES.get(language.lower(), language)


def build_document_request(
    artifact: TranscriptArtifact,
    *,
    issue_date: date,
    document_id: str | None = None,
    title: str | None = None,
    locale: str | None = None,
) -> dict[str, Any]:
    """Build the deterministic ``transcript-document-request.v1`` payload."""

    resolved_document_id = document_id or artifact.transcript_id
    resolved_title = title or f"Transcript {artifact.transcript_id}"
    if not artifact.source_name:
        raise DocumentRequestError(
            "transcript artifact needs source_name to build a document request; "
            "re-emit the artifact with a bare source file name"
        )
    segments = [
        {
            "start_seconds": segment.start_seconds,
            "end_seconds": segment.end_seconds,
            "text": segment.text,
            **({"speaker": segment.speaker} if segment.speaker else {}),
        }
        for segment in artifact.segments
    ]
    payload: dict[str, Any] = {
        "schema_version": TRANSCRIPT_DOCUMENT_REQUEST_SCHEMA_VERSION,
        "document_id": resolved_document_id,
        "title": resolved_title,
        "locale": locale or resolve_locale(artifact.language),
        "issue_date": issue_date.isoformat(),
        "source_name": artifact.source_name,
        "segments": segments,
    }
    if artifact.language:
        payload["language"] = artifact.language
    if artifact.source_tool:
        payload["source_tool"] = artifact.source_tool
    return payload


def validate_document_request_payload(payload: Any) -> list[str]:
    """Return sorted contract violations for a document request payload."""

    schema = json.loads(DOCUMENT_REQUEST_SCHEMA_PATH.read_text(encoding="utf-8"))
    validator = Draft202012Validator(schema)
    messages: list[str] = []
    for error in validator.iter_errors(payload):
        path = ".".join(str(part) for part in error.absolute_path) or "$"
        messages.append(f"{path}: {error.message}")
    return sorted(messages)


def write_document_request(
    artifact: TranscriptArtifact,
    output_path: str | Path,
    *,
    issue_date: date,
    document_id: str | None = None,
    title: str | None = None,
    locale: str | None = None,
) -> Path:
    """Validate and write the document request JSON; returns the written path."""

    payload = build_document_request(artifact, issue_date=issue_date, document_id=document_id, title=title, locale=locale)
    issues = validate_document_request_payload(payload)
    if issues:
        raise DocumentRequestError("emitted request failed contract validation: " + "; ".join(issues[:5]))
    TranscriptDocumentRequest.model_validate(payload)
    target = Path(output_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    return target
