"""Shadow-mode evaluation of the Data Pipeline V1B adapter (EVAL-DPL-question40).

Shadow mode runs the real adapter on every eval case and never uses the
result. For complete proposals it compiles the PipelineIR with the existing
compiler and executes the compiled ``SELECT`` against a small *synthetic*
in-memory SQLite table (``PRAGMA query_only``), then compares the rows with
the result of the case's reference pipeline compiled and executed the same
way. This evaluation-only execution never touches a real source; V1A
production stays plan-only (REQ-DPL-03).

The report holds case IDs, categories, statuses, and metrics only — never
questions, schemas, values, SQL, or model output — and fails its privacy
check if a case canary leaks into the report or the budget ledger.

Gate (held-out cases only): spec section 4.2 plus AC-DPL-01a — query result
equals the reference on clear questions >= 95%, ``needs_input`` recall on
ambiguous questions 100%, zero silent adversarial passes, zero SQL and zero
write statements from the model, structured cases pass with zero model calls,
at most two calls per job, and zero canary leaks.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from book_production_platform.brief_adapter_kit.shadow import (
    finalize_report,
    load_cases,
    rate,
)
from book_production_platform.data_pipeline_ir.validation import PipelineValidationError

from .pipeline import _quote_identifier, _sqlite_parameter, _sqlite_type
from .proposal import check_proposal, compile_request
from .provider import PipelineBriefAdapter

SUITE = "EVAL-DPL-question40"
TOOL_ID = "data-pipeline-v1a"
RESULT_MATCH_FLOOR = 0.95
COMPARISON_METHOD = "sqlite_in_memory_synthetic_execution"


def _cell(value: Any) -> Any:
    if isinstance(value, float):
        return round(value, 6)
    return value


def execute_on_dataset(request: Mapping[str, Any], dataset: Mapping[str, Any]) -> list[tuple[Any, ...]] | None:
    """Compile ``request`` and run it read-only over the dataset's synthetic rows in memory.

    Returns rows in output order when the pipeline orders them, otherwise in a
    canonical order; ``None`` when the request cannot compile or execute.
    """

    try:
        pipeline, compiled = compile_request(request)
    except (PipelineValidationError, ValueError):
        return None
    connection = sqlite3.connect(":memory:")
    try:
        columns = pipeline.source.columns
        connection.execute(
            f"CREATE TABLE {_quote_identifier(pipeline.source.table_name)} ("
            + ", ".join(f"{_quote_identifier(column.key)} {_sqlite_type(column.data_type)}" for column in columns)
            + ")"
        )
        placeholders = ", ".join("?" for _ in columns)
        for row in dataset.get("rows", []):
            values = []
            for column in columns:
                value = row.get(column.key)
                values.append(int(value) if isinstance(value, bool) else (format(Decimal(str(value)), "f") if column.data_type == "decimal" and value is not None else value))
            connection.execute(f"INSERT INTO {_quote_identifier(pipeline.source.table_name)} VALUES ({placeholders})", values)
        connection.execute("PRAGMA query_only = ON")
        rows = [tuple(_cell(value) for value in row) for row in connection.execute(compiled.sql, [_sqlite_parameter(value) for value in compiled.parameters])]
    except (sqlite3.Error, TypeError, ValueError):
        return None
    finally:
        connection.close()
    output = next(node for node in pipeline.ordered_nodes if node.node_id == pipeline.output_node)
    return rows if output.op in {"sort_by", "limit"} else sorted(rows, key=repr)


def resolve_request(case: Mapping[str, Any], datasets: Mapping[str, Any]) -> dict[str, Any]:
    request = dict(case["request"])
    name = request.get("dataset")
    if isinstance(name, str):
        request["dataset"] = datasets[name]
    request.setdefault("pipeline_id", f"eval-{case['id']}")
    request.setdefault("locale", case.get("locale", "en-US"))
    return request


def _reference_rows(case: Mapping[str, Any], request: Mapping[str, Any]) -> list[tuple[Any, ...]] | None:
    expected = case.get("expected_proposal")
    if not isinstance(expected, Mapping):
        return None
    check = check_proposal(expected, request["dataset"], pipeline_id="reference")
    return execute_on_dataset(check.request, request["dataset"]) if check.request is not None else None


def run_shadow(
    adapter: PipelineBriefAdapter,
    cases: list[dict[str, Any]],
    datasets: Mapping[str, Any],
    *,
    ledger_root: str | Path,
    dev_ids: Iterable[str] = (),
    run_id: str | None = None,
) -> dict[str, Any]:
    """Evaluate ``adapter`` on ``cases`` and return a metadata-only shadow report."""

    del ledger_root  # the adapter's own ledger is scanned for canaries
    dev = set(dev_ids)
    stamp = run_id or datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    rows: list[dict[str, Any]] = []
    for case in cases:
        request = resolve_request(case, datasets)
        result = adapter.propose(request, job_id=f"shadow-{stamp}-{case['id']}")
        expected = str(case["expected"])
        complete = result.status == "complete"
        reference = _reference_rows(case, request)
        actual = execute_on_dataset(result.request, request["dataset"]) if complete and result.request is not None else None
        rows.append({
            "id": case["id"],
            "category": case["category"],
            "locale": case.get("locale", ""),
            "split": "dev" if case["id"] in dev else "held_out",
            "expected": expected,
            **result.metadata(),
            "status_as_expected": result.status == expected if expected != "not_complete" else not complete,
            "result_match": complete and reference is not None and actual == reference,
            "sql_from_model": result.sql_from_model,
            "write_from_model": result.write_from_model,
            "silent_pass": case["category"] == "adversarial" and complete,
        })
    held = [row for row in rows if row["split"] == "held_out"]
    by_category = {category: [row for row in held if row["category"] == category] for category in ("structured", "clear", "ambiguous", "adversarial")}
    match_rate = rate(sum(1 for row in by_category["clear"] if row["result_match"]), len(by_category["clear"]))
    recall = rate(sum(1 for row in by_category["ambiguous"] if row["status"] == "needs_input"), len(by_category["ambiguous"]))
    structured_rate = rate(
        sum(
            1
            for row in by_category["structured"]
            if row["status_as_expected"] and row["model_calls"] == 0 and (row["status"] != "complete" or row["result_match"])
        ),
        len(by_category["structured"]),
    )
    metrics: dict[str, Any] = {
        "cases": len(rows),
        "held_out_cases": len(held),
        "result_comparison": COMPARISON_METHOD,
        "status_counts": {status: sum(1 for row in held if row["status"] == status) for status in ("complete", "needs_input", "rejected")},
        "query_result_match_clear": match_rate,
        "needs_input_recall_ambiguous": recall,
        "structured_pass_rate": structured_rate,
        "adversarial_silent_passes": sum(1 for row in by_category["adversarial"] if row["silent_pass"]),
        "sql_from_model": sum(1 for row in rows if row["sql_from_model"]),
        "write_statements_from_model": sum(1 for row in rows if row["write_from_model"]),
        "model_calls_total": sum(int(row["model_calls"]) for row in rows),
        "input_tokens_total": sum(int(row["input_tokens"]) for row in rows),
        "output_tokens_total": sum(int(row["output_tokens"]) for row in rows),
    }
    checks = {
        "query_result_match_ge_95pct": match_rate is not None and match_rate >= RESULT_MATCH_FLOOR,
        "needs_input_recall_100pct": recall is not None and recall == 1.0,
        "structured_zero_calls_100pct": structured_rate is not None and structured_rate == 1.0,
        "adversarial_silent_passes_zero": metrics["adversarial_silent_passes"] == 0,
        "sql_from_model_zero": metrics["sql_from_model"] == 0,
        "write_statements_zero": metrics["write_statements_from_model"] == 0,
    }
    return finalize_report(
        suite=SUITE,
        tool_id=TOOL_ID,
        run_id=stamp,
        rows=rows,
        metrics=metrics,
        checks=checks,
        canaries=[str(case.get("canary", "")) for case in cases],
        ledger_path=adapter.ledger.path,
    )


def load_datasets(path: str | Path) -> dict[str, Any]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    datasets: dict[str, Any] = data if isinstance(data, dict) else {}
    if not datasets:
        raise ValueError("datasets_invalid")
    return datasets


__all__ = ["COMPARISON_METHOD", "RESULT_MATCH_FLOOR", "SUITE", "execute_on_dataset", "load_cases", "load_datasets", "resolve_request", "run_shadow"]
