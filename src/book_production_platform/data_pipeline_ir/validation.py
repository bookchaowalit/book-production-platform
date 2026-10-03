"""Graph, schema, type, and lineage validation for PipelineIR."""

from __future__ import annotations

import json
import re
from collections import defaultdict
from datetime import date
from decimal import Decimal
from typing import Any

from pydantic import ValidationError

from .models import (
    CellValue,
    ColumnSpec,
    ColumnType,
    PipelineIR,
    PipelineLineageRef,
    PipelineMetric,
    PipelineNode,
    PipelineRequest,
    PipelineSource,
    ValidationIssue,
)

MAX_PIPELINE_REQUEST_BYTES = 262_144
MAX_FILTER_TEXT_LENGTH = 2_048
MAX_LIMIT_ROWS = 10_000
ORDERED_TYPES = frozenset({"integer", "decimal", "date"})
_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class PipelineValidationError(ValueError):
    """Raised when a request cannot be represented by the supported PipelineIR."""

    def __init__(self, issues: list[ValidationIssue]) -> None:
        self.issues = issues
        super().__init__("; ".join(issue.code for issue in issues))


def _issue(code: str, path: str, message: str) -> PipelineValidationError:
    return PipelineValidationError([ValidationIssue(code=code, path=path, message=message)])


def _json_default(value: object) -> str:
    if isinstance(value, Decimal):
        return format(value, "f")
    if type(value) is date:
        return value.isoformat()
    raise TypeError("unsupported request value")


def _request_size(request: PipelineRequest | dict[str, Any]) -> int:
    payload: object = request.model_dump(mode="json") if isinstance(request, PipelineRequest) else request
    try:
        encoded = json.dumps(
            payload,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
            default=_json_default,
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeError) as error:
        raise _issue("invalid_pipeline_request", "request", "Request must contain JSON-compatible values") from error
    if len(encoded) > MAX_PIPELINE_REQUEST_BYTES:
        raise _issue("pipeline_request_too_large", "request", "Serialized request exceeds the size limit")
    return len(encoded)


def _parse_request(request: PipelineRequest | dict[str, Any]) -> PipelineRequest:
    _request_size(request)
    if isinstance(request, PipelineRequest):
        return request
    if not isinstance(request, dict):
        raise _issue("invalid_pipeline_request", "request", "Request must be an object")
    try:
        return PipelineRequest.model_validate(request)
    except ValidationError as error:
        errors = error.errors(include_input=False, include_context=False)
        locations = [item.get("loc", ()) for item in errors]
        if any(location and location[0] == "source" and len(location) > 1 for location in locations):
            code = "invalid_pipeline_source_schema"
            path = "source"
            message = "Source table schema is invalid"
        elif any(len(location) > 2 and location[0] == "nodes" and location[2] == "value" for location in locations):
            code = "pipeline_type_mismatch"
            path = "nodes.value"
            message = "Filter value must be a supported scalar"
        elif any(location and location[0] == "nodes" and len(location) > 1 for location in locations):
            code = "invalid_pipeline_operation_fields"
            path = "nodes"
            message = "Pipeline node fields are invalid"
        else:
            code = "invalid_pipeline_request"
            path = "request"
            message = "Request does not match pipeline-request.v1"
        raise _issue(code, path, message) from error


def _validate_source_schema(source: PipelineSource) -> None:
    keys = [column.key for column in source.columns]
    source_names = [column.source_name or column.key for column in source.columns]
    if len(set(keys)) != len(keys) or len(set(source_names)) != len(source_names):
        raise _issue("invalid_pipeline_source_schema", "source.columns", "Source keys and names must be unique")


def _validate_graph_shape(request: PipelineRequest) -> dict[str, PipelineNode]:
    nodes_by_id: dict[str, PipelineNode] = {}
    for node in request.nodes:
        if node.node_id == "source":
            raise _issue("reserved_pipeline_node_id", "nodes", "The node id source is reserved")
        if node.node_id in nodes_by_id:
            raise _issue("duplicate_pipeline_node_id", "nodes", "Node ids must be unique")
        nodes_by_id[node.node_id] = node

    for node in request.nodes:
        if len(node.parent_ids) != len(set(node.parent_ids)):
            raise _issue("duplicate_pipeline_parent", f"nodes.{node.node_id}.parent_ids", "Parent ids must be unique")
        expected_parents = 2 if node.op == "union_all" else 1
        if len(node.parent_ids) != expected_parents:
            raise _issue(
                "invalid_pipeline_operation_fields",
                f"nodes.{node.node_id}.parent_ids",
                "Operation has an invalid number of parents",
            )
        for parent_id in node.parent_ids:
            if parent_id != "source" and parent_id not in nodes_by_id:
                raise _issue("unknown_pipeline_parent", f"nodes.{node.node_id}.parent_ids", "Parent node is unknown")

    if request.output_node not in nodes_by_id:
        raise _issue("unknown_output_node", "output_node", "Output node is unknown")
    return nodes_by_id


def _topological_order(nodes: list[PipelineNode]) -> list[PipelineNode]:
    nodes_by_id = {node.node_id: node for node in nodes}
    child_ids: dict[str, list[str]] = defaultdict(list)
    indegrees: dict[str, int] = {}
    for node in nodes:
        operation_parents = [parent for parent in node.parent_ids if parent != "source"]
        indegrees[node.node_id] = len(operation_parents)
        for parent_id in operation_parents:
            child_ids[parent_id].append(node.node_id)

    ready = sorted(node_id for node_id, count in indegrees.items() if count == 0)
    ordered: list[PipelineNode] = []
    while ready:
        node_id = ready.pop(0)
        ordered.append(nodes_by_id[node_id])
        for child_id in sorted(child_ids[node_id]):
            indegrees[child_id] -= 1
            if indegrees[child_id] == 0:
                ready.append(child_id)
                ready.sort()
    if len(ordered) != len(nodes):
        raise _issue("pipeline_cycle", "nodes", "Pipeline graph contains a cycle")
    return ordered


def _validate_output_reachability(output_node: str, ordered_nodes: list[PipelineNode]) -> None:
    nodes_by_id = {node.node_id: node for node in ordered_nodes}
    reachable: set[str] = set()
    pending = [output_node]
    while pending:
        node_id = pending.pop()
        if node_id in reachable:
            continue
        reachable.add(node_id)
        pending.extend(parent for parent in nodes_by_id[node_id].parent_ids if parent != "source")
    if reachable != set(nodes_by_id):
        raise _issue("pipeline_dead_node", "nodes", "Every declared node must reach the output")


def _validate_operation_fields(node: PipelineNode) -> None:
    allowed = {
        "select": {"columns"},
        "filter_equals": {"column", "value"},
        "filter_compare": {"column", "operator", "value"},
        "filter_null": {"column"},
        "distinct": set(),
        "sort_by": {"column", "direction"},
        "limit": {"limit"},
        "aggregate": {"group_by", "metrics"},
        "union_all": set(),
    }[node.op]
    common = {"node_id", "parent_ids", "op"}
    supplied = node.model_fields_set - common
    required = {
        "select": {"columns"},
        "filter_equals": {"column", "value"},
        "filter_compare": {"column", "operator", "value"},
        "filter_null": {"column"},
        "distinct": set(),
        "sort_by": {"column", "direction"},
        "limit": {"limit"},
        "aggregate": {"group_by", "metrics"},
        "union_all": set(),
    }[node.op]
    if not supplied <= allowed or not required <= supplied:
        raise _issue(
            "invalid_pipeline_operation_fields",
            f"nodes.{node.node_id}",
            "Operation fields are missing or not allowed",
        )
    if node.op == "select" and (not node.columns or len(node.columns) > 100 or len(set(node.columns)) != len(node.columns)):
        raise _issue("invalid_pipeline_operation_fields", f"nodes.{node.node_id}.columns", "Select columns must be unique and non-empty")
    if node.op in {"filter_equals", "filter_compare"} and node.value is None:
        raise _issue("invalid_pipeline_operation_fields", f"nodes.{node.node_id}.value", f"{node.op} requires a non-null value")
    if node.op == "aggregate":
        if node.group_by is None or len(node.group_by) > 100 or len(set(node.group_by)) != len(node.group_by):
            raise _issue("invalid_pipeline_operation_fields", f"nodes.{node.node_id}.group_by", "group_by must contain unique columns")
        if not node.metrics or len(node.metrics) > 100:
            raise _issue("invalid_pipeline_operation_fields", f"nodes.{node.node_id}.metrics", "aggregate requires metrics")
    if node.op == "limit" and (type(node.limit) is not int or not 1 <= node.limit <= MAX_LIMIT_ROWS):
        raise _issue("invalid_pipeline_operation_fields", f"nodes.{node.node_id}.limit", "limit must be between 1 and 10000")


def _find_column(columns: list[ColumnSpec], key: str, node: PipelineNode) -> ColumnSpec:
    found = next((column for column in columns if column.key == key), None)
    if found is None:
        raise _issue("unknown_pipeline_column", f"nodes.{node.node_id}", "Operation refers to an unknown column")
    return found


def _typed_filter_value(value: CellValue, data_type: ColumnType, node: PipelineNode) -> CellValue:
    path = f"nodes.{node.node_id}.value"
    if data_type == "text" and type(value) is str:
        if len(value) > MAX_FILTER_TEXT_LENGTH:
            raise _issue("invalid_pipeline_operation_fields", path, "Text filter value exceeds 2048 characters")
        return value
    if data_type == "integer" and type(value) is int:
        return value
    if data_type == "decimal" and type(value) is int:
        return Decimal(value)
    if data_type == "decimal" and isinstance(value, Decimal) and value.is_finite():
        return value
    if data_type == "date":
        if type(value) is date:
            return value
        if type(value) is str and _ISO_DATE.fullmatch(value):
            try:
                parsed = date.fromisoformat(value)
            except ValueError as error:
                raise _issue("pipeline_type_mismatch", path, "Date filter must be a valid ISO date") from error
            if parsed.isoformat() == value:
                return parsed
    if data_type == "boolean" and type(value) is bool:
        return value
    raise _issue("pipeline_type_mismatch", path, "Filter value does not match the declared column type")


def _check_columns(columns: list[ColumnSpec], keys: list[str], node: PipelineNode) -> list[ColumnSpec]:
    return [_find_column(columns, key, node) for key in keys]


def _propagate_schemas(
    source_columns: list[ColumnSpec], ordered_nodes: list[PipelineNode]
) -> dict[str, list[ColumnSpec]]:
    schemas: dict[str, list[ColumnSpec]] = {}
    for index, node in enumerate(ordered_nodes):
        _validate_operation_fields(node)
        parents = [schemas[parent] if parent != "source" else source_columns for parent in node.parent_ids]
        parent_columns = parents[0]
        if node.op == "select":
            output = _check_columns(parent_columns, node.columns or [], node)
        elif node.op in {"filter_equals", "filter_compare", "filter_null", "distinct", "sort_by", "limit"}:
            _find_column(parent_columns, node.column, node) if node.column is not None else None
            if node.op in {"filter_equals", "filter_compare"} and node.column is not None:
                column = _find_column(parent_columns, node.column, node)
                if node.op == "filter_compare" and node.operator != "ne" and column.data_type not in ORDERED_TYPES:
                    raise _issue("pipeline_type_mismatch", f"nodes.{node.node_id}.operator", "Ordered comparisons need integer, decimal, or date columns")
                node = node.model_copy(update={"value": _typed_filter_value(node.value, column.data_type, node)})
                ordered_nodes[index] = node
            output = list(parent_columns)
        elif node.op == "aggregate":
            groups = _check_columns(parent_columns, node.group_by or [], node)
            metrics = node.metrics or []
            aliases = [metric.alias for metric in metrics]
            if len(set(aliases)) != len(aliases) or set(aliases).intersection(item.key for item in groups):
                raise _issue("invalid_pipeline_operation_fields", f"nodes.{node.node_id}.metrics", "Metric aliases must be unique")
            output = list(groups)
            for metric in metrics:
                output.append(_metric_column(parent_columns, metric, node))
        elif node.op == "union_all":
            right = parents[1]
            left_signature = [(column.key, column.data_type) for column in parent_columns]
            right_signature = [(column.key, column.data_type) for column in right]
            if left_signature != right_signature:
                raise _issue("union_schema_mismatch", f"nodes.{node.node_id}", "union_all parent schemas must match in order and type")
            output = list(parent_columns)
        else:
            raise _issue("invalid_pipeline_operation_fields", f"nodes.{node.node_id}.op", "Operation is not supported")
        schemas[node.node_id] = output
    return schemas


def _metric_column(parent_columns: list[ColumnSpec], metric: PipelineMetric, node: PipelineNode) -> ColumnSpec:
    if metric.op == "count":
        if metric.column is not None:
            _find_column(parent_columns, metric.column, node)
        data_type: ColumnType = "integer"
    else:
        if metric.column is None:
            raise _issue("invalid_pipeline_operation_fields", f"nodes.{node.node_id}.metrics", "sum and avg require a column")
        source_column = _find_column(parent_columns, metric.column, node)
        if source_column.data_type not in {"integer", "decimal"}:
            raise _issue("pipeline_type_mismatch", f"nodes.{node.node_id}.metrics", "sum and avg require numeric columns")
        data_type = source_column.data_type if metric.op == "sum" else "decimal"
    return ColumnSpec(key=metric.alias, label=metric.alias, data_type=data_type)


def _propagate_lineage(
    source: PipelineSource, ordered_nodes: list[PipelineNode]
) -> dict[str, dict[str, list[PipelineLineageRef]]]:
    lineage: dict[str, dict[str, list[PipelineLineageRef]]] = {}
    for node in ordered_nodes:
        parents = [lineage[parent] if parent != "source" else _source_lineage(source) for parent in node.parent_ids]
        parent_lineage = parents[0]
        if node.op == "select":
            current = {key: list(parent_lineage[key]) for key in node.columns or []}
        elif node.op in {"filter_equals", "filter_compare", "filter_null", "distinct", "sort_by", "limit"}:
            current = {key: list(refs) for key, refs in parent_lineage.items()}
        elif node.op == "aggregate":
            current = {key: list(parent_lineage[key]) for key in node.group_by or []}
            for metric in node.metrics or []:
                if metric.column is None:
                    current[metric.alias] = [
                        PipelineLineageRef(source_ref=source.source_ref, table_name=source.table_name, column_key=None)
                    ]
                else:
                    current[metric.alias] = list(parent_lineage[metric.column])
        else:
            current = {}
            for key in parent_lineage:
                refs = [*parents[0][key], *parents[1][key]]
                identities: set[tuple[str, str, str | None]] = set()
                merged_refs: list[PipelineLineageRef] = []
                for ref in refs:
                    identity = (ref.source_ref, ref.table_name, ref.column_key)
                    if identity not in identities:
                        identities.add(identity)
                        merged_refs.append(ref)
                current[key] = merged_refs
        lineage[node.node_id] = current
    return lineage


def _source_lineage(source: PipelineSource) -> dict[str, list[PipelineLineageRef]]:
    return {
        column.key: [
            PipelineLineageRef(source_ref=source.source_ref, table_name=source.table_name, column_key=column.key)
        ]
        for column in source.columns
    }


def _validate_terminal_order(output_node: str, ordered_nodes: list[PipelineNode]) -> None:
    nodes_by_id = {node.node_id: node for node in ordered_nodes}
    for node in ordered_nodes:
        if node.op == "limit":
            parent_id = node.parent_ids[0]
            if node.node_id != output_node or parent_id == "source" or nodes_by_id[parent_id].op != "sort_by":
                raise _issue("nondeterministic_limit", f"nodes.{node.node_id}", "limit must be the output directly after sort_by")
        if node.op == "sort_by":
            is_output = node.node_id == output_node
            is_limit_parent = (
                node.node_id in nodes_by_id[output_node].parent_ids
                and nodes_by_id[output_node].op == "limit"
            )
            if not is_output and not is_limit_parent:
                raise _issue("nondeterministic_limit", f"nodes.{node.node_id}", "sort_by must be output or parent of terminal limit")


def build_pipeline_ir(request: PipelineRequest | dict[str, Any]) -> PipelineIR:
    """Validate a bounded transform graph and derive its schema and lineage."""
    parsed = _parse_request(request)
    _validate_source_schema(parsed.source)
    _validate_graph_shape(parsed)
    ordered_nodes = _topological_order(parsed.nodes)
    _validate_output_reachability(parsed.output_node, ordered_nodes)
    _validate_terminal_order(parsed.output_node, ordered_nodes)
    schemas_by_node = _propagate_schemas(parsed.source.columns, ordered_nodes)
    lineage_by_node = _propagate_lineage(parsed.source, ordered_nodes)
    return PipelineIR(
        pipeline_id=parsed.pipeline_id,
        source=parsed.source,
        ordered_nodes=ordered_nodes,
        output_node=parsed.output_node,
        schemas_by_node=schemas_by_node,
        output_columns=schemas_by_node[parsed.output_node],
        lineage_by_node=lineage_by_node,
        output_lineage=lineage_by_node[parsed.output_node],
        source_refs=[parsed.source.source_ref],
    )


__all__ = ["PipelineValidationError", "build_pipeline_ir"]
