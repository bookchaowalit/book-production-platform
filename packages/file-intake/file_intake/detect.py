"""Detect what a local file is from its leading bytes, not only its name.

The extension and the content must agree; a mismatch is reported instead of
trusted. Only a bounded prefix and the ZIP directory are read, so detection is
cheap and safe on untrusted files.
"""

from __future__ import annotations

import zipfile
from dataclasses import dataclass
from pathlib import Path

MAX_FILE_BYTES = 200 * 1024 * 1024
_PREFIX_BYTES = 4096

# kind -> accepted extensions
EXTENSIONS: dict[str, tuple[str, ...]] = {
    "csv": (".csv", ".tsv"),
    "xlsx": (".xlsx", ".xlsm"),
    "docx": (".docx",),
    "pdf": (".pdf",),
    "png": (".png",),
    "jpeg": (".jpg", ".jpeg"),
    "tiff": (".tif", ".tiff"),
    "webp": (".webp",),
    "svg": (".svg",),
    "glb": (".glb",),
    "json": (".json",),
    "video": (".mp4", ".m4v", ".mov", ".webm", ".mkv"),
    "audio": (".wav", ".flac", ".mp3", ".m4a"),
    "ifc": (".ifc",),
    "legacy_office": (".xls", ".doc"),
}
_KIND_BY_EXTENSION = {ext: kind for kind, exts in EXTENSIONS.items() for ext in exts}


@dataclass(frozen=True)
class Detection:
    """What a file is. ``kind`` is ``unknown`` when nothing matches."""

    kind: str
    extension: str
    size: int
    issue: str | None = None

    @property
    def ok(self) -> bool:
        return self.issue is None and self.kind != "unknown"

    def to_dict(self) -> dict[str, object]:
        return {"kind": self.kind, "extension": self.extension, "size": self.size, "ok": self.ok, "issue": self.issue}


def _zip_kind(path: Path) -> str:
    try:
        with zipfile.ZipFile(path) as archive:
            names = set(archive.namelist())
    except (zipfile.BadZipFile, OSError, ValueError):
        return "unknown"
    if "xl/workbook.xml" in names:
        return "xlsx"
    if "word/document.xml" in names:
        return "docx"
    return "zip"


def _content_kind(prefix: bytes, path: Path) -> str:
    if prefix.startswith(b"%PDF-"):
        return "pdf"
    if prefix.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if prefix.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    if prefix[:4] in (b"II*\x00", b"MM\x00*"):
        return "tiff"
    if prefix[:4] == b"RIFF" and prefix[8:12] == b"WEBP":
        return "webp"
    if prefix[:4] == b"RIFF" and prefix[8:12] == b"WAVE":
        return "audio"
    if prefix[:4] == b"fLaC" or prefix[:3] == b"ID3" or prefix[:2] in (b"\xff\xfb", b"\xff\xf3", b"\xff\xf2"):
        return "audio"
    if prefix[:4] == b"glTF":
        return "glb"
    if prefix[4:8] == b"ftyp":
        brand = prefix[8:12]
        return "audio" if brand in (b"M4A ", b"M4B ") else "video"
    if prefix[:4] == b"\x1aE\xdf\xa3":
        return "video"
    if prefix[:4] == b"\xd0\xcf\x11\xe0":
        # OLE container: legacy .xls/.doc, or an encrypted OOXML file.
        return "legacy_office"
    if prefix[:4] == b"PK\x03\x04":
        return _zip_kind(path)
    return _text_kind(prefix)


def _text_kind(prefix: bytes) -> str:
    if b"\x00" in prefix:
        return "unknown"
    try:
        text = prefix.decode("utf-8-sig")
    except UnicodeDecodeError:
        # Thai Excel CSV is often cp874; the table reader decides the encoding.
        return "text"
    stripped = text.lstrip()
    if stripped.startswith(("{", "[")):
        return "json"
    if stripped.startswith("<svg") or ("<svg" in stripped[:512] and stripped.startswith("<?xml")):
        return "svg"
    if stripped.upper().startswith("ISO-10303-21"):
        return "ifc"
    return "text"


def detect(path: str | Path) -> Detection:
    """Return the file kind; ``issue`` names why it cannot be used as-is."""

    file = Path(path)
    extension = file.suffix.lower()
    try:
        if not file.is_file():
            return Detection("unknown", extension, 0, "not_a_file")
        size = file.stat().st_size
        if size > MAX_FILE_BYTES:
            return Detection("unknown", extension, size, "file_too_large")
        if size == 0:
            return Detection("unknown", extension, 0, "file_empty")
        with file.open("rb") as handle:
            prefix = handle.read(_PREFIX_BYTES)
    except OSError:
        return Detection("unknown", extension, 0, "read_failed")

    content = _content_kind(prefix, file)
    named = _KIND_BY_EXTENSION.get(extension)
    if content == "text":
        # Plain text: the extension decides between CSV and other text kinds.
        if named in ("csv", "ifc", "json", "svg"):
            return Detection(named, extension, size)
        return Detection("unknown", extension, size, "unsupported_type")
    if content == "legacy_office":
        if extension in (".xlsx", ".xlsm", ".docx"):
            return Detection(content, extension, size, "encrypted_or_legacy_office")
        return Detection(content, extension, size, "legacy_office_unsupported")
    if content in ("unknown", "zip"):
        return Detection("unknown", extension, size, "unsupported_type")
    if named is not None and named != content:
        return Detection(content, extension, size, "extension_mismatch")
    return Detection(content, extension, size)


__all__ = ["EXTENSIONS", "MAX_FILE_BYTES", "Detection", "detect"]
