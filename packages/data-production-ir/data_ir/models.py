"""Pydantic models for the versioned Data + Data Viz V1A contract."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

ColumnType = Literal["text", "integer", "decimal", "date", "boolean"]
TransformOp = Literal["trim_text", "fill_null", "filter_equals", "sort_by"]
SortDirection = Literal["asc", "desc"]
ChartType = Literal["bar", "line", "horizontal_bar", "pie"]
ChartSort = Literal["none", "value_desc", "value_asc", "category_asc", "category_desc"]
Aggregation = Literal["sum", "count", "avg"]
CellValue = str | int | Decimal | date | bool | None


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ColumnSpec(ContractModel):
    key: str = Field(pattern=r"^(?:_[\w]*|[^\W\d]\w*)$")
    label: str
    data_type: ColumnType
    source_name: str | None = None
    required: bool = False


class TransformSpec(ContractModel):
    op: TransformOp
    column: str | None = None
    value: Any = None
    direction: SortDirection = "asc"


class ChartSpec(ContractModel):
    chart_type: ChartType = "bar"
    x_column: str
    y_column: str | None = None
    aggregation: Aggregation = "count"
    title: str = "Chart"
    x_axis_label: str | None = Field(default=None, min_length=1, max_length=120)
    y_axis_label: str | None = Field(default=None, min_length=1, max_length=120)
    value_unit: str | None = Field(default=None, min_length=1, max_length=32)
    width: int = Field(default=720, ge=240, le=4096)
    height: int = Field(default=420, ge=180, le=4096)
    # Display controls added in recipe v4. Defaults reproduce the v3 output
    # exactly, and the job hash omits them while they are at their defaults.
    sort: ChartSort = "none"
    top_n: int | None = Field(default=None, ge=1, le=50)
    group_rest_as: str | None = Field(default=None, min_length=1, max_length=40)
    value_decimals: int | None = Field(default=None, ge=0, le=6)
    thousands_separator: bool = False
    data_labels: bool = False


CHART_DISPLAY_DEFAULTS: dict[str, Any] = {
    "sort": "none",
    "top_n": None,
    "group_rest_as": None,
    "value_decimals": None,
    "thousands_separator": False,
    "data_labels": False,
}


class DatasetRequest(ContractModel):
    schema_version: Literal["dataset-request.v1"] = "dataset-request.v1"
    dataset_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,99}$")
    title: str = "Dataset"
    csv_text: str = Field(min_length=1, max_length=2_000_000)
    delimiter: Literal[",", ";", "\t"] = ","
    columns: list[ColumnSpec] | None = Field(default=None, max_length=100)
    transforms: list[TransformSpec] = Field(default_factory=list, max_length=20)
    chart: ChartSpec | None = None
    source_ref: str = "input.csv"


class DatasetIR(ContractModel):
    schema_version: Literal["dataset-ir.v1"] = "dataset-ir.v1"
    dataset_id: str
    title: str
    source_ref: str
    columns: list[ColumnSpec]
    rows: list[dict[str, CellValue]]
    lineage: dict[str, str]
    transform_log: list[str] = Field(default_factory=list)
    assumptions: dict[str, str] = Field(default_factory=dict)


class ChartIR(ContractModel):
    schema_version: Literal["chart-ir.v1"] = "chart-ir.v1"
    dataset_id: str
    spec: ChartSpec
    categories: list[str]
    values: list[Decimal]
    source_refs: list[str]
    aggregate_total: Decimal
    row_count: int
    folded_categories: list[str] = Field(default_factory=list)


class ArtifactRef(ContractModel):
    filename: str
    media_type: str
    content_hash: str
    byte_size: int


class ValidationIssue(ContractModel):
    code: str
    path: str
    message: str


__all__ = [
    "CHART_DISPLAY_DEFAULTS",
    "Aggregation",
    "ArtifactRef",
    "CellValue",
    "ChartIR",
    "ChartSort",
    "ChartSpec",
    "ChartType",
    "ColumnSpec",
    "ColumnType",
    "DatasetIR",
    "DatasetRequest",
    "SortDirection",
    "TransformOp",
    "TransformSpec",
    "ValidationIssue",
]
