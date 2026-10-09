import hashlib
import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from document_production.job_store import JobStore

REPO_ROOT = Path(__file__).resolve().parents[3]
SERVICE_ROOT = REPO_ROOT / "services" / "document-production-api"
IR_ROOT = REPO_ROOT / "packages" / "document-production-ir"

CLAIM_WORKER = """
import hashlib
import json
import sys

from document_production.job_store import JobStore

root, key, job_id, request_hash, mode = sys.argv[1:6]
store = JobStore(root)
claim = store.claim(
    key_hash=hashlib.sha256(key.encode("utf-8")).hexdigest(),
    request_hash=request_hash,
    job_id=job_id,
    recipe_version="invoice-v1",
    schema_version="document-request.v1",
    engine_version="document-engine.v1",
)
if mode == "publish" and claim.action == "run":
    manifest = store.root / "jobs" / claim.job_id / "manifest.json"
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(json.dumps({"job_id": claim.job_id, "artifacts": []}), encoding="utf-8")
    store.finish(claim.job_id, state="accepted", manifest_path=manifest.relative_to(store.root).as_posix())
print(json.dumps({"action": claim.action, "job_id": claim.job_id}))
"""


def claim(store: JobStore, key: str, request_hash: str, job_id: str, *, lease_seconds: float = 300.0):
    return store.claim(
        key_hash=hashlib.sha256(key.encode("utf-8")).hexdigest(),
        request_hash=request_hash,
        job_id=job_id,
        recipe_version="invoice-v1",
        schema_version="document-request.v1",
        engine_version="document-engine.v1",
        lease_seconds=lease_seconds,
    )


def _spawn_claim(root, key: str, job_id: str, mode: str):
    env = dict(os.environ)
    parts = [str(SERVICE_ROOT), str(IR_ROOT)]
    if env.get("PYTHONPATH"):
        parts.append(env["PYTHONPATH"])
    env["PYTHONPATH"] = os.pathsep.join(parts)
    return subprocess.Popen(
        [sys.executable, "-c", CLAIM_WORKER, str(root), key, job_id, "request", mode],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
    )


def test_claim_is_unique_and_accepted_jobs_replay(tmp_path):
    store = JobStore(tmp_path)
    first = claim(store, "same", "request", "job-1")
    assert first.action == "run"

    second = claim(store, "same", "request", "job-2")
    assert second.action == "running"
    manifest = tmp_path / "jobs" / "job-1" / "manifest.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(json.dumps({"job_id": "job-1", "artifacts": []}), encoding="utf-8")
    store.finish("job-1", state="accepted", manifest_path="jobs/job-1/manifest.json")

    replay = claim(store, "same", "request", "job-3")
    assert replay.action == "replay"
    assert replay.job_id == "job-1"
    assert replay.manifest_path == "jobs/job-1/manifest.json"


def test_legacy_json_index_is_migrated(tmp_path):
    manifest = tmp_path / "jobs" / "legacy-job" / "manifest.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(json.dumps({"job_id": "legacy-job", "artifacts": []}), encoding="utf-8")
    legacy_key_hash = hashlib.sha256(b"legacy-key").hexdigest()
    (tmp_path / "idempotency.json").write_text(
        json.dumps({legacy_key_hash: {"request_hash": "request", "manifest_path": "jobs/legacy-job/manifest.json"}}),
        encoding="utf-8",
    )

    migrated = claim(JobStore(tmp_path), "legacy-key", "request", "new-job")
    assert migrated.action == "replay"
    assert migrated.job_id == "legacy-job"


def test_stale_running_lease_can_be_claimed_again(tmp_path):
    store = JobStore(tmp_path)
    first = claim(store, "stale", "request", "job-1", lease_seconds=0)
    assert first.action == "run"
    second = claim(store, "stale", "request", "job-2")
    assert second.action == "run"
    assert second.job_id == "job-2"
    try:
        store.finish("job-1", state="accepted", manifest_path="jobs/job-1/manifest.json")
    except KeyError:
        pass
    else:
        raise AssertionError("stale claim was allowed to finish")
    store.finish("job-2", state="accepted", manifest_path="jobs/job-2/manifest.json")


def test_accepted_job_with_missing_manifest_is_rebuilt(tmp_path):
    store = JobStore(tmp_path)
    claim(store, "missing", "request", "job-1")
    store.finish("job-1", state="accepted", manifest_path="jobs/job-1/manifest.json")
    rebuilt = claim(store, "missing", "request", "job-2")
    assert rebuilt.action == "run"
    assert rebuilt.job_id == "job-1"


def test_concurrent_claims_have_one_runner(tmp_path):
    def run(index: int):
        return claim(JobStore(tmp_path), "concurrent", "request", f"job-{index}").action

    with ThreadPoolExecutor(max_workers=2) as executor:
        actions = list(executor.map(run, range(2)))
    assert sorted(actions) == ["run", "running"]


def test_separate_processes_claiming_same_key_elect_one_runner(tmp_path):
    processes = [_spawn_claim(tmp_path, "same-key", f"proc-job-{index}", "claim") for index in range(2)]
    payloads = []
    for process in processes:
        stdout, stderr = process.communicate(timeout=120)
        assert process.returncode == 0, stderr
        payloads.append(json.loads(stdout))

    actions = sorted(payload["action"] for payload in payloads)
    assert actions == ["run", "running"]
    resumed = claim(JobStore(tmp_path), "same-key", "request", "parent-job")
    assert resumed.action == "running"


def test_separate_processes_claiming_different_keys_publish_independently(tmp_path):
    processes = [
        _spawn_claim(tmp_path, "key-a", "job-a", "publish"),
        _spawn_claim(tmp_path, "key-b", "job-b", "publish"),
    ]
    payloads = []
    for process in processes:
        stdout, stderr = process.communicate(timeout=120)
        assert process.returncode == 0, stderr
        payloads.append(json.loads(stdout))

    assert sorted(payload["action"] for payload in payloads) == ["run", "run"]
    assert payloads[0]["job_id"] != payloads[1]["job_id"]
    store = JobStore(tmp_path)
    for key, job_id in (("key-a", "job-a"), ("key-b", "job-b")):
        assert (tmp_path / "jobs" / job_id / "manifest.json").is_file()
        replay = claim(store, key, "request", "parent-job")
        assert replay.action == "replay"
        assert replay.job_id == job_id
