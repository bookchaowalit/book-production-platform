"""CSV parsing and typed validation for Data V1A."""

from __future__ import annotations

import csv
import io
import re
import unicodedata
from collections.abc import Iterable
from datetime import date
from decimal import Decimal, InvalidOperation

from .models import CellValue, ColumnType, DatasetRequest, ValidationIssue


class DataContractError(ValueError):
    """Raised when CSV or typed contract validation cannot continue."""

    def __init__(self, issues: list[ValidationIssue]) -> None:
        self.issues = issues
        super().__init__("; ".join(issue.code for issue in issues))


def parse_typed_value(raw: str | None, data_type: str, path: str) -> CellValue:
    """Parse one CSV cell without executing or interpreting formulas."""

    if raw is None or raw == "":
        return None
    if data_type == "text":
        return raw
    if data_type == "integer":
        try:
            return int(raw)
        except ValueError as exc:
            raise DataContractError([ValidationIssue(code="invalid_integer", path=path, message="expected an integer")]) from exc
    if data_type == "decimal":
        try:
            value = Decimal(raw)
        except InvalidOperation as exc:
            raise DataContractError([ValidationIssue(code="invalid_decimal", path=path, message="expected a decimal")]) from exc
        if not value.is_finite():
            raise DataContractError([ValidationIssue(code="non_finite_decimal", path=path, message="decimal must be finite")])
        return value
    if data_type == "date":
        try:
            return date.fromisoformat(raw)
        except ValueError as exc:
            raise DataContractError([ValidationIssue(code="invalid_date", path=path, message="expected YYYY-MM-DD")]) from exc
    if data_type == "boolean":
        normalized = raw.strip().lower()
        if normalized in {"true", "1", "yes", "y"}:
            return True
        if normalized in {"false", "0", "no", "n"}:
            return False
        raise DataContractError([ValidationIssue(code="invalid_boolean", path=path, message="expected true/false")])
    raise DataContractError([ValidationIssue(code="unsupported_type", path=path, message=f"unsupported data type: {data_type}")])


def infer_column_type(values: Iterable[str]) -> ColumnType:
    """Infer the narrowest stable type using all non-empty values."""

    materialized = [value for value in values if value != ""]
    if not materialized:
        return "text"
    if any(re.fullmatch(r"[+-]?0\d+", value.strip()) for value in materialized):
        return "text"
    if all(_try_parse(value, "boolean") is not None for value in materialized):
        return "boolean"
    if all(_try_parse(value, "integer") is not None for value in materialized):
        return "integer"
    if all(_try_parse(value, "decimal") is not None for value in materialized):
        return "decimal"
    if all(_try_parse(value, "date") is not None for value in materialized):
        return "date"
    return "text"


def _try_parse(value: str, data_type: str) -> CellValue:
    try:
        return parse_typed_value(value, data_type, "inference")
    except DataContractError:
        return None


def parse_csv_rows(csv_text: str, delimiter: str = ",") -> tuple[list[str], list[list[str]]]:
    """Read a bounded CSV payload and reject malformed row widths."""

    try:
        rows = list(csv.reader(io.StringIO(csv_text), delimiter=delimiter, strict=True))
    except csv.Error as exc:
        raise DataContractError([ValidationIssue(code="malformed_csv", path="csv_text", message=str(exc))]) from exc
    if not rows or not rows[0]:
        raise DataContractError([ValidationIssue(code="header_required", path="csv_text", message="CSV header is required")])
    headers = [header.strip() for header in rows[0]]
    if any(not header for header in headers):
        raise DataContractError([ValidationIssue(code="empty_header", path="csv_text", message="CSV headers must not be empty")])
    if len(set(headers)) != len(headers):
        raise DataContractError([ValidationIssue(code="duplicate_header", path="csv_text", message="CSV headers must be unique")])
    expected = len(headers)
    if len(rows) - 1 > 10000:
        raise DataContractError([ValidationIssue(code="row_limit_exceeded", path="csv_text", message="CSV is limited to 10000 data rows")])
    for index, row in enumerate(rows[1:], start=2):
        if len(row) != expected:
            raise DataContractError([ValidationIssue(code="row_width_mismatch", path=f"csv_text.row[{index}]", message=f"expected {expected} fields")])
    return headers, rows[1:]


def validate_request(request: DatasetRequest) -> list[ValidationIssue]:
    """Validate references before parsing transforms and chart columns."""

    issues: list[ValidationIssue] = []
    if request.columns is not None:
        keys = [column.key for column in request.columns]
        if len(set(keys)) != len(keys):
            issues.append(ValidationIssue(code="duplicate_column_key", path="columns", message="column keys must be unique"))
        source_names = [column.source_name or column.key for column in request.columns]
        if len(set(source_names)) != len(source_names):
            issues.append(ValidationIssue(code="duplicate_source_name", path="columns", message="source names must be unique"))
    known = {column.key for column in request.columns} if request.columns else None
    for index, transform in enumerate(request.transforms):
        if transform.column is None:
            issues.append(ValidationIssue(code="transform_column_required", path=f"transforms[{index}].column", message="transform column is required"))
        elif known is not None and transform.column not in known:
            issues.append(ValidationIssue(code="unknown_transform_column", path=f"transforms[{index}].column", message="transform column is not in schema"))
        if transform.op == "fill_null" and transform.value is None:
            issues.append(ValidationIssue(code="fill_value_required", path=f"transforms[{index}].value", message="fill_null requires a value"))
    if request.chart is not None and known is not None:
        chart_columns = [request.chart.x_column]
        if request.chart.y_column is not None:
            chart_columns.append(request.chart.y_column)
        for column in chart_columns:
            if column not in known:
                issues.append(ValidationIssue(code="unknown_chart_column", path="chart", message=f"unknown chart column: {column}"))
    return issues


def safe_key(header: str, index: int, used: set[str]) -> str:
    """Create a stable schema key from an arbitrary CSV header."""

    candidate = "".join(
        character if character == "_" or character.isalnum() or unicodedata.category(character).startswith("M") else "_"
        for character in header.strip()
    )
    candidate = re.sub(r"_+", "_", candidate).strip("_") or f"column_{index}"
    if candidate[0].isdigit() or unicodedata.category(candidate[0]).startswith("M"):
        candidate = f"column_{candidate}"
    base = candidate
    suffix = 2
    while candidate in used:
        candidate = f"{base}_{suffix}"
        suffix += 1
    used.add(candidate)
    return candidate


__all__ = ["DataContractError", "infer_column_type", "parse_csv_rows", "parse_typed_value", "safe_key", "validate_request"]
