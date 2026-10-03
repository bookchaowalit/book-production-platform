"""Typed input validation and coercion for WorkbookIR."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

from pydantic import ValidationError

from .models import (
    DATE_NUMBER_FORMATS,
    NUMERIC_NUMBER_FORMATS,
    ColumnType,
    ValidationIssue,
    WorkbookRequest,
)

MAX_ALLOWED_VALUE_CHARS = 64
MAX_LIST_SOURCE_CHARS = 255  # Excel's limit for a typed list-validation source
_UNSAFE_LIST_VALUE = ("=", "+", "-", "@")


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


def _validate_column_options(column: Any, path: str) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    if column.number_format is not None:
        allowed = NUMERIC_NUMBER_FORMATS if column.data_type in {"integer", "decimal"} else (
            DATE_NUMBER_FORMATS if column.data_type == "date" else frozenset()
        )
        if column.number_format not in allowed:
            issues.append(_issue("number_format_not_allowed", f"{path}.number_format", "number format is not allowlisted for this column type"))
    if column.allowed_values is not None:
        values = column.allowed_values
        if column.data_type != "text":
            issues.append(_issue("allowed_values_requires_text", f"{path}.allowed_values", "dropdown validation applies to text columns"))
        elif (
            len(set(values)) != len(values)
            or any(
                not value or len(value) > MAX_ALLOWED_VALUE_CHARS or "," in value or '"' in value or value.startswith(_UNSAFE_LIST_VALUE)
                for value in values
            )
            or len(",".join(values)) > MAX_LIST_SOURCE_CHARS
        ):
            issues.append(_issue("allowed_values_invalid", f"{path}.allowed_values", "dropdown values must be unique, short, plain text without commas or quotes"))
    return issues


def _validate_table(table: Any, index: int) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    keys = [column.key for column in table.columns]
    if len(set(keys)) != len(keys):
        issues.append(_issue("duplicate_column", f"tables[{index}].columns", "column keys must be unique"))
    for column_index, column in enumerate(table.columns):
        issues.extend(_validate_column_options(column, f"tables[{index}].columns[{column_index}]"))
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
            value = row.get(column.key)
            if column.allowed_values is not None and value not in (None, "") and str(value) not in column.allowed_values:
                issues.append(_issue(
                    "value_not_in_allowed_values", f"tables[{index}].rows[{row_index}].{column.key}", "value is not one of the dropdown values",
                ))
    return issues


def _validate_conditional_formats(parsed: WorkbookRequest) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    tables = {table.name: table for table in parsed.tables}
    for index, rule in enumerate(parsed.conditional_formats):
        path = f"conditional_formats[{index}]"
        table = tables.get(rule.table)
        column = next((item for item in table.columns if item.key == rule.column), None) if table is not None else None
        if table is None or column is None:
            issues.append(_issue("conditional_format_target_unknown", path, "table or column is not declared"))
            continue
        if column.data_type not in {"integer", "decimal"}:
            issues.append(_issue("conditional_format_requires_numeric", path, "conditional formats apply to numeric columns"))
        if not table.rows:
            issues.append(_issue("conditional_format_empty_table", path, "conditional formats need at least one data row"))
        values_ok = rule.value.is_finite() and (rule.operator == "between") == (rule.value2 is not None)
        if rule.value2 is not None:
            values_ok = values_ok and rule.value2.is_finite() and rule.value2 >= rule.value
        if not values_ok:
            issues.append(_issue("conditional_format_value_invalid", path, "between needs value <= value2; other operators take one value"))
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
    issues.extend(_validate_conditional_formats(parsed))
    return issues
