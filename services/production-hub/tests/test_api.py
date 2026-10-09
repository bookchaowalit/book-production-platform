from __future__ import annotations

import hashlib
import json
import subprocess
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Iterator
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

from production_hub import send as send_module
from production_hub.api import HubApi, make_handler
from production_hub.hub import ArtifactInput, Hub, Producer
from production_hub.send import SendAdapter, SendContext, ToolRun


def _api_build(ctx: SendContext) -> ToolRun:
    out = ctx.work_dir / "out"
    return ToolRun(argv=["fake", str(out)], cwd=ctx.work_dir, manifest_path=lambda: out / "manifest-x.json")


def _api_run(argv: list[str], **_: Any) -> subprocess.CompletedProcess[str]:
    out = Path(argv[1])
    out.mkdir(parents=True, exist_ok=True)
    png = b"\x89PNG api"
    (out / "card.png").write_bytes(png)
    (out / "manifest-x.json").write_text(json.dumps({
        "schema_version": "graphics-batch.v1", "batch_id": "b", "status": "accepted", "metadata": {},
            "items": [{"request_id": "card", "status": "accepted", "artifacts": [
            {"filename": "card.png", "media_type": "image/png", "content_hash": hashlib.sha256(png).hexdigest()}]}],
    }), encoding="utf-8")
    return subprocess.CompletedProcess(argv, 0, "", "")


@pytest.fixture
def server(tmp_path: Path) -> Iterator[tuple[str, Hub, str]]:
    hub = Hub(tmp_path / "home")
    path = tmp_path / "post.png"
    path.write_bytes(b"\x89PNG post")
    with hub.transaction() as conn:
        artifact_id = hub.register(conn, ArtifactInput(
            path, "graphic.png", Producer("graphics-v1a", "graphics.v1a", "b1"), project_id="pod-1",
        )).artifact_id
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(HubApi(hub), token=None))
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}", hub, artifact_id
    httpd.shutdown()
    httpd.server_close()


def _get(url: str) -> tuple[int, Any, str]:
    try:
        with urllib.request.urlopen(url) as response:
            body = response.read()
            kind = response.headers["Content-Type"]
            return response.status, json.loads(body) if "json" in kind else body, kind
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read()), exc.headers["Content-Type"]


def _post(url: str, body: dict[str, Any], *, header: bool = True) -> tuple[int, Any]:
    headers = {"Content-Type": "application/json"}
    if header:
        headers["X-Hub-Client"] = "console"
    request = urllib.request.Request(url, json.dumps(body).encode(), headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read())


def test_read_endpoints(server: tuple[str, Hub, str]) -> None:
    base, _, artifact_id = server
    assert _get(f"{base}/api/v1/health")[1]["status"] == "ok"
    status, listing, _ = _get(f"{base}/api/v1/artifacts?project=pod-1")
    assert status == 200 and listing["items"][0]["artifact_id"] == artifact_id
    status, record, _ = _get(f"{base}/api/v1/artifacts/{artifact_id}")
    assert record["kind"] == "graphic.png"
    assert {item["handoff_id"] for item in record["sends"]} == {"E3-graphic-to-video", "E7b-graphic-to-web"}
    sends = {item["handoff_id"]: item for item in record["sends"]}
    assert sends["E3-graphic-to-video"]["optional_params"]["position"]["default"] == "bottom-right"
    assert sends["E7b-graphic-to-web"]["optional_params"]["language"]["default"] == "th"
    assert _get(f"{base}/api/v1/artifacts/{artifact_id}/lineage")[1]["inputs"] == []
    status, content, kind = _get(f"{base}/api/v1/artifacts/{artifact_id}/content")
    assert (status, content, kind) == (200, b"\x89PNG post", "image/png")
    assert _get(f"{base}/api/v1/artifacts/sha256:{'0' * 64}")[0] == 404
    assert len(_get(f"{base}/api/v1/registry")[1]["handoffs"]) >= 20


def test_project_review_and_package_flow(server: tuple[str, Hub, str]) -> None:
    base, _, artifact_id = server
    assert _post(f"{base}/api/v1/projects", {"project_id": "pod-1", "name": "POD"})[0] == 200
    status, detail, _ = _get(f"{base}/api/v1/projects/pod-1")
    assert status == 200 and detail["artifacts"][0]["review_status"] == "draft"
    assert _post(f"{base}/api/v1/projects/pod-1/review", {"artifact_id": artifact_id, "status": "approved"})[0] == 200
    status, package = _post(f"{base}/api/v1/projects/pod-1/package", {})
    assert status == 200 and package["files"] == 1
    assert Path(package["path"]).is_file()
    status, error = _post(f"{base}/api/v1/projects/pod-1/review", {"artifact_id": artifact_id, "status": "draft"})
    assert status == 422 and error["error"]["code"] == "workspace_error"


def test_mutations_need_console_header_and_valid_json(server: tuple[str, Hub, str]) -> None:
    base, _, _ = server
    assert _post(f"{base}/api/v1/projects", {"project_id": "x"}, header=False)[0] == 403
    request = urllib.request.Request(
        f"{base}/api/v1/projects", b"[1]", headers={"X-Hub-Client": "console"}, method="POST"
    )
    with pytest.raises(urllib.error.HTTPError) as caught:
        urllib.request.urlopen(request)
    assert caught.value.code == 400


def test_send_errors_are_reported(server: tuple[str, Hub, str]) -> None:
    base, _, artifact_id = server
    status, error = _post(f"{base}/api/v1/send", {"artifact_id": artifact_id, "to": "game-v1a"})
    assert status == 422 and "no send adapter" in error["error"]["message"]


def test_external_http_submission_is_accepted_persisted_and_conflicts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PRODUCTION_HUB_JOB_MODE", "external")
    saved = dict(send_module.SEND_ADAPTERS)
    send_module.register_adapter(SendAdapter("E4-chart-to-graphics", _api_build,
                                            required_params=frozenset({"title"})))
    hub = Hub(tmp_path)
    chart = tmp_path / "chart.svg"
    chart.write_bytes(b"<svg>queue</svg>")
    with hub.transaction() as conn:
        artifact = hub.register(conn, ArtifactInput(chart, "chart.svg", Producer("data-viz-v1a", "v", "j")))
    api = HubApi(hub, runner=_api_run)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(api, token=None))
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    body = {"artifact_id": artifact.artifact_id, "to": "graphics-v1a", "params": {"title": "t"},
            "idempotency_key": "api-request"}
    try:
        status, job = _post(f"{base}/api/v1/send", body)
        assert status == 202
        assert _get(f"{base}/api/v1/jobs/{job['job_id']}")[1]["status"] == "queued"
        assert _post(f"{base}/api/v1/send", body)[1]["job_id"] == job["job_id"]
        conflict, error = _post(f"{base}/api/v1/send", {**body, "params": {"title": "other"}})
        assert conflict == 409 and error["error"]["code"] == "idempotency_conflict"
        assert _post(f"{base}/api/v1/jobs/{job['job_id']}/cancel", {})[0] == 200
        assert _get(f"{base}/api/v1/jobs/{job['job_id']}")[1]["cancel_requested"] == 1
        assert _post(f"{base}/api/v1/jobs/{job['job_id']}/cancel", {})[0] == 409
        api.close()
        fresh = HubApi(Hub(tmp_path))
        assert fresh.jobs.status(job["job_id"])["attempts"] == 0  # type: ignore[index]
        fresh.close()
    finally:
        httpd.shutdown()
        httpd.server_close()
        api.close()
        send_module.SEND_ADAPTERS.clear()
        send_module.SEND_ADAPTERS.update(saved)


def _template_request() -> dict[str, Any]:
    return {"project_id": "promo", "inputs": {"captions": "sha256:" + "a" * 64},
            "params": {"campaign_name": "campaign", "headline": "headline", "title": "landing",
                       "cta_label": "more", "features": "fast:quick"}, "idempotency_key": "template-request"}


def test_external_template_deduplicates_before_flow_allocation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from production_hub import api as api_module
    from production_hub.api import ApiError

    monkeypatch.setenv("PRODUCTION_HUB_JOB_MODE", "external")
    api = HubApi(Hub(tmp_path))
    body = _template_request()
    first = api.post("/api/v1/flow-templates/promote-product", body)

    def should_not_build(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("retry allocated another flow")

    monkeypatch.setattr(api_module, "build_template", should_not_build)
    assert api.post("/api/v1/flow-templates/promote-product", body) == first
    with pytest.raises(ApiError) as conflict:
        api.post("/api/v1/flow-templates/promote-product", {**body, "project_id": "other"})
    assert conflict.value.status == 409 and conflict.value.code == "idempotency_conflict"
    with api.hub.db.connect() as conn:
        assert conn.execute("SELECT count(*) FROM flows").fetchone()[0] == 1
        assert conn.execute("SELECT count(*) FROM jobs").fetchone()[0] == 1
        assert conn.execute("SELECT count(*) FROM projects").fetchone()[0] == 1
    api.close()


def test_external_template_concurrent_requests_create_one_flow_and_job(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from concurrent.futures import ThreadPoolExecutor

    monkeypatch.setenv("PRODUCTION_HUB_JOB_MODE", "external")
    first, second = HubApi(Hub(tmp_path)), HubApi(Hub(tmp_path))
    gate = threading.Barrier(2)

    def submit(api: HubApi) -> Any:
        gate.wait()
        return api.post("/api/v1/flow-templates/promote-product", _template_request())

    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(submit, [first, second]))
    assert responses[0] == responses[1]
    with first.hub.db.connect() as conn:
        assert conn.execute("SELECT count(*) FROM flows").fetchone()[0] == 1
        assert conn.execute("SELECT count(*) FROM jobs").fetchone()[0] == 1
    first.close()
    second.close()


def test_external_template_job_insert_failure_rolls_back_flow_and_project(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import sqlite3

    monkeypatch.setenv("PRODUCTION_HUB_JOB_MODE", "external")
    api = HubApi(Hub(tmp_path))
    with api.hub.db.connect() as conn:
        conn.execute("""CREATE TRIGGER reject_job BEFORE INSERT ON jobs
                        BEGIN SELECT RAISE(ABORT, 'test insert fault'); END""")
        conn.commit()
    with pytest.raises(sqlite3.IntegrityError):
        api.post("/api/v1/flow-templates/promote-product", _template_request())
    with api.hub.db.connect() as conn:
        assert conn.execute("SELECT count(*) FROM flows").fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM projects").fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM jobs").fetchone()[0] == 0
    api.close()


def test_external_template_processes_share_one_request(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import subprocess
    import sys

    monkeypatch.setenv("PRODUCTION_HUB_JOB_MODE", "external")
    api = HubApi(Hub(tmp_path))
    program = (
        "import json,sys; from pathlib import Path; from production_hub.api import HubApi; "
        "from production_hub.hub import Hub; api=HubApi(Hub(Path(sys.argv[1]))); "
        "print(json.dumps(api.post('/api/v1/flow-templates/promote-product',json.loads(sys.argv[2])))); "
        "api.close()"
    )
    children = [subprocess.Popen([sys.executable, "-c", program, str(tmp_path), json.dumps(_template_request())],
                                 text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE) for _ in range(2)]
    outputs = [child.communicate(timeout=20) for child in children]
    assert all(child.returncode == 0 for child in children), outputs
    assert json.loads(outputs[0][0]) == json.loads(outputs[1][0])
    with api.hub.db.connect() as conn:
        assert conn.execute("SELECT count(*) FROM flows").fetchone()[0] == 1
        assert conn.execute("SELECT count(*) FROM jobs").fetchone()[0] == 1
        assert conn.execute("SELECT count(*) FROM projects").fetchone()[0] == 1
    api.close()


def test_external_template_audit_failure_rolls_back_entire_owner_transaction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PRODUCTION_HUB_JOB_MODE", "external")
    api = HubApi(Hub(tmp_path))

    def fail_after_insert(conn: Any, job_id: str, event: str, actor: str,
                          evidence_ref: str | None = None) -> None:
        assert conn.in_transaction
        assert conn.execute("SELECT count(*) FROM projects").fetchone()[0] == 1
        assert conn.execute("SELECT count(*) FROM flows").fetchone()[0] == 1
        assert conn.execute("SELECT count(*) FROM jobs").fetchone()[0] == 1
        raise RuntimeError("mock owner transaction aborted")

    monkeypatch.setattr(api.jobs.queue, "_event", fail_after_insert)
    with pytest.raises(RuntimeError, match="mock owner transaction aborted"):
        api.post("/api/v1/flow-templates/promote-product", _template_request())
    with api.hub.db.connect() as conn:
        assert conn.execute("SELECT count(*) FROM projects").fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM flows").fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM jobs").fetchone()[0] == 0
    api.close()


def test_inline_template_rejects_request_key_before_flow_allocation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from production_hub import api as api_module
    from production_hub.api import ApiError

    monkeypatch.setenv("PRODUCTION_HUB_JOB_MODE", "inline")
    api = HubApi(Hub(tmp_path))

    def should_not_build(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("unsupported inline request allocated a flow")

    monkeypatch.setattr(api_module, "build_template", should_not_build)
    with pytest.raises(ApiError) as unsupported:
        api.post("/api/v1/flow-templates/promote-product", _template_request())
    assert unsupported.value.status == 400 and unsupported.value.code == "unsupported_idempotency"
    with api.hub.db.connect() as conn:
        assert conn.execute("SELECT count(*) FROM jobs").fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM sqlite_master WHERE type='table' "
                            "AND name IN ('flows','projects')").fetchone()[0] == 0
    api.close()


def test_token_is_enforced_when_configured(tmp_path: Path) -> None:
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(HubApi(Hub(tmp_path / "h")), token="s3cret"))
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        base = f"http://127.0.0.1:{httpd.server_address[1]}"
        assert _post(f"{base}/api/v1/projects", {"project_id": "x"})[0] == 401
    finally:
        httpd.shutdown()
        httpd.server_close()


def _serve_with_fake_adapter(hub: Hub, runner: Any) -> tuple[str, Callable[[], None]]:
    saved = dict(send_module.SEND_ADAPTERS)
    send_module.register_adapter(SendAdapter("E4-chart-to-graphics", _api_build, required_params=frozenset({"title"})))
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(HubApi(hub, runner=runner), token=None))
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()

    def stop() -> None:
        httpd.shutdown()
        httpd.server_close()
        send_module.SEND_ADAPTERS.clear()
        send_module.SEND_ADAPTERS.update(saved)

    return f"http://127.0.0.1:{httpd.server_address[1]}", stop


def _await_job(base: str, job_id: str) -> dict[str, Any]:
    job: dict[str, Any] = {}
    for _ in range(200):
        code, job, _ = _get(f"{base}/api/v1/jobs/{job_id}")
        assert code == 200
        if job["status"] in ("completed", "failed"):
            return job
        time.sleep(0.05)
    pytest.fail("job did not settle")


def test_async_send_returns_a_job_and_completes(tmp_path: Path) -> None:
    hub = Hub(tmp_path / "home")
    path = tmp_path / "chart.svg"
    path.write_bytes(b"<svg>api</svg>")
    with hub.transaction() as conn:
        artifact_id = hub.register(conn, ArtifactInput(path, "chart.svg", Producer("data-viz-v1a", "v", "j"))).artifact_id
    base, stop = _serve_with_fake_adapter(hub, _api_run)
    try:
        status, start = _post(f"{base}/api/v1/send", {"artifact_id": artifact_id, "to": "graphics-v1a",
                                                      "params": {"title": "t"}})
        assert status == 200 and start["status"] == "queued" and start["kind"] == "send"
        job = _await_job(base, start["job_id"])
        assert job["status"] == "completed" and job["result"]["outputs"][0]["kind"] == "graphic.png"
        assert any("completed" in line for line in job["log"])
        assert _get(f"{base}/api/v1/jobs/20260930T000000-00000000")[0] == 404
    finally:
        stop()


def test_flow_runs_as_a_job_and_a_concurrent_run_is_conflicted(tmp_path: Path) -> None:
    hub = Hub(tmp_path / "home")
    path = tmp_path / "chart.svg"
    path.write_bytes(b"<svg>api-flow</svg>")
    with hub.transaction() as conn:
        artifact_id = hub.register(conn, ArtifactInput(path, "chart.svg", Producer("data-viz-v1a", "v", "j"))).artifact_id
    release = threading.Event()

    def gated(argv: list[str], **_: Any) -> subprocess.CompletedProcess[str]:
        release.wait(10)
        return _api_run(argv)

    saved = dict(send_module.SEND_ADAPTERS)
    send_module.register_adapter(SendAdapter("E4-chart-to-graphics", _api_build, required_params=frozenset({"title"})))
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(HubApi(hub, runner=gated), token=None))
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        base = f"http://127.0.0.1:{httpd.server_address[1]}"
        definition = {
            "schema_version": "hub-flow.v1", "flow_id": "launch-1", "project_id": "launch",
            "steps": [{"id": "card", "send": {"handoff": "E4-chart-to-graphics",
                                              "input": {"artifact": artifact_id}, "params": {"title": "t"}}}],
        }
        status, start = _post(f"{base}/api/v1/flows", definition)
        assert status == 200 and start["flow_id"] == "launch-1" and start["status"] == "queued"
        conflict = _post(f"{base}/api/v1/flows/launch-1/resume", {})
        assert conflict[0] == 409 and conflict[1]["error"]["code"] == "flow_busy"
        release.set()
        job = _await_job(base, start["job_id"])
        assert job["status"] == "completed" and job["result"]["flow_status"] == "completed"
        _, flow, _ = _get(f"{base}/api/v1/flows/launch-1")
        assert flow["status"] == "completed"
    finally:
        httpd.shutdown()
        httpd.server_close()
        send_module.SEND_ADAPTERS.clear()
        send_module.SEND_ADAPTERS.update(saved)


def test_resume_of_unknown_flow_stays_422(server: tuple[str, Hub, str]) -> None:
    base, _, _ = server
    status, error = _post(f"{base}/api/v1/flows/nope/resume", {})
    assert status == 422 and "unknown flow" in error["error"]["message"]
