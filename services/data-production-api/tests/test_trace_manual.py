from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest
from data_ir import DatasetRequest
from data_ir.models import CHART_DISPLAY_DEFAULTS
from production_trace import read_history, sha256_json
from production_trace.cli import main as trace_cli

from data_production import jobs
from data_production.cli import main as data_cli
from data_production.jobs import run_data_job
from data_production.proposal_trace import split_proposal, trace_proposal

CANARY = "CANARY-dvz-trace-7c1f"


def _request(**chart: Any) -> dict[str, Any]:
    return {
        "dataset_id": "orders",
        "title": f"Private title {CANARY}",
        "csv_text": f"region,amount,status\n BKK ,10.50,ok\nCNX,2,ok\nHKT,,void {CANARY}\nBKK,4,ok\nKKC,1,ok\n",
        "columns": [
            {"key": "region", "label": "Region", "data_type": "text"},
            {"key": "amount", "label": "Amount", "data_type": "decimal"},
            {"key": "status", "label": "Status", "data_type": "text"},
        ],
        "transforms": [
            {"op": "trim_text", "column": "region"},
            {"op": "fill_null", "column": "amount", "value": "0"},
            {"op": "filter_equals", "column": "status", "value": "ok"},
            {"op": "sort_by", "column": "amount", "direction": "desc"},
        ],
        "chart": {"x_column": "region", "y_column": "amount", "aggregation": "sum", "title": f"Sales {CANARY}", "value_unit": "THB", **chart},
    }


def _trace(root: Path) -> dict[str, Any]:
    entry = read_history(root)[-1]
    return dict(json.loads((root / entry["trace"]).read_text(encoding="utf-8")))


def test_accepted_job_writes_trace_ir_manual_and_history(tmp_path: Path) -> None:
    result = run_data_job(_request(), tmp_path, "trace-1")
    assert result.status == "accepted"
    job_dir = tmp_path / f"job-{hashlib.sha256(b'trace-1').hexdigest()[:16]}"
    trace = json.loads((job_dir / "trace.json").read_text(encoding="utf-8"))
    assert trace["status"] == "accepted" and trace["job_id"] == result.job_id
    assert trace["tool_id"] == "data-viz-v1a" and trace["recipe_version"] == jobs.RECIPE_VERSION
    assert [step["operation"] for step in trace["steps"]] == ["parse_request", "build_dataset_ir", "build_chart_ir", "render_outputs", "qa_outputs", "write_manifest"]
    assert {item["file"] for item in trace["artifacts"]} == {"cleaned.csv", "chart.svg", "report.json"}
    ir = json.loads((job_dir / "ir.json").read_text(encoding="utf-8"))
    assert trace["ir"] == {"schema": jobs.IR_SCHEMA, "sha256": sha256_json(ir), "file": "ir.json"}
    assert trace["ai"]["used"] is False
    dataset_step = trace["steps"][1]["params"]
    assert [item["op"] for item in dataset_step["transforms"]] == ["trim_text", "fill_null", "filter_equals", "sort_by"]
    assert dataset_step["transforms"][2]["dropped_rows"] == 1
    assert trace["manual_guide"]["program"] == "Microsoft Excel"
    assert (job_dir / "MANUAL-STEPS.md").is_file()
    history = read_history(tmp_path)
    assert len(history) == 1 and history[0]["job_id"] == result.job_id and history[0]["status"] == "accepted"


def test_trace_and_history_never_hold_private_text(tmp_path: Path) -> None:
    run_data_job(_request(), tmp_path, "canary-1")
    run_data_job({**_request(), "chart": {"x_column": "missing"}}, tmp_path, "canary-2")
    for path in [*tmp_path.glob("job-*/trace.json"), tmp_path / "history.jsonl"]:
        text = path.read_text(encoding="utf-8")
        assert CANARY not in text, path
        assert "Private title" not in text and "BKK" not in text


def test_verify_passes_fresh_and_fails_after_tampering(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    result = run_data_job(_request(), tmp_path, "verify-1")
    assert trace_cli(["verify", "--root", str(tmp_path), result.job_id]) == 0
    assert result.manifest_path is not None
    (result.manifest_path.parent / "chart.svg").write_text("<svg/>", encoding="utf-8")
    assert trace_cli(["verify", "--root", str(tmp_path), result.job_id]) == 1
    assert '"ok": false' in capsys.readouterr().out


def test_replay_does_not_append_history(tmp_path: Path) -> None:
    run_data_job(_request(), tmp_path, "replay-1")
    replay = run_data_job(_request(), tmp_path, "replay-1")
    assert replay.replayed is True
    assert len(read_history(tmp_path)) == 1


@pytest.mark.parametrize(
    ("request_payload", "code"),
    [
        ({"dataset_id": "bad id!", "csv_text": "x\n1\n"}, "invalid_request"),
        ({"dataset_id": "bad", "csv_text": "x\n1\n", "chart": {"x_column": "missing"}}, "unknown_chart_column"),
        ({"dataset_id": "bad", "csv_text": "x,y\nA,-1\n", "chart": {"x_column": "x", "y_column": "y", "aggregation": "sum", "chart_type": "pie"}}, "pie_requires_non_negative_values"),
    ],
)
def test_rejected_jobs_are_traced_as_failed(tmp_path: Path, request_payload: dict[str, Any], code: str) -> None:
    result = run_data_job(request_payload, tmp_path, "fail-1")
    assert result.status == "rejected" and result.error_code == code
    entry = read_history(tmp_path)[-1]
    assert entry["status"] == "failed"
    trace = _trace(tmp_path)
    assert trace["steps"][-1] == {**trace["steps"][-1], "stage": "outcome", "operation": "failed", "note": code}
    assert not (tmp_path / Path(entry["trace"]).parent / "manifest.json").exists()


def test_qa_failure_is_traced(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(jobs, "qa_outputs", lambda *_: ("csv_row_count_mismatch",))
    result = run_data_job(_request(), tmp_path, "qa-1")
    assert result.error_code == "qa_failed"
    trace = _trace(tmp_path)
    assert trace["status"] == "failed" and trace["qa"] == {"status": "failed", "checks": 1}
    assert [step["status"] for step in trace["steps"] if step["stage"] == "qa"] == ["failed"]


def test_unexpected_error_is_traced_and_still_raised(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*_: Any) -> Any:
        raise RuntimeError("disk")

    monkeypatch.setattr(jobs, "render_outputs", boom)
    with pytest.raises(RuntimeError, match="disk"):
        run_data_job(_request(), tmp_path, "boom-1")
    trace = _trace(tmp_path)
    assert trace["status"] == "failed"
    assert trace["steps"][-2]["status"] == "failed" and trace["steps"][-1]["note"] == "RuntimeError"


def test_display_defaults_keep_v3_request_hash() -> None:
    request = DatasetRequest.model_validate(_request())
    legacy = request.model_dump(mode="json", exclude={"chart": set(CHART_DISPLAY_DEFAULTS)})
    expected = hashlib.sha256(json.dumps({"recipe": "r", "request": legacy}, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    assert jobs._canonical_hash(request, "r") == expected
    changed = DatasetRequest.model_validate(_request(top_n=2))
    assert jobs._canonical_hash(changed, "r") != expected


def test_cli_build_from_complete_proposal_records_ai(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    proposal = {"status": "complete", "request": _request(), "adapter": "provider", "model_calls": 1, "provided": ["x_column"], "inferred": ["title"]}
    source = tmp_path / "proposal.json"
    source.write_text(json.dumps(proposal), encoding="utf-8")
    assert data_cli(["build", "--input", str(source), "--output", str(tmp_path / "out"), "--idempotency-key", "ai-1"]) == 0
    trace = _trace(tmp_path / "out")
    assert trace["ai"] == {"used": True, "model_calls": 1, "adapter": "provider", "mode": "live", "provided": ["x_column"], "inferred": ["title"]}
    assert read_history(tmp_path / "out")[-1]["ai_used"] is True
    capsys.readouterr()
    source.write_text(json.dumps({**proposal, "status": "needs_input", "request": None}), encoding="utf-8")
    assert data_cli(["build", "--input", str(source), "--output", str(tmp_path / "out"), "--idempotency-key", "ai-2"]) == 1
    assert "proposal_not_complete" in capsys.readouterr().out


def test_split_proposal_passes_plain_requests_through() -> None:
    payload = _request()
    assert split_proposal(payload) == (payload, None)


def test_trace_proposal_writes_metadata_only_trace(tmp_path: Path) -> None:
    result = {"status": "needs_input", "request": None, "adapter": "provider", "adapter_version": "brief-adapter.v1", "model_calls": 2,
              "provided": [], "inferred": [], "missing": ["aggregation"], "issues": [{"code": "ambiguous", "path": "brief", "message": "m"}]}
    output = tmp_path / "proposal.json"
    output.write_text(json.dumps(result), encoding="utf-8")
    trace = trace_proposal(result, output, "p-1", envelope={"brief": f"secret brief {CANARY}"})
    assert trace is not None and trace["status"] == "needs_input" and trace["ai"]["model_calls"] == 2
    assert trace_cli(["verify", "--root", str(tmp_path), trace["job_id"]]) == 0
    assert CANARY not in (tmp_path / "history.jsonl").read_text(encoding="utf-8")
    assert CANARY not in json.dumps(trace)
