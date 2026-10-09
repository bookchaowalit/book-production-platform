"""Deterministic video post-production pipeline tools (roadmap T6.2).

The package owns the ``transcript-artifact.v1`` contract and converts it into:

- SRT/VTT subtitle files,
- an extracted audio track plus an ``audio-batch.v1`` manifest for the
  audio master pipeline (``book-audio-production`` input contract), and
- a ``transcript-document-request.v1`` payload for the Documents service.

Semantic summarization needs AI (T8 / Phase 4) and is intentionally out of
scope: the Documents output is a deterministic verbatim, timestamped
transcript document, never an AI summary.
"""

from .contract import (
    TRANSCRIPT_ARTIFACT_SCHEMA_ID,
    TRANSCRIPT_ARTIFACT_SCHEMA_VERSION,
    TranscriptArtifact,
    TranscriptSegment,
    ValidationError,
    load_transcript_artifact,
    parse_transcript_artifact,
    validate_transcript_artifact_payload,
)

__all__ = [
    "TRANSCRIPT_ARTIFACT_SCHEMA_ID",
    "TRANSCRIPT_ARTIFACT_SCHEMA_VERSION",
    "TranscriptArtifact",
    "TranscriptSegment",
    "ValidationError",
    "load_transcript_artifact",
    "parse_transcript_artifact",
    "validate_transcript_artifact_payload",
]
