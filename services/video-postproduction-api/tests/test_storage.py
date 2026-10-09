from __future__ import annotations

from io import BytesIO

import pytest

from video_postproduction.storage.base import StoragePathError
from video_postproduction.storage.local import LocalFilesystemStorage


def test_storage_rejects_path_traversal(tmp_path):
    storage = LocalFilesystemStorage(tmp_path)

    with pytest.raises(StoragePathError):
        storage.local_path("../../outside.mp4")


def test_storage_rejects_absolute_path(tmp_path):
    storage = LocalFilesystemStorage(tmp_path)

    with pytest.raises(StoragePathError):
        storage.local_path("C:/outside.mp4")


def test_storage_puts_atomically_and_returns_content_hash(tmp_path):
    storage = LocalFilesystemStorage(tmp_path)
    payload = b"video bytes"

    stored = storage.put_atomic("projects/project-1/clip.mp4", BytesIO(payload), "video/mp4")

    assert stored.storage_key == "projects/project-1/clip.mp4"
    assert stored.size_bytes == len(payload)
    assert stored.sha256 == "96b050b919f3fca2fc8b6923537136a197ad13c583beb1438d1a12ccbc999c42"
    assert storage.local_path(stored.storage_key).read_bytes() == payload


def test_storage_open_and_delete_use_confined_path(tmp_path):
    storage = LocalFilesystemStorage(tmp_path)
    storage.put_atomic("clip.mp4", BytesIO(b"data"), "video/mp4")

    with storage.open("clip.mp4", "rb") as handle:
        assert handle.read() == b"data"

    storage.delete("clip.mp4")
    assert not storage.local_path("clip.mp4").exists()
