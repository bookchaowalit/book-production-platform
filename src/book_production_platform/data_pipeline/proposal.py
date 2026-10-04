"""Deterministic PipelineIR proposal checks for Data Pipeline V1B.

An AI brief adapter (or a structured caller) may only propose an ordered list
of registered steps (source, select, filter with eq/gt/gte/lt/lte/ne, distinct,
aggregate, sort, limit; join is
registered in the spec but not supported by the V1A single-source engine)
over one existing table. It never sends SQL and never proposes a write. This
module rejects SQL/write text, keeps references only to existing tables and
columns, requires filter literals and limits to be stated in the question,
and compiles the steps into a ``pipeline-request.v1`` that the existing
validator, compiler, and in-memory ``EXPLAIN`` accept.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any, Literal

from book_production_platform.data_pipeline_ir.models import (
    ColumnType,
    PipelineIR,
    PipelineSource,
)
from book_production_platform.data_pipeline_ir.validation import (
    MAX_LIMIT_ROWS,
    PipelineValidationError,
    build_pipeline_ir,
)

from .pipeline import (
    CompiledPipeline,
    PipelineExplainError,
    compile_pipeline_ir,
    explain_pipeline_sql,
)

ProposalStatus = Literal["complete", "needs_input", "rejected"]
STEP_TYPES = ("source", "select", "filter", "distinct", "aggregate", "sort", "limit", "join")
FILTER_OPERATORS = ("eq", "gt", "gte", "lt", "lte", "ne")
MAX_STEPS = 12
WRITE_STEP_TYPES = frozenset(
    ("insert", "update", "delete", "drop", "write", "create", "alter", "merge", "upsert", "truncate", "replace", "save", "export")
)
SQL_TEXT = re.compile(
    r"\bselect\b[\s\S]*\bfrom\b|\b(?:insert\s+into|update\s+[\w\"`\[\]]+\s+set|delete\s+from|drop\s+(?:table|view|index|database)"
    r"|alter\s+table|create\s+(?:table|view|index|trigger)|truncate\s+table|replace\s+into|merge\s+into|attach\s+database"
    r"|detach\s+database|pragma\s+\w+|union\s+(?:all\s+)?select|grant\s+\w+|revoke\s+\w+)\b|;|--|/\*",
    re.IGNORECASE,
)
WRITE_TEXT = re.compile(
    r"\b(?:insert\s+into|update\s+[\w\"`\[\]]+\s+set|delete\s+from|drop\s+(?:table|view|index|database)|alter\s+table"
    r"|create\s+(?:table|view|index|trigger)|truncate(?:\s+table)?|replace\s+into|merge\s+into|attach\s+database|detach\s+database"
    r"|pragma\s+\w+|vacuum|grant\s+\w+|revoke\s+\w+)\b",
    re.IGNORECASE,
)
SQL_KEYS = frozenset(("sql", "query", "statement", "raw_sql", "where", "expression"))
_STEP_FIELDS: dict[str, tuple[frozenset[str], frozenset[str]]] = {
    "source": (frozenset({"table"}), frozenset()),
    "select": (frozenset({"columns"}), frozenset()),
    "filter": (frozenset({"column"}), frozenset({"value", "is_null", "operator"})),
    "distinct": (frozenset(), frozenset()),
    "aggregate": (frozenset({"group_by", "metrics"}), frozenset()),
    "sort": (frozenset({"column", "direction"}), frozenset()),
    "limit": (frozenset({"count"}), frozenset()),
    "join": (frozenset(), frozenset({"table", "on", "left_column", "right_column", "how", "columns"})),
}
_ALIAS = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,62}$")
Grounder = Callable[[Any, ColumnType, str, str], bool]


def _normalize_aliases(steps: list[Any]) -> list[Any]:
    """Rename metric aliases that are not ASCII identifiers to ``<op>_<column>``.

    An alias only names an output column; it carries no value. A model that
    names it in the question's language (for example Thai) would otherwise be
    rejected as ``step_invalid``. Later steps that refer to the old alias are
    rewritten to the new name. Anything that is not a well-formed aggregate is
    left untouched for the normal checks to judge.
    """

    renamed: dict[str, str] = {}
    taken: set[str] = set()
    out: list[Any] = []
    for step in steps:
        if not isinstance(step, Mapping):
            out.append(step)
            continue
        step = dict(step)
        if renamed:
            if isinstance(step.get("column"), str):
                step["column"] = renamed.get(step["column"], step["column"])
            for key in ("columns", "group_by"):
                if isinstance(step.get(key), list):
                    step[key] = [renamed.get(item, item) if isinstance(item, str) else item for item in step[key]]
        metrics = step.get("metrics")
        if isinstance(step.get("type"), str) and step["type"].strip().lower() == "aggregate" and isinstance(metrics, list):
            new_metrics: list[Any] = []
            for metric in metrics:
                if isinstance(metric, Mapping):
                    metric = dict(metric)
                    if renamed and isinstance(metric.get("column"), str):
                        metric["column"] = renamed.get(metric["column"], metric["column"])
                    alias, op = metric.get("alias"), metric.get("op")
                    if isinstance(alias, str) and alias and not _ALIAS.fullmatch(alias) and op in {"count", "sum", "avg"}:
                        column = metric.get("column")
                        base = f"{op}_{column}" if isinstance(column, str) and _ALIAS.fullmatch(f"{op}_{column}") else f"{op}_rows"
                        candidate, suffix = base, 2
                        while candidate in taken:
                            candidate, suffix = f"{base}_{suffix}", suffix + 1
                        renamed[alias] = candidate
                        metric["alias"] = candidate
                    if isinstance(metric.get("alias"), str):
                        taken.add(metric["alias"])
                new_metrics.append(metric)
            step["metrics"] = new_metrics
        out.append(step)
    return out


@dataclass
class ProposalIssue:
    code: str
    path: str

    def as_dict(self) -> dict[str, str]:
        return {"code": self.code, "path": self.path}


@dataclass
class PipelineProposalCheck:
    status: ProposalStatus
    steps: list[dict[str, Any]] | None = None
    request: dict[str, Any] | None = None
    missing: list[str] = field(default_factory=list)
    issues: list[ProposalIssue] = field(default_factory=list)
    sql_detected: bool = False
    write_detected: bool = False


def _strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, Mapping):
        return [text for key, item in value.items() for text in (*_strings(key), *_strings(item))]
    if isinstance(value, list):
        return [text for item in value for text in _strings(item)]
    return []


def _keys(value: Any) -> set[str]:
    if isinstance(value, Mapping):
        return {str(key).lower() for key in value} | {key for item in value.values() for key in _keys(item)}
    if isinstance(value, list):
        return {key for item in value for key in _keys(item)}
    return set()


def detect_sql(raw: Any) -> tuple[bool, bool]:
    """Return (sql_detected, write_detected) for any untrusted proposal object."""

    texts = _strings(raw)
    write = any(WRITE_TEXT.search(text) for text in texts)
    steps = raw.get("steps") if isinstance(raw, Mapping) else None
    if isinstance(steps, list):
        write = write or any(
            isinstance(step, Mapping) and isinstance(step.get("type"), str) and step["type"].strip().lower() in WRITE_STEP_TYPES
            for step in steps
        )
    sql = bool(_keys(raw) & SQL_KEYS) or any(SQL_TEXT.search(text) for text in texts)
    return sql or write, write


def _typed_value(value: Any, data_type: ColumnType) -> Any:
    """Convert a JSON literal to the strict type the PipelineIR validator expects; ValueError if impossible."""

    if isinstance(value, bool) != (data_type == "boolean"):
        raise ValueError("type")
    if data_type in {"text", "date"}:
        if not isinstance(value, str) or not value:
            raise ValueError("type")
        return value
    if data_type == "integer":
        if isinstance(value, int):
            return value
        if isinstance(value, str) and re.fullmatch(r"-?\d{1,18}", value.strip()):
            return int(value)
        raise ValueError("type")
    if data_type == "decimal":
        if isinstance(value, (int, float, str)):
            try:
                number = Decimal(str(value).strip())
            except InvalidOperation:
                raise ValueError("type") from None
            if number.is_finite():
                return number
        raise ValueError("type")
    return value


def check_proposal(
    raw: Mapping[str, Any],
    dataset: Mapping[str, Any],
    *,
    pipeline_id: str,
    question: str | None = None,
    grounded: Grounder | None = None,
) -> PipelineProposalCheck:
    """Validate an untrusted step proposal against the dataset schema and compile it.

    ``question``/``grounded`` are required for model proposals: filter literals
    and limits must be stated in the question. Structured callers pass ``None``.
    """

    sql, write = detect_sql(raw)
    if write:
        return PipelineProposalCheck("rejected", issues=[ProposalIssue("write_operation_forbidden", "proposal")], sql_detected=sql, write_detected=True)
    if sql:
        return PipelineProposalCheck("rejected", issues=[ProposalIssue("sql_forbidden", "proposal")], sql_detected=True)
    if set(raw) - {"table", "steps", "missing"}:
        return PipelineProposalCheck("rejected", issues=[ProposalIssue("proposal_field_not_allowed", "proposal")])
    model_missing = [item for item in raw.get("missing", []) if isinstance(item, str)] if isinstance(raw.get("missing"), list) else []
    try:
        source = PipelineSource.model_validate({key: dataset[key] for key in ("table_name", "source_ref", "columns")})
    except (KeyError, ValueError):
        return PipelineProposalCheck("rejected", issues=[ProposalIssue("dataset_schema_invalid", "dataset")])

    steps_raw = raw.get("steps", [])
    if not isinstance(steps_raw, list) or len(steps_raw) > MAX_STEPS:
        return PipelineProposalCheck("rejected", issues=[ProposalIssue("steps_invalid", "steps")])
    steps_raw = _normalize_aliases(steps_raw)
    steps: list[dict[str, Any]] = []
    for index, step in enumerate(steps_raw):
        path = f"steps[{index}]"
        if not isinstance(step, Mapping) or not isinstance(step.get("type"), str):
            return PipelineProposalCheck("rejected", issues=[ProposalIssue("step_invalid", path)])
        kind = step["type"].strip().lower()
        if kind not in _STEP_FIELDS:
            return PipelineProposalCheck("rejected", issues=[ProposalIssue("node_not_allowed", path)])
        if kind == "join":
            return PipelineProposalCheck("rejected", issues=[ProposalIssue("join_not_supported", path)])
        required, optional = _STEP_FIELDS[kind]
        keys = set(step) - {"type"}
        if not required <= keys or keys - required - optional:
            return PipelineProposalCheck("rejected", issues=[ProposalIssue("step_invalid", path)])
        steps.append({"type": kind, **{key: step[key] for key in sorted(keys)}})

    issues: list[ProposalIssue] = []
    missing = list(model_missing)
    table = raw.get("table")
    declared = [step["table"] for step in steps if step["type"] == "source"]
    if any(step["type"] == "source" for step in steps[1:]) or len(declared) > 1:
        return PipelineProposalCheck("rejected", issues=[ProposalIssue("step_invalid", "steps")])
    table = table if table is not None else (declared[0] if declared else None)
    if not isinstance(table, str) or not table:
        missing.append("table")
    elif table != source.table_name or (declared and declared[0] != table):
        issues.append(ProposalIssue("unknown_table", "table"))
    steps = [step for step in steps if step["type"] != "source"]
    if not steps:
        missing.append("steps")

    types: dict[str, ColumnType] = {column.key: column.data_type for column in source.columns}
    labels = {column.key: column.label for column in source.columns}
    nodes: list[dict[str, Any]] = []
    for index, step in enumerate(steps):
        path = f"steps[{index}]"
        node: dict[str, Any] = {"node_id": f"n{index + 1}", "parent_ids": [nodes[-1]["node_id"] if nodes else "source"]}
        kind = step["type"]
        if kind == "select":
            columns = step["columns"]
            if not isinstance(columns, list) or not columns or not all(isinstance(item, str) for item in columns):
                return PipelineProposalCheck("rejected", issues=[ProposalIssue("step_invalid", path)])
            if any(item not in types for item in columns):
                issues.append(ProposalIssue("unknown_column", path))
                break
            node.update(op="select", columns=list(columns))
            types = {item: types[item] for item in columns}
        elif kind == "filter":
            column = step["column"]
            if not isinstance(column, str) or column not in types:
                issues.append(ProposalIssue("unknown_column", path))
                break
            operator = step.get("operator", "eq")
            if "operator" in step and (operator not in FILTER_OPERATORS or "value" not in step or "is_null" in step):
                return PipelineProposalCheck("rejected", issues=[ProposalIssue("step_invalid", path)])
            if step.get("is_null") is True and "value" not in step:
                node.update(op="filter_null", column=column)
            elif "value" in step and "is_null" not in step:
                try:
                    value = _typed_value(step["value"], types[column])
                except ValueError:
                    issues.append(ProposalIssue("filter_value_type_mismatch", path))
                    break
                if question is not None and (grounded is None or not grounded(step["value"], types[column], question, labels.get(column, column))):
                    issues.append(ProposalIssue("filter_value_ungrounded", path))
                    missing.append(f"filter:{column}")
                    break
                if operator == "eq":
                    node.update(op="filter_equals", column=column, value=value)
                else:
                    node.update(op="filter_compare", column=column, operator=operator, value=value)
            else:
                return PipelineProposalCheck("rejected", issues=[ProposalIssue("step_invalid", path)])
        elif kind == "distinct":
            node.update(op="distinct")
        elif kind == "aggregate":
            group_by, metrics = step["group_by"], step["metrics"]
            if not isinstance(group_by, list) or not all(isinstance(item, str) for item in group_by) or not isinstance(metrics, list) or not metrics:
                return PipelineProposalCheck("rejected", issues=[ProposalIssue("step_invalid", path)])
            if any(item not in types for item in group_by):
                issues.append(ProposalIssue("unknown_column", path))
                break
            parsed_metrics: list[dict[str, Any]] = []
            new_types: dict[str, ColumnType] = {item: types[item] for item in group_by}
            for metric in metrics:
                if not isinstance(metric, Mapping) or set(metric) - {"alias", "op", "column"} or metric.get("op") not in {"count", "sum", "avg"}:
                    return PipelineProposalCheck("rejected", issues=[ProposalIssue("metric_not_allowed", path)])
                alias, column = metric.get("alias"), metric.get("column")
                if not isinstance(alias, str) or not _ALIAS.fullmatch(alias):
                    return PipelineProposalCheck("rejected", issues=[ProposalIssue("step_invalid", path)])
                if column is not None and (not isinstance(column, str) or column not in types):
                    issues.append(ProposalIssue("unknown_column", path))
                    break
                parsed_metrics.append({"alias": alias, "op": metric["op"], "column": column})
                new_types[alias] = "integer" if metric["op"] == "count" else ("decimal" if metric["op"] == "avg" or column is None else types[column])
            else:
                node.update(op="aggregate", group_by=list(group_by), metrics=parsed_metrics)
                types = new_types
                nodes.append(node)
                continue
            break
        elif kind == "sort":
            column, direction = step["column"], step["direction"]
            if direction not in {"asc", "desc"}:
                return PipelineProposalCheck("rejected", issues=[ProposalIssue("step_invalid", path)])
            if not isinstance(column, str) or column not in types:
                issues.append(ProposalIssue("unknown_column", path))
                break
            node.update(op="sort_by", column=column, direction=direction)
        else:  # limit
            count = step["count"]
            if isinstance(count, bool) or not isinstance(count, int) or not 1 <= count <= MAX_LIMIT_ROWS:
                return PipelineProposalCheck("rejected", issues=[ProposalIssue("step_invalid", path)])
            if question is not None and (grounded is None or not grounded(count, "integer", question, "")):
                issues.append(ProposalIssue("limit_ungrounded", path))
                missing.append("limit")
                break
            node.update(op="limit", limit=count)
        nodes.append(node)

    if issues or missing:
        return PipelineProposalCheck("needs_input", steps=steps, missing=sorted(set(missing)), issues=issues)
    request = {
        "schema_version": "pipeline-request.v1",
        "pipeline_id": pipeline_id,
        "source": source.model_dump(mode="python"),
        "nodes": nodes,
        "output_node": nodes[-1]["node_id"],
    }
    try:
        pipeline, compiled = compile_request(request)
        explain_pipeline_sql(pipeline, compiled)
    except PipelineValidationError as exc:
        return PipelineProposalCheck("rejected", steps=steps, issues=[ProposalIssue(issue.code, "pipeline") for issue in exc.issues])
    except PipelineExplainError as exc:
        return PipelineProposalCheck("rejected", steps=steps, issues=[ProposalIssue(exc.code, "pipeline")])
    return PipelineProposalCheck("complete", steps=steps, request=request)


def compile_request(request: Mapping[str, Any]) -> tuple[PipelineIR, CompiledPipeline]:
    pipeline = build_pipeline_ir(dict(request))
    return pipeline, compile_pipeline_ir(pipeline)


__all__ = [
    "FILTER_OPERATORS",
    "SQL_TEXT",
    "STEP_TYPES",
    "WRITE_TEXT",
    "PipelineProposalCheck",
    "ProposalIssue",
    "check_proposal",
    "compile_request",
    "detect_sql",
]
