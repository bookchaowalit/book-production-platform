"""SQLite index of registered artifacts and their lineage (``hub.db``).

The index is separate from the Solo Empire control-plane database. Records are
keyed by content hash: the first registration of a given content wins, and a
later registration of the same bytes adds only new lineage edges.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator, Mapping
from contextlib import closing, contextmanager
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS artifacts (
    artifact_id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    media_type TEXT NOT NULL,
    byte_size INTEGER NOT NULL,
    filename TEXT NOT NULL,
    tool_id TEXT NOT NULL,
    job_id TEXT NOT NULL,
    project_id TEXT,
    qa_status TEXT NOT NULL,
    privacy_class TEXT NOT NULL,
    created_at TEXT NOT NULL,
    record_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS artifacts_kind ON artifacts(kind);
CREATE INDEX IF NOT EXISTS artifacts_project ON artifacts(project_id);
CREATE INDEX IF NOT EXISTS artifacts_tool_job ON artifacts(tool_id, job_id);
CREATE TABLE IF NOT EXISTS lineage (
    artifact_id TEXT NOT NULL REFERENCES artifacts(artifact_id),
    input_id TEXT NOT NULL REFERENCES artifacts(artifact_id),
    PRIMARY KEY (artifact_id, input_id)
);
CREATE INDEX IF NOT EXISTS lineage_input ON lineage(input_id);
"""


class HubDB:
    def __init__(self, path: Path) -> None:
        self.path = path

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.path)) as conn:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys = ON")
            conn.executescript(SCHEMA)
            yield conn

    def insert(self, conn: sqlite3.Connection, record: Mapping[str, Any]) -> bool:
        """Insert one validated record; returns False when the content already exists."""
        missing = [item for item in record["lineage"] if self.get(conn, item) is None]
        if missing:
            raise KeyError(f"lineage inputs are not registered: {', '.join(missing)}")
        cursor = conn.execute(
            """INSERT OR IGNORE INTO artifacts
               (artifact_id, kind, media_type, byte_size, filename, tool_id, job_id,
                project_id, qa_status, privacy_class, created_at, record_json)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                record["artifact_id"], record["kind"], record["media_type"], record["byte_size"],
                record["filename"], record["producer"]["tool_id"], record["producer"]["job_id"],
                record.get("project_id"), record["qa"]["status"], record["privacy_class"],
                record["created_at"], json.dumps(record, ensure_ascii=False, sort_keys=True),
            ),
        )
        conn.executemany(
            "INSERT OR IGNORE INTO lineage (artifact_id, input_id) VALUES (?, ?)",
            [(record["artifact_id"], item) for item in record["lineage"]],
        )
        return cursor.rowcount == 1

    def get(self, conn: sqlite3.Connection, artifact_id: str) -> dict[str, Any] | None:
        row = conn.execute("SELECT record_json FROM artifacts WHERE artifact_id = ?", (artifact_id,)).fetchone()
        if row is None:
            return None
        record: dict[str, Any] = json.loads(row["record_json"])
        record["lineage"] = self.inputs(conn, artifact_id)
        return record

    def inputs(self, conn: sqlite3.Connection, artifact_id: str) -> list[str]:
        rows = conn.execute(
            "SELECT input_id FROM lineage WHERE artifact_id = ? ORDER BY input_id", (artifact_id,)
        ).fetchall()
        return [row["input_id"] for row in rows]

    def outputs(self, conn: sqlite3.Connection, artifact_id: str) -> list[str]:
        rows = conn.execute(
            "SELECT artifact_id FROM lineage WHERE input_id = ? ORDER BY artifact_id", (artifact_id,)
        ).fetchall()
        return [row["artifact_id"] for row in rows]

    def list(
        self,
        conn: sqlite3.Connection,
        *,
        kind: str | None = None,
        tool_id: str | None = None,
        project_id: str | None = None,
        limit: int = 200,
    ) -> list[sqlite3.Row]:
        clauses: list[str] = []
        params: list[Any] = []
        for column, value in (("kind", kind), ("tool_id", tool_id), ("project_id", project_id)):
            if value is not None:
                clauses.append(f"{column} = ?")
                params.append(value)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(limit)
        return conn.execute(
            f"""SELECT artifact_id, kind, filename, byte_size, tool_id, job_id, project_id,
                       qa_status, created_at
                FROM artifacts {where} ORDER BY created_at DESC, artifact_id LIMIT ?""",
            params,
        ).fetchall()
