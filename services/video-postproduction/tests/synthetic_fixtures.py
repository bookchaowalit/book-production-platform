"""Synthetic transcript fixtures shared by the pipeline tests.

The fixture text is synthetic; it never carries content from a private
transcription session.
"""

from __future__ import annotations

from typing import Any


def synthetic_artifact_payload() -> dict[str, Any]:
    """Return a valid ``transcript-artifact.v1`` payload with Thai and English cues."""

    return {
        "schema_version": "transcript-artifact.v1",
        "transcript_id": "demo-001",
        "language": "th",
        "duration_seconds": 9.5,
        "source_tool": "faster-whisper",
        "source_name": "demo.mp4",
        "segments": [
            {"start_seconds": 0.0, "end_seconds": 2.5, "text": "สวัสดีครับ นี่คือการทดสอบระบบถอดเสียง"},
            {"start_seconds": 2.5, "end_seconds": 6.0, "text": "Hello from the synthetic transcript fixture", "speaker": "Host"},
            {"start_seconds": 6.0, "end_seconds": 9.5, "text": "x" * 50},
        ],
    }


def synthetic_document_request() -> dict[str, Any]:
    """Return the deterministic document request expected from the fixture artifact."""

    return {
        "schema_version": "transcript-document-request.v1",
        "document_id": "demo-001",
        "title": "Transcript demo-001",
        "locale": "th-TH",
        "issue_date": "2026-09-26",
        "source_name": "demo.mp4",
        "language": "th",
        "source_tool": "faster-whisper",
        "segments": [
            {"start_seconds": 0.0, "end_seconds": 2.5, "text": "สวัสดีครับ นี่คือการทดสอบระบบถอดเสียง"},
            {"start_seconds": 2.5, "end_seconds": 6.0, "text": "Hello from the synthetic transcript fixture", "speaker": "Host"},
            {"start_seconds": 6.0, "end_seconds": 9.5, "text": "x" * 50},
        ],
    }
