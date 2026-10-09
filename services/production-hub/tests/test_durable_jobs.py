from __future__ import annotations

import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import pytest

from production_hub.hub import Hub
from production_hub.jobs import FlowBusy, JobManager


def manager(home: Path) -> JobManager:
    return JobManager(Hub(home), mode="external")


def payload() -> dict[str, Any]:
    return {"artifact_id": "sha256:" + "a" * 64, "to": "graphics-v1a"}


def test_queue_survives_api_restart_without_execution(tmp_path: Path) -> None:
    first = manager(tmp_path)
    job = first.submit_send(payload())
    first.close()
    fresh = manager(tmp_path)
    assert fresh.status(job["job_id"])["status"] == "queued"  # type: ignore[index]
    assert fresh.status(job["job_id"])["attempts"] == 0  # type: ignore[index]
    fresh.close()


def test_idempotency_reuses_and_conflicts_across_managers(tmp_path: Path) -> None:
    from production_hub.jobs import IdempotencyConflict

    first, second = manager(tmp_path), manager(tmp_path)
    original = first.submit_send(payload(), idempotency_key="request-1")
    assert second.submit_send(payload(), idempotency_key="request-1") == original
    with pytest.raises(IdempotencyConflict):
        second.submit_send({**payload(), "to": "video-v1a"}, idempotency_key="request-1")


def test_competing_workers_claim_once(tmp_path: Path) -> None:
    first, second = manager(tmp_path), manager(tmp_path)
    job = first.submit_send(payload())
    barrier = threading.Barrier(2)

    def claim(item: JobManager) -> Any:
        barrier.wait()
        return item.queue.claim("worker", lease_seconds=30)

    with ThreadPoolExecutor(max_workers=2) as pool:
        leases = list(pool.map(claim, [first, second]))
    claimed = [lease for lease in leases if lease is not None]
    assert len(claimed) == 1 and claimed[0].job_id == job["job_id"]


def expire(item: JobManager, job_id: str) -> None:
    with item.hub.db.connect() as conn:
        conn.execute("UPDATE jobs SET lease_until = 0 WHERE job_id = ?", (job_id,))
        conn.commit()


def make_available(item: JobManager, job_id: str) -> None:
    with item.hub.db.connect() as conn:
        conn.execute("UPDATE jobs SET available_at = 0 WHERE job_id = ?", (job_id,))
        conn.commit()


def test_stale_lease_is_fenced_and_safe_work_reclaimed(tmp_path: Path) -> None:
    item = manager(tmp_path)
    job = item.submit_send(payload())
    old = item.queue.claim("old", lease_seconds=30)
    assert old is not None
    expire(item, job["job_id"])
    assert item.queue.heartbeat(old, lease_seconds=30) is False
    item.queue.recover()
    make_available(item, job["job_id"])
    new = item.queue.claim("new", lease_seconds=30)
    assert new is not None and new.token != old.token
    assert item.queue.reserve(old) is False
    assert item.queue.finish(old, {"wrong": True}) is False
    assert item.queue.reserve(new)
    assert item.queue.finish(new, {"correct": True})
    assert item.status(job["job_id"])["result"] == {"correct": True}  # type: ignore[index]


def test_retry_is_bounded_and_backoff_is_persisted(tmp_path: Path) -> None:
    item = manager(tmp_path)
    job = item.submit_send(payload())
    for attempt in range(1, 4):
        lease = item.queue.claim("worker", lease_seconds=30)
        assert lease is not None and lease.attempt == attempt
        assert item.queue.retry_before_effect(lease)
        status = item.status(job["job_id"])
        assert status is not None
        if attempt < 3:
            assert status["status"] == "queued"
            assert item.queue.claim("worker", lease_seconds=30) is None
            make_available(item, job["job_id"])
        else:
            assert status["status"] == "failed" and status["effect_state"] == "pending"
    assert item.queue.claim("worker", lease_seconds=30) is None


def test_crash_after_effect_requires_operator_reconcile(tmp_path: Path) -> None:
    item = manager(tmp_path)
    job = item.submit_flow("launch")
    lease = item.queue.claim("worker", lease_seconds=30)
    assert lease is not None and item.queue.reserve(lease)
    effect = tmp_path / "uploaded-object"
    effect.write_text("effect already happened")
    expire(item, job["job_id"])
    restarted = manager(tmp_path)
    restarted.queue.recover()
    status = restarted.status(job["job_id"])
    assert status is not None and status["status"] == "failed"
    assert status["effect_state"] == "needs_review"
    assert restarted.queue.claim("new", lease_seconds=30) is None
    assert restarted.queue.finish(lease, {}) is False
    with pytest.raises(FlowBusy):
        restarted.submit_flow("launch")
    assert restarted.queue.reconcile(
        job["job_id"], actor="owner", outcome="completed", evidence_ref="mock-upload-reopened",
        result={"verified": True}, worker_stopped_confirmed=True,
    )
    assert restarted.status(job["job_id"])["status"] == "completed"  # type: ignore[index]
    assert effect.read_text() == "effect already happened"


def test_cross_process_flow_uniqueness_and_different_flows(tmp_path: Path) -> None:
    first, second = manager(tmp_path), manager(tmp_path)
    first.submit_flow("launch", idempotency_key="flow-request")
    assert second.submit_flow("launch", idempotency_key="flow-request")["kind"] == "flow"
    with pytest.raises(FlowBusy):
        second.submit_flow("launch")
    second.submit_flow("other")
    a = first.queue.claim("a", lease_seconds=30)
    b = second.queue.claim("b", lease_seconds=30)
    assert a is not None and b is not None and a.job_id != b.job_id


def test_cancel_before_execution_never_executes(tmp_path: Path) -> None:
    item = manager(tmp_path)
    job = item.submit_send(payload())
    assert item.cancel(job["job_id"])
    assert item.queue.claim("worker", lease_seconds=30) is None
    status = item.status(job["job_id"])
    assert status is not None and status["status"] == "failed" and status["cancel_requested"] == 1
    job = item.submit_send(payload())
    lease = item.queue.claim("worker", lease_seconds=30)
    assert lease is not None and item.cancel(job["job_id"])
    assert not item.queue.reserve(lease)


def test_cancel_after_reservation_holds_flow_until_review(tmp_path: Path) -> None:
    item = manager(tmp_path)
    job = item.submit_flow("launch")
    lease = item.queue.claim("worker", lease_seconds=30)
    assert lease is not None and item.queue.reserve(lease)
    assert item.cancel(job["job_id"])
    assert not item.queue.finish(lease, {})
    with pytest.raises(FlowBusy):
        item.submit_flow("launch")
    assert item.status(job["job_id"])["effect_state"] == "needs_review"  # type: ignore[index]


def test_worker_executes_only_persisted_owned_operation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from production_hub.worker import Worker

    item = manager(tmp_path)
    job = item.submit_send(payload())
    worker = Worker(Hub(tmp_path), worker_id="one")
    monkeypatch.setattr(worker, "prepare", lambda lease: None)
    monkeypatch.setattr(worker.jobs, "_send_work", lambda data, log: {"outputs": [], "send_id": "s"})
    assert worker.run_once()
    assert item.status(job["job_id"])["status"] == "completed"  # type: ignore[index]
    assert not worker.run_once()
    worker.close()


def test_worker_failure_after_start_is_safe_and_redacted(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from production_hub.worker import Worker

    item = manager(tmp_path)
    job = item.submit_send(payload())
    worker = Worker(Hub(tmp_path), worker_id="one")
    monkeypatch.setattr(worker, "prepare", lambda lease: None)

    def fail(data: Any, log: Any) -> dict[str, Any]:
        raise RuntimeError("private-payload-do-not-log")

    monkeypatch.setattr(worker.jobs, "_send_work", fail)
    assert worker.run_once()
    status = item.status(job["job_id"])
    assert status is not None and status["effect_state"] == "needs_review"
    assert "private-payload" not in str(status)
    assert not worker.run_once()
    worker.close()


def test_worker_heartbeat_keeps_long_effect_leased(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import time

    from production_hub.worker import Worker

    item = manager(tmp_path)
    job = item.submit_send(payload())
    worker = Worker(Hub(tmp_path), worker_id="one", lease_seconds=0.15)
    monkeypatch.setattr(worker, "prepare", lambda lease: None)

    def slow(data: Any, log: Any) -> dict[str, Any]:
        time.sleep(0.4)
        item.queue.recover()
        assert item.status(job["job_id"])["status"] == "running"  # type: ignore[index]
        return {"outputs": []}

    monkeypatch.setattr(worker.jobs, "_send_work", slow)
    assert worker.run_once()
    assert item.status(job["job_id"])["status"] == "completed"  # type: ignore[index]
    worker.close()


def test_worker_cli_requires_explicit_persistent_home(tmp_path: Path) -> None:
    import sys

    command = [sys.executable, "-m", "production_hub.worker", "--once", "--home", str(tmp_path)]
    denied = subprocess.run(command, text=True, capture_output=True, check=False)
    assert denied.returncode != 0 and "persistent" in denied.stderr.lower()
    assert not (tmp_path / "hub.db").exists()
    accepted = subprocess.run(command + ["--persistent-volume-confirmed"], text=True,
                              capture_output=True, check=False)
    assert accepted.returncode == 0
    assert (tmp_path / "hub.db").is_file()


def test_real_processes_compete_for_one_lease(tmp_path: Path) -> None:
    import sys

    item = manager(tmp_path)
    job = item.submit_send(payload())
    program = (
        "import sys; from pathlib import Path; from production_hub.hub import Hub; "
        "from production_hub.jobs import JobManager; "
        "m=JobManager(Hub(Path(sys.argv[1])),mode='external'); "
        "lease=m.queue.claim(sys.argv[2],lease_seconds=30); "
        "print(lease.job_id if lease else 'none')"
    )
    children = [subprocess.Popen([sys.executable, "-c", program, str(tmp_path), owner],
                                 text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                for owner in ["process-a", "process-b"]]
    outputs = [child.communicate(timeout=20) for child in children]
    assert all(child.returncode == 0 for child in children), outputs
    assert sorted(output[0].strip() for output in outputs) == sorted([job["job_id"], "none"])
    assert item.status(job["job_id"])["attempts"] == 1  # type: ignore[index]


def test_worker_process_dies_after_effect_without_automatic_duplicate(tmp_path: Path) -> None:
    import sys

    item = manager(tmp_path)
    job = item.submit_send(payload())
    program = """
import os
import sys
from pathlib import Path
from production_hub.hub import Hub
from production_hub.worker import Worker
home = Path(sys.argv[1])
worker = Worker(Hub(home), worker_id='crashing-worker')
worker.prepare = lambda lease: None
def upload(data, log):
    (home / 'effect-count').write_text('1')
    os._exit(42)
worker.jobs._send_work = upload
worker.run_once()
"""
    child = subprocess.run([sys.executable, "-c", program, str(tmp_path)],
                           text=True, capture_output=True, timeout=20, check=False)
    assert child.returncode == 42
    assert (tmp_path / "effect-count").read_text() == "1"
    expire(item, job["job_id"])
    restarted = manager(tmp_path)
    restarted.queue.recover()
    assert restarted.queue.claim("next-process", lease_seconds=30) is None
    status = restarted.status(job["job_id"])
    assert status is not None and status["effect_state"] == "needs_review"
    assert (tmp_path / "effect-count").read_text() == "1"
    with pytest.raises(ValueError, match="stopped"):
        restarted.queue.reconcile(job["job_id"], actor="owner", outcome="abandoned",
                                   evidence_ref="effect-inspected")
    assert restarted.queue.reconcile(job["job_id"], actor="owner", outcome="abandoned",
                                      evidence_ref="effect-inspected", worker_stopped_confirmed=True)
    with restarted.hub.db.connect() as conn:
        event = conn.execute("SELECT * FROM job_events WHERE event='reconciled_abandoned'").fetchone()
        assert event is not None and event["actor"] == "owner" and event["evidence_ref"] == "effect-inspected"


def test_mode_change_does_not_fail_external_queue(tmp_path: Path) -> None:
    external = manager(tmp_path)
    job = external.submit_send(payload())
    inline = JobManager(Hub(tmp_path), mode="inline")
    assert inline.status(job["job_id"])["status"] == "queued"  # type: ignore[index]
    inline.close()


def test_real_processes_duplicate_enqueue_once(tmp_path: Path) -> None:
    import sys

    item = manager(tmp_path)
    program = (
        "import sys; from pathlib import Path; from production_hub.hub import Hub; "
        "from production_hub.jobs import JobManager; "
        "m=JobManager(Hub(Path(sys.argv[1])),mode='external'); "
        "print(m.submit_flow('launch',idempotency_key='shared-request')['job_id'])"
    )
    children = [subprocess.Popen([sys.executable, "-c", program, str(tmp_path)],
                                 text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE) for _ in range(2)]
    outputs = [child.communicate(timeout=20) for child in children]
    assert all(child.returncode == 0 for child in children), outputs
    assert outputs[0][0] == outputs[1][0]
    with item.hub.db.connect() as conn:
        assert conn.execute("SELECT count(*) FROM jobs").fetchone()[0] == 1


def test_safe_prepare_errors_retry_to_cap_without_leaking(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from production_hub.worker import Worker

    item = manager(tmp_path)
    job = item.submit_send(payload())
    worker = Worker(Hub(tmp_path))

    def temporary(lease: Any) -> None:
        raise OSError("private-source-path")

    monkeypatch.setattr(worker, "prepare", temporary)
    for _ in range(3):
        make_available(item, job["job_id"])
        assert worker.run_once()
    assert not worker.run_once()
    status = item.status(job["job_id"])
    assert status is not None and status["status"] == "failed" and status["attempts"] == 3
    assert status["effect_state"] == "pending" and "private-source" not in str(status)
    worker.close()


def test_legacy_completed_job_migrates_without_changing_result(tmp_path: Path) -> None:
    import json

    from production_hub.jobs import JOBS_SCHEMA

    hub = Hub(tmp_path)
    with hub.db.connect() as conn:
        conn.executescript(JOBS_SCHEMA)
        conn.execute("""INSERT INTO jobs
            (job_id,kind,status,payload_json,result_json,created_at,updated_at)
            VALUES ('20260930T000000-00000000','flow','completed',?,?, 't','t')""",
                     (json.dumps({"flow_id": "legacy"}), json.dumps({"flow_id": "legacy", "steps": []})))
        conn.commit()
    upgraded = manager(tmp_path)
    job = upgraded.status("20260930T000000-00000000")
    assert job is not None and job["status"] == "completed"
    assert job["result"] == {"flow_id": "legacy", "steps": []}
    assert job["flow_id"] == "legacy" and job["queue_mode"] == "inline"
    assert not {"payload_json", "payload_hash", "lease_token", "idempotency_key"}.intersection(job)


def test_worker_cli_storage_error_does_not_print_paths_or_traceback(tmp_path: Path) -> None:
    import sys

    bad_home = tmp_path / "private-owned-volume-path"
    bad_home.write_text("not a directory")
    child = subprocess.run([sys.executable, "-m", "production_hub.worker", "--once",
                            "--home", str(bad_home), "--persistent-volume-confirmed"],
                           text=True, capture_output=True, check=False)
    assert child.returncode == 1 and "worker_storage_unavailable" in child.stdout
    assert "Traceback" not in child.stderr and "private-owned" not in child.stderr
