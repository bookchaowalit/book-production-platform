"""Register files as ``artifact.v1`` records in the store and index.

``Hub.register`` stores the bytes, builds the record, validates it against the
contract, and indexes it with its lineage in one transaction. If validation or
indexing fails, a newly stored object is removed again so the store holds only
registered content.
"""

from __future__ import annotations

import mimetypes
import os
import sqlite3
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .contracts import CONTRACT_ARTIFACT, validate_artifact
from .db import HubDB
from .paths import WORKSPACE_ROOT
from .registry import load_registries
from .store import Store

HOME_ENV = "PRODUCTION_HUB_HOME"
DEFAULT_HOME = WORKSPACE_ROOT / ".runtime" / "production-hub"

_EXTRA_TYPES = {
    ".svg": "image/svg+xml",
    ".glb": "model/gltf-binary",
    ".srt": "application/x-subrip",
    ".vtt": "text/vtt",
    ".blend": "application/x-blender",
    ".fcstd": "application/x-freecad",
    ".step": "model/step",
    ".stp": "model/step",
    ".stl": "model/stl",
    ".ifc": "application/x-step",
    ".md": "text/markdown",
    ".sql": "application/sql",
    ".flac": "audio/flac",
    ".wav": "audio/wav",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}


def resolve_home(home: Path | None = None) -> Path:
    """Absolute Hub home: tools run in other working directories and must get full paths."""
    if home is not None:
        return home.resolve()
    env = os.environ.get(HOME_ENV)
    return (Path(env) if env else DEFAULT_HOME).resolve()


def guess_media_type(path: Path, allowed: Sequence[str]) -> str:
    suffix = path.suffix.lower()
    guessed = _EXTRA_TYPES.get(suffix) or mimetypes.guess_type(path.name)[0]
    if guessed is not None and guessed in allowed:
        return guessed
    return allowed[0]


@dataclass(frozen=True)
class Producer:
    tool_id: str
    tool_version: str
    job_id: str
    recipe_version: str | None = None

    def as_dict(self) -> dict[str, str]:
        data = {"tool_id": self.tool_id, "tool_version": self.tool_version, "job_id": self.job_id}
        if self.recipe_version:
            data["recipe_version"] = self.recipe_version
        return data


@dataclass(frozen=True)
class ArtifactInput:
    path: Path
    kind: str
    producer: Producer
    lineage: Sequence[str] = ()
    qa_status: str = "accepted"
    qa_manifest_ref: str | None = None
    privacy_class: str = "internal"
    project_id: str | None = None
    brand_id: str | None = None
    media_type: str | None = None
    filename: str | None = None
    attributes: Mapping[str, Any] = field(default_factory=dict)
    created_at: str | None = None


@dataclass(frozen=True)
class Registered:
    artifact_id: str
    kind: str
    filename: str
    created: bool


class Hub:
    def __init__(self, home: Path | None = None) -> None:
        self.home = resolve_home(home)
        self.store = Store(self.home)
        self.db = HubDB(self.home / "hub.db")
        self.kinds = load_registries().kinds
        self._created: list[str] = []

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """Commit every registration together; on failure also drop newly stored objects."""
        self._created = []
        with self.db.connect() as conn:
            try:
                yield conn
                conn.commit()
            except BaseException:
                conn.rollback()
                for artifact_id in self._created:
                    self.store.path_for(artifact_id).unlink(missing_ok=True)
                raise
            finally:
                self._created = []

    def build_record(self, item: ArtifactInput, artifact_id: str, byte_size: int) -> dict[str, Any]:
        kind_spec = self.kinds.get(item.kind)
        allowed = list(kind_spec["media_types"]) if kind_spec else ["application/octet-stream"]
        record: dict[str, Any] = {
            "schema_version": CONTRACT_ARTIFACT,
            "artifact_id": artifact_id,
            "kind": item.kind,
            "media_type": item.media_type or guess_media_type(item.path, allowed),
            "byte_size": byte_size,
            "filename": item.filename or item.path.name,
            "producer": item.producer.as_dict(),
            "lineage": sorted(set(item.lineage) - {artifact_id}),
            "qa": {"status": item.qa_status},
            "privacy_class": item.privacy_class,
            "created_at": item.created_at or datetime.now(UTC).isoformat(timespec="seconds"),
        }
        if item.qa_manifest_ref:
            record["qa"]["manifest_ref"] = item.qa_manifest_ref
        if item.project_id:
            record["project_id"] = item.project_id
        if item.brand_id:
            record["brand_id"] = item.brand_id
        if item.attributes:
            record["attributes"] = dict(item.attributes)
        return record

    def register(self, conn: sqlite3.Connection, item: ArtifactInput) -> Registered:
        stored = self.store.put(item.path)
        try:
            record = self.build_record(item, stored.artifact_id, stored.byte_size)
            validate_artifact(record, self.kinds)
            inserted = self.db.insert(conn, record)
        except BaseException:
            if stored.created:
                self.store.path_for(stored.artifact_id).unlink(missing_ok=True)
            raise
        if stored.created:
            self._created.append(stored.artifact_id)
        return Registered(stored.artifact_id, record["kind"], record["filename"], inserted)

    def lineage_tree(self, conn: sqlite3.Connection, artifact_id: str, depth: int = 20) -> dict[str, Any]:
        """Return the artifact and its inputs, recursively, up to ``depth`` levels."""
        record = self.db.get(conn, artifact_id)
        if record is None:
            raise KeyError(artifact_id)
        node: dict[str, Any] = {
            "artifact_id": artifact_id,
            "kind": record["kind"],
            "filename": record["filename"],
            "producer": record["producer"],
        }
        if depth > 0:
            node["inputs"] = [self.lineage_tree(conn, item, depth - 1) for item in record["lineage"]]
        elif record["lineage"]:
            node["inputs_truncated"] = len(record["lineage"])
        return node
