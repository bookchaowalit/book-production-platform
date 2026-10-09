"""Local filesystem storage with path confinement and atomic writes."""

from __future__ import annotations

import hashlib
import os
import tempfile
from collections.abc import Iterable, Iterator
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import BinaryIO, cast

from .base import StoragePathError, StorageSource, StoredObject


class LocalFilesystemStorage:
    """Store media below one root without allowing path traversal."""

    _CHUNK_SIZE = 1024 * 1024

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).expanduser().resolve(strict=False)
        self.root.mkdir(parents=True, exist_ok=True)

    def _resolve(self, storage_key: str) -> Path:
        if not storage_key or "\x00" in storage_key:
            raise StoragePathError("storage key must be a non-empty relative path")

        normalized_key = storage_key.replace("\\", "/")
        posix_key = PurePosixPath(normalized_key)
        windows_key = PureWindowsPath(storage_key)
        if posix_key.is_absolute() or windows_key.is_absolute() or windows_key.drive:
            raise StoragePathError("storage key must be relative")
        if any(part == ".." for part in posix_key.parts):
            raise StoragePathError("storage key cannot contain parent traversal")

        candidate = (self.root / Path(*posix_key.parts)).resolve(strict=False)
        try:
            candidate.relative_to(self.root)
        except ValueError as exc:
            raise StoragePathError("storage key escapes the storage root") from exc
        return candidate

    def local_path(self, storage_key: str) -> Path:
        """Return the confined local path for ``storage_key``."""

        return self._resolve(storage_key)

    @staticmethod
    def _iter_chunks(source: StorageSource) -> Iterator[bytes]:
        if isinstance(source, bytes):
            yield source
            return

        reader = getattr(source, "read", None)
        if callable(reader):
            while True:
                chunk = reader(LocalFilesystemStorage._CHUNK_SIZE)
                if not chunk:
                    break
                if not isinstance(chunk, (bytes, bytearray, memoryview)):
                    raise TypeError("storage source must yield bytes")
                yield bytes(chunk)
            return

        for chunk in cast(Iterable[bytes], source):
            if not isinstance(chunk, (bytes, bytearray, memoryview)):
                raise TypeError("storage source must yield bytes")
            yield bytes(chunk)

    def put_atomic(
        self,
        storage_key: str,
        source: StorageSource,
        content_type: str = "application/octet-stream",
    ) -> StoredObject:
        """Write to a same-directory temporary file and atomically replace the target."""

        destination = self._resolve(storage_key)
        destination.parent.mkdir(parents=True, exist_ok=True)
        digest = hashlib.sha256()
        size_bytes = 0
        temporary_path: Path | None = None

        try:
            with tempfile.NamedTemporaryFile(
                mode="wb",
                dir=destination.parent,
                prefix=f".{destination.name}.",
                suffix=".tmp",
                delete=False,
            ) as temporary:
                temporary_path = Path(temporary.name)
                for chunk in self._iter_chunks(source):
                    temporary.write(chunk)
                    digest.update(chunk)
                    size_bytes += len(chunk)
                temporary.flush()
                try:
                    os.fsync(temporary.fileno())
                except OSError:
                    pass

            os.replace(temporary_path, destination)
            return StoredObject(
                storage_key=storage_key,
                size_bytes=size_bytes,
                sha256=digest.hexdigest(),
                content_type=content_type,
            )
        finally:
            if temporary_path is not None and temporary_path.exists():
                temporary_path.unlink()

    def open(self, storage_key: str, mode: str = "rb") -> BinaryIO:
        """Open a confined path; text-mode access is intentionally not supported."""

        if "b" not in mode:
            raise ValueError("local media storage requires binary mode")
        return cast(BinaryIO, self._resolve(storage_key).open(mode))

    def delete(self, storage_key: str) -> None:
        """Delete an object after applying the same confinement checks as reads."""

        self._resolve(storage_key).unlink(missing_ok=True)
