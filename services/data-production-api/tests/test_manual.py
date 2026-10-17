from __future__ import annotations

from decimal import Decimal
from typing import Any

from data_ir import ChartSpec, DatasetRequest
from data_production.aggregation import build_chart_ir
from data_production.manual import build_excel_guide
from data_production.recipe import build_dataset_ir_with_stats
from data_production.render import PIE_COLORS, pie_percentages, value_axis
from production_trace import render_manual_markdown

CSV = "region,amount,status\n BKK ,10.50,ok\nCNX,2,ok\nHKT,,void\nBKK,4,ok\nKKC,1,ok\nLPG,3,ok\n"
COLUMNS = [
    {"key": "region", "label": "Region", "data_type": "text"},
    {"key": "amount", "label": "Amount", "data_type": "decimal"},
    {"key": "status", "label": "Status", "data_type": "text"},
]
TRANSFORMS = [
    {"op": "trim_text", "column": "region"},
    {"op": "fill_null", "column": "amount", "value": "0"},
    {"op": "filter_equals", "column": "status", "value": "ok"},
]


def _guide(**chart: Any) -> tuple[Any, Any, str]:
    request = DatasetRequest.model_validate({"dataset_id": "m", "title": "Sales", "csv_text": CSV, "columns": COLUMNS, "transforms": TRANSFORMS, "chart": chart or None})
    dataset, stats = build_dataset_ir_with_stats(request)
    ir = build_chart_ir(dataset, request.chart) if request.chart else None
    guide = build_excel_guide(request, dataset, ir, stats)
    return guide, ir, render_manual_markdown(guide)


def _values(guide: Any) -> str:
    return "\n".join(f"{step.tool} {step.target} {step.value} {step.note}" for step in guide.steps)


def test_guide_reproduces_import_types_and_every_transform_in_order() -> None:
    guide, _, markdown = _guide()
    text = _values(guide)
    assert "65001: Unicode (UTF-8)" in text and "Comma" in text and "Do not detect data types" in text
    for column, pq_type in (("region", "Text"), ("amount", "Decimal Number"), ("status", "Text")):
        assert any(step.target == f"คอลัมน์ {column}" and step.value == pq_type for step in guide.steps)
    tools = [step.tool for step in guide.steps]
    trim, replace, filt = (tools.index(t) for t in ("Transform ▸ Text Column ▸ Format ▸ Trim", "Transform ▸ Any Column ▸ Replace Values", "ปุ่ม ▼ ที่หัวคอลัมน์ ▸ Text/Number Filters ▸ Equals…"))
    assert trim < replace < filt
    filter_step = guide.steps[filt]
    assert filter_step.value == "ok" and "ตัด 1 แถว" in filter_step.note and ": 4" in filter_step.note  # CSV line 4 (HKT) dropped
    assert "chart_data" not in markdown  # no chart in the request → no chart section
    assert guide.estimated_minutes is not None and guide.estimated_minutes > 0


def test_every_category_value_axis_and_series_appear_in_steps() -> None:
    guide, chart, markdown = _guide(x_column="region", y_column="amount", aggregation="sum", title="Sales", value_unit="THB")
    text = _values(guide)
    assert chart.categories == ["BKK", "CNX", "KKC", "LPG"]
    for row, (category, value) in enumerate(zip(chart.categories, chart.values), start=2):
        step = next(step for step in guide.steps if step.target == f"B{row}")
        assert step.value == f"=SUMIFS(cleaned[amount],cleaned[region],A{row})"
        assert format(value, "f") in step.note and category in step.action
    assert " | ".join(chart.categories) in text
    low, high = value_axis(chart)
    assert f"Minimum = {low}; Maximum = {high}; Major = {(high - low) / 4}" in text
    assert "Amount (THB)" in text and "Region" in text
    assert "Clustered Column" in text and "Legend ▸ None" in text
    assert format(chart.aggregate_total, "f") in markdown
    assert "| Chart | Clustered Column |" in markdown


def test_count_avg_and_other_bucket_formulas() -> None:
    guide, chart, _ = _guide(x_column="region", aggregation="count", title="Count")
    assert next(step for step in guide.steps if step.target == "B2").value == "=COUNTIFS(cleaned[region],A2)"
    guide, chart, _ = _guide(x_column="region", y_column="amount", aggregation="avg", title="Avg", sort="value_desc", top_n=2, group_rest_as="Other")
    assert chart.categories == ["BKK", "LPG", "Other"] and chart.folded_categories == ["CNX", "KKC"]
    other = next(step for step in guide.steps if step.target == "B4")
    assert other.value.startswith("=IFERROR(SUMPRODUCT(SUMIFS(cleaned[amount],cleaned[region],$E$2:$E$3))")
    assert chart.values[-1] == Decimal("1.5")
    assert any(step.target == "E2:E3" and step.value == "CNX | KKC" for step in guide.steps)


def test_pie_and_horizontal_bar_steps_mirror_renderer() -> None:
    guide, chart, _ = _guide(x_column="region", y_column="amount", aggregation="sum", title="Share", chart_type="pie")
    text = _values(guide)
    assert "2-D Pie" in text and "Angle of first slice" in text
    for index, percent in enumerate(pie_percentages(chart.values)):
        assert PIE_COLORS[index].upper() in text and f"{percent}%" in text
    guide, _, _ = _guide(x_column="region", y_column="amount", aggregation="sum", title="H", chart_type="horizontal_bar", value_decimals=1, thousands_separator=True, data_labels=True)
    text = _values(guide)
    assert "Clustered Bar" in text and "Categories in reverse order" in text
    assert "#,##0.0" in text and "Data Labels ▸ Outside End" in text
    assert "Axis Titles ▸ Primary Vertical Region" in text.replace("  ", " ")


def test_spec_axis_labels_override_defaults() -> None:
    guide, chart, _ = _guide(x_column="region", y_column="amount", aggregation="sum", title="T", x_axis_label="Branch", y_axis_label="Net")
    assert isinstance(chart.spec, ChartSpec)
    values = [step.value for step in guide.steps]
    assert "Branch" in values and "Net" in values
