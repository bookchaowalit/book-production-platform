"""Reopen and content checks for Data V1A artifacts."""

from __future__ import annotations

import csv
import json
import re
from datetime import date
from decimal import Decimal
from pathlib import Path
from xml.etree import ElementTree

from data_ir import ChartIR, DatasetIR

from .render import display_number, pie_percentages, value_axis, y_axis_text


def _value_text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _nodes(svg_root: ElementTree.Element, css_class: str) -> list[ElementTree.Element]:
    return svg_root.findall(f".//*[@class='{css_class}']")


def _svg_structure_issues(svg_root: ElementTree.Element, chart: ChartIR) -> list[str]:
    """Axis, tick, legend, and data-label checks per chart type."""

    issues: list[str] = []
    spec = chart.spec
    if spec.data_labels:
        labels = [node.text for node in _nodes(svg_root, "data-label")]
        if labels != [display_number(value, spec) for value in chart.values]:
            issues.append("chart_data_labels_mismatch")
    elif _nodes(svg_root, "data-label"):
        issues.append("chart_data_labels_unexpected")
    if spec.chart_type == "pie":
        if len(_nodes(svg_root, "pie-slice")) != len(chart.values):
            issues.append("chart_slice_count_mismatch")
        expected = [f"{category} ({percent}%)" for category, percent in zip(chart.categories, pie_percentages(chart.values))]
        if [node.text for node in _nodes(svg_root, "legend-label")] != expected:
            issues.append("chart_legend_mismatch")
        if _nodes(svg_root, "x-axis-label") or _nodes(svg_root, "y-axis-label"):
            issues.append("chart_pie_has_axes")
        return issues
    x_axis_nodes = _nodes(svg_root, "x-axis-label")
    y_axis_nodes = _nodes(svg_root, "y-axis-label")
    tick_class = "x-tick" if spec.chart_type == "horizontal_bar" else "y-tick"
    tick_nodes = _nodes(svg_root, tick_class)
    tick_label_nodes = _nodes(svg_root, f"{tick_class}-label")
    if len(x_axis_nodes) != 1 or x_axis_nodes[0].text != (spec.x_axis_label or spec.x_column):
        issues.append("chart_x_axis_label_mismatch")
    if len(y_axis_nodes) != 1 or y_axis_nodes[0].text != y_axis_text(chart):
        issues.append("chart_y_axis_label_mismatch")
    if len(tick_nodes) != 5 or len(tick_label_nodes) != 5:
        issues.append("chart_y_axis_ticks_mismatch")
    else:
        low, high = value_axis(chart)
        expected_ticks = [display_number(low + (high - low) * Decimal(index) / Decimal(4), spec) for index in range(5)]
        if [node.text for node in tick_label_nodes] != expected_ticks:
            issues.append("chart_tick_labels_mismatch")
    if spec.chart_type == "horizontal_bar" and [node.text for node in _nodes(svg_root, "category-label")] != chart.categories:
        issues.append("chart_category_labels_mismatch")
    return issues


def qa_outputs(dataset: DatasetIR, chart: ChartIR | None, output_dir: str | Path) -> tuple[str, ...]:
    root = Path(output_dir)
    issues: list[str] = []
    csv_path, report_path = root / "cleaned.csv", root / "report.json"
    if not csv_path.exists() or not report_path.exists():
        return ("artifact_missing",)
    with csv_path.open("r", encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    if len(rows) != len(dataset.rows):
        issues.append("csv_row_count_mismatch")
    for row_index, row in enumerate(rows):
        for column in dataset.columns:
            expected = dataset.rows[row_index].get(column.key)
            actual = row.get(column.key, "")
            if expected is None:
                if actual != "":
                    issues.append(f"csv_null_mismatch:{row_index}:{column.key}")
            elif _value_text(expected) != actual:
                issues.append(f"csv_value_mismatch:{row_index}:{column.key}")
    if chart is not None:
        svg_path = root / "chart.svg"
        if not svg_path.exists():
            issues.append("chart_missing")
        else:
            svg = svg_path.read_text(encoding="utf-8")
            if "<script" in svg.lower() or re.search(r"(?:href|xlink:href)=\"https?://", svg, re.IGNORECASE):
                issues.append("unsafe_svg_reference")
            if svg.count("data-value=") != len(chart.values):
                issues.append("chart_point_count_mismatch")
            try:
                svg_root = ElementTree.fromstring(svg)
            except ElementTree.ParseError:
                issues.append("chart_invalid_svg")
            else:
                issues.extend(_svg_structure_issues(svg_root, chart))
    report = json.loads(report_path.read_text(encoding="utf-8"))
    if report.get("model_calls") != 0:
        issues.append("model_calls_nonzero")
    if report.get("rows") != len(dataset.rows):
        issues.append("report_row_count_mismatch")
    if chart is not None:
        chart_report = report.get("chart", {})
        if chart_report.get("aggregate_total") != format(chart.aggregate_total, "f"):
            issues.append("report_total_mismatch")
        if chart_report.get("values") != [format(value, "f") for value in chart.values]:
            issues.append("report_values_mismatch")
        if chart_report.get("source_refs") != chart.source_refs:
            issues.append("report_lineage_mismatch")
        if chart_report.get("x_axis_label") != (chart.spec.x_axis_label or chart.spec.x_column):
            issues.append("report_x_axis_label_mismatch")
        if chart_report.get("y_axis_label") != (chart.spec.y_axis_label or ("Count" if chart.spec.aggregation == "count" else chart.spec.y_column or "Value")):
            issues.append("report_y_axis_label_mismatch")
        if chart_report.get("value_unit") != chart.spec.value_unit:
            issues.append("report_value_unit_mismatch")
        display = chart_report.get("display", {})
        if display.get("labels") != [display_number(value, chart.spec) for value in chart.values] or display.get("folded_categories") != chart.folded_categories:
            issues.append("report_display_mismatch")
    return tuple(issues)


__all__ = ["qa_outputs"]
