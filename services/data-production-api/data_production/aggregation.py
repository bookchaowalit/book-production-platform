"""Deterministic chart aggregation over the normalized DatasetIR."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from data_ir import ChartIR, ChartSpec, DatasetIR
from data_ir.models import CellValue

PIE_MAX_SLICES = 12
VALUE_SORTS = frozenset(("value_desc", "value_asc"))


class ChartBuildError(ValueError):
    """Raised when a chart cannot be computed from the dataset."""


@dataclass
class _Group:
    category: str
    raw_x: CellValue
    row_indexes: list[int] = field(default_factory=list)
    numeric: list[Decimal] = field(default_factory=list)


def _aggregate(spec: ChartSpec, rows: int, numeric: list[Decimal]) -> Decimal:
    if spec.aggregation == "count":
        return Decimal(rows)
    if spec.aggregation == "sum":
        return sum(numeric, Decimal(0))
    return sum(numeric, Decimal(0)) / Decimal(len(numeric)) if numeric else Decimal(0)


def _category_key(group: _Group) -> tuple[int, Any]:
    # Nulls always go last; within one column every non-null value has one type.
    return (1, "") if group.raw_x is None else (0, group.raw_x)


def _check_spec(dataset: DatasetIR, spec: ChartSpec) -> str | None:
    columns = {column.key: column for column in dataset.columns}
    if spec.x_column not in columns:
        raise ChartBuildError(f"unknown_chart_column:{spec.x_column}")
    y_column = spec.y_column
    if spec.aggregation in {"sum", "avg"} and y_column is None:
        raise ChartBuildError("y_column_required")
    if y_column is not None and y_column not in columns:
        raise ChartBuildError(f"unknown_chart_column:{y_column}")
    if spec.aggregation in {"sum", "avg"} and y_column is not None and columns[y_column].data_type not in {"integer", "decimal"}:
        raise ChartBuildError("numeric_column_required")
    if spec.chart_type == "line" and spec.sort in VALUE_SORTS:
        raise ChartBuildError("line_requires_category_order")
    if spec.chart_type == "pie" and spec.aggregation == "avg":
        raise ChartBuildError("pie_requires_sum_or_count")
    if spec.group_rest_as is not None and spec.top_n is None:
        raise ChartBuildError("group_rest_requires_top_n")
    return y_column


def build_chart_ir(dataset: DatasetIR, spec: ChartSpec) -> ChartIR:
    y_column = _check_spec(dataset, spec)
    columns = {column.key: column for column in dataset.columns}
    resolved_spec = spec.model_copy(
        update={
            "x_axis_label": spec.x_axis_label or columns[spec.x_column].label,
            "y_axis_label": spec.y_axis_label
            or ("Count" if spec.aggregation == "count" else columns[y_column].label if y_column is not None else "Value"),
        }
    )

    groups: dict[str, _Group] = {}
    order: list[str] = []
    for row_index, row in enumerate(dataset.rows):
        raw_x = row.get(spec.x_column)
        category = "(null)" if raw_x is None else str(raw_x)
        if category not in groups:
            groups[category] = _Group(category=category, raw_x=raw_x)
            order.append(category)
        group = groups[category]
        group.row_indexes.append(row_index)
        if y_column is not None:
            value = row.get(y_column)
            if isinstance(value, (int, Decimal)) and not isinstance(value, bool):
                group.numeric.append(Decimal(str(value)))

    ordered = [groups[category] for category in order]
    values_by_category = {group.category: _aggregate(spec, len(group.row_indexes), group.numeric) for group in ordered}
    if spec.sort == "value_desc":
        ordered.sort(key=lambda group: values_by_category[group.category], reverse=True)
    elif spec.sort == "value_asc":
        ordered.sort(key=lambda group: values_by_category[group.category])
    elif spec.sort in {"category_asc", "category_desc"}:
        non_null = sorted((group for group in ordered if group.raw_x is not None), key=_category_key, reverse=spec.sort == "category_desc")
        ordered = non_null + [group for group in ordered if group.raw_x is None]

    kept, rest = (ordered[: spec.top_n], ordered[spec.top_n :]) if spec.top_n is not None else (ordered, [])
    categories = [group.category for group in kept]
    values = [values_by_category[group.category] for group in kept]
    shown_groups = list(kept)
    folded: list[str] = []
    if rest and spec.group_rest_as is not None:
        if spec.group_rest_as in categories:
            raise ChartBuildError("group_label_conflict")
        folded = [group.category for group in rest]
        rest_rows = sum(len(group.row_indexes) for group in rest)
        rest_numeric = [value for group in rest for value in group.numeric]
        categories.append(spec.group_rest_as)
        values.append(_aggregate(spec, rest_rows, rest_numeric))
        shown_groups += rest

    if spec.chart_type == "pie":
        if any(value < 0 for value in values):
            raise ChartBuildError("pie_requires_non_negative_values")
        if sum(values, Decimal(0)) <= 0:
            raise ChartBuildError("pie_total_zero")
        if len(values) > PIE_MAX_SLICES:
            raise ChartBuildError("pie_too_many_slices")

    refs: list[str] = []
    seen: set[str] = set()
    for output_index in (index for group in shown_groups for index in group.row_indexes):
        for key in (spec.x_column, y_column):
            if key is not None:
                ref = dataset.lineage.get(f"rows[{output_index}].{key}")
                if ref is not None and ref not in seen:
                    seen.add(ref)
                    refs.append(ref)

    return ChartIR(
        dataset_id=dataset.dataset_id,
        spec=resolved_spec,
        categories=categories,
        values=values,
        source_refs=refs,
        aggregate_total=sum(values, Decimal(0)),
        row_count=len(dataset.rows),
        folded_categories=folded,
    )


__all__ = ["PIE_MAX_SLICES", "ChartBuildError", "build_chart_ir"]
