"""Typed contracts for deterministic Data SQL/Pipeline Builder IR."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

ColumnType = Literal["text", "integer", "decimal", "date", "boolean"]
PipelineOp = Literal["select", "filter_equals", "filter_null", "sort_by", "limit", "aggregate", "union_all"]
SortDirection = Literal["asc", "desc"]
MetricOp = Literal["count", "sum", "avg"]
CellValue = str | int | Decimal | date | bool | None


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class ColumnSpec(ContractModel):
    key: str = Field(min_length=1, max_length=63, pattern=r"^(?:_[\w]*|[^\W\d]\w*)$")
    label: str = Field(min_length=1, max_length=120)
    data_type: ColumnType
    source_name: str | None = Field(default=None, min_length=1, max_length=128)


class PipelineSource(ContractModel):
    table_name: str = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]{0,62}$")
    source_ref: str = Field(min_length=1, max_length=255)
    columns: list[ColumnSpec] = Field(min_length=1, max_length=100)


class PipelineMetric(ContractModel):
    alias: str = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]{0,62}$")
    op: MetricOp
    column: str | None = None


class PipelineNode(ContractModel):
    node_id: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_-]{0,49}$")
    parent_ids: list[str]
    op: PipelineOp
    columns: list[str] | None = Field(default=None, max_length=100)
    column: str | None = None
    value: CellValue = None
    direction: SortDirection | None = None
    group_by: list[str] | None = Field(default=None, max_length=100)
    metrics: list[PipelineMetric] | None = Field(default=None, max_length=100)
    limit: int | None = None


class PipelineRequest(ContractModel):
    schema_version: Literal["pipeline-request.v1"] = "pipeline-request.v1"
    pipeline_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,99}$")
    source: PipelineSource
    nodes: list[PipelineNode] = Field(min_length=1, max_length=20)
    output_node: str


class PipelineLineageRef(ContractModel):
    source_ref: str
    table_name: str
    column_key: str | None


class PipelineIR(ContractModel):
    schema_version: Literal["pipeline-ir.v1"] = "pipeline-ir.v1"
    pipeline_id: str
    source: PipelineSource
    ordered_nodes: list[PipelineNode]
    output_node: str
    schemas_by_node: dict[str, list[ColumnSpec]]
    output_columns: list[ColumnSpec]
    lineage_by_node: dict[str, dict[str, list[PipelineLineageRef]]]
    output_lineage: dict[str, list[PipelineLineageRef]]
    source_refs: list[str]


class ValidationIssue(ContractModel):
    code: str
    path: str
    message: str


__all__ = [
    "CellValue",
    "ColumnSpec",
    "ColumnType",
    "MetricOp",
    "PipelineIR",
    "PipelineLineageRef",
    "PipelineMetric",
    "PipelineNode",
    "PipelineOp",
    "PipelineRequest",
    "PipelineSource",
    "SortDirection",
    "ValidationIssue",
]
