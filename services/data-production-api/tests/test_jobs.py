from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from data_production import jobs
from data_production.jobs import IdempotencyConflict, run_data_job

REQUEST = {
    "dataset_id": "orders",
    "csv_text": "region,amount\nBKK,10\nCNX,2\n",
    "columns": [
        {"key": "region", "label": "Region", "data_type": "text"},
        {"key": "amount", "label": "Amount", "data_type": "integer"},
    ],
    "chart": {"x_column": "region", "y_column": "amount", "aggregation": "sum", "title": "Orders"},
}


def test_job_replay_returns_same_artifacts_without_writing(tmp_path: Path) -> None:
    first = run_data_job(REQUEST, tmp_path, "orders-1")
    second = run_data_job(REQUEST, tmp_path, "orders-1")
    assert first.status == second.status == "accepted"
    assert second.replayed is True
    assert second.bytes_written == 0
    assert [item.filename for item in first.artifacts] == [item.filename for item in second.artifacts]
    with pytest.raises(IdempotencyConflict, match="different_input"):
        run_data_job({**REQUEST, "title": "changed"}, tmp_path, "orders-1")


def test_replay_keeps_original_recipe_output_after_recipe_upgrade(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(jobs, "RECIPE_VERSION", "csv-cleaner-chart-builder-v1")
    first = jobs.run_data_job(REQUEST, tmp_path, "upgrade-replay")
    assert first.status == "accepted"

    monkeypatch.setattr(jobs, "RECIPE_VERSION", "csv-cleaner-chart-builder-v2")
    replay = jobs.run_data_job(REQUEST, tmp_path, "upgrade-replay")

    assert replay.status == "accepted"
    assert replay.replayed is True
    assert replay.bytes_written == 0


def test_invalid_request_is_rejected_without_model_calls(tmp_path: Path) -> None:
    result = run_data_job({"dataset_id": "bad", "csv_text": "x\n1\n", "chart": {"x_column": "missing"}}, tmp_path, "bad-1")
    assert result.status == "rejected"
    assert result.model_calls == 0


def test_replay_rejects_tampered_artifact(tmp_path: Path) -> None:
    first = run_data_job(REQUEST, tmp_path, "tamper-1")
    assert first.manifest_path is not None
    (first.manifest_path.parent / "cleaned.csv").write_text("corrupted\n", encoding="utf-8")
    replay = run_data_job(REQUEST, tmp_path, "tamper-1")
    assert replay.status == "rejected"
    assert replay.error_code == "artifact_integrity_failed"


def test_concurrent_duplicate_requests_publish_one_accepted_artifact(tmp_path: Path) -> None:
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: run_data_job(REQUEST, tmp_path, "concurrent-1"), range(2)))
    assert {result.status for result in results} == {"accepted"}
    assert sum(result.replayed for result in results) == 1
    assert len(list(tmp_path.glob("job-*/manifest.json"))) == 1
