"""Content checks for private Data Pipeline plan artifacts."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from book_production_platform.data_pipeline_ir.models import PipelineIR, PipelineNode

from .pipeline import (
    CompiledPipeline,
    ExplainResult,
    ExplainStep,
    PipelineExplainError,
    explain_pipeline_sql,
    parameter_document,
)

REPORT_SCHEMA = "pipeline-report.v1"
MAX_ARTIFACT_BYTES = 1_048_576
_INPUT_ARTIFACT_NAMES = frozenset({"pipeline.sql", "parameters.json", "report.json"})


def _query_plan_document(steps: tuple[ExplainStep, ...]) -> list[dict[str, Any]]:
    return [{"node_id": step.node_id, "parent_id": step.parent_id, "detail": step.detail} for step in steps]


def _operation_document(node: PipelineNode) -> dict[str, Any]:
    operation: dict[str, Any] = {
        "node_id": node.node_id,
        "parent_ids": list(node.parent_ids),
        "op": node.op,
    }
    if node.op == "select":
        operation["columns"] = list(node.columns or [])
    elif node.op in {"filter_equals", "filter_null"}:
        operation["column"] = node.column
    elif node.op == "sort_by":
        operation["column"] = node.column
        operation["direction"] = node.direction
    elif node.op == "limit":
        operation["limit"] = node.limit
    elif node.op == "aggregate":
        operation["group_by"] = list(node.group_by or [])
        operation["metrics"] = [metric.model_dump(mode="json") for metric in node.metrics or []]
    return operation


def build_pipeline_report(
    pipeline: PipelineIR,
    compiled: CompiledPipeline,
    query_plan: tuple[ExplainStep, ...],
    sqlite_version: str,
) -> dict[str, Any]:
    """Build a report with lineage and query-plan evidence, without filter values."""
    sql_hash = hashlib.sha256(compiled.sql.encode("utf-8")).hexdigest()
    return {
        "schema_version": REPORT_SCHEMA,
        "pipeline_id": pipeline.pipeline_id,
        "source_ref": pipeline.source.source_ref,
        "source_table": pipeline.source.table_name,
        "operations": [_operation_document(node) for node in pipeline.ordered_nodes],
        "output_node": pipeline.output_node,
        "output_schema": [column.model_dump(mode="json") for column in pipeline.output_columns],
        "output_lineage": {
            key: [lineage.model_dump(mode="json") for lineage in refs]
            for key, refs in pipeline.output_lineage.items()
        },
        "sql_sha256": sql_hash,
        "query_plan": _query_plan_document(query_plan),
        "sqlite_version": sqlite_version,
        "execution_mode": "plan_only",
        "query_plan_verified": True,
        "model_calls": 0,
        "parameter_count": len(compiled.parameters),
    }


def qa_pipeline_artifacts(
    pipeline: PipelineIR,
    compiled: CompiledPipeline,
    query_plan: ExplainResult,
    staging_dir: Path,
) -> tuple[str, ...]:
    """Reopen and compare staged compiler outputs against the typed inputs."""
    issues: list[str] = []
    try:
        if staging_dir.is_symlink() or not staging_dir.is_dir():
            return ("artifact_path_invalid",)
        if {path.name for path in staging_dir.iterdir()} != _INPUT_ARTIFACT_NAMES:
            return ("artifact_set_mismatch",)
        artifact_bytes: dict[str, bytes] = {}
        for name in _INPUT_ARTIFACT_NAMES:
            path = staging_dir / name
            if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_ARTIFACT_BYTES:
                issues.append("artifact_file_invalid")
                continue
            artifact_bytes[name] = path.read_bytes()
        if issues:
            return tuple(sorted(set(issues)))

        sql = artifact_bytes["pipeline.sql"].decode("utf-8")
        if (
            sql != compiled.sql
            or not sql.lstrip().upper().startswith("WITH ")
            or ";" in sql
            or sql.count("?") != len(compiled.parameters)
        ):
            issues.append("pipeline_sql_mismatch")

        try:
            parameters = json.loads(artifact_bytes["parameters.json"])
        except (UnicodeDecodeError, json.JSONDecodeError, RecursionError):
            issues.append("parameter_document_invalid")
        else:
            if parameters != parameter_document(compiled):
                issues.append("parameter_document_mismatch")

        try:
            report = json.loads(artifact_bytes["report.json"])
        except (UnicodeDecodeError, json.JSONDecodeError, RecursionError):
            issues.append("pipeline_report_invalid")
        else:
            expected = build_pipeline_report(pipeline, compiled, query_plan.steps, query_plan.sqlite_version)
            if report != expected:
                issues.append("pipeline_report_mismatch")

        try:
            reopened = explain_pipeline_sql(pipeline, compiled)
        except PipelineExplainError:
            issues.append("pipeline_sql_invalid")
        else:
            if reopened != query_plan:
                issues.append("query_plan_mismatch")
    except (OSError, TypeError, ValueError, RecursionError):
        issues.append("artifact_read_error")
    return tuple(sorted(set(issues)))


__all__ = ["REPORT_SCHEMA", "build_pipeline_report", "qa_pipeline_artifacts"]
