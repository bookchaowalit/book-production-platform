from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from workflow_production.jobs import (
    IdempotencyConflict,
    ReplayIntegrityError,
    run_workflow_job,
)


def _workflow() -> dict:
    return {
        "workflow_id": "job-01",
        "name": "Onboarding",
        "fields": [{"field_id": "customer_name", "label": "Customer", "field_type": "text", "required": True}],
        "form_values": {"customer_name": "Ada"},
        "steps": [
            {"step_id": "collect", "kind": "collect_form"},
            {"step_id": "render", "kind": "render_document", "depends_on": ["collect"]},
        ],
        "document": {"title": "Brief", "sections": [{"kind": "field", "field_id": "customer_name"}]},
        "permissions": {"allowed_roles": ["ops"]},
    }


def test_job_writes_plan_document_manifest_and_zero_model_calls(tmp_path: Path) -> None:
    result = run_workflow_job(_workflow(), tmp_path / "out", "key-1")

    assert result.status == "accepted"
    assert result.replayed is False
    assert result.model_calls == 0
    assert {artifact.media_type for artifact in result.artifacts} == {"application/json", "text/markdown", "application/xml"}
    assert (tmp_path / "out" / result.manifest_filename).exists()


def test_replay_returns_same_artifacts_without_writes(tmp_path: Path) -> None:
    first = run_workflow_job(_workflow(), tmp_path / "out", "key-1")

    replay = run_workflow_job(_workflow(), tmp_path / "out", "key-1")

    assert replay.replayed is True
    assert replay.artifacts == first.artifacts
    assert replay.bytes_written == 0


def test_changed_input_conflicts(tmp_path: Path) -> None:
    run_workflow_job(_workflow(), tmp_path / "out", "key-1")
    changed = {**_workflow(), "name": "Changed"}

    with pytest.raises(IdempotencyConflict):
        run_workflow_job(changed, tmp_path / "out", "key-1")


def test_tampered_document_is_rejected_on_replay(tmp_path: Path) -> None:
    first = run_workflow_job(_workflow(), tmp_path / "out", "key-1")
    document = next(artifact for artifact in first.artifacts if artifact.media_type == "text/markdown")
    document_path = tmp_path / "out" / document.filename
    document_path.write_text(document_path.read_text(encoding="utf-8") + "tampered\n", encoding="utf-8")

    with pytest.raises(ReplayIntegrityError):
        run_workflow_job(_workflow(), tmp_path / "out", "key-1")


def test_tampered_manifest_is_rejected_on_replay(tmp_path: Path) -> None:
    first = run_workflow_job(_workflow(), tmp_path / "out", "key-1")
    manifest_path = tmp_path / "out" / str(first.manifest_filename)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["status"] = "rejected"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(ReplayIntegrityError):
        run_workflow_job(_workflow(), tmp_path / "out", "key-1")


def test_tampered_manifest_telemetry_is_rejected_on_replay(tmp_path: Path) -> None:
    first = run_workflow_job(_workflow(), tmp_path / "out", "key-1")
    manifest_path = tmp_path / "out" / str(first.manifest_filename)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["telemetry"]["bytes_written"] = 0
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(ReplayIntegrityError):
        run_workflow_job(_workflow(), tmp_path / "out", "key-1")


def test_duplicate_threads_share_one_package(tmp_path: Path) -> None:
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: run_workflow_job(_workflow(), tmp_path / "out", "key-1"), range(2)))

    assert {result.status for result in results} == {"accepted"}
    assert sum(result.replayed for result in results) == 1
