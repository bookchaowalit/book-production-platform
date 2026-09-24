"""Typed input validation and coercion for WorkbookIR."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

from pydantic import ValidationError

from .models import ColumnType, ValidationIssue, WorkbookRequest


def _issue(code: str, path: str, message: str) -> ValidationIssue:
    return ValidationIssue(code=code, path=path, message=message)


def parse_typed_value(value: Any, data_type: ColumnType, *, path: str = "value") -> str | int | Decimal | date | bool | None:
    """Coerce one value without turning formula-like text into a formula."""

    if value is None or (value == "" and data_type != "text"):
        return None
    if data_type == "text":
        return str(value)
    if data_type == "integer":
        if isinstance(value, bool):
            raise ValueError(f"{path}: integer_expected")
        try:
            parsed = int(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{path}: integer_expected") from exc
        if str(value).strip() not in {str(parsed), f"+{parsed}", f"{parsed}.0"}:
            raise ValueError(f"{path}: integer_expected")
        return parsed
    if data_type == "decimal":
        try:
            return Decimal(str(value))
        except (InvalidOperation, ValueError) as exc:
            raise ValueError(f"{path}: decimal_expected") from exc
    if data_type == "date":
        try:
            return value if isinstance(value, date) else date.fromisoformat(str(value))
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{path}: iso_date_expected") from exc
    if data_type == "boolean":
        if isinstance(value, bool):
            return value
        normalized = str(value).strip().lower()
        if normalized in {"true", "1", "yes"}:
            return True
        if normalized in {"false", "0", "no"}:
            return False
        raise ValueError(f"{path}: boolean_expected")
    raise ValueError(f"{path}: unsupported_type")


def _validate_table(table: Any, index: int) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    keys = [column.key for column in table.columns]
    if len(set(keys)) != len(keys):
        issues.append(_issue("duplicate_column", f"tables[{index}].columns", "column keys must be unique"))
    known = set(keys)
    for row_index, row in enumerate(table.rows):
        for key in row:
            if key not in known:
                issues.append(_issue("unknown_column", f"tables[{index}].rows[{row_index}].{key}", "row key is not declared"))
        for column in table.columns:
            if column.required and (column.key not in row or row[column.key] is None or row[column.key] == ""):
                issues.append(_issue("required_value_missing", f"tables[{index}].rows[{row_index}].{column.key}", "required value is missing"))
            if column.key in row:
                try:
                    parse_typed_value(row[column.key], column.data_type, path=f"tables[{index}].rows[{row_index}].{column.key}")
                except ValueError as exc:
                    issues.append(_issue("invalid_typed_value", f"tables[{index}].rows[{row_index}].{column.key}", str(exc)))
    return issues


def validate_request(request: Mapping[str, Any] | WorkbookRequest) -> list[ValidationIssue]:
    """Return stable issues without mutating input or inferring formulas."""

    if isinstance(request, WorkbookRequest):
        parsed = request
    else:
        try:
            parsed = WorkbookRequest.model_validate(request)
        except ValidationError as exc:
            return [_issue("invalid_request", ".".join(str(part) for part in error["loc"]) or "$", error["msg"]) for error in exc.errors()]
    issues: list[ValidationIssue] = []
    if not parsed.workbook_id.strip():
        issues.append(_issue("workbook_id_required", "workbook_id", "workbook_id is required"))
    names = [table.name for table in parsed.tables]
    if len(set(names)) != len(names):
        issues.append(_issue("duplicate_sheet", "tables", "table names must be unique"))
    for index, table in enumerate(parsed.tables):
        issues.extend(_validate_table(table, index))
    for index, formula in enumerate(parsed.formulas):
        if not formula.expression.startswith("="):
            issues.append(_issue("formula_must_start_with_equals", f"formulas[{index}].expression", "formula must start with ="))
    return issues
