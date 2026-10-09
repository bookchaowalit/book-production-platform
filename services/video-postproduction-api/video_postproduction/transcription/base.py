"""Provider protocol and safe provider errors."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from ..media.models import MediaMetadata
from .models import NormalizedTranscript


class TranscriptionProviderError(RuntimeError):
    """Provider failure with safe diagnostics and no credential-bearing payload."""

    def __init__(self, code: str, safe_message: str, diagnostics: str = "") -> None:
        self.code = code
        self.safe_message = safe_message
        self.diagnostics = diagnostics
        super().__init__(safe_message)


class TranscriptionProvider(Protocol):
    """Common contract for local and OpenAI-compatible transcription."""

    def transcribe(
        self,
        audio_path: str | Path,
        metadata: MediaMetadata,
        language: str | None = None,
    ) -> NormalizedTranscript:
        """Transcribe one media asset using its already-inspected metadata."""
