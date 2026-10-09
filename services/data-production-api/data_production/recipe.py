"""Compile CSV input into a typed, lineage-preserving DatasetIR."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, cast

from data_ir import (
    ColumnSpec,
    DataContractError,
    DatasetIR,
    DatasetRequest,
    ValidationIssue,
    infer_column_type,
    parse_csv_rows,
    parse_typed_value,
    safe_key,
    validate_request,
)
from data_ir.models import CellValue, ColumnType


class DatasetBuildError(ValueError):
    """Raised when a CSV cannot enter the deterministic cleaner recipe."""

    def __init__(self, issues: list[ValidationIssue]) -> None:
        self.issues = issues
        super().__init__(", ".join(issue.code for issue in issues))


TypedValue = CellValue


@dataclass(frozen=True)
class TransformStat:
    """What one allowlisted transform did (for the trace and the manual guide)."""

    index: int
    op: str
    column: str
    rows_before: int
    rows_after: int
    cells_changed: int = 0
    dropped_source_rows: tuple[int, ...] = ()


@dataclass(frozen=True)
class DatasetBuildStats:
    source_rows: int
    schema_mode: str
    transforms: tuple[TransformStat, ...] = field(default_factory=tuple)


def _parse_transform_value(value: Any, data_type: ColumnType, path: str) -> TypedValue:
    if value is None:
        return None
    if data_type == "text":
        return str(value)
    if data_type == "boolean":
        if isinstance(value, bool):
            return value
        return parse_typed_value(str(value), data_type, path)
    return parse_typed_value(str(value), data_type, path)


def _build_columns(headers: list[str], rows: list[list[str]], request: DatasetRequest) -> tuple[list[ColumnSpec], dict[str, int], bool]:
    if request.columns is None:
        used: set[str] = set()
        inferred_columns: list[ColumnSpec] = []
        for index, header in enumerate(headers, start=1):
            inferred_columns.append(
                ColumnSpec(
                    key=safe_key(header, index, used),
                    label=header,
                    data_type=infer_column_type([row[index - 1] for row in rows]),
                    source_name=header,
                )
            )
        return inferred_columns, {column.key: index for index, column in enumerate(inferred_columns)}, True

    header_indexes = {header: index for index, header in enumerate(headers)}
    issues: list[ValidationIssue] = []
    selected_columns: list[ColumnSpec] = []
    indexes: dict[str, int] = {}
    for column_index, column in enumerate(request.columns):
        source_name = column.source_name or column.key
        if source_name not in header_indexes:
            issues.append(ValidationIssue(code="missing_source_column", path=f"columns[{column_index}].source_name", message=f"missing CSV column: {source_name}"))
            continue
        selected_columns.append(column)
        indexes[column.key] = header_indexes[source_name]
    if issues:
        raise DatasetBuildError(issues)
    return selected_columns, indexes, False


def build_dataset_ir(request: DatasetRequest | dict[str, Any]) -> DatasetIR:
    """Parse, type, transform and lineage-track one CSV payload."""

    return build_dataset_ir_with_stats(request)[0]


def build_dataset_ir_with_stats(request: DatasetRequest | dict[str, Any]) -> tuple[DatasetIR, DatasetBuildStats]:
    """Same as :func:`build_dataset_ir`, plus per-transform row and cell counts."""

    try:
        parsed = request if isinstance(request, DatasetRequest) else DatasetRequest.model_validate(request)
    except Exception as exc:
        raise DatasetBuildError([ValidationIssue(code="invalid_request", path="request", message=str(exc))]) from exc
    request_issues = validate_request(parsed)
    if request_issues:
        raise DatasetBuildError(request_issues)
    try:
        headers, raw_rows = parse_csv_rows(parsed.csv_text, parsed.delimiter)
    except DataContractError as exc:
        raise DatasetBuildError(exc.issues) from exc

    columns, indexes, inferred = _build_columns(headers, raw_rows, parsed)
    work: list[tuple[dict[str, TypedValue], dict[str, str], int]] = []
    issues: list[ValidationIssue] = []
    for source_row_index, raw_row in enumerate(raw_rows, start=2):
        values: dict[str, TypedValue] = {}
        row_lineage: dict[str, str] = {}
        for column in columns:
            raw_value = raw_row[indexes[column.key]]
            path = f"rows[{source_row_index}].{column.key}"
            try:
                values[column.key] = parse_typed_value(raw_value, column.data_type, path)
            except DataContractError as exc:
                issues.extend(exc.issues)
            row_lineage[column.key] = f"{parsed.source_ref}:row:{source_row_index}:{column.source_name or column.key}"
            if column.required and values.get(column.key) is None:
                issues.append(ValidationIssue(code="required_value_missing", path=path, message="required value is missing"))
        work.append((values, row_lineage, source_row_index))
    if issues:
        raise DatasetBuildError(issues)

    transform_log: list[str] = []
    stats: list[TransformStat] = []
    by_key = {column.key: column for column in columns}
    for transform_index, transform in enumerate(parsed.transforms):
        rows_before = len(work)
        changed = 0
        dropped: tuple[int, ...] = ()
        if transform.column is None or transform.column not in by_key:
            raise DatasetBuildError([ValidationIssue(code="unknown_transform_column", path=f"transforms[{transform_index}].column", message="transform column is not in schema")])
        column = by_key[transform.column]
        if transform.op == "trim_text":
            if column.data_type != "text":
                raise DatasetBuildError([ValidationIssue(code="trim_requires_text", path=f"transforms[{transform_index}]", message="trim_text requires a text column")])
            for values, _, _ in work:
                current = values[column.key]
                if isinstance(current, str):
                    values[column.key] = current.strip()
                    changed += values[column.key] != current
        elif transform.op == "fill_null":
            try:
                replacement = _parse_transform_value(transform.value, column.data_type, f"transforms[{transform_index}].value")
            except DataContractError as exc:
                raise DatasetBuildError(exc.issues) from exc
            if replacement is None:
                raise DatasetBuildError([ValidationIssue(code="fill_value_required", path=f"transforms[{transform_index}].value", message="fill_null requires a non-null value")])
            for values, _, _ in work:
                if values[column.key] is None:
                    values[column.key] = replacement
                    changed += 1
        elif transform.op == "filter_equals":
            try:
                expected = _parse_transform_value(transform.value, column.data_type, f"transforms[{transform_index}].value")
            except DataContractError as exc:
                raise DatasetBuildError(exc.issues) from exc
            dropped = tuple(entry[2] for entry in work if entry[0][column.key] != expected)
            work = [entry for entry in work if entry[0][column.key] == expected]
        elif transform.op == "sort_by":
            non_null = [entry for entry in work if entry[0][column.key] is not None]
            nulls = [entry for entry in work if entry[0][column.key] is None]
            non_null.sort(key=lambda entry: cast(Any, entry[0][column.key]), reverse=transform.direction == "desc")
            work = non_null + nulls
        transform_log.append(f"{transform.op}:{transform.column}")
        stats.append(TransformStat(transform_index, transform.op, column.key, rows_before, len(work), int(changed), dropped))

    if not work:
        raise DatasetBuildError([ValidationIssue(code="empty_dataset", path="rows", message="no rows remain after cleaning")])
    rows = [entry[0] for entry in work]
    source_lineage: dict[str, str] = {}
    for output_index, (_, row_lineage, _) in enumerate(work):
        for key, source_ref in row_lineage.items():
            source_lineage[f"rows[{output_index}].{key}"] = source_ref
    dataset = DatasetIR(
        dataset_id=parsed.dataset_id,
        title=parsed.title,
        source_ref=parsed.source_ref,
        columns=columns,
        rows=rows,
        lineage=source_lineage,
        transform_log=transform_log,
        assumptions={"model_calls": "0", "schema_mode": "inferred" if inferred else "explicit", "recipe": "csv-cleaner-v1"},
    )
    return dataset, DatasetBuildStats(source_rows=len(raw_rows), schema_mode=dataset.assumptions["schema_mode"], transforms=tuple(stats))


__all__ = ["DatasetBuildError", "DatasetBuildStats", "TransformStat", "build_dataset_ir", "build_dataset_ir_with_stats"]
