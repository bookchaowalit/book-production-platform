"""Transactional local ledger for idempotent document jobs."""

from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

JobAction = Literal["run", "running", "replay"]
JobState = Literal["pending", "running", "accepted", "failed"]


class IdempotencyConflict(RuntimeError):
    """Raised when an idempotency key is reused for a different request."""


@dataclass(frozen=True)
class JobClaim:
    action: JobAction
    job_id: str
    request_hash: str
    manifest_path: str | None
    state: JobState


class JobStore:
    """Persist job claims without holding a database transaction during rendering."""

    def __init__(self, root: str | Path, *, scope: str = "documents") -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / "jobs.sqlite3"
        self.scope = scope
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 30000")
        return connection

    def _initialize(self) -> None:
        connection = self._connect()
        try:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS document_jobs (
                    scope TEXT NOT NULL,
                    key_hash TEXT NOT NULL,
                    request_hash TEXT NOT NULL,
                    job_id TEXT NOT NULL,
                    state TEXT NOT NULL CHECK (state IN ('pending', 'running', 'accepted', 'failed')),
                    manifest_path TEXT,
                    recipe_version TEXT NOT NULL,
                    schema_version TEXT NOT NULL,
                    engine_version TEXT NOT NULL,
                    lease_until REAL,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    PRIMARY KEY (scope, key_hash),
                    UNIQUE (job_id)
                );
                CREATE INDEX IF NOT EXISTS idx_document_jobs_job_id ON document_jobs(job_id);
                """
            )
            self._migrate_legacy_index(connection)
        finally:
            connection.close()

    def _migrate_legacy_index(self, connection: sqlite3.Connection) -> None:
        legacy_path = self.root / "idempotency.json"
        if not legacy_path.exists():
            return
        try:
            payload = json.loads(legacy_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError("legacy idempotency index is not valid JSON") from exc
        if not isinstance(payload, dict):
            raise TypeError("legacy idempotency index must be an object")
        now = time.time()
        for key_hash, raw_entry in payload.items():
            if not isinstance(raw_entry, dict):
                continue
            manifest_path = str(raw_entry.get("manifest_path", ""))
            manifest_file = self.root / manifest_path
            job_id = f"legacy-{str(key_hash)[:24]}"
            recipe_version = "invoice-v1"
            if manifest_file.exists():
                try:
                    manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
                    job_id = str(manifest.get("job_id", job_id))
                    recipe_version = str(manifest.get("recipe", recipe_version))
                except (OSError, json.JSONDecodeError):
                    pass
            request_hash = str(raw_entry.get("request_hash", ""))
            if not request_hash:
                continue
            connection.execute(
                """
                INSERT OR IGNORE INTO document_jobs
                    (scope, key_hash, request_hash, job_id, state, manifest_path,
                     recipe_version, schema_version, engine_version, lease_until,
                     created_at, updated_at)
                VALUES (?, ?, ?, ?, 'accepted', ?, ?, ?, ?, NULL, ?, ?)
                """,
                (self.scope, str(key_hash), request_hash, job_id, manifest_path, recipe_version, "document-request.v1", "document-engine.v1", now, now),
            )

    def _manifest_complete(self, manifest_path: str | None) -> bool:
        if not manifest_path:
            return False
        path = self.root / manifest_path
        if not path.exists():
            return False
        try:
            manifest = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return False
        artifacts = manifest.get("artifacts", [])
        if not isinstance(artifacts, list):
            return False
        return all((path.parent / str(artifact.get("filename", ""))).is_file() for artifact in artifacts if isinstance(artifact, dict))

    def claim(
        self,
        *,
        key_hash: str,
        request_hash: str,
        job_id: str,
        recipe_version: str,
        schema_version: str,
        engine_version: str,
        lease_seconds: float = 300.0,
    ) -> JobClaim:
        """Claim an idempotency key atomically, then release the transaction before rendering."""

        now = time.time()
        lease_until = now + max(lease_seconds, 0.0)
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM document_jobs WHERE scope = ? AND key_hash = ?",
                (self.scope, key_hash),
            ).fetchone()
            if row is None:
                connection.execute(
                    """
                    INSERT INTO document_jobs
                        (scope, key_hash, request_hash, job_id, state, manifest_path,
                         recipe_version, schema_version, engine_version, lease_until,
                         created_at, updated_at)
                    VALUES (?, ?, ?, ?, 'running', NULL, ?, ?, ?, ?, ?, ?)
                    """,
                    (self.scope, key_hash, request_hash, job_id, recipe_version, schema_version, engine_version, lease_until, now, now),
                )
                connection.commit()
                return JobClaim("run", job_id, request_hash, None, "running")

            if str(row["request_hash"]) != request_hash:
                connection.rollback()
                raise IdempotencyConflict("idempotency key is already bound to different input")

            existing_job_id = str(row["job_id"])
            state = str(row["state"])
            manifest_path = str(row["manifest_path"]) if row["manifest_path"] is not None else None
            versions_match = (
                str(row["recipe_version"]) == recipe_version
                and str(row["schema_version"]) == schema_version
                and str(row["engine_version"]) == engine_version
            )
            if state == "accepted" and versions_match and self._manifest_complete(manifest_path):
                connection.commit()
                return JobClaim("replay", existing_job_id, request_hash, manifest_path, "accepted")

            running_lease = float(row["lease_until"]) if row["lease_until"] is not None else 0.0
            if state in {"pending", "running"} and running_lease > now:
                connection.commit()
                return JobClaim("running", existing_job_id, request_hash, manifest_path, state)  # type: ignore[arg-type]

            replacement_job_id = existing_job_id
            if state in {"pending", "running", "failed"} or not versions_match:
                # A reclaimed lease must fence the previous worker. Reusing its
                # job id would let a late completion update the new run.
                replacement_job_id = job_id

            connection.execute(
                """
                UPDATE document_jobs
                SET job_id = ?, state = 'running', lease_until = ?, updated_at = ?,
                    recipe_version = ?, schema_version = ?, engine_version = ?
                WHERE scope = ? AND key_hash = ?
                """,
                (replacement_job_id, lease_until, now, recipe_version, schema_version, engine_version, self.scope, key_hash),
            )
            connection.commit()
            return JobClaim("run", replacement_job_id, request_hash, manifest_path, "running")
        except Exception:
            if connection.in_transaction:
                connection.rollback()
            raise
        finally:
            connection.close()

    def finish(self, job_id: str, *, state: Literal["accepted", "failed"], manifest_path: str | None) -> None:
        now = time.time()
        connection = self._connect()
        try:
            cursor = connection.execute(
                """
                UPDATE document_jobs
                SET state = ?, manifest_path = ?, lease_until = NULL, updated_at = ?
                WHERE scope = ? AND job_id = ?
                """,
                (state, manifest_path, now, self.scope, job_id),
            )
            if cursor.rowcount != 1:
                raise KeyError(f"unknown document job: {job_id}")
        finally:
            connection.close()
