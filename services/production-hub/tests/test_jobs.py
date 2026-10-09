from __future__ import annotations

import hashlib
import json
import subprocess
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from production_hub import send as send_module
from production_hub.flows import load_flow
from production_hub.hub import ArtifactInput, Hub, Producer
from production_hub.jobs import JOBS_SCHEMA, FlowBusy, JobManager
from production_hub.send import SendAdapter, SendContext, ToolRun


def _fake_build(ctx: SendContext) -> ToolRun:
    out = ctx.work_dir / "out"
    return ToolRun(argv=["fake", str(out)], cwd=ctx.work_dir, manifest_path=lambda: out / "manifest-x.json")


def _fake_run(argv: list[str], **_: Any) -> subprocess.CompletedProcess[str]:
    out = Path(argv[1])
    out.mkdir(parents=True, exist_ok=True)
    png = b"\x89PNG job"
    (out / "card.png").write_bytes(png)
    (out / "manifest-x.json").write_text(json.dumps({
        "schema_version": "graphics-batch.v1", "batch_id": "b", "status": "accepted", "metadata": {},
        "items": [{"request_id": "card", "status": "accepted", "artifacts": [
            {"filename": "card.png", "media_type": "image/png", "content_hash": hashlib.sha256(png).hexdigest()}]}],
    }), encoding="utf-8")
    return subprocess.CompletedProcess(argv, 0, "", "")


@pytest.fixture
def fake_adapters() -> Iterator[None]:
    saved = dict(send_module.SEND_ADAPTERS)
    send_module.register_adapter(SendAdapter("E4-chart-to-graphics", _fake_build, required_params=frozenset({"title"})))
    yield
    send_module.SEND_ADAPTERS.clear()
    send_module.SEND_ADAPTERS.update(saved)


@pytest.fixture
def env(tmp_path: Path) -> Iterator[tuple[Hub, JobManager]]:
    hub = Hub(tmp_path / "home")
    manager = JobManager(hub, runner=_fake_run)
    yield hub, manager
    manager.close()


def _chart(hub: Hub) -> str:
    hub.home.mkdir(parents=True, exist_ok=True)
    path = hub.home / "chart.svg"
    path.write_bytes(b"<svg>job</svg>")
    with hub.transaction() as conn:
        return hub.register(conn, ArtifactInput(path, "chart.svg", Producer("data-viz-v1a", "v", "j"))).artifact_id


def _flow(chart_id: str) -> dict[str, Any]:
    return {
        "schema_version": "hub-flow.v1", "flow_id": "launch-1", "project_id": "launch",
        "steps": [
            {"id": "card", "send": {"handoff": "E4-chart-to-graphics", "input": {"artifact": chart_id},
                                    "params": {"title": "t"}}},
        ],
    }


def _await_job(manager: JobManager, job_id: str) -> dict[str, Any]:
    for _ in range(200):
        job = manager.status(job_id)
        assert job is not None
        if job["status"] in ("completed", "failed"):
            return job
        time.sleep(0.05)
    pytest.fail("job did not settle")


def _wait_running(manager: JobManager, job_id: str) -> None:
    for _ in range(200):
        job = manager.status(job_id)
        assert job is not None
        if job["status"] == "running":
            return
        time.sleep(0.05)
    pytest.fail("job never started running")


def test_send_job_completes_with_outputs(env: tuple[Hub, JobManager], fake_adapters: None) -> None:
    hub, manager = env
    chart_id = _chart(hub)
    job = manager.submit_send({"artifact_id": chart_id, "to": "graphics-v1a", "params": {"title": "t"}})
    assert job == {"job_id": job["job_id"], "kind": "send", "status": "queued"}
    done = _await_job(manager, job["job_id"])
    assert done["status"] == "completed" and done["error"] is None
    outputs = done["result"]["outputs"]
    assert outputs and outputs[0]["kind"] == "graphic.png"
    assert any("completed" in line for line in done["log"])


def test_failed_send_job_records_the_error(tmp_path: Path, fake_adapters: None) -> None:
    hub = Hub(tmp_path / "home")
    chart_id = _chart(hub)

    def failing(argv: list[str], **_: Any) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(argv, 3, "", "boom")

    manager = JobManager(hub, runner=failing)
    try:
        job = manager.submit_send({"artifact_id": chart_id, "to": "graphics-v1a", "params": {"title": "t"}})
        done = _await_job(manager, job["job_id"])
    finally:
        manager.close()
    assert done["status"] == "failed" and "exit 3" in done["error"]


def test_external_workers_execute_real_sends_and_independent_flows(
    tmp_path: Path, fake_adapters: None,
) -> None:
    from concurrent.futures import ThreadPoolExecutor

    from production_hub.worker import Worker

    hub = Hub(tmp_path / "home")
    manager = JobManager(hub, mode="external")
    chart_id = _chart(hub)
    send_job = manager.submit_send({"artifact_id": chart_id, "to": "graphics-v1a", "params": {"title": "t"}})
    one = Worker(Hub(hub.home), worker_id="one", runner=_fake_run)
    two = Worker(Hub(hub.home), worker_id="two", runner=_fake_run)
    try:
        assert one.run_once()
        sent = manager.status(send_job["job_id"])
        assert sent is not None and sent["status"] == "completed" and sent["result"]["outputs"]
        first = _flow(chart_id)
        second = _flow(chart_id)
        second["flow_id"], second["project_id"] = "launch-2", "other"
        a = manager.submit_flow(load_flow(hub, first))
        b = manager.submit_flow(load_flow(hub, second))
        with ThreadPoolExecutor(max_workers=2) as pool:
            assert list(pool.map(lambda worker: worker.run_once(), [one, two])) == [True, True]
        left, right = manager.status(a["job_id"]), manager.status(b["job_id"])
        assert left is not None and right is not None
        assert left["status"] == right["status"] == "completed"
        assert left["result"]["flow_id"] == "launch-1" and right["result"]["flow_id"] == "launch-2"
        assert left["result"]["flow_status"] == right["result"]["flow_status"] == "completed"
    finally:
        one.close()
        two.close()
        manager.close()


def test_flow_job_runs_and_the_per_flow_lock_blocks_a_second_run(tmp_path: Path, fake_adapters: None) -> None:
    hub = Hub(tmp_path / "home")
    flow_id = load_flow(hub, _flow(_chart(hub)))
    release = threading.Event()

    def gated(argv: list[str], **_: Any) -> subprocess.CompletedProcess[str]:
        release.wait(10)
        return _fake_run(argv)

    manager = JobManager(hub, runner=gated)
    try:
        job = manager.submit_flow(flow_id)
        _wait_running(manager, job["job_id"])
        with pytest.raises(FlowBusy):
            manager.submit_flow(flow_id)
        release.set()
        done = _await_job(manager, job["job_id"])
        assert done["status"] == "completed" and done["result"]["flow_status"] == "completed"
        # the lock is released when the job settles, so the flow can run again
        again = manager.submit_flow(flow_id)
        assert _await_job(manager, again["job_id"])["status"] == "completed"
    finally:
        manager.close()


def test_stale_jobs_fail_on_startup(tmp_path: Path) -> None:
    hub = Hub(tmp_path / "home")
    first = JobManager(hub)
    first.close()
    with hub.db.connect() as conn:
        conn.executescript(JOBS_SCHEMA)
        conn.execute(
            "INSERT INTO jobs (job_id, kind, status, payload_json, created_at, updated_at)"
            " VALUES ('20260930T000000-00000000', 'send', 'queued', '{}', 't', 't')")
        conn.commit()
    fresh = JobManager(hub)
    try:
        job = fresh.status("20260930T000000-00000000")
    finally:
        fresh.close()
    assert job is not None and job["status"] == "failed" and "restarted" in job["error"]


def test_unknown_job_status_is_none(tmp_path: Path) -> None:
    manager = JobManager(Hub(tmp_path / "home"))
    try:
        assert manager.status("20260930T000000-00000000") is None
    finally:
        manager.close()


def test_visible_terminal_flow_can_resume_before_old_thread_returns(
    tmp_path: Path, fake_adapters: None, monkeypatch: pytest.MonkeyPatch,
) -> None:
    hub = Hub(tmp_path / "home")
    flow_id = load_flow(hub, _flow(_chart(hub)))
    manager = JobManager(hub, runner=_fake_run)
    terminal_written, return_from_update = threading.Event(), threading.Event()
    original = manager._update

    def update(job_id: str, **kwargs: Any) -> None:
        original(job_id, **kwargs)
        if kwargs.get("status") == "completed":
            terminal_written.set()
            return_from_update.wait(5)

    monkeypatch.setattr(manager, "_update", update)
    try:
        first = manager.submit_flow(flow_id)
        assert terminal_written.wait(5)
        assert manager.status(first["job_id"])["status"] == "completed"  # type: ignore[index]
        again = manager.submit_flow(flow_id)
        assert again["job_id"] != first["job_id"]
    finally:
        return_from_update.set()
        manager.close()


def test_inline_running_flow_reuses_matching_request_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = JobManager(Hub(tmp_path), mode="inline")
    started, finish = threading.Event(), threading.Event()

    def work(flow_id: str, log: Any) -> dict[str, Any]:
        started.set()
        finish.wait(5)
        return {"flow_id": flow_id, "flow_status": "completed"}

    monkeypatch.setattr(manager, "_flow_work", work)
    try:
        first = manager.submit_flow("flow", idempotency_key="inline-request")
        assert started.wait(5)
        duplicate = manager.submit_flow("flow", idempotency_key="inline-request")
        assert duplicate["job_id"] == first["job_id"] and duplicate["status"] == "running"
        with pytest.raises(FlowBusy):
            manager.submit_flow("flow", idempotency_key="different-request")
    finally:
        finish.set()
        manager.close()
