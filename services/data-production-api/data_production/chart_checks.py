"""Deterministic axis, unit, and aggregation checks for a chart proposal (REQ-DVZ-01).

The model may only *propose* a column mapping and a chart type. These checks are
the engine's own rules and run on every proposal before it can be accepted:

- the chart type and aggregation come from the engine allowlist;
- ``sum``/``avg`` need a distinct numeric y column, ``count`` takes none;
- a ``line`` chart needs an ordered x axis (date or number);
- a value unit must agree with the aggregation and with any unit named in the
  y column header (``amount_thb`` cannot be labelled ``USD``);
- a brief that names more than one currency cannot be summed or averaged
  (mixed units) until a person picks one.

Messages are static: they never echo column names, brief text, or values.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Sequence
from typing import get_args

from brief_adapter_kit.grounding import brief_currencies
from data_ir import ChartSpec, ColumnSpec, ValidationIssue
from data_ir.models import Aggregation

# The AI adapter may only propose these two chart types. The engine renders
# more (``horizontal_bar``, ``pie``; see ``data_ir.models.ChartType``), but those
# are chosen by a person in a structured request, never inferred from a brief.
ALLOWED_CHART_TYPES: tuple[str, ...] = ("bar", "line")
ALLOWED_AGGREGATIONS: tuple[str, ...] = get_args(Aggregation)
NUMERIC_TYPES = frozenset(("integer", "decimal"))
ORDERED_X_TYPES = frozenset(("date", "integer", "decimal"))

_UNIT_ALIASES: dict[str, str] = {
    "thb": "THB", "baht": "THB", "บาท": "THB", "฿": "THB",
    "usd": "USD", "dollar": "USD", "dollars": "USD", "$": "USD", "ดอลลาร์": "USD",
    "eur": "EUR", "euro": "EUR", "euros": "EUR", "€": "EUR", "ยูโร": "EUR",
    "jpy": "JPY", "yen": "JPY", "¥": "JPY", "เยน": "JPY",
    "%": "PCT", "pct": "PCT", "percent": "PCT", "เปอร์เซ็นต์": "PCT",
    "kg": "KG", "กก.": "KG", "กิโลกรัม": "KG",
    "count": "COUNT", "units": "COUNT", "orders": "COUNT", "items": "COUNT", "times": "COUNT",
    "ชิ้น": "COUNT", "รายการ": "COUNT", "ครั้ง": "COUNT", "ออเดอร์": "COUNT", "คน": "COUNT",
}
_CURRENCY_UNITS = frozenset(("THB", "USD", "EUR", "JPY"))


def _issue(code: str, path: str, message: str) -> ValidationIssue:
    return ValidationIssue(code=code, path=path, message=message)


def canonical_unit(unit: str) -> str | None:
    """Map a free-text unit label to a canonical unit code, or ``None`` when unknown."""

    text = unicodedata.normalize("NFC", unit).strip()
    return _UNIT_ALIASES.get(text.casefold()) or _UNIT_ALIASES.get(text)


def header_units(header: str) -> set[str]:
    """Return the canonical units a column header names (``amount_thb`` → ``{"THB"}``)."""

    text = unicodedata.normalize("NFC", header)
    units: set[str] = set()
    for token in re.split(r"[\s_()\[\]/,.-]+", text):
        if token:
            code = canonical_unit(token)
            if code is not None:
                units.add(code)
    for symbol in ("฿", "$", "€", "¥", "%"):
        if symbol in text:
            units.add(_UNIT_ALIASES[symbol])
    return units


def chart_semantic_issues(columns: Sequence[ColumnSpec], spec: ChartSpec, *, brief: str | None = None) -> list[ValidationIssue]:
    """Return every axis/unit/aggregation problem in ``spec`` against the typed ``columns``."""

    issues: list[ValidationIssue] = []
    by_key = {column.key: column for column in columns}
    if spec.chart_type not in ALLOWED_CHART_TYPES:
        issues.append(_issue("chart_type_not_allowed", "chart.chart_type", "chart type is outside the engine allowlist"))
    if spec.aggregation not in ALLOWED_AGGREGATIONS:
        issues.append(_issue("aggregation_not_allowed", "chart.aggregation", "aggregation is outside the engine allowlist"))
    x_column = by_key.get(spec.x_column)
    if x_column is None:
        issues.append(_issue("unknown_chart_column", "chart.x_column", "x column is not in the dataset"))
    y_column = by_key.get(spec.y_column) if spec.y_column is not None else None
    if spec.y_column is not None and y_column is None:
        issues.append(_issue("unknown_chart_column", "chart.y_column", "y column is not in the dataset"))

    if spec.aggregation in {"sum", "avg"}:
        if spec.y_column is None:
            issues.append(_issue("axis_mismatch", "chart.y_column", "sum and avg need a numeric y column"))
        elif y_column is not None and y_column.data_type not in NUMERIC_TYPES:
            issues.append(_issue("axis_type_mismatch", "chart.y_column", "sum and avg need a numeric y column"))
    elif spec.aggregation == "count" and spec.y_column is not None:
        issues.append(_issue("axis_mismatch", "chart.y_column", "count charts do not take a y column"))
    if spec.y_column is not None and spec.y_column == spec.x_column:
        issues.append(_issue("axis_mismatch", "chart", "x and y must be different columns"))
    if spec.chart_type == "line" and x_column is not None and x_column.data_type not in ORDERED_X_TYPES:
        issues.append(_issue("axis_type_mismatch", "chart.x_column", "a line chart needs a date or numeric x column"))

    if spec.value_unit is not None:
        unit = canonical_unit(spec.value_unit)
        if spec.aggregation == "count" and unit != "COUNT":
            issues.append(_issue("unit_mismatch", "chart.value_unit", "a count chart cannot carry a measurement unit"))
        elif spec.aggregation in {"sum", "avg"} and y_column is not None:
            named = header_units(y_column.source_name or y_column.label or y_column.key) | header_units(y_column.label)
            if named and unit not in named:
                issues.append(_issue("unit_mismatch", "chart.value_unit", "the unit disagrees with the unit in the y column header"))
            if unit == "COUNT":
                issues.append(_issue("unit_mismatch", "chart.value_unit", "a summed or averaged value cannot use a count unit"))

    if brief is not None and spec.aggregation in {"sum", "avg"} and len(brief_currencies(brief) & _CURRENCY_UNITS) > 1:
        issues.append(_issue("mixed_units", "chart.value_unit", "the brief names more than one currency; choose one before aggregating"))
    return issues


__all__ = [
    "ALLOWED_AGGREGATIONS",
    "ALLOWED_CHART_TYPES",
    "canonical_unit",
    "chart_semantic_issues",
    "header_units",
]
