"""Owned Production Hub worker on one persistent SQLite volume.

The CLI requires an explicit home and a deployment-owner volume assertion;
it cannot establish cloud volume persistence or backup health on its own.
No caller-supplied command is executed: only persisted send/flow operations
through the existing adapters are supported.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import signal
import sqlite3
import subprocess
import threading
import uuid
from pathlib import Path
from typing import Any

from .durable_jobs import Lease
from .flows import flow_status
from .hub import Hub
from .jobs import JobManager, Runner
from .paths import REPO_ROOT, WORKSPACE_ROOT
from .send import preflight


class Worker:
    def __init__(self, hub: Hub, *, worker_id: str | None = None,
                 lease_seconds: float = 60, runner: Runner = subprocess.run) -> None:
        if lease_seconds <= 0 or not math.isfinite(lease_seconds):
            raise ValueError("lease_seconds must be positive and finite")
        # Hub.transaction has mutable rollback state. Each worker owns its Hub.
        self.jobs = JobManager(Hub(hub.home), mode="external", runner=runner)
        self.worker_id = worker_id or f"hub-worker-{os.getpid()}-{uuid.uuid4().hex[:8]}"
        self.lease_seconds = lease_seconds
        self._running = threading.Lock()

    def close(self) -> None:
        self.jobs.close()

    def prepare(self, lease: Lease) -> None:
        """Read-only validation before reserving an unrepeatable operation."""
        if lease.kind == "send":
            data = lease.payload
            preflight(self.jobs.hub, str(data["artifact_id"]), str(data["to"]),
                      extra_ids=[str(item) for item in data.get("with", [])],
                      params={str(k): str(v) for k, v in dict(data.get("params", {})).items()})
        elif lease.kind == "flow":
            flow_status(self.jobs.hub, str(lease.payload["flow_id"]))
        else:
            raise ValueError("unsupported owned job kind")

    def run_once(self) -> bool:
        """Claim at most one job; finish/review it and close every DB context."""
        if not self._running.acquire(blocking=False):
            raise RuntimeError("one Worker cannot execute overlapping jobs")
        try:
            return self._run_once()
        finally:
            self._running.release()

    def _run_once(self) -> bool:
        queue = self.jobs.queue
        lease = queue.claim(self.worker_id, lease_seconds=self.lease_seconds)
        if lease is None:
            return False
        stop = threading.Event()
        lost = threading.Event()

        def heartbeat() -> None:
            while not stop.wait(self.lease_seconds / 3):
                try:
                    if not queue.heartbeat(lease, lease_seconds=self.lease_seconds):
                        lost.set()
                        return
                except sqlite3.Error:
                    lost.set()
                    return

        thread = threading.Thread(target=heartbeat, name="hub-job-heartbeat", daemon=True)
        thread.start()
        try:
            try:
                self.prepare(lease)
            except Exception as exc:  # noqa: BLE001 - safe errors exclude tool/private payload text
                queue.retry_before_effect(lease, permanent=not isinstance(exc, (OSError, sqlite3.OperationalError)))
                return True
            if lost.is_set() or not queue.reserve(lease):
                queue.recover()
                return True

            def log(line: str) -> None:
                # Adapter output may include paths or private caller data. Keep bounded telemetry only.
                if not lost.is_set():
                    queue.log(lease, "owned operation progress")

            try:
                if lease.kind == "send":
                    result = self.jobs._send_work(lease.payload, log)
                else:
                    result = self.jobs._flow_work(str(lease.payload["flow_id"]), log)
                # A flow reports failure as data; do not treat it as a successful job.
                if (lease.kind == "flow" and result.get("flow_status") == "failed") or lost.is_set():
                    queue.uncertain(lease)
                else:
                    queue.finish(lease, result)
            except Exception:  # noqa: BLE001 - reservation may already have emitted an external effect
                queue.uncertain(lease)
            queue.recover()
            return True
        finally:
            stop.set()
            thread.join()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--home", type=Path, required=True, help="owned persistent volume directory")
    parser.add_argument("--persistent-volume-confirmed", action="store_true",
                        help="deployment owner confirms this home survives container/process replacement")
    actions = parser.add_mutually_exclusive_group()
    actions.add_argument("--once", action="store_true", help="at most one claim, then exit")
    actions.add_argument("--reconcile-job", help="owner review only; never re-executes a job")
    parser.add_argument("--outcome", choices=("completed", "abandoned"))
    parser.add_argument("--actor")
    parser.add_argument("--evidence-ref")
    parser.add_argument("--worker-stopped-confirmed", action="store_true",
                        help="owner has stopped/verified termination of the previous effect executor")
    parser.add_argument("--result-json", help="verified .v1 result for completed reconciliation")
    parser.add_argument("--lease-seconds", type=float, default=60)
    parser.add_argument("--poll-seconds", type=float, default=2)
    args = parser.parse_args(argv)
    home = args.home.resolve()
    if not args.persistent_volume_confirmed:
        parser.error("persistent volume readiness requires --persistent-volume-confirmed")
    if home == Path("/") or any(home == root or root in home.parents for root in (REPO_ROOT, WORKSPACE_ROOT)):
        parser.error("worker home must be an owned volume outside the root repository")
    if args.poll_seconds <= 0 or not math.isfinite(args.poll_seconds):
        parser.error("poll-seconds must be positive and finite")
    try:
        worker = Worker(Hub(home), lease_seconds=args.lease_seconds)
    except ValueError as exc:
        parser.error(str(exc))
    except (OSError, sqlite3.Error):
        print(json.dumps({"error": "worker_storage_unavailable"}))
        return 1
    try:
        if args.reconcile_job:
            if not args.actor or not args.evidence_ref or not args.outcome:
                parser.error("reconciliation requires owner actor, evidence-ref and outcome")
            try:
                result: Any = json.loads(args.result_json) if args.result_json else None
                if result is not None and not isinstance(result, dict):
                    raise ValueError("verified result must be an object")
                reconciled = worker.jobs.queue.reconcile(args.reconcile_job, actor=args.actor,
                                                        outcome=args.outcome, evidence_ref=args.evidence_ref,
                                                        result=result,
                                                        worker_stopped_confirmed=args.worker_stopped_confirmed)
            except ValueError as exc:
                parser.error(str(exc))
            print(json.dumps({"job_id": args.reconcile_job, "reconciled": reconciled}))
            return 0 if reconciled else 1
        if args.once:
            worker.run_once()
            return 0
        shutdown = threading.Event()
        for name in (signal.SIGTERM, signal.SIGINT):
            signal.signal(name, lambda signum, frame: shutdown.set())
        while not shutdown.is_set():
            if not worker.run_once():
                shutdown.wait(args.poll_seconds)
        return 0
    except (OSError, sqlite3.Error):
        print(json.dumps({"error": "worker_storage_unavailable"}))
        return 1
    finally:
        worker.close()


if __name__ == "__main__":
    raise SystemExit(main())
