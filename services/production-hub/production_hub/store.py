"""Content-addressed file store.

Files live at ``store/sha256/<first two hex>/<hex>`` under the Hub home. A put
streams the source into a temporary file while hashing, then renames it into
place atomically; identical content is stored once.
"""

from __future__ import annotations

import hashlib
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

_CHUNK = 1024 * 1024


@dataclass(frozen=True)
class StoredFile:
    artifact_id: str
    byte_size: int
    created: bool


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as src:
        while chunk := src.read(_CHUNK):
            digest.update(chunk)
    return f"sha256:{digest.hexdigest()}"


class StoreError(RuntimeError):
    """Raised when the store is inconsistent or a source cannot be read."""


class Store:
    def __init__(self, home: Path) -> None:
        self.root = home / "store"
        self._objects = self.root / "sha256"
        self._tmp = self.root / "tmp"

    def path_for(self, artifact_id: str) -> Path:
        algorithm, _, digest = artifact_id.partition(":")
        if algorithm != "sha256" or len(digest) != 64 or not all(c in "0123456789abcdef" for c in digest):
            raise StoreError(f"invalid artifact id: {artifact_id!r}")
        return self._objects / digest[:2] / digest

    def put(self, source: Path) -> StoredFile:
        if not source.is_file():
            raise StoreError(f"not a file: {source.name}")
        self._tmp.mkdir(parents=True, exist_ok=True)
        digest = hashlib.sha256()
        size = 0
        handle, tmp_name = tempfile.mkstemp(dir=self._tmp, prefix="put-")
        tmp_path = Path(tmp_name)
        try:
            with os.fdopen(handle, "wb") as out, source.open("rb") as src:
                while chunk := src.read(_CHUNK):
                    digest.update(chunk)
                    size += len(chunk)
                    out.write(chunk)
            artifact_id = f"sha256:{digest.hexdigest()}"
            target = self.path_for(artifact_id)
            if target.exists():
                if target.stat().st_size != size:
                    raise StoreError(f"stored object size mismatch for {artifact_id}")
                return StoredFile(artifact_id, size, created=False)
            target.parent.mkdir(parents=True, exist_ok=True)
            os.replace(tmp_path, target)
            return StoredFile(artifact_id, size, created=True)
        finally:
            tmp_path.unlink(missing_ok=True)

    def verify(self, artifact_id: str) -> bool:
        path = self.path_for(artifact_id)
        return path.is_file() and file_sha256(path) == artifact_id
