"""Data pipeline job history, trace, Power Query manual, new nodes (distinct, filter_compare), and local execution."""

from __future__ import annotations

import copy
import hashlib
import json
import os
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

import book_production_platform.data_pipeline.jobs as jobs_module
from book_production_platform.data_pipeline.cli import main as data_main
from book_production_platform.data_pipeline.execute import (
    RESULTS_FILENAME,
    execute_pipeline,
    normalize_rows,
    run_pipeline_execution_job,
)
from book_production_platform.data_pipeline.jobs import (
    PipelineJobError,
    run_pipeline_job,
)
from book_production_platform.data_pipeline.manual import (
    build_power_query_guide,
    inline_sql,
    m_literal,
)
from book_production_platform.data_pipeline.pipeline import (
    compile_pipeline_ir,
    explain_pipeline_sql,
)
from book_production_platform.data_pipeline.proposal import check_proposal
from book_production_platform.data_pipeline_ir.models import PipelineRequest
from book_production_platform.data_pipeline_ir.validation import (
    PipelineValidationError,
    build_pipeline_ir,
)
from book_production_platform.production_trace import (
    read_history,
    render_manual_markdown,
    sha256_file,
    sha256_json,
    verify_trace,
)

CANARY = "CANARYDPLTRACE4410"
SOURCE = {
    "table_name": "orders",
    "source_ref": "warehouse.orders",
    "columns": [
        {"key": "region", "label": "Region", "data_type": "text"},
        {"key": "amount", "label": "Amount", "data_type": "decimal"},
        {"key": "sold_on", "label": "Sold on", "data_type": "date"},
    ],
}
ROWS = [
    {"region": "BKK", "amount": "10.50", "sold_on": "2026-01-02"},
    {"region": "BKK", "amount": "10.50", "sold_on": "2026-01-02"},
    {"region": "BKK", "amount": "3", "sold_on": "2026-01-05"},
    {"region": "CNX", "amount": "20", "sold_on": "2026-01-09"},
    {"region": "=HYPERLINK(1)", "amount": "7", "sold_on": "2026-01-03"},
    {"region": "HKT", "amount": "9", "sold_on": "2026-02-01"},
    {"region": CANARY, "amount": None, "sold_on": None},
]


def rich_request() -> dict[str, Any]:
    return {
        "pipeline_id": "trace-demo",
        "source": copy.deepcopy(SOURCE),
        "nodes": [
            {"node_id": "recent", "parent_ids": ["source"], "op": "filter_compare", "column": "sold_on", "operator": "gte", "value": "2026-01-02"},
            {"node_id": "big", "parent_ids": ["recent"], "op": "filter_compare", "column": "amount", "operator": "gt", "value": 5},
            {"node_id": "dedup", "parent_ids": ["big"], "op": "distinct"},
            {"node_id": "by_region", "parent_ids": ["dedup"], "op": "aggregate", "group_by": ["region"],
             "metrics": [{"alias": "total", "op": "sum", "column": "amount"}, {"alias": "n", "op": "count"}]},
            {"node_id": "top_total", "parent_ids": ["by_region"], "op": "filter_compare", "column": "total", "operator": "gte", "value": Decimal("10.5")},
            {"node_id": "ranked", "parent_ids": ["top_total"], "op": "sort_by", "column": "total", "direction": "desc"},
            {"node_id": "top", "parent_ids": ["ranked"], "op": "limit", "limit": 5},
        ],
        "output_node": "top",
    }


def canary_request() -> dict[str, Any]:
    return {
        "pipeline_id": "canary",
        "source": copy.deepcopy(SOURCE),
        "nodes": [
            {"node_id": "only", "parent_ids": ["source"], "op": "filter_equals", "column": "region", "value": CANARY},
            {"node_id": "picked", "parent_ids": ["only"], "op": "select", "columns": ["region", "amount"]},
        ],
        "output_node": "picked",
    }


def branching_request() -> dict[str, Any]:
    return {
        "pipeline_id": "branches",
        "source": copy.deepcopy(SOURCE),
        "nodes": [
            {"node_id": "north", "parent_ids": ["source"], "op": "filter_equals", "column": "region", "value": "CNX"},
            {"node_id": "south", "parent_ids": ["source"], "op": "filter_equals", "column": "region", "value": "HKT"},
            {"node_id": "both", "parent_ids": ["north", "south"], "op": "union_all"},
            {"node_id": "empty_amount", "parent_ids": ["both"], "op": "filter_null", "column": "amount"},
        ],
        "output_node": "empty_amount",
    }


def test_accepted_plan_job_records_trace_ir_manual_and_history(tmp_path: Path) -> None:
    result = run_pipeline_job(canary_request(), tmp_path)

    assert result.status == "accepted" and result.artifact_dir is not None
    trace = json.loads((result.artifact_dir / "trace.json").read_text(encoding="utf-8"))
    assert trace["tool_id"] == "data-pipeline-v1a" and trace["job_id"] == result.fingerprint and trace["status"] == "accepted"
    node_steps = [step["params"]["node_id"] for step in trace["steps"] if step["operation"] == "node"]
    assert node_steps == ["only", "picked"]
    assert [step["operation"] for step in trace["steps"] if step["stage"] in {"compile", "plan", "qa", "publish"}] == [
        "compile_sql", "explain_query_plan", "reopen_artifacts", "publish_artifacts",
    ]
    assert {item["file"]: item["sha256"] for item in trace["artifacts"]} == result.artifact_hashes
    for name, digest in result.artifact_hashes.items():
        assert sha256_file(result.artifact_dir / name) == digest
    ir_payload = json.loads((result.artifact_dir / "ir.json").read_text(encoding="utf-8"))
    assert trace["ir"] == {"schema": "pipeline-ir.v1", "sha256": sha256_json(ir_payload), "file": "ir.json"}
    assert trace["manual_guide"]["program"] == "Microsoft Excel Power Query"
    history = read_history(tmp_path)
    assert [(item["job_id"], item["status"]) for item in history] == [(result.fingerprint, "accepted")]
    if os.name != "nt":
        for name in ("trace.json", "MANUAL-STEPS.md", "ir.json"):
            assert (result.artifact_dir / name).stat().st_mode & 0o077 == 0


def test_trace_and_history_never_hold_filter_values(tmp_path: Path) -> None:
    result = run_pipeline_job(canary_request(), tmp_path)
    assert result.artifact_dir is not None

    for text in ((result.artifact_dir / "trace.json").read_text(encoding="utf-8"), (tmp_path / "history.jsonl").read_text(encoding="utf-8")):
        assert CANARY not in text
    # The manual is the human recipe (same sensitivity as parameters.json): it carries the value to type.
    assert CANARY in (result.artifact_dir / "MANUAL-STEPS.md").read_text(encoding="utf-8")


def test_replay_ignores_sidecars_and_verify_detects_tamper(tmp_path: Path) -> None:
    first = run_pipeline_job(canary_request(), tmp_path)
    second = run_pipeline_job(canary_request(), tmp_path)

    assert second.replay is True and second.artifact_hashes == first.artifact_hashes
    assert len(read_history(tmp_path)) == 1
    assert first.fingerprint is not None and first.artifact_dir is not None
    assert verify_trace(tmp_path, first.fingerprint)["ok"] is True
    (first.artifact_dir / "pipeline.sql").write_text("SELECT 1", encoding="utf-8")
    report = verify_trace(tmp_path, first.fingerprint)
    assert report["ok"] is False and [item["file"] for item in report["artifacts"] if not item["ok"]] == ["pipeline.sql"]
    with pytest.raises(PipelineJobError, match="replay_integrity_error"):
        run_pipeline_job(canary_request(), tmp_path)


def test_unknown_extra_file_still_fails_replay(tmp_path: Path) -> None:
    first = run_pipeline_job(canary_request(), tmp_path)
    assert first.artifact_dir is not None
    (first.artifact_dir / "extra.txt").write_text("x", encoding="utf-8")

    with pytest.raises(PipelineJobError, match="replay_integrity_error"):
        run_pipeline_job(canary_request(), tmp_path)


def test_rejected_jobs_are_traced_without_values(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    invalid = canary_request()
    invalid["nodes"][0]["column"] = "missing"
    rejected = run_pipeline_job(invalid, tmp_path)
    monkeypatch.setattr(jobs_module, "qa_pipeline_artifacts", lambda *args: ("pipeline_report_mismatch",))
    qa_failed = run_pipeline_job(canary_request(), tmp_path)

    assert rejected.error_code == "unknown_pipeline_column" and qa_failed.error_code == "pipeline_artifact_qa_failed"
    assert not (tmp_path / "jobs").exists() or not list((tmp_path / "jobs").iterdir())
    history = read_history(tmp_path)
    assert [item["status"] for item in history] == ["failed", "failed"]
    notes = []
    for item in history:
        text = (tmp_path / item["trace"]).read_text(encoding="utf-8")
        assert CANARY not in text
        trace = json.loads(text)
        assert trace["artifacts"] == [] and item["trace"].startswith("traces/")
        notes.append(trace["steps"][-1]["note"])
    assert notes == ["unknown_pipeline_column", "pipeline_artifact_qa_failed"]


def test_unsafe_output_root_rejection_writes_no_trace(tmp_path: Path) -> None:
    target = tmp_path / "real"
    target.mkdir()
    link = tmp_path / "link"
    try:
        link.symlink_to(target, target_is_directory=True)
    except OSError as error:
        pytest.skip(f"host does not permit symlink creation: {error}")
    invalid = canary_request()
    invalid["nodes"][0]["column"] = "missing"

    assert run_pipeline_job(invalid, link).status == "rejected"
    assert not any(target.iterdir())


def test_ai_proposal_metadata_is_recorded(tmp_path: Path) -> None:
    result = run_pipeline_job(canary_request(), tmp_path, ai_proposal={"model_calls": 1, "adapter": "data-pipeline-provider-gateway", "mode": "live"})

    assert result.artifact_dir is not None
    ai = json.loads((result.artifact_dir / "trace.json").read_text(encoding="utf-8"))["ai"]
    assert ai["used"] is True and ai["model_calls"] == 1 and ai["adapter"] == "data-pipeline-provider-gateway"


def test_guide_has_every_node_in_order_with_exact_values() -> None:
    pipeline = build_pipeline_ir(rich_request())
    compiled = compile_pipeline_ir(pipeline)
    guide = build_power_query_guide(pipeline, compiled)
    text = render_manual_markdown(guide)

    step_names = [layer.name for layer in guide.layers if layer.kind.startswith("Applied Step")]
    assert step_names == [node.node_id for node in pipeline.ordered_nodes]
    positions = [text.index(f"Applied Step ชื่อ `{node.node_id}`") for node in pipeline.ordered_nodes]
    assert positions == sorted(positions)
    assert "each [sold_on] <> null and [sold_on] >= #date(2026, 1, 2)" in text
    assert "each [amount] <> null and [amount] > 5" in text
    assert "Table.Distinct(" in text and "Remove Duplicates" in text
    assert 'Table.Group(#"dedup", {"region"}, {{"total", each List.Sum([amount]), type nullable number}, {"n", each Table.RowCount(_), Int64.Type}})' in text
    assert 'Table.Sort(#"top_total", {{"total", Order.Descending}, {"n", Order.Ascending}, {"region", Order.Ascending}})' in text
    assert 'Table.FirstN(#"ranked", 5)' in text and "Keep Top Rows" in text
    sql = inline_sql(compiled)
    assert "?" not in sql and "'2026-01-02'" in sql and "CAST(10.5 AS NUMERIC)" in sql
    assert f"`{sql}`" in text
    assert hashlib.sha256(compiled.sql.encode()).hexdigest() in text
    assert guide.estimated_minutes is not None and guide.estimated_minutes > 0


def test_guide_turns_branches_into_reference_queries_and_append() -> None:
    pipeline = build_pipeline_ir(branching_request())
    guide = build_power_query_guide(pipeline, compile_pipeline_ir(pipeline))
    text = render_manual_markdown(guide)

    references = [step.value for step in guide.steps if "Reference" in step.tool]
    assert references == ["north", "south"]
    append = next(step for step in guide.steps if "Append Queries" in step.tool)
    assert append.value == "south" and 'Table.Combine({#"north", #"south"})' in (append.note or "")
    assert "each [region] = \"CNX\"" in text and "each [amount] = null" in text
    load = next(step for step in guide.steps if "Table ▸ New worksheet" in step.tool)
    assert load.value == "north"


def test_compare_and_distinct_validation_rules() -> None:
    request = rich_request()
    request["nodes"][0] = {"node_id": "recent", "parent_ids": ["source"], "op": "filter_compare", "column": "region", "operator": "gt", "value": "A"}
    with pytest.raises(PipelineValidationError, match="pipeline_type_mismatch"):
        build_pipeline_ir(request)
    request["nodes"][0]["operator"] = "ne"
    assert build_pipeline_ir(request).ordered_nodes[0].operator == "ne"
    missing_operator = rich_request()
    del missing_operator["nodes"][1]["operator"]
    with pytest.raises(PipelineValidationError, match="invalid_pipeline_operation_fields"):
        build_pipeline_ir(missing_operator)
    extra = rich_request()
    extra["nodes"][2]["column"] = "region"
    with pytest.raises(PipelineValidationError, match="invalid_pipeline_operation_fields"):
        build_pipeline_ir(extra)


def test_new_nodes_compile_explain_and_execute_correctly() -> None:
    pipeline = build_pipeline_ir(rich_request())
    compiled = compile_pipeline_ir(pipeline)

    assert "SELECT DISTINCT * FROM" in compiled.sql and '"total" >= CAST(? AS NUMERIC)' in compiled.sql
    assert explain_pipeline_sql(pipeline, compiled).steps
    result = execute_pipeline(pipeline, compiled, normalize_rows(pipeline, ROWS))
    # BKK duplicates collapse to one 10.5 row; HKT 9 and the formula-like 7 fall under the 10.5 threshold.
    assert result.rows == [("CNX", 20, 1), ("BKK", 10.5, 1)]
    assert result.numeric_totals == {"total": "30.5", "n": "2"}


def test_execution_job_exports_csv_traces_and_replays(tmp_path: Path) -> None:
    result = run_pipeline_execution_job(canary_request(), ROWS, tmp_path)

    assert result.status == "accepted" and result.result_dir is not None and result.row_count == 1
    assert (result.result_dir / RESULTS_FILENAME).read_text(encoding="utf-8") == f"region,amount\n{CANARY},\n"
    history = read_history(tmp_path)
    assert [item["artifacts"] for item in history] == [
        ["parameters.json", "pipeline.sql", "qa-manifest.json", "report.json"], ["results.csv", "execution-manifest.json"],
    ]
    trace_text = (result.result_dir / "trace.json").read_text(encoding="utf-8")
    assert CANARY not in trace_text and CANARY not in (tmp_path / "history.jsonl").read_text(encoding="utf-8")
    trace = json.loads(trace_text)
    assert trace["inputs"][1]["rows"] == len(ROWS) and trace["inputs"][1]["sha256"] == sha256_json(ROWS)
    manual = (result.result_dir / "MANUAL-STEPS.md").read_text(encoding="utf-8")
    assert "ผลลัพธ์มี 1 แถว" in manual and hashlib.sha256((result.result_dir / RESULTS_FILENAME).read_bytes()).hexdigest() in manual
    assert result.run_id is not None and verify_trace(tmp_path, result.run_id)["ok"] is True

    again = run_pipeline_execution_job(canary_request(), ROWS, tmp_path)
    assert again.replay is True and again.artifact_hashes == result.artifact_hashes
    assert len(read_history(tmp_path)) == 2


def test_execution_guards_formula_text_in_csv(tmp_path: Path) -> None:
    request = {"pipeline_id": "all", "source": copy.deepcopy(SOURCE), "nodes": [
        {"node_id": "picked", "parent_ids": ["source"], "op": "select", "columns": ["region"]},
        {"node_id": "ordered", "parent_ids": ["picked"], "op": "sort_by", "column": "region", "direction": "asc"},
    ], "output_node": "ordered"}
    result = run_pipeline_execution_job(request, ROWS[:5], tmp_path)

    assert result.result_dir is not None
    lines = (result.result_dir / RESULTS_FILENAME).read_text(encoding="utf-8").splitlines()
    assert lines[1] == "'=HYPERLINK(1)" and lines[2:] == ["BKK", "BKK", "BKK", "CNX"]


def test_execution_rejects_bad_rows_and_traces_the_failure(tmp_path: Path) -> None:
    bad = [{"region": "BKK", "amount": f"not-a-number-{CANARY}", "sold_on": None}]
    result = run_pipeline_execution_job(canary_request(), bad, tmp_path)
    unknown = run_pipeline_execution_job(canary_request(), [{"nope": 1}], tmp_path)

    assert (result.status, result.error_code) == ("rejected", "execution_rows_invalid")
    assert unknown.error_code == "execution_rows_invalid"
    history = read_history(tmp_path)
    assert [item["status"] for item in history] == ["accepted", "failed", "failed"]
    for item in history[1:]:
        assert CANARY not in (tmp_path / item["trace"]).read_text(encoding="utf-8")


def test_proposal_allowlist_accepts_distinct_and_comparisons() -> None:
    dataset = {**copy.deepcopy(SOURCE)}
    raw = {"table": "orders", "steps": [
        {"type": "filter", "column": "amount", "operator": "gte", "value": 10},
        {"type": "distinct"},
        {"type": "sort", "column": "amount", "direction": "desc"},
    ]}
    check = check_proposal(raw, dataset, pipeline_id="p")
    assert check.status == "complete" and check.request is not None
    assert [node["op"] for node in check.request["nodes"]] == ["filter_compare", "distinct", "sort_by"]

    grounded = check_proposal(raw, dataset, pipeline_id="p", question="amount at least 10", grounded=lambda value, *_: str(value) in "amount at least 10")
    assert grounded.status == "complete"
    ungrounded = check_proposal(raw, dataset, pipeline_id="p", question="large amounts", grounded=lambda value, *_: False)
    assert ungrounded.status == "needs_input" and "filter:amount" in ungrounded.missing

    for bad_step in ({"type": "filter", "column": "amount", "operator": "like", "value": 1}, {"type": "filter", "column": "amount", "operator": "gt", "is_null": True}):
        assert check_proposal({"table": "orders", "steps": [bad_step]}, dataset, pipeline_id="p").status == "rejected"


def test_requests_without_operator_keep_their_fingerprint() -> None:
    parsed = PipelineRequest.model_validate(canary_request())
    legacy = parsed.model_dump(mode="json")
    for node in legacy["nodes"]:
        node.pop("operator")
    expected = hashlib.sha256(jobs_module._json_bytes({"recipe_version": jobs_module.RECIPE_VERSION, "request": legacy})).hexdigest()

    assert jobs_module._canonical_fingerprint(parsed, jobs_module.RECIPE_VERSION) == expected


def test_cli_run_executes_and_prints_metadata_only(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    request_path = tmp_path / "request.json"
    request_path.write_text(json.dumps(canary_request()), encoding="utf-8")
    rows_path = tmp_path / "rows.json"
    rows_path.write_text(json.dumps({"rows": ROWS}), encoding="utf-8")

    code = data_main(["pipeline", "run", "--input", str(request_path), "--rows", str(rows_path), "--output-root", str(tmp_path / "out")])

    output = capsys.readouterr().out
    assert code == 0 and CANARY not in output
    assert json.loads(output)["row_count"] == 1
    assert m_literal(Decimal("10.50")) == "10.5"


@pytest.mark.parametrize("node", [
    {"node_id": "f", "parent_ids": ["agg"], "op": "filter_equals", "column": "s", "value": Decimal("10.5")},
    {"node_id": "f", "parent_ids": ["agg"], "op": "filter_compare", "column": "s", "operator": "gte", "value": Decimal("10.5")},
])
def test_decimal_filters_after_aggregates_compare_numerically(node: dict[str, Any]) -> None:
    source = {"table_name": "t", "source_ref": "r", "columns": [
        {"key": "g", "label": "G", "data_type": "text"}, {"key": "a", "label": "A", "data_type": "decimal"},
    ]}
    request = {"pipeline_id": "agg-filter", "source": source, "output_node": "f", "nodes": [
        {"node_id": "agg", "parent_ids": ["source"], "op": "aggregate", "group_by": ["g"], "metrics": [{"alias": "s", "op": "sum", "column": "a"}]},
        node,
    ]}
    pipeline = build_pipeline_ir(request)
    compiled = compile_pipeline_ir(pipeline)

    assert "CAST(? AS NUMERIC)" in compiled.sql
    rows = [{"g": "x", "a": "10.5"}, {"g": "y", "a": "3"}]
    assert execute_pipeline(pipeline, compiled, normalize_rows(pipeline, rows)).rows == [("x", 10.5)]
