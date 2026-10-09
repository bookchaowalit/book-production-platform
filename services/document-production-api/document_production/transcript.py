"""The pinned, model-free transcript document recipe (Documents V1A/T6.2).

Renders a ``transcript-document-request.v1`` payload as a verbatim,
timestamped transcript document. Semantic summarization requires AI (T8 /
Phase 4) and is intentionally out of scope; this recipe never calls a model.

Pinned formatting rules:

- Timestamps are ``HH:MM:SS`` plus exactly three millisecond digits with a
  dot separator; a paragraph is ``[<start> - <end>] <text>`` and includes a
  ``<speaker>: `` prefix when a speaker is present.
- The section heading is Thai (``บทถอดเสียง``) for ``th*`` locales and
  ``Transcript`` otherwise.
- ``derived_totals`` stays empty: the transcript has no monetary totals, so
  the renderer skips the invoice Totals block and QA skips the invoice
  totals arithmetic while every artifact integrity gate is inherited.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import date
from pathlib import Path
from typing import Any, Literal

from document_ir import DocumentIR, DocumentSection, ValidationIssue
from pydantic import BaseModel, ConfigDict, Field, model_validator

from .recipe import RequestValidationError

TRANSCRIPT_RECIPE_ID = "transcript-v1"
TRANSCRIPT_DOCUMENT_REQUEST_SCHEMA_VERSION = "transcript-document-request.v1"
SERVICE_ROOT = Path(__file__).resolve().parents[1]
TRANSCRIPT_DOCUMENT_REQUEST_SCHEMA_PATH = SERVICE_ROOT / "schemas" / "transcript-document-request.v1.schema.json"
_THAI_HEADING = "บทถอดเสียง"
_DEFAULT_HEADING = "Transcript"
_FALLBACK_CURRENCY = "NONE"


class TranscriptSegmentInput(BaseModel):
    """One verbatim segment: ``start_seconds < end_seconds`` and non-empty text."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    start_seconds: float = Field(ge=0)
    end_seconds: float = Field(gt=0)
    text: str = Field(min_length=1, max_length=2000)
    speaker: str | None = Field(default=None, min_length=1, max_length=120)

    @model_validator(mode="after")
    def _check_order(self) -> TranscriptSegmentInput:
        if self.end_seconds <= self.start_seconds:
            raise ValueError("end_seconds must be greater than start_seconds")
        return self


class TranscriptDocumentRequest(BaseModel):
    """Structured request for the deterministic transcript document recipe."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    schema_version: Literal["transcript-document-request.v1"]
    document_id: str = Field(min_length=1, max_length=128)
    title: str = Field(min_length=1, max_length=200)
    locale: str = Field(default="th-TH", min_length=2, max_length=35)
    issue_date: date
    source_name: str = Field(min_length=1, max_length=200)
    language: str | None = Field(default=None, min_length=2, max_length=35)
    source_tool: str | None = Field(default=None, min_length=1, max_length=120)
    segments: list[TranscriptSegmentInput] = Field(min_length=1, max_length=20000)


def format_document_timestamp(seconds: float) -> str:
    """Format seconds as the pinned ``HH:MM:SS.mmm`` document timestamp."""

    if seconds < 0:
        raise ValueError("timestamp seconds must not be negative")
    total_milliseconds = round(seconds * 1000)
    hours, remainder = divmod(total_milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    whole_seconds, milliseconds = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{whole_seconds:02d}.{milliseconds:03d}"


def _segment_paragraph(segment: TranscriptSegmentInput) -> str:
    stamp = f"[{format_document_timestamp(segment.start_seconds)} - {format_document_timestamp(segment.end_seconds)}]"
    text = f"{segment.speaker}: {segment.text}" if segment.speaker else segment.text
    return f"{stamp} {text}"


def _validate_structural(request: Mapping[str, Any] | TranscriptDocumentRequest) -> TranscriptDocumentRequest:
    parsed = request if isinstance(request, TranscriptDocumentRequest) else TranscriptDocumentRequest.model_validate(request)
    if parsed.schema_version != TRANSCRIPT_DOCUMENT_REQUEST_SCHEMA_VERSION:
        issue = ValidationIssue(
            code="unsupported_schema_version",
            path="schema_version",
            message=f"schema_version must be {TRANSCRIPT_DOCUMENT_REQUEST_SCHEMA_VERSION}",
        )
        raise RequestValidationError([issue])
    return parsed


def build_transcript_ir(request: Mapping[str, Any] | TranscriptDocumentRequest) -> DocumentIR:
    """Normalize a transcript request into document IR without model calls."""

    parsed = _validate_structural(request)
    heading = _THAI_HEADING if parsed.locale.lower().startswith("th") else _DEFAULT_HEADING
    paragraphs = [_segment_paragraph(segment) for segment in parsed.segments]
    section = DocumentSection(heading=heading, paragraphs=paragraphs)
    return DocumentIR(
        document_id=parsed.document_id,
        template_ref="transcript@1",
        locale=parsed.locale,
        currency=_FALLBACK_CURRENCY,
        title=parsed.title,
        customer_name=parsed.source_name,
        issue_date=parsed.issue_date,
        sections=[section],
        derived_totals={},
        source_refs=[f"transcript:{parsed.document_id}"],
        assumptions={
            "calculation": "none-verbatim-transcript-v1",
            "model_calls": "0",
            "recipe": TRANSCRIPT_RECIPE_ID,
            "schema_version": TRANSCRIPT_DOCUMENT_REQUEST_SCHEMA_VERSION,
            "segment_count": str(len(paragraphs)),
        },
    )


def load_transcript_document_request_schema() -> dict[str, Any]:
    """Load the published ``transcript-document-request.v1`` JSON Schema."""

    return json.loads(TRANSCRIPT_DOCUMENT_REQUEST_SCHEMA_PATH.read_text(encoding="utf-8"))
