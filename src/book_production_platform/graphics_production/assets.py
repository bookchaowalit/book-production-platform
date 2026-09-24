"""Local-only asset and font reference validation."""

from __future__ import annotations

from pathlib import Path, PureWindowsPath
from urllib.parse import urlparse


class AssetReferenceError(ValueError):
    """Raised when an input reference would escape the local asset boundary."""

    def __init__(self, code: str, reference: str, field: str) -> None:
        self.code = code
        self.reference = reference
        self.field = field
        super().__init__(f"{code}: {field}={reference!r}")


def validate_local_reference(reference: str, root: str | Path, *, field: str) -> Path:
    """Resolve a required file beneath ``root`` without network access."""

    if not reference.strip():
        raise AssetReferenceError("asset_reference_required", reference, field)
    parsed = urlparse(reference)
    if parsed.scheme or "://" in reference or reference.lower().startswith("data:"):
        raise AssetReferenceError("network_reference_forbidden", reference, field)
    if Path(reference).is_absolute() or PureWindowsPath(reference).is_absolute():
        raise AssetReferenceError("absolute_reference_forbidden", reference, field)
    root_path = Path(root).resolve()
    candidate = (root_path / Path(reference)).resolve()
    try:
        candidate.relative_to(root_path)
    except ValueError as exc:
        raise AssetReferenceError("asset_outside_root", reference, field) from exc
    if not candidate.exists():
        raise AssetReferenceError("missing_local_asset", reference, field)
    if not candidate.is_file():
        raise AssetReferenceError("asset_not_file", reference, field)
    return candidate
