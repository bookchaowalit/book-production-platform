"""Compile validated PipelineIR graphs into parameterized SQLite SELECT plans."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any

from book_production_platform.data_pipeline_ir.models import (
    CellValue,
    ColumnSpec,
    ColumnType,
    PipelineIR,
    PipelineLineageRef,
    PipelineNode,
)

COMPARE_SQL: dict[str, str] = {"gt": ">", "gte": ">=", "lt": "<", "lte": "<=", "ne": "<>"}


@dataclass(frozen=True)
class CompiledPipeline:
    pipeline_id: str
    dialect: str
    sql: str
    parameters: list[CellValue]
    parameter_types: list[ColumnType]
    output_node: str
    output_columns: list[ColumnSpec]
    output_lineage: dict[str, list[PipelineLineageRef]]
    source_refs: list[str]
    model_calls: int = 0


@dataclass(frozen=True)
class ExplainStep:
    node_id: int
    parent_id: int
    detail: str


@dataclass(frozen=True)
class ExplainResult:
    sqlite_version: str
    steps: tuple[ExplainStep, ...]


class PipelineExplainError(ValueError):
    """A stable, non-sensitive SQL planner error."""

    code = "pipeline_sql_invalid"

    def __init__(self) -> None:
        super().__init__(self.code)


def _quote_identifier(identifier: str) -> str:
    """Quote a SQLite identifier, escaping quotes even for defensive callers."""
    return '"' + identifier.replace('"', '""') + '"'


def _parent_columns(pipeline: PipelineIR, node: PipelineNode) -> list[ColumnSpec]:
    parent_id = node.parent_ids[0]
    return pipeline.source.columns if parent_id == "source" else pipeline.schemas_by_node[parent_id]


def _source_for_node(pipeline: PipelineIR, node: PipelineNode, cte_names: dict[str, str]) -> str:
    parent_id = node.parent_ids[0]
    return _source_for_parent(pipeline, parent_id, cte_names)


def _source_for_parent(pipeline: PipelineIR, parent_id: str, cte_names: dict[str, str]) -> str:
    return _quote_identifier(pipeline.source.table_name) if parent_id == "source" else _quote_identifier(cte_names[parent_id])


def _sort_terms(sort_node: PipelineNode, columns: list[ColumnSpec]) -> list[str]:
    sort_key = sort_node.column or ""
    keys = [sort_key, *sorted(column.key for column in columns if column.key != sort_key)]
    return [
        f"{_quote_identifier(key)} {str(sort_node.direction).upper() if key == sort_key else 'ASC'}"
        for key in keys
    ]


def _compile_nodes(
    pipeline: PipelineIR,
) -> tuple[list[str], list[CellValue], list[ColumnType], dict[str, str]]:
    ctes: list[str] = []
    parameters: list[CellValue] = []
    parameter_types: list[ColumnType] = []
    cte_names = {node.node_id: f"step_{index}" for index, node in enumerate(pipeline.ordered_nodes)}

    for node in pipeline.ordered_nodes:
        cte_name = _quote_identifier(cte_names[node.node_id])
        parent = _source_for_node(pipeline, node, cte_names)
        if node.op == "select":
            selected = ", ".join(_quote_identifier(key) for key in node.columns or [])
            body = f"SELECT {selected} FROM {parent}"
        elif node.op == "filter_equals":
            parent_columns = _parent_columns(pipeline, node)
            column = next(column for column in parent_columns if column.key == node.column)
            # Decimal parameters are bound as exact text; CAST keeps equality
            # numeric on aggregate outputs, which carry no column affinity.
            placeholder = "CAST(? AS NUMERIC)" if column.data_type == "decimal" else "?"
            body = f"SELECT * FROM {parent} WHERE {_quote_identifier(column.key)} = {placeholder}"
            parameters.append(node.value)
            parameter_types.append(column.data_type)
        elif node.op == "filter_compare":
            parent_columns = _parent_columns(pipeline, node)
            column = next(column for column in parent_columns if column.key == node.column)
            # Decimal parameters are bound as exact text; CAST keeps the
            # comparison numeric even on aggregate outputs without affinity.
            placeholder = "CAST(? AS NUMERIC)" if column.data_type == "decimal" else "?"
            body = f"SELECT * FROM {parent} WHERE {_quote_identifier(column.key)} {COMPARE_SQL[str(node.operator)]} {placeholder}"
            parameters.append(node.value)
            parameter_types.append(column.data_type)
        elif node.op == "filter_null":
            body = f"SELECT * FROM {parent} WHERE {_quote_identifier(node.column or '')} IS NULL"
        elif node.op == "distinct":
            body = f"SELECT DISTINCT * FROM {parent}"
        elif node.op == "sort_by":
            # A sort feeding a terminal limit is applied on that limit node so
            # its tie-breakers govern both row selection and final presentation.
            body = f"SELECT * FROM {parent}"
        elif node.op == "limit":
            sort_node = next(parent_node for parent_node in pipeline.ordered_nodes if parent_node.node_id == node.parent_ids[0])
            parent_columns = pipeline.schemas_by_node[sort_node.node_id]
            order_terms = _sort_terms(sort_node, parent_columns)
            order_clause = ", ".join(order_terms)
            body = f"SELECT * FROM {parent} ORDER BY {order_clause} LIMIT ?"
            parameters.append(node.limit)
            parameter_types.append("integer")
        elif node.op == "aggregate":
            selections = [_quote_identifier(key) for key in node.group_by or []]
            for metric in node.metrics or []:
                if metric.op == "count":
                    expression = "COUNT(*)" if metric.column is None else f"COUNT({_quote_identifier(metric.column)})"
                elif metric.op == "sum":
                    expression = f"SUM({_quote_identifier(metric.column or '')})"
                else:
                    expression = f"AVG({_quote_identifier(metric.column or '')})"
                selections.append(f"{expression} AS {_quote_identifier(metric.alias)}")
            body = f"SELECT {', '.join(selections)} FROM {parent}"
            if node.group_by:
                body += " GROUP BY " + ", ".join(_quote_identifier(key) for key in node.group_by)
        else:  # union_all is the only remaining validated operation.
            left, right = node.parent_ids
            left_source = _source_for_parent(pipeline, left, cte_names)
            right_source = _source_for_parent(pipeline, right, cte_names)
            body = f"SELECT * FROM {left_source} UNION ALL SELECT * FROM {right_source}"

        ctes.append(f"{cte_name} AS ({body})")

    return ctes, parameters, parameter_types, cte_names


def _output_order(pipeline: PipelineIR) -> list[str]:
    nodes_by_id = {node.node_id: node for node in pipeline.ordered_nodes}
    output = nodes_by_id[pipeline.output_node]
    if output.op == "sort_by":
        return [f"{_quote_identifier(output.column or '')} {str(output.direction).upper()}"]
    if output.op == "limit":
        sort_node = nodes_by_id[output.parent_ids[0]]
        return _sort_terms(sort_node, pipeline.schemas_by_node[sort_node.node_id])
    return []


def compile_pipeline_ir(pipeline: PipelineIR) -> CompiledPipeline:
    """Compile the allowlisted graph without executing it or interpolating values."""
    ctes, parameters, parameter_types, cte_names = _compile_nodes(pipeline)
    final_order = _output_order(pipeline)
    sql = f"WITH {', '.join(ctes)} SELECT * FROM {_quote_identifier(cte_names[pipeline.output_node])}"
    if final_order:
        sql += " ORDER BY " + ", ".join(final_order)
    return CompiledPipeline(
        pipeline_id=pipeline.pipeline_id,
        dialect="sqlite",
        sql=sql,
        parameters=parameters,
        parameter_types=parameter_types,
        output_node=pipeline.output_node,
        output_columns=pipeline.output_columns,
        output_lineage=pipeline.output_lineage,
        source_refs=pipeline.source_refs,
        model_calls=0,
    )


def _json_parameter(value: CellValue, data_type: ColumnType) -> str | int | bool:
    if data_type == "decimal" and isinstance(value, Decimal) and value.is_finite():
        return format(value, "f")
    if data_type == "date" and type(value) is date:
        return value.isoformat()
    if data_type == "boolean" and type(value) is bool:
        return value
    if data_type == "integer" and type(value) is int:
        return value
    if data_type == "text" and type(value) is str:
        return value
    raise ValueError("pipeline_parameter_type_mismatch")


def parameter_document(compiled: CompiledPipeline) -> dict[str, Any]:
    """Return the separate, typed JSON artifact for bound parameters."""
    if len(compiled.parameters) != len(compiled.parameter_types):
        raise ValueError("pipeline_parameter_type_mismatch")
    return {
        "schema_version": "pipeline-parameters.v1",
        "parameters": [
            {"type": data_type, "value": _json_parameter(value, data_type)}
            for value, data_type in zip(compiled.parameters, compiled.parameter_types, strict=True)
        ],
    }


def _sqlite_parameter(value: CellValue) -> str | int | float | bytes | None:
    if isinstance(value, Decimal):
        return format(value, "f")
    if type(value) is date:
        return value.isoformat()
    if type(value) is bool:
        return int(value)
    if isinstance(value, str):
        return value
    if type(value) is int:
        return value
    if value is None:
        return None
    raise TypeError("invalid pipeline parameter")


def _sqlite_type(data_type: ColumnType) -> str:
    return {
        "text": "TEXT",
        "integer": "INTEGER",
        "decimal": "NUMERIC",
        "date": "TEXT",
        "boolean": "INTEGER",
    }[data_type]


def explain_pipeline_sql(pipeline: PipelineIR, compiled: CompiledPipeline) -> ExplainResult:
    """Validate a compiled query with in-memory DDL and EXPLAIN only."""
    connection: sqlite3.Connection | None = None
    try:
        connection = sqlite3.connect(":memory:")
        columns = ", ".join(
            f"{_quote_identifier(column.key)} {_sqlite_type(column.data_type)}"
            for column in pipeline.source.columns
        )
        connection.execute(f"CREATE TABLE {_quote_identifier(pipeline.source.table_name)} ({columns})")
        parameters = [_sqlite_parameter(value) for value in compiled.parameters]
        rows = connection.execute(f"EXPLAIN QUERY PLAN {compiled.sql}", parameters).fetchall()
        steps = tuple(ExplainStep(node_id=row[0], parent_id=row[1], detail=row[3]) for row in rows)
        if not steps:
            raise sqlite3.DatabaseError("empty query plan")
        return ExplainResult(sqlite_version=sqlite3.sqlite_version, steps=steps)
    except (sqlite3.Error, IndexError, TypeError, ValueError):
        raise PipelineExplainError() from None
    finally:
        if connection is not None:
            connection.close()


__all__ = [
    "COMPARE_SQL",
    "CompiledPipeline",
    "ExplainResult",
    "ExplainStep",
    "PipelineExplainError",
    "compile_pipeline_ir",
    "explain_pipeline_sql",
    "parameter_document",
]
