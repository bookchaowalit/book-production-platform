"""Render engine contracts and safe domain errors."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from ..edl import EDLDocument
from ..media.models import MediaMetadata


class RenderProcessError(RuntimeError):
    """Raised when FFmpeg cannot produce a valid output."""

    def __init__(self, code: str, safe_message: str, diagnostics: str = "") -> None:
        self.code = code
        self.safe_message = safe_message
        self.diagnostics = diagnostics
        super().__init__(safe_message)


class RenderCapabilityError(RenderProcessError):
    """Raised before process start when an EDL requires unsupported behavior."""

    def __init__(self, safe_message: str) -> None:
        super().__init__("unsupported_capability", safe_message)


class RenderOutputValidationError(RenderProcessError):
    """Raised when FFprobe rejects the generated output contract."""


@dataclass(frozen=True, slots=True)
class RenderArtifact:
    duration: float
    metadata: MediaMetadata


class RenderEngine(Protocol):
    """Render one validated EDL into a local temporary output."""

    def render(
        self,
        edl: EDLDocument,
        *,
        input_path: Path,
        output_path: Path,
        caption_path: Path | None = None,
    ) -> RenderArtifact:
        """Render and validate an output file."""
