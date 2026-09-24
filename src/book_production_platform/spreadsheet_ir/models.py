"""Pydantic models for the versioned Spreadsheet V1A contract."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

ColumnType = Literal["text", "integer", "decimal", "date", "boolean"]
CellType = Literal["text", "integer", "decimal", "date", "boolean", "null", "formula"]


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class ColumnSpec(ContractModel):
    key: str = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")
    label: str
    data_type: ColumnType
    required: bool = False


class TableRequest(ContractModel):
    name: str = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")
    columns: list[ColumnSpec] = Field(min_length=1, max_length=100)
    rows: list[dict[str, Any]] = Field(max_length=1000)
    source_ref: str = "input"


class FormulaSpec(ContractModel):
    target: str = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]*![A-Z]{1,3}[1-9][0-9]*$")
    expression: str = Field(min_length=2)
    result_type: Literal["integer", "decimal", "text", "boolean"] = "decimal"
    source_ref: str = "recipe"


class WorkbookRequest(ContractModel):
    schema_version: Literal["workbook-request.v1"] = "workbook-request.v1"
    workbook_id: str
    title: str = "Workbook"
    locale: str = "en-US"
    tables: list[TableRequest] = Field(min_length=1, max_length=20)
    formulas: list[FormulaSpec] = Field(default_factory=list, max_length=100)
    template_parameters: dict[str, str | int | float | Decimal | date | None] = Field(default_factory=dict)


CellValue = str | int | Decimal | date | bool | None


class CellIR(ContractModel):
    address: str = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]*![A-Z]{1,3}[1-9][0-9]*$")
    data_type: CellType
    value: CellValue
    formula: str | None = None
    source_ref: str = "input"


class SheetIR(ContractModel):
    name: str = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")
    cells: list[CellIR] = Field(default_factory=list)
    column_labels: list[str] = Field(default_factory=list)


class WorkbookIR(ContractModel):
    schema_version: Literal["workbook-ir.v1"] = "workbook-ir.v1"
    workbook_id: str
    title: str
    locale: str
    sheets: list[SheetIR]
    formulas: list[FormulaSpec] = Field(default_factory=list)
    named_ranges: dict[str, str] = Field(default_factory=dict)
    source_lineage: dict[str, str] = Field(default_factory=dict)
    assumptions: dict[str, str] = Field(default_factory=dict)


class ArtifactRef(ContractModel):
    filename: str
    media_type: str
    content_hash: str
    byte_size: int


class ValidationIssue(ContractModel):
    code: str
    path: str
    message: str
