"""Deterministic cleaned CSV, SVG chart and report rendering."""

from __future__ import annotations

import csv
import hashlib
import html
import json
import math
import tempfile
from datetime import date
from decimal import ROUND_FLOOR, ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any

from data_ir import ArtifactRef, ChartIR, ChartSpec, DatasetIR


class RenderError(ValueError):
    """Raised when an output cannot be written safely."""


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


def _artifact(path: Path, media_type: str) -> ArtifactRef:
    data = path.read_bytes()
    return ArtifactRef(filename=path.name, media_type=media_type, content_hash=hashlib.sha256(data).hexdigest(), byte_size=len(data))


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent, delete=False) as handle:
        handle.write(data)
        temporary = Path(handle.name)
    temporary.replace(path)


def render_clean_csv(dataset: DatasetIR, path: Path) -> ArtifactRef:
    """Write a stable, schema-keyed CSV for downstream tools."""

    output = []
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", newline="", suffix=".csv", delete=False) as handle:
        writer = csv.DictWriter(handle, fieldnames=[column.key for column in dataset.columns], lineterminator="\n")
        writer.writeheader()
        for row in dataset.rows:
            writer.writerow({column.key: _value_text(row.get(column.key)) for column in dataset.columns})
        temporary = Path(handle.name)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary.replace(path)
    output.append(_artifact(path, "text/csv; charset=utf-8"))
    return output[0]


_NICE_MULTIPLIERS = (Decimal(1), Decimal("1.5"), Decimal(2), Decimal("2.5"), Decimal(3), Decimal(4), Decimal(5),
                     Decimal(6), Decimal(8))
_TICK_INTERVALS = 4


def nice_axis(minimum: Decimal, maximum: Decimal) -> tuple[Decimal, Decimal]:
    """Axis bounds with a round tick step and exactly five ticks that cover every value.

    The step is the smallest 1/1.5/2/2.5/3/4/5/6/8 x 10^n such that the axis
    starting at the step multiple at or below ``minimum`` reaches ``maximum``
    within four steps. Zero stays on a tick whenever the data spans zero.
    """

    span = maximum - minimum
    if span <= 0:
        span = abs(maximum) or Decimal(1)
    exponent = (span / _TICK_INTERVALS).adjusted()
    for power in range(exponent - 1, exponent + 3):
        scale = Decimal(10) ** power
        for multiplier in _NICE_MULTIPLIERS:
            step = multiplier * scale
            low = (minimum / step).to_integral_value(rounding=ROUND_FLOOR) * step
            if low + step * _TICK_INTERVALS >= maximum and step * _TICK_INTERVALS >= span:
                return low, low + step * _TICK_INTERVALS
    return minimum, maximum


def _plain(value: Decimal) -> str:
    if value == 0:
        return "0"
    text = format(value, "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


def display_number(value: Decimal, spec: ChartSpec) -> str:
    """Format a chart number the way the SVG shows it (ticks and data labels).

    ``value_decimals`` rounds half away from zero (as Excel's number formats
    do); ``thousands_separator`` adds ``,`` grouping. With neither set the
    plain, trailing-zero-free text of v3 is kept.
    """

    if spec.value_decimals is not None:
        quantum = Decimal(1).scaleb(-spec.value_decimals)
        rounded = value.quantize(quantum, rounding=ROUND_HALF_UP)
        if rounded == 0:
            rounded = abs(rounded)
        return format(rounded, f",.{spec.value_decimals}f" if spec.thousands_separator else f".{spec.value_decimals}f")
    text = _plain(value)
    if spec.thousands_separator:
        return format(Decimal(text), ",f")
    return text


def excel_number_format(spec: ChartSpec, values: list[Decimal]) -> str:
    """The Excel ``Format Cells ▸ Number ▸ Custom`` code that shows ``display_number`` text."""

    if spec.value_decimals is not None:
        decimals = spec.value_decimals
    else:
        decimals = max((len(_plain(value).partition(".")[2]) for value in values), default=0)
    if decimals == 0 and spec.value_decimals is None and not spec.thousands_separator:
        return "General"
    base = "#,##0" if spec.thousands_separator else "0"
    return base + ("." + "0" * decimals if decimals else "")


def pie_percentages(values: list[Decimal]) -> list[Decimal]:
    total = sum(values, Decimal(0))
    return [(value * 100 / total).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP) if total else Decimal(0) for value in values]


PIE_COLORS = ("#2563eb", "#f97316", "#16a34a", "#dc2626", "#9333ea", "#0891b2",
              "#ca8a04", "#db2777", "#4b5563", "#65a30d", "#7c3aed", "#0d9488")


def _svg_open(chart: ChartIR) -> list[str]:
    width, height = chart.spec.width, chart.spec.height
    title = html.escape(chart.spec.title, quote=True)
    return [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        f'<title>{title}</title>',
    ]


def y_axis_text(chart: ChartIR) -> str:
    text = chart.spec.y_axis_label or ("Count" if chart.spec.aggregation == "count" else chart.spec.y_column or "Value")
    return f"{text} ({chart.spec.value_unit})" if chart.spec.value_unit else text


def value_axis(chart: ChartIR) -> tuple[Decimal, Decimal]:
    decimal_values = chart.values
    decimal_minimum = min([Decimal(0), *decimal_values]) if decimal_values else Decimal(0)
    decimal_maximum = max([Decimal(0), *decimal_values]) if decimal_values else Decimal(1)
    if decimal_maximum == decimal_minimum:
        decimal_maximum = decimal_minimum + Decimal(1)
    return nice_axis(decimal_minimum, decimal_maximum)


def _pie_svg(chart: ChartIR) -> str:
    width, height = chart.spec.width, chart.spec.height
    top = 76
    radius = max(20.0, min((width * 0.6 - 48) / 2, (height - top - 24) / 2))
    cx, cy = 24 + radius, top + radius
    total = float(sum(chart.values, Decimal(0)))
    percents = pie_percentages(chart.values)
    elements = _svg_open(chart)
    elements.append(f'<text x="24" y="26" font-family="sans-serif" font-size="18">{html.escape(chart.spec.title, quote=True)}</text>')
    angle = -math.pi / 2
    for index, value in enumerate(chart.values):
        color = PIE_COLORS[index % len(PIE_COLORS)]
        share = float(value) / total if total else 0.0
        if share >= 1.0:
            elements.append(f'<circle class="pie-slice" cx="{cx:.2f}" cy="{cy:.2f}" r="{radius:.2f}" fill="{color}" data-value="{float(value):g}" />')
        else:
            end = angle + share * 2 * math.pi
            x1, y1 = cx + radius * math.cos(angle), cy + radius * math.sin(angle)
            x2, y2 = cx + radius * math.cos(end), cy + radius * math.sin(end)
            large = 1 if share > 0.5 else 0
            elements.append(
                f'<path class="pie-slice" d="M {cx:.2f} {cy:.2f} L {x1:.2f} {y1:.2f} A {radius:.2f} {radius:.2f} 0 {large} 1 {x2:.2f} {y2:.2f} Z" '
                f'fill="{color}" stroke="#ffffff" data-value="{float(value):g}" />'
            )
        middle = angle + share * math.pi
        if chart.spec.data_labels:
            lx, ly = cx + radius * 0.65 * math.cos(middle), cy + radius * 0.65 * math.sin(middle)
            elements.append(f'<text class="data-label" x="{lx:.2f}" y="{ly + 4:.2f}" text-anchor="middle" font-family="sans-serif" font-size="11" fill="#ffffff">{display_number(value, chart.spec)}</text>')
        angle += share * 2 * math.pi
    legend_x = 24 + radius * 2 + 32
    for index, (category, percent) in enumerate(zip(chart.categories, percents)):
        y = top + 12 + index * 22
        color = PIE_COLORS[index % len(PIE_COLORS)]
        elements.append(f'<rect class="legend-swatch" x="{legend_x:.2f}" y="{y - 10}" width="12" height="12" fill="{color}" />')
        elements.append(f'<text class="legend-label" x="{legend_x + 18:.2f}" y="{y}" font-family="sans-serif" font-size="12">{html.escape(category, quote=True)} ({percent}%)</text>')
    elements.append("</svg>")
    return "\n".join(elements) + "\n"


def _horizontal_bar_svg(chart: ChartIR) -> str:
    width, height = chart.spec.width, chart.spec.height
    left, right, top, bottom = 140, 32, 76, 72
    plot_width, plot_height = width - left - right, height - top - bottom
    decimal_minimum, decimal_maximum = value_axis(chart)
    minimum, maximum = float(decimal_minimum), float(decimal_maximum)

    def x_for(value: float) -> float:
        return left + (value - minimum) * plot_width / (maximum - minimum)

    tick_values = [decimal_minimum + (decimal_maximum - decimal_minimum) * Decimal(index) / Decimal(4) for index in range(5)]
    elements = _svg_open(chart)
    elements += [
        f'<text x="{left}" y="26" font-family="sans-serif" font-size="18">{html.escape(chart.spec.title, quote=True)}</text>',
        f'<text class="x-axis-label" x="12" y="51" font-family="sans-serif" font-size="12">{html.escape(chart.spec.x_axis_label or chart.spec.x_column, quote=True)}</text>',
    ]
    for tick_value in tick_values:
        x = x_for(float(tick_value))
        elements.append(f'<line class="x-tick" x1="{x:.2f}" y1="{top}" x2="{x:.2f}" y2="{height - bottom}" stroke="#e2e8f0" />')
        elements.append(f'<text class="x-tick-label" x="{x:.2f}" y="{height - bottom + 16}" text-anchor="middle" font-family="sans-serif" font-size="10">{display_number(tick_value, chart.spec)}</text>')
    elements += [
        f'<line class="y-axis" x1="{x_for(0):.2f}" y1="{top}" x2="{x_for(0):.2f}" y2="{height - bottom}" stroke="#64748b" />',
        f'<line class="x-axis" x1="{left}" y1="{height - bottom}" x2="{width - right}" y2="{height - bottom}" stroke="#64748b" />',
        f'<text class="y-axis-label" x="{left + plot_width / 2:.2f}" y="{height - 8}" text-anchor="middle" font-family="sans-serif" font-size="12">{html.escape(y_axis_text(chart), quote=True)}</text>',
    ]
    count = max(1, len(chart.values))
    slot = plot_height / count
    for index, (category, value) in enumerate(zip(chart.categories, chart.values)):
        y = top + slot * index + slot * 0.15
        x_zero, x_value = x_for(0), x_for(float(value))
        elements.append(f'<rect x="{min(x_zero, x_value):.2f}" y="{y:.2f}" width="{abs(x_value - x_zero):.2f}" height="{slot * 0.7:.2f}" fill="#2563eb" data-value="{float(value):g}" />')
        elements.append(f'<text class="category-label" x="{left - 8}" y="{y + slot * 0.35 + 4:.2f}" text-anchor="end" font-family="sans-serif" font-size="11">{html.escape(category, quote=True)}</text>')
        if chart.spec.data_labels:
            label_x = max(x_zero, x_value) + 4
            elements.append(f'<text class="data-label" x="{label_x:.2f}" y="{y + slot * 0.35 + 4:.2f}" font-family="sans-serif" font-size="10">{display_number(value, chart.spec)}</text>')
    elements.append("</svg>")
    return "\n".join(elements) + "\n"


def _chart_svg(chart: ChartIR) -> str:
    if chart.spec.chart_type == "pie":
        return _pie_svg(chart)
    if chart.spec.chart_type == "horizontal_bar":
        return _horizontal_bar_svg(chart)
    return _column_or_line_svg(chart)


def _column_or_line_svg(chart: ChartIR) -> str:
    width, height = chart.spec.width, chart.spec.height
    left, right, top, bottom = 84, 24, 76, 72
    plot_width, plot_height = width - left - right, height - top - bottom
    decimal_values = chart.values
    values = [float(value) for value in decimal_values]
    decimal_minimum = min([Decimal(0), *decimal_values]) if decimal_values else Decimal(0)
    decimal_maximum = max([Decimal(0), *decimal_values]) if decimal_values else Decimal(1)
    if decimal_maximum == decimal_minimum:
        decimal_maximum = decimal_minimum + Decimal(1)
    decimal_minimum, decimal_maximum = nice_axis(decimal_minimum, decimal_maximum)
    minimum = float(decimal_minimum)
    maximum = float(decimal_maximum)

    def x_for(index: int) -> float:
        if len(chart.categories) == 1:
            return left + plot_width / 2
        return left + (plot_width * index / (len(chart.categories) - 1))

    def y_for(value: float) -> float:
        return top + (maximum - value) * plot_height / (maximum - minimum)

    def tick_label(value: Decimal) -> str:
        return display_number(value, chart.spec)

    tick_values = [decimal_minimum + (decimal_maximum - decimal_minimum) * Decimal(index) / Decimal(4) for index in range(5)]
    tick_positions = [y_for(float(value)) for value in tick_values]
    title = html.escape(chart.spec.title, quote=True)
    x_axis_label = html.escape(chart.spec.x_axis_label or chart.spec.x_column, quote=True)
    y_axis_label = html.escape(y_axis_text(chart), quote=True)
    elements = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        f'<title>{title}</title>',
        f'<text x="{left}" y="26" font-family="sans-serif" font-size="18">{title}</text>',
        f'<text class="y-axis-label" x="{left}" y="51" font-family="sans-serif" font-size="12">{y_axis_label}</text>',
    ]
    for tick_value, y in zip(tick_values, tick_positions):
        elements.append(f'<line class="y-tick" x1="{left}" y1="{y:.2f}" x2="{width - right}" y2="{y:.2f}" stroke="#e2e8f0" />')
        elements.append(f'<text class="y-tick-label" x="{left - 8}" y="{y + 4:.2f}" text-anchor="end" font-family="sans-serif" font-size="10">{tick_label(tick_value)}</text>')
    elements.extend(
        [
            f'<line class="y-axis" x1="{left}" y1="{top}" x2="{left}" y2="{height - bottom}" stroke="#64748b" />',
            f'<line class="x-axis" x1="{left}" y1="{y_for(0)}" x2="{width - right}" y2="{y_for(0)}" stroke="#64748b" />',
            f'<text class="x-axis-label" x="{left + plot_width / 2:.2f}" y="{height - 8}" text-anchor="middle" font-family="sans-serif" font-size="12">{x_axis_label}</text>',
        ]
    )
    if chart.spec.chart_type == "bar":
        count = max(1, len(chart.values))
        slot = plot_width / count
        for index, (category, value) in enumerate(zip(chart.categories, values)):
            x = left + slot * index + slot * 0.15
            y_zero = y_for(0)
            y_value = y_for(value)
            y = min(y_zero, y_value)
            bar_height = abs(y_zero - y_value)
            label = html.escape(category, quote=True)
            elements.append(f'<rect x="{x:.2f}" y="{y:.2f}" width="{slot * 0.7:.2f}" height="{bar_height:.2f}" fill="#2563eb" data-value="{value:g}" />')
            elements.append(f'<text x="{x + slot * 0.35:.2f}" y="{height - 28}" text-anchor="middle" font-family="sans-serif" font-size="11">{label}</text>')
            if chart.spec.data_labels:
                label_y = y - 4 if value >= 0 else y + bar_height + 12
                elements.append(f'<text class="data-label" x="{x + slot * 0.35:.2f}" y="{label_y:.2f}" text-anchor="middle" font-family="sans-serif" font-size="10">{display_number(decimal_values[index], chart.spec)}</text>')
    else:
        points = " ".join(f"{x_for(index):.2f},{y_for(value):.2f}" for index, value in enumerate(values))
        elements.append(f'<polyline points="{points}" fill="none" stroke="#2563eb" stroke-width="3" data-point-count="{len(values)}" />')
        for index, (category, value) in enumerate(zip(chart.categories, values)):
            x, y = x_for(index), y_for(value)
            label = html.escape(category, quote=True)
            elements.append(f'<circle cx="{x:.2f}" cy="{y:.2f}" r="4" fill="#2563eb" data-value="{value:g}" />')
            elements.append(f'<text x="{x:.2f}" y="{height - 28}" text-anchor="middle" font-family="sans-serif" font-size="11">{label}</text>')
            if chart.spec.data_labels:
                elements.append(f'<text class="data-label" x="{x:.2f}" y="{y - 8:.2f}" text-anchor="middle" font-family="sans-serif" font-size="10">{display_number(decimal_values[index], chart.spec)}</text>')
    elements.append("</svg>")
    return "\n".join(elements) + "\n"


def render_chart_svg(chart: ChartIR, path: Path) -> ArtifactRef:
    _atomic_write(path, _chart_svg(chart).encode("utf-8"))
    return _artifact(path, "image/svg+xml")


def _report_payload(dataset: DatasetIR, chart: ChartIR | None) -> dict[str, Any]:
    null_counts = {column.key: sum(row.get(column.key) is None for row in dataset.rows) for column in dataset.columns}
    payload: dict[str, Any] = {
        "schema_version": "data-report.v1",
        "dataset_id": dataset.dataset_id,
        "title": dataset.title,
        "source_ref": dataset.source_ref,
        "rows": len(dataset.rows),
        "columns": [column.model_dump(mode="json") for column in dataset.columns],
        "null_counts": null_counts,
        "transform_log": dataset.transform_log,
        "lineage": dataset.lineage,
        "assumptions": dataset.assumptions,
        "model_calls": 0,
    }
    if chart is not None:
        payload["chart"] = {
            "chart_type": chart.spec.chart_type,
            "aggregation": chart.spec.aggregation,
            "x_column": chart.spec.x_column,
            "y_column": chart.spec.y_column,
            "x_axis_label": chart.spec.x_axis_label or chart.spec.x_column,
            "y_axis_label": chart.spec.y_axis_label or ("Count" if chart.spec.aggregation == "count" else chart.spec.y_column or "Value"),
            "value_unit": chart.spec.value_unit,
            "categories": chart.categories,
            "values": [_value_text(value) for value in chart.values],
            "aggregate_total": _value_text(chart.aggregate_total),
            "row_count": chart.row_count,
            "source_refs": chart.source_refs,
            "display": {
                "sort": chart.spec.sort,
                "top_n": chart.spec.top_n,
                "group_rest_as": chart.spec.group_rest_as,
                "folded_categories": chart.folded_categories,
                "value_decimals": chart.spec.value_decimals,
                "thousands_separator": chart.spec.thousands_separator,
                "data_labels": chart.spec.data_labels,
                "labels": [display_number(value, chart.spec) for value in chart.values],
            },
        }
        if chart.spec.chart_type == "pie":
            payload["chart"]["percentages"] = [format(value, "f") for value in pie_percentages(chart.values)]
    return payload


def render_report(dataset: DatasetIR, chart: ChartIR | None, path: Path) -> ArtifactRef:
    data = json.dumps(_report_payload(dataset, chart), ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8")
    _atomic_write(path, data)
    return _artifact(path, "application/json")


def render_outputs(dataset: DatasetIR, chart: ChartIR | None, output_dir: str | Path) -> tuple[ArtifactRef, ...]:
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    artifacts: list[ArtifactRef] = [render_clean_csv(dataset, root / "cleaned.csv")]
    if chart is not None:
        artifacts.append(render_chart_svg(chart, root / "chart.svg"))
    artifacts.append(render_report(dataset, chart, root / "report.json"))
    return tuple(artifacts)


__all__ = [
    "PIE_COLORS",
    "RenderError",
    "display_number",
    "excel_number_format",
    "nice_axis",
    "pie_percentages",
    "render_chart_svg",
    "render_clean_csv",
    "render_outputs",
    "render_report",
    "value_axis",
    "y_axis_text",
]
