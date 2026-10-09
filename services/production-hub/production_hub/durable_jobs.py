"""Transactional job leases on one owned SQLite volume (not an HA queue).

Only work which has not reserved an effect may be retried automatically. A
reservation is deliberately conservative: even a crash just before the tool
starts requires reconciliation, since tool execution is not provably idempotent.
"""

from __future__ import annotations

import hashlib
import json
import math
import sqlite3
import time
import uuid
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from .db import HubDB

JOBS_SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    job_id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    status TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    result_json TEXT,
    error TEXT,
    log TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""

_COLUMNS = {
    "queue_mode": "TEXT NOT NULL DEFAULT 'inline'",
    "idempotency_key": "TEXT",
    "payload_hash": "TEXT",
    "flow_id": "TEXT",
    "attempts": "INTEGER NOT NULL DEFAULT 0",
    "max_attempts": "INTEGER NOT NULL DEFAULT 3",
    "available_at": "REAL NOT NULL DEFAULT 0",
    "lease_token": "TEXT",
    "lease_owner": "TEXT",
    "lease_until": "REAL",
    "effect_state": "TEXT NOT NULL DEFAULT 'pending'",
    "cancel_requested": "INTEGER NOT NULL DEFAULT 0",
}


class FlowBusy(RuntimeError):
    """Another active or unreconciled job owns this flow."""


class IdempotencyConflict(RuntimeError):
    """A request key was already used with a different kind or payload."""


@dataclass(frozen=True)
class Lease:
    job_id: str
    kind: str
    payload: dict[str, Any]
    token: str
    owner: str
    attempt: int


def timestamp() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _duration(seconds: float) -> None:
    if not math.isfinite(seconds) or seconds <= 0:
        raise ValueError("lease duration must be positive and finite")


class DurableQueue:
    def __init__(self, db: HubDB) -> None:
        self.db = db
        self._migrate()

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        with self.db.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                yield conn
                conn.commit()
            except BaseException:
                conn.rollback()
                raise

    def _migrate(self) -> None:
        with self._transaction() as conn:
            conn.execute(JOBS_SCHEMA.strip().rstrip(";"))
            existing = {row["name"] for row in conn.execute("PRAGMA table_info(jobs)")}
            for name, definition in _COLUMNS.items():
                if name not in existing:
                    conn.execute(f"ALTER TABLE jobs ADD COLUMN {name} {definition}")
            # Upgrade only old flow records; their .v1 job IDs and result shape stay intact.
            for row in conn.execute("SELECT job_id, payload_json FROM jobs WHERE kind='flow' AND flow_id IS NULL"):
                flow_id = json.loads(row["payload_json"]).get("flow_id")
                if flow_id:
                    conn.execute("UPDATE jobs SET flow_id=? WHERE job_id=?", (flow_id, row["job_id"]))
            conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS jobs_request ON jobs(idempotency_key) "
                         "WHERE idempotency_key IS NOT NULL")
            conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS jobs_active_flow ON jobs(flow_id) "
                         "WHERE flow_id IS NOT NULL AND "
                         "(status IN ('queued', 'running') OR effect_state='needs_review')")
            conn.execute("CREATE INDEX IF NOT EXISTS jobs_ready ON jobs(queue_mode, status, available_at)")
            conn.execute("""CREATE TABLE IF NOT EXISTS job_effects (
                job_id TEXT PRIMARY KEY REFERENCES jobs(job_id), lease_token TEXT NOT NULL,
                state TEXT NOT NULL, updated_at TEXT NOT NULL)""")
            conn.execute("""CREATE TABLE IF NOT EXISTS job_events (
                event_id INTEGER PRIMARY KEY, job_id TEXT NOT NULL REFERENCES jobs(job_id),
                event TEXT NOT NULL, actor TEXT NOT NULL, evidence_ref TEXT, created_at TEXT NOT NULL)""")

    def enqueue(self, kind: str, payload: Mapping[str, Any], *, mode: str,
                idempotency_key: str | None = None, max_attempts: int = 3,
                materialize: Callable[[sqlite3.Connection], Mapping[str, Any]] | None = None,
                ) -> tuple[dict[str, str], bool]:
        """Hash the client request before invoking an owned transactional allocation."""
        if kind not in {"send", "flow"} or mode not in {"inline", "external"}:
            raise ValueError("unsupported owned job operation or mode")
        if not 1 <= max_attempts <= 10:
            raise ValueError("max_attempts must be between 1 and 10")
        if idempotency_key is not None and (
            not isinstance(idempotency_key, str) or not idempotency_key.strip() or len(idempotency_key) > 256
        ):
            raise ValueError("idempotency_key must be a nonempty string of at most 256 characters")
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
        digest = hashlib.sha256((kind + "\n" + encoded).encode()).hexdigest()
        job_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:8]
        with self._transaction() as conn:
            if idempotency_key is not None:
                old = conn.execute("SELECT * FROM jobs WHERE idempotency_key=?", (idempotency_key,)).fetchone()
                if old is not None:
                    if old["payload_hash"] != digest:
                        raise IdempotencyConflict("idempotency key already used for a different payload")
                    return {key: str(old[key]) for key in ("job_id", "kind", "status")}, False
            execution_payload = materialize(conn) if materialize is not None else payload
            encoded = json.dumps(execution_payload, ensure_ascii=False, sort_keys=True,
                                 separators=(",", ":"), allow_nan=False)
            flow_id = str(execution_payload["flow_id"]) if kind == "flow" else None
            if flow_id and conn.execute(
                "SELECT 1 FROM jobs WHERE flow_id=? AND "
                "(status IN ('queued', 'running') OR effect_state='needs_review')", (flow_id,)
            ).fetchone():
                raise FlowBusy(f"flow {flow_id} is active or needs owner review")
            conn.execute("""INSERT INTO jobs (job_id, kind, status, payload_json, created_at, updated_at,
                queue_mode, idempotency_key, payload_hash, flow_id, max_attempts)
                VALUES (?, ?, 'queued', ?, ?, ?, ?, ?, ?, ?, ?)""",
                         (job_id, kind, encoded, timestamp(), timestamp(), mode, idempotency_key,
                          digest, flow_id, max_attempts))
            self._event(conn, job_id, "enqueued", "api")
        return {"job_id": job_id, "kind": kind, "status": "queued"}, True

    def _event(self, conn: sqlite3.Connection, job_id: str, event: str, actor: str,
               evidence_ref: str | None = None) -> None:
        conn.execute("INSERT INTO job_events (job_id,event,actor,evidence_ref,created_at) VALUES (?,?,?,?,?)",
                     (job_id, event, actor, evidence_ref, timestamp()))

    def _live(self, conn: sqlite3.Connection, lease: Lease) -> sqlite3.Row | None:
        row: sqlite3.Row | None = conn.execute("""SELECT * FROM jobs WHERE job_id=? AND lease_token=? AND lease_owner=?
            AND status='running' AND lease_until>? AND cancel_requested=0""",
                            (lease.job_id, lease.token, lease.owner, time.time())).fetchone()
        return row

    def _backoff(self, attempt: int) -> float:
        return float(min(300, 2 ** min(attempt, 10)))

    def _recover(self, conn: sqlite3.Connection) -> None:
        rows = conn.execute("SELECT * FROM jobs WHERE queue_mode='external' AND status='running' "
                            "AND lease_until<=?", (time.time(),)).fetchall()
        for row in rows:
            review = row["effect_state"] == "reserved"
            status = "failed" if review or row["attempts"] >= row["max_attempts"] else "queued"
            effect_state = "needs_review" if review else "pending"
            error = "effect outcome uncertain; owner review required" if review else "worker lease expired"
            conn.execute("""UPDATE jobs SET status=?, effect_state=?, error=?, lease_token=NULL,
                lease_owner=NULL, lease_until=NULL, available_at=?, updated_at=? WHERE job_id=?""",
                         (status, effect_state, error, time.time() + self._backoff(row["attempts"]),
                          timestamp(), row["job_id"]))
            if review:
                conn.execute("UPDATE job_effects SET state='needs_review',updated_at=? WHERE job_id=?",
                             (timestamp(), row["job_id"]))
            self._event(conn, row["job_id"], "lease_expired_" + effect_state, "recovery")

    def recover(self) -> None:
        with self._transaction() as conn:
            self._recover(conn)

    def claim(self, owner: str, *, lease_seconds: float = 60) -> Lease | None:
        _duration(lease_seconds)
        if not owner:
            raise ValueError("worker owner is required")
        with self._transaction() as conn:
            self._recover(conn)
            row = conn.execute("""SELECT * FROM jobs WHERE queue_mode='external' AND status='queued'
                AND cancel_requested=0 AND effect_state='pending' AND attempts<max_attempts AND available_at<=?
                ORDER BY available_at, created_at, job_id LIMIT 1""", (time.time(),)).fetchone()
            if row is None:
                return None
            token = uuid.uuid4().hex
            conn.execute("""UPDATE jobs SET status='running', attempts=attempts+1, lease_owner=?, lease_token=?,
                lease_until=?, updated_at=?, error=NULL WHERE job_id=?""",
                         (owner, token, time.time() + lease_seconds, timestamp(), row["job_id"]))
            self._event(conn, row["job_id"], "claimed", owner)
            return Lease(row["job_id"], row["kind"], json.loads(row["payload_json"]), token, owner,
                         row["attempts"] + 1)

    def heartbeat(self, lease: Lease, *, lease_seconds: float = 60) -> bool:
        _duration(lease_seconds)
        with self._transaction() as conn:
            if self._live(conn, lease) is None:
                return False
            conn.execute("UPDATE jobs SET lease_until=?,updated_at=? WHERE job_id=?",
                         (time.time() + lease_seconds, timestamp(), lease.job_id))
            return True

    def reserve(self, lease: Lease) -> bool:
        with self._transaction() as conn:
            row = self._live(conn, lease)
            if row is None or row["effect_state"] != "pending":
                return False
            conn.execute("UPDATE jobs SET effect_state='reserved',updated_at=? WHERE job_id=?",
                         (timestamp(), lease.job_id))
            conn.execute("INSERT INTO job_effects (job_id,lease_token,state,updated_at) VALUES (?,?,'reserved',?)",
                         (lease.job_id, lease.token, timestamp()))
            self._event(conn, lease.job_id, "effect_reserved", lease.owner)
            return True

    def finish(self, lease: Lease, result: Mapping[str, Any]) -> bool:
        encoded = json.dumps(result, ensure_ascii=False, sort_keys=True, allow_nan=False)
        with self._transaction() as conn:
            row = self._live(conn, lease)
            if row is None or row["effect_state"] != "reserved":
                return False
            conn.execute("""UPDATE jobs SET status='completed',effect_state='confirmed',result_json=?,error=NULL,
                lease_token=NULL,lease_owner=NULL,lease_until=NULL,updated_at=? WHERE job_id=?""",
                         (encoded, timestamp(), lease.job_id))
            conn.execute("UPDATE job_effects SET state='confirmed',updated_at=? WHERE job_id=?",
                         (timestamp(), lease.job_id))
            self._event(conn, lease.job_id, "completed", lease.owner)
            return True

    def retry_before_effect(self, lease: Lease, *, permanent: bool = False) -> bool:
        with self._transaction() as conn:
            row = self._live(conn, lease)
            if row is None or row["effect_state"] != "pending":
                return False
            failed = permanent or row["attempts"] >= row["max_attempts"]
            error = "job preparation failed" if failed else "job preparation failed; retry scheduled"
            conn.execute("""UPDATE jobs SET status=?,error=?,available_at=?,updated_at=?,
                lease_token=NULL,lease_owner=NULL,lease_until=NULL WHERE job_id=?""",
                         ("failed" if failed else "queued", error,
                          time.time() + self._backoff(row["attempts"]), timestamp(), lease.job_id))
            self._event(conn, lease.job_id, "preparation_failed" if failed else "retry_scheduled", lease.owner)
            return True

    def uncertain(self, lease: Lease) -> bool:
        with self._transaction() as conn:
            row = self._live(conn, lease)
            if row is None or row["effect_state"] != "reserved":
                return False
            conn.execute("""UPDATE jobs SET status='failed',effect_state='needs_review',
                error='effect outcome uncertain; owner review required',updated_at=?,
                lease_token=NULL,lease_owner=NULL,lease_until=NULL WHERE job_id=?""",
                         (timestamp(), lease.job_id))
            conn.execute("UPDATE job_effects SET state='needs_review',updated_at=? WHERE job_id=?",
                         (timestamp(), lease.job_id))
            self._event(conn, lease.job_id, "needs_review", lease.owner)
            return True

    def cancel(self, job_id: str) -> bool:
        with self._transaction() as conn:
            row = conn.execute("SELECT * FROM jobs WHERE job_id=? AND queue_mode='external'", (job_id,)).fetchone()
            if row is None or row["status"] not in {"queued", "running"}:
                return False
            review = row["effect_state"] == "reserved"
            conn.execute("""UPDATE jobs SET status='failed',cancel_requested=1,effect_state=?,error=?,
                updated_at=?,lease_token=NULL,lease_owner=NULL,lease_until=NULL WHERE job_id=?""",
                         ("needs_review" if review else "pending",
                          "cancel requested; effect requires owner review" if review else "cancelled before execution",
                          timestamp(), job_id))
            if review:
                conn.execute("UPDATE job_effects SET state='needs_review',updated_at=? WHERE job_id=?",
                             (timestamp(), job_id))
            self._event(conn, job_id, "cancel_requested", "api")
            return True

    def reconcile(self, job_id: str, *, actor: str, outcome: str,
                  evidence_ref: str, result: Mapping[str, Any] | None = None,
                  worker_stopped_confirmed: bool = False) -> bool:
        """Owner confirms completion or final abandonment; this never re-executes work.

        The owner must first stop/verify the old worker is dead, then verify remote
        effects. A new intentional submit/resume after abandonment is a separate
        approved action, since this queue cannot fence an external tool's writes.
        """
        if not actor.strip() or not evidence_ref.strip() or outcome not in {"completed", "abandoned"}:
            raise ValueError("owner, evidence reference and completed/abandoned outcome are required")
        if not worker_stopped_confirmed:
            raise ValueError("owner must confirm the previous worker is stopped before reconciliation")
        if outcome == "completed" and result is None:
            raise ValueError("verified completion result is required")
        with self._transaction() as conn:
            row = conn.execute("SELECT * FROM jobs WHERE job_id=? AND effect_state='needs_review'",
                               (job_id,)).fetchone()
            if row is None:
                return False
            conn.execute("""UPDATE jobs SET status=?,effect_state='reconciled',result_json=?,error=?,updated_at=?
                WHERE job_id=?""",
                         ("completed" if outcome == "completed" else "failed",
                          json.dumps(result, ensure_ascii=False, sort_keys=True) if result is not None else None,
                          None if outcome == "completed" else "abandoned after owner review", timestamp(), job_id))
            conn.execute("UPDATE job_effects SET state='reconciled',updated_at=? WHERE job_id=?",
                         (timestamp(), job_id))
            self._event(conn, job_id, "worker_stopped_confirmed", actor, evidence_ref)
            self._event(conn, job_id, "reconciled_" + outcome, actor, evidence_ref)
            return True

    def log(self, lease: Lease, line: str) -> bool:
        with self._transaction() as conn:
            row = self._live(conn, lease)
            if row is None:
                return False
            lines = (json.loads(row["log"]) + [line])[-50:]
            conn.execute("UPDATE jobs SET log=?,updated_at=? WHERE job_id=?",
                         (json.dumps(lines, ensure_ascii=False), timestamp(), lease.job_id))
            return True
