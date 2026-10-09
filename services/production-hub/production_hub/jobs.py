"""Persisted jobs with legacy development execution or an explicit external worker.

``PRODUCTION_HUB_JOB_MODE=external`` makes submission persist-only. Only the
legacy inline mode fails interrupted inline jobs on startup.
"""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import threading
from collections.abc import Callable, Mapping
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from typing import Any

from .durable_jobs import JOBS_SCHEMA, DurableQueue, FlowBusy, IdempotencyConflict
from .flows import FLOWS_SCHEMA, FlowStatus, load_flow, run_flow
from .hub import Hub
from .send import send
from .workspace import WORKSPACE_SCHEMA

__all__ = ["JOBS_SCHEMA", "FlowBusy", "IdempotencyConflict", "JobManager", "Log", "Runner"]

MAX_LOG_LINES = 20

Runner = Callable[..., subprocess.CompletedProcess[str]]
Log = Callable[[str], None]


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class JobManager:
    def __init__(self, hub: Hub, *, max_workers: int = 2, runner: Runner = subprocess.run,
                 mode: str | None = None) -> None:
        self.hub = hub
        self.mode = mode if mode is not None else os.environ.get("PRODUCTION_HUB_JOB_MODE", "inline")
        if self.mode not in {"inline", "external"}:
            raise ValueError("PRODUCTION_HUB_JOB_MODE must be inline or external")
        self._runner = runner
        self.queue = DurableQueue(hub.db)
        self._pool = (ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="hub-job")
                      if self.mode == "inline" else None)
        self._flow_locks: dict[str, threading.Lock] = {}
        self._locks_guard = threading.Lock()
        if self.mode == "inline":
            self._recover_stale_jobs()

    def close(self) -> None:
        if self._pool is not None:
            self._pool.shutdown(wait=True)

    def _recover_stale_jobs(self) -> None:
        with self.hub.db.connect() as conn:
            conn.executescript(JOBS_SCHEMA)
            conn.execute(
                "UPDATE jobs SET status = 'failed', error = 'hub restarted before this job finished', "
                "updated_at = ? WHERE queue_mode = 'inline' AND status IN ('queued', 'running')",
                (_now(),),
            )
            conn.commit()

    def _update(self, job_id: str, *, status: str | None = None, error: str | None = None,
                result: Mapping[str, Any] | None = None, log_line: str | None = None) -> None:
        assignments, values = ["updated_at = ?"], [_now()]
        if status is not None:
            assignments.append("status = ?")
            values.append(status)
        if error is not None:
            assignments.append("error = ?")
            values.append(error)
        if result is not None:
            assignments.append("result_json = ?")
            values.append(json.dumps(result, ensure_ascii=False, sort_keys=True))
        if log_line is not None:
            assignments.append("log = ?")
            values.append(json.dumps(
                (self._read_log(job_id) + [log_line])[-50:], ensure_ascii=False))
        values.append(job_id)
        with self.hub.db.connect() as conn:
            conn.executescript(JOBS_SCHEMA)
            conn.execute(f"UPDATE jobs SET {', '.join(assignments)} WHERE job_id = ?", values)
            conn.commit()

    def _read_log(self, job_id: str) -> list[str]:
        with self.hub.db.connect() as conn:
            conn.executescript(JOBS_SCHEMA)
            row = conn.execute("SELECT log FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
        return list(json.loads(row["log"])) if row else []

    def status(self, job_id: str) -> dict[str, Any] | None:
        with self.hub.db.connect() as conn:
            conn.executescript(JOBS_SCHEMA)
            row = conn.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
        if row is None:
            return None
        record = dict(row)
        record.pop("payload_json")
        for private in ("lease_token", "idempotency_key", "payload_hash"):
            record.pop(private, None)
        record["schema_version"] = "job.v1"
        record["log"] = json.loads(record.pop("log"))[-MAX_LOG_LINES:]
        result_json = record.pop("result_json")
        record["result"] = json.loads(result_json) if result_json else None
        return record

    def submit_send(self, payload: Mapping[str, Any], *, idempotency_key: str | None = None) -> dict[str, str]:
        return self._submit("send", payload, lambda job_id, log: self._send_work(payload, log),
                            idempotency_key=idempotency_key)

    def submit_flow(self, flow_id: str, *, idempotency_key: str | None = None) -> dict[str, str]:
        # Keyed submissions use the DB's uniqueness lock, allowing a retry to
        # find its existing job while the original inline operation is running.
        if self.mode == "external" or idempotency_key is not None:
            return self._submit("flow", {"flow_id": flow_id},
                                lambda job_id, log: self._flow_work(flow_id, log),
                                idempotency_key=idempotency_key)
        lock = self._flow_lock(flow_id)
        if not lock.acquire(blocking=False):
            raise FlowBusy(f"flow {flow_id} is already queued or running")
        try:
            def work(job_id: str, log: Log) -> dict[str, Any]:
                return self._flow_work(flow_id, log)

            return self._submit("flow", {"flow_id": flow_id}, work, release=lock,
                                idempotency_key=idempotency_key)
        except BaseException:
            lock.release()
            raise

    def submit_template(self, request: Mapping[str, Any],
                        build: Callable[[], Mapping[str, Any]], *,
                        idempotency_key: str | None = None) -> dict[str, str]:
        if self.mode != "external":
            raise ValueError("atomic template submission requires external job mode")
        # Schema setup precedes the queue's BEGIN IMMEDIATE. Domain DML below
        # joins that transaction without any implicit executescript commits.
        with self.hub.db.connect() as conn:
            conn.executescript(FLOWS_SCHEMA + WORKSPACE_SCHEMA)

        def materialize(conn: sqlite3.Connection) -> Mapping[str, Any]:
            return {"flow_id": load_flow(self.hub, build(), conn=conn)}

        job, _ = self.queue.enqueue("flow", request, mode="external", idempotency_key=idempotency_key,
                                    materialize=materialize)
        with self.hub.db.connect() as conn:
            row = conn.execute("SELECT flow_id FROM jobs WHERE job_id=?", (job["job_id"],)).fetchone()
            assert row is not None
            return {"job_id": job["job_id"], "flow_id": str(row["flow_id"]), "status": job["status"]}

    def _submit(self, kind: str, payload: Mapping[str, Any],
                work: Callable[[str, Log], dict[str, Any]],
                release: threading.Lock | None = None,
                idempotency_key: str | None = None) -> dict[str, str]:
        job, created = self.queue.enqueue(kind, payload, mode=self.mode, idempotency_key=idempotency_key)
        if created and self._pool is not None:
            self._pool.submit(self._run, job["job_id"], work, release)
        elif release is not None:
            release.release()
        return job

    def cancel(self, job_id: str) -> bool:
        return self.queue.cancel(job_id)

    def _flow_lock(self, flow_id: str) -> threading.Lock:
        with self._locks_guard:
            return self._flow_locks.setdefault(flow_id, threading.Lock())

    def _run(self, job_id: str, work: Callable[[str, Log], dict[str, Any]],
             release: threading.Lock | None) -> None:
        def log(line: str) -> None:
            self._update(job_id, log_line=line)

        try:
            self._update(job_id, status="running")
            try:
                result = work(job_id, log)
            except Exception as exc:  # noqa: BLE001 - the job record is the error report; never crash the pool
                if release is not None:
                    release.release()
                    release = None
                self._update(job_id, status="failed", error=str(exc))
                return
            # The database's active-flow index protects the running interval;
            # publish terminal status only after the local lock is released.
            if release is not None:
                release.release()
                release = None
            self._update(job_id, status="completed", result=result)
        finally:
            if release is not None:
                release.release()

    def _send_work(self, payload: Mapping[str, Any], log: Log) -> dict[str, Any]:
        artifact_id, to_tool = str(payload["artifact_id"]), str(payload["to"])
        log(f"sending {artifact_id[:19]}... to {to_tool}")
        result = send(
            self.hub, artifact_id, to_tool,
            extra_ids=[str(item) for item in payload.get("with", [])],
            params={str(k): str(v) for k, v in dict(payload.get("params", {})).items()},
            project_id=str(payload["project_id"]) if payload.get("project_id") else None,
            runner=self._runner,
        )
        log(f"send {result.send_id} completed ({result.handoff_id})")
        return {
            "send_id": result.send_id, "handoff_id": result.handoff_id,
            "outputs": [
                {"artifact_id": item.artifact_id, "kind": item.kind, "filename": item.filename}
                for item in (result.ingest.outputs if result.ingest else [])
            ],
        }

    def _flow_work(self, flow_id: str, log: Log) -> dict[str, Any]:
        log(f"running flow {flow_id}")
        status: FlowStatus = run_flow(self.hub, flow_id, runner=self._runner)
        log(f"flow {flow_id} finished with status {status.status}")
        return {"flow_id": status.flow_id, "flow_status": status.status, "error": status.error,
                "steps": status.steps}
