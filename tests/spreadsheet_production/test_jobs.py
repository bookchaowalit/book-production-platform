import json
from pathlib import Path

import pytest
from test_recipe import request

from book_production_platform.spreadsheet_production.jobs import (
    IdempotencyConflict,
    run_workbook_job,
)


def test_run_workbook_job_writes_xlsx_and_manifest(tmp_path: Path) -> None:
    result = run_workbook_job(request(), tmp_path, "workbook-1")

    assert result.status == "accepted"
    assert result.model_calls == 0
    assert result.replayed is False
    assert result.artifact is not None and result.artifact.media_type.endswith("spreadsheetml.sheet")
    assert result.manifest_path is not None and result.manifest_path.exists()
    manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
    assert manifest["qa"] == []
    assert manifest["telemetry"]["model_calls"] == 0


def test_run_workbook_job_replays_without_extra_writes(tmp_path: Path) -> None:
    first = run_workbook_job(request(), tmp_path, "workbook-2")
    second = run_workbook_job(request(), tmp_path, "workbook-2")

    assert first.job_id == second.job_id
    assert second.replayed is True
    assert second.bytes_written == 0
    assert second.artifact == first.artifact


def test_changed_input_with_same_key_conflicts(tmp_path: Path) -> None:
    run_workbook_job(request(), tmp_path, "workbook-3")
    changed = {**request(), "title": "changed"}

    with pytest.raises(IdempotencyConflict):
        run_workbook_job(changed, tmp_path, "workbook-3")


def test_invalid_workbook_returns_rejected_without_manifest(tmp_path: Path) -> None:
    bad = {**request(), "tables": []}

    result = run_workbook_job(bad, tmp_path, "workbook-4")

    assert result.status == "rejected"
    assert result.manifest_path is None
    assert result.error_code == "invalid_request"
