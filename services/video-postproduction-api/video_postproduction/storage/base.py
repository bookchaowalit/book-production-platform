"""Storage provider interfaces and value objects."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Protocol

StorageSource = BinaryIO | Iterable[bytes] | bytes


class StoragePathError(ValueError):
    """Raised when a storage key would escape the configured storage root."""


@dataclass(frozen=True, slots=True)
class StoredObject:
    """Metadata returned after an object is atomically stored."""

    storage_key: str
    size_bytes: int
    sha256: str
    content_type: str


class StorageProvider(Protocol):
    """Minimal storage contract used by the API and worker layers."""

    def local_path(self, storage_key: str) -> Path:
        """Resolve a safe storage key to a local path."""

    def put_atomic(
        self,
        storage_key: str,
        source: StorageSource,
        content_type: str = "application/octet-stream",
    ) -> StoredObject:
        """Write an object atomically and return its content metadata."""

    def open(self, storage_key: str, mode: str = "rb") -> BinaryIO:
        """Open a confined object path."""

    def delete(self, storage_key: str) -> None:
        """Delete a confined object path if it exists."""
