"""Upload filename, MIME, and bounded-stream validation."""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import BinaryIO, Protocol


class UploadValidationError(ValueError):
    """Raised when an upload is unsafe or unsupported."""


class BinaryWriter(Protocol):
    """Small write-only protocol compatible with temporary upload files."""

    def write(self, data: bytes, /) -> int:
        """Write bytes and return the number written."""


DEFAULT_ALLOWED_MIME_TYPES = frozenset(
    {"video/mp4", "video/quicktime", "video/webm", "video/x-matroska"}
)

EXTENSION_MIME_TYPES: dict[str, frozenset[str]] = {
    ".mp4": frozenset({"video/mp4"}),
    ".m4v": frozenset({"video/mp4"}),
    ".mov": frozenset({"video/quicktime"}),
    ".webm": frozenset({"video/webm"}),
    ".mkv": frozenset({"video/x-matroska"}),
}


def validate_upload_filename(filename: str) -> str:
    """Validate a browser filename and return it without path components."""

    if not filename or "\x00" in filename:
        raise UploadValidationError("filename is required")
    normalized = filename.replace("\\", "/")
    if "/" in normalized or normalized in {".", ".."}:
        raise UploadValidationError("filename must not contain path components")

    suffix = Path(filename).suffix.lower()
    if suffix not in EXTENSION_MIME_TYPES:
        raise UploadValidationError(f"filename suffix {suffix or '<none>'} is not supported")
    return filename


def validate_media_type(
    content_type: str | None,
    filename: str,
    allowed_mime_types: Iterable[str] | None = None,
) -> str:
    """Validate MIME allowlisting and the filename/MIME pairing."""

    validate_upload_filename(filename)
    normalized_type = (content_type or "").split(";", 1)[0].strip().lower()
    allowed = frozenset(
        item.strip().lower() for item in (allowed_mime_types or DEFAULT_ALLOWED_MIME_TYPES) if item.strip()
    )
    if normalized_type not in allowed:
        raise UploadValidationError(f"MIME type {normalized_type or '<missing>'} is not supported")

    expected_types = EXTENSION_MIME_TYPES[Path(filename).suffix.lower()]
    if normalized_type not in expected_types:
        raise UploadValidationError(
            f"MIME type {normalized_type} does not match filename suffix {Path(filename).suffix.lower()}"
        )
    return normalized_type


def stream_upload(source: BinaryIO, destination: BinaryWriter, max_bytes: int) -> int:
    """Copy an upload while enforcing a hard byte limit."""

    if max_bytes <= 0:
        raise ValueError("max_bytes must be positive")
    size_bytes = 0
    while True:
        chunk = source.read(1024 * 1024)
        if not chunk:
            break
        if not isinstance(chunk, bytes):
            raise UploadValidationError("upload stream must contain bytes")
        size_bytes += len(chunk)
        if size_bytes > max_bytes:
            raise UploadValidationError("upload exceeds the maximum allowed size")
        destination.write(chunk)
    return size_bytes
