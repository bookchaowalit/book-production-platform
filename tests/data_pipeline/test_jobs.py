from __future__ import annotations

import hashlib
import json
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import pytest

import book_production_platform.data_pipeline.jobs as jobs_module
from book_production_platform.data_pipeline.jobs import (
    PipelineJobError,
    run_pipeline_job,
)
from book_production_platform.data_pipeline.pipeline import (
    compile_pipeline_ir,
    explain_pipeline_sql,
    parameter_document,
)
from book_production_platform.data_pipeline.qa import (
    build_pipeline_report,
    qa_pipeline_artifacts,
)
from book_production_platform.data_pipeline_ir.validation import build_pipeline_ir


def test_pipeline_job_writes_private_artifacts_and_replays_without_new_bytes(
    tmp_path: Path,
    valid_pipeline_request: dict[str, Any],
) -> None:
    first = run_pipeline_job(valid_pipeline_request, tmp_path)
    second = run_pipeline_job(valid_pipeline_request, tmp_path)

    assert first.status == second.status == "accepted"
    assert first.fingerprint == second.fingerprint
    assert len(first.fingerprint) == 64
    assert second.replay is True and second.bytes_written == 0
    assert first.replay is False and first.bytes_written > 0
    assert first.model_calls == second.model_calls == 0
    assert first.metadata == {"execution_mode": "plan_only", "query_plan_verified": True}
    assert set(first.artifact_hashes) == {"pipeline.sql", "parameters.json", "report.json", "qa-manifest.json"}
    assert first.artifact_dir is not None
    assert {path.name for path in first.artifact_dir.iterdir()} == set(first.artifact_hashes)

    sql = (first.artifact_dir / "pipeline.sql").read_text(encoding="utf-8")
    parameters = json.loads((first.artifact_dir / "parameters.json").read_text(encoding="utf-8"))
    report_text = (first.artifact_dir / "report.json").read_text(encoding="utf-8")
    report = json.loads(report_text)
    manifest_text = (first.artifact_dir / "qa-manifest.json").read_text(encoding="utf-8")
    manifest = json.loads(manifest_text)
    receipt_path = tmp_path / ".receipts" / f"{first.fingerprint}.json"
    receipt_text = receipt_path.read_text(encoding="utf-8")
    receipt = json.loads(receipt_text)

    assert "BKK" not in sql and sql.count("?") == 1
    assert parameters == {
        "schema_version": "pipeline-parameters.v1",
        "parameters": [{"type": "text", "value": "BKK"}],
    }
    assert report["schema_version"] == "pipeline-report.v1"
    assert report["execution_mode"] == "plan_only"
    assert report["model_calls"] == 0
    assert report["query_plan"] and report["sqlite_version"]
    assert report["source_ref"] == "warehouse.orders"
    assert report["output_lineage"]["region"][0]["source_ref"] == "warehouse.orders"
    assert "BKK" not in report_text
    assert manifest["schema_version"] == "pipeline-qa-manifest.v1"
    assert manifest["execution_mode"] == "plan_only"
    assert manifest["model_calls"] == 0 and manifest["query_plan_verified"] is True
    assert set(manifest["artifact_hashes"]) == {"pipeline.sql", "parameters.json", "report.json"}
    assert "BKK" not in manifest_text and "warehouse.orders" not in manifest_text
    assert receipt["schema_version"] == "data-pipeline.receipt.v1"
    assert set(receipt["hashes"]) == set(first.artifact_hashes)
    assert "BKK" not in receipt_text and "warehouse.orders" not in receipt_text

    for name, expected_hash in first.artifact_hashes.items():
        data = (first.artifact_dir / name).read_bytes()
        assert hashlib.sha256(data).hexdigest() == expected_hash == receipt["hashes"][name]
    response_text = json.dumps(first.to_dict(), ensure_ascii=False)
    assert "BKK" not in response_text and "warehouse.orders" not in response_text
    assert "request" not in first.to_dict() and "source_refs" not in first.to_dict()


def test_changed_request_gets_a_distinct_fingerprint_and_artifact_directory(
    tmp_path: Path,
    valid_pipeline_request: dict[str, Any],
) -> None:
    changed = json.loads(json.dumps(valid_pipeline_request))
    changed["nodes"][0]["value"] = "CNX"

    first = run_pipeline_job(valid_pipeline_request, tmp_path)
    second = run_pipeline_job(changed, tmp_path)

    assert first.fingerprint != second.fingerprint
    assert first.artifact_dir != second.artifact_dir
    assert first.artifact_dir is not None and second.artifact_dir is not None
    assert first.artifact_dir.is_dir() and second.artifact_dir.is_dir()


def test_tampered_artifact_fails_closed_without_overwriting_it(
    tmp_path: Path,
    valid_pipeline_request: dict[str, Any],
) -> None:
    first = run_pipeline_job(valid_pipeline_request, tmp_path)
    assert first.artifact_dir is not None
    tampered = first.artifact_dir / "parameters.json"
    tampered.write_text("{\"modified\":true}", encoding="utf-8")

    with pytest.raises(PipelineJobError, match="replay_integrity_error"):
        run_pipeline_job(valid_pipeline_request, tmp_path)

    assert tampered.read_text(encoding="utf-8") == "{\"modified\":true}"


def test_tampered_receipt_fails_closed(tmp_path: Path, valid_pipeline_request: dict[str, Any]) -> None:
    first = run_pipeline_job(valid_pipeline_request, tmp_path)
    receipt = tmp_path / ".receipts" / f"{first.fingerprint}.json"
    receipt.write_text("{}", encoding="utf-8")

    with pytest.raises(PipelineJobError, match="replay_integrity_error"):
        run_pipeline_job(valid_pipeline_request, tmp_path)


def test_missing_receipt_for_intact_artifacts_is_recovered(
    tmp_path: Path,
    valid_pipeline_request: dict[str, Any],
) -> None:
    first = run_pipeline_job(valid_pipeline_request, tmp_path)
    receipt = tmp_path / ".receipts" / f"{first.fingerprint}.json"
    original = receipt.read_bytes()
    receipt.unlink()

    recovered = run_pipeline_job(valid_pipeline_request, tmp_path)

    assert recovered.status == "accepted" and recovered.replay is True
    assert recovered.artifact_hashes == first.artifact_hashes
    assert recovered.metadata["receipt_recovered"] is True
    assert recovered.bytes_written == len(original)
    assert receipt.read_bytes() == original
    replay = run_pipeline_job(valid_pipeline_request, tmp_path)
    assert replay.replay is True and replay.bytes_written == 0
    assert "receipt_recovered" not in replay.metadata


def test_missing_receipt_for_tampered_artifacts_fails_closed(
    tmp_path: Path,
    valid_pipeline_request: dict[str, Any],
) -> None:
    first = run_pipeline_job(valid_pipeline_request, tmp_path)
    assert first.artifact_dir is not None
    receipt = tmp_path / ".receipts" / f"{first.fingerprint}.json"
    receipt.unlink()
    tampered = first.artifact_dir / "pipeline.sql"
    tampered.write_text("SELECT 1", encoding="utf-8")

    with pytest.raises(PipelineJobError, match="replay_integrity_error"):
        run_pipeline_job(valid_pipeline_request, tmp_path)

    assert not receipt.exists()
    assert tampered.read_text(encoding="utf-8") == "SELECT 1"
    assert not any(path.name.startswith(".") for path in (tmp_path / "jobs").iterdir())


def test_missing_receipt_with_incomplete_artifacts_fails_closed(
    tmp_path: Path,
    valid_pipeline_request: dict[str, Any],
) -> None:
    first = run_pipeline_job(valid_pipeline_request, tmp_path)
    assert first.artifact_dir is not None
    (tmp_path / ".receipts" / f"{first.fingerprint}.json").unlink()
    (first.artifact_dir / "report.json").unlink()

    with pytest.raises(PipelineJobError, match="replay_integrity_error"):
        run_pipeline_job(valid_pipeline_request, tmp_path)


def test_crash_between_publish_and_receipt_is_recovered_on_retry(
    tmp_path: Path,
    valid_pipeline_request: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class SimulatedCrash(BaseException):
        pass

    def crash(*_args: object, **_kwargs: object) -> int:
        raise SimulatedCrash

    with monkeypatch.context() as patched:
        patched.setattr(jobs_module, "_write_receipt_exclusive", crash)
        with pytest.raises(SimulatedCrash):
            run_pipeline_job(valid_pipeline_request, tmp_path)

    published = [path for path in (tmp_path / "jobs").iterdir() if not path.name.startswith(".")]
    assert len(published) == 1
    receipts_dir = tmp_path / ".receipts"
    assert not receipts_dir.exists() or not any(receipts_dir.iterdir())

    recovered = run_pipeline_job(valid_pipeline_request, tmp_path)
    assert recovered.status == "accepted" and recovered.metadata["receipt_recovered"] is True
    assert recovered.artifact_dir == published[0]


def test_crash_while_linking_receipt_leaves_no_partial_receipt(
    tmp_path: Path,
    valid_pipeline_request: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class SimulatedCrash(BaseException):
        pass

    def crash(*_args: object, **_kwargs: object) -> None:
        raise SimulatedCrash

    with monkeypatch.context() as patched:
        patched.setattr(jobs_module.os, "link", crash)
        with pytest.raises(SimulatedCrash):
            run_pipeline_job(valid_pipeline_request, tmp_path)

    receipts_dir = tmp_path / ".receipts"
    assert not any(receipts_dir.iterdir())

    recovered = run_pipeline_job(valid_pipeline_request, tmp_path)
    assert recovered.metadata["receipt_recovered"] is True


def test_receipt_falls_back_to_replace_without_hard_links(
    tmp_path: Path,
    valid_pipeline_request: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def no_links(*_args: object, **_kwargs: object) -> None:
        raise OSError(1, "hard links not supported")

    monkeypatch.setattr(jobs_module.os, "link", no_links)

    first = run_pipeline_job(valid_pipeline_request, tmp_path)
    second = run_pipeline_job(valid_pipeline_request, tmp_path)

    assert first.status == "accepted" and second.replay is True
    assert [path.name for path in (tmp_path / ".receipts").iterdir()] == [f"{first.fingerprint}.json"]


def test_abandoned_staging_dirs_are_removed_on_next_run(
    tmp_path: Path,
    valid_pipeline_request: dict[str, Any],
) -> None:
    first = run_pipeline_job(valid_pipeline_request, tmp_path)
    jobs_dir = tmp_path / "jobs"
    stale = jobs_dir / f".{first.fingerprint}.staging-crashed"
    stale.mkdir()
    (stale / "pipeline.sql").write_text("partial", encoding="utf-8")
    other = jobs_dir / f".{'0' * 64}.staging-other"
    other.mkdir()

    replay = run_pipeline_job(valid_pipeline_request, tmp_path)

    assert replay.replay is True
    assert not stale.exists()
    assert other.exists()


def test_invalid_pipeline_returns_a_stable_rejection_without_artifacts(
    tmp_path: Path,
    valid_pipeline_request: dict[str, Any],
) -> None:
    invalid = json.loads(json.dumps(valid_pipeline_request))
    invalid["nodes"][0]["column"] = "missing"

    result = run_pipeline_job(invalid, tmp_path)

    assert result.status == "rejected"
    assert result.error_code == "unknown_pipeline_column"
    assert result.artifact_dir is None
    assert result.bytes_written == 0
    assert not (tmp_path / "jobs").exists()


def test_content_qa_rejects_tampered_sql_parameters_and_report(
    tmp_path: Path,
    valid_pipeline_request: dict[str, Any],
) -> None:
    pipeline = build_pipeline_ir(valid_pipeline_request)
    compiled = compile_pipeline_ir(pipeline)
    query_plan = explain_pipeline_sql(pipeline, compiled)
    (tmp_path / "pipeline.sql").write_text(compiled.sql, encoding="utf-8")
    (tmp_path / "parameters.json").write_text(json.dumps(parameter_document(compiled)), encoding="utf-8")
    (tmp_path / "report.json").write_text(
        json.dumps(build_pipeline_report(pipeline, compiled, query_plan.steps, query_plan.sqlite_version)),
        encoding="utf-8",
    )

    assert qa_pipeline_artifacts(pipeline, compiled, query_plan, tmp_path) == ()

    (tmp_path / "pipeline.sql").write_text("DELETE FROM orders", encoding="utf-8")
    assert "pipeline_sql_mismatch" in qa_pipeline_artifacts(pipeline, compiled, query_plan, tmp_path)
    (tmp_path / "pipeline.sql").write_text(compiled.sql, encoding="utf-8")
    (tmp_path / "parameters.json").write_text("{}", encoding="utf-8")
    assert "parameter_document_mismatch" in qa_pipeline_artifacts(pipeline, compiled, query_plan, tmp_path)
    (tmp_path / "parameters.json").write_text(json.dumps(parameter_document(compiled)), encoding="utf-8")
    (tmp_path / "report.json").write_text("{}", encoding="utf-8")
    assert "pipeline_report_mismatch" in qa_pipeline_artifacts(pipeline, compiled, query_plan, tmp_path)


def test_qa_failure_removes_staging_and_does_not_publish(
    tmp_path: Path,
    valid_pipeline_request: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(jobs_module, "qa_pipeline_artifacts", lambda *args: ("pipeline_report_mismatch",))

    result = run_pipeline_job(valid_pipeline_request, tmp_path)

    assert result.status == "rejected"
    assert result.error_code == "pipeline_artifact_qa_failed"
    assert not list((tmp_path / "jobs").glob("*"))
    assert not (tmp_path / ".receipts").exists()


def test_concurrent_identical_jobs_publish_once_and_replay_the_second(
    tmp_path: Path,
    valid_pipeline_request: dict[str, Any],
) -> None:
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(run_pipeline_job, valid_pipeline_request, tmp_path) for _ in range(2)]
        results = [future.result(timeout=10) for future in futures]

    assert {result.status for result in results} == {"accepted"}
    assert len({result.fingerprint for result in results}) == 1
    assert sorted(result.replay for result in results) == [False, True]
    assert sorted(result.bytes_written for result in results) == [0, max(result.bytes_written for result in results)]


@pytest.mark.skipif(os.name != "nt", reason="transient sharing-lock retries apply only on Windows")
def test_transient_publish_permission_error_retries_once(
    tmp_path: Path,
    valid_pipeline_request: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_rename = Path.rename
    attempts = 0

    def transient_rename(path: Path, target: str | Path) -> Path:
        nonlocal attempts
        if ".staging-" in path.name and path.parent.name == "jobs":
            attempts += 1
            if attempts == 1:
                raise PermissionError(13, "transient directory sharing lock")
        return original_rename(path, target)

    monkeypatch.setattr(Path, "rename", transient_rename)

    result = run_pipeline_job(valid_pipeline_request, tmp_path)

    assert result.status == "accepted"
    assert attempts == 2


@pytest.mark.skipif(os.name == "nt", reason="POSIX hosts do not retry publish permission errors")
def test_publish_permission_error_fails_closed_without_retry_on_posix(
    tmp_path: Path,
    valid_pipeline_request: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_rename = Path.rename
    attempts = 0

    def denied_rename(path: Path, target: str | Path) -> Path:
        nonlocal attempts
        if ".staging-" in path.name and path.parent.name == "jobs":
            attempts += 1
            raise PermissionError(13, "permission denied")
        return original_rename(path, target)

    monkeypatch.setattr(Path, "rename", denied_rename)

    with pytest.raises(PipelineJobError) as error:
        run_pipeline_job(valid_pipeline_request, tmp_path)

    assert error.value.code == "artifact_publish_failed"
    assert attempts == 1
    jobs_dir = tmp_path / "jobs"
    assert not jobs_dir.exists() or not any(jobs_dir.iterdir())
    receipts_dir = tmp_path / ".receipts"
    assert not receipts_dir.exists() or not any(receipts_dir.iterdir())


def test_symlinked_output_root_is_rejected(tmp_path: Path, valid_pipeline_request: dict[str, Any]) -> None:
    real_root = tmp_path / "real-output"
    real_root.mkdir()
    output_link = tmp_path / "output-link"
    try:
        output_link.symlink_to(real_root, target_is_directory=True)
    except OSError as error:
        pytest.skip(f"host does not permit symlink creation: {error}")

    with pytest.raises(PipelineJobError, match="unsafe_output_path"):
        run_pipeline_job(valid_pipeline_request, output_link)

    assert not (real_root / "jobs").exists()


def test_symlinked_persisted_artifact_fails_replay_integrity(
    tmp_path: Path,
    valid_pipeline_request: dict[str, Any],
) -> None:
    first = run_pipeline_job(valid_pipeline_request, tmp_path)
    assert first.artifact_dir is not None
    artifact = first.artifact_dir / "pipeline.sql"
    external = tmp_path / "external.sql"
    external.write_text("SELECT 1", encoding="utf-8")
    artifact.unlink()
    try:
        artifact.symlink_to(external)
    except OSError as error:
        pytest.skip(f"host does not permit symlink creation: {error}")

    with pytest.raises(PipelineJobError, match="replay_integrity_error"):
        run_pipeline_job(valid_pipeline_request, tmp_path)

    assert external.read_text(encoding="utf-8") == "SELECT 1"
