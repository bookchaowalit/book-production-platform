from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

import pytest
from data_ir import ChartSpec

from data_production.aggregation import ChartBuildError, build_chart_ir
from data_production.chart_checks import ALLOWED_CHART_TYPES
from data_production.qa import qa_outputs
from data_production.recipe import build_dataset_ir
from data_production.render import display_number, excel_number_format, render_outputs

CSV = "region,amount\nBKK,1200.5\nCNX,300\nHKT,2500\nKKC,300\nLPG,50\n"


def _chart(**spec: Any) -> Any:
    dataset = build_dataset_ir({"dataset_id": "p", "csv_text": CSV})
    return dataset, build_chart_ir(dataset, ChartSpec(x_column="region", y_column="amount", aggregation="sum", **spec))


def _svg(tmp_path: Path, dataset: Any, chart: Any) -> ElementTree.Element:
    render_outputs(dataset, chart, tmp_path)
    assert qa_outputs(dataset, chart, tmp_path) == ()
    return ElementTree.fromstring((tmp_path / "chart.svg").read_text(encoding="utf-8"))


def _texts(root: ElementTree.Element, css: str) -> list[str | None]:
    return [node.text for node in root.findall(f".//*[@class='{css}']")]


def test_ai_allowlist_stays_bar_and_line() -> None:
    assert ALLOWED_CHART_TYPES == ("bar", "line")


def test_sort_value_desc_with_top_n_and_other_bucket() -> None:
    _, chart = _chart(sort="value_desc", top_n=2, group_rest_as="Other")
    assert chart.categories == ["HKT", "BKK", "Other"]
    assert chart.values == [Decimal(2500), Decimal("1200.5"), Decimal(650)]
    assert chart.folded_categories == ["CNX", "KKC", "LPG"]
    assert chart.aggregate_total == Decimal("4350.5")


def test_sort_ties_keep_first_appearance_and_top_n_without_bucket_drops_rest() -> None:
    _, chart = _chart(sort="value_asc", top_n=3)
    assert chart.categories == ["LPG", "CNX", "KKC"]
    assert chart.folded_categories == []
    assert all("HKT" not in ref for ref in chart.source_refs)


def test_category_sort_uses_typed_values_and_nulls_last() -> None:
    dataset = build_dataset_ir({"dataset_id": "n", "csv_text": "n,v\n10,1\n9,1\n,1\n100,1\n"})
    chart = build_chart_ir(dataset, ChartSpec(x_column="n", aggregation="count", sort="category_asc"))
    assert chart.categories == ["9", "10", "100", "(null)"]


@pytest.mark.parametrize(
    ("spec", "code"),
    [
        ({"chart_type": "line", "sort": "value_desc"}, "line_requires_category_order"),
        ({"group_rest_as": "Other"}, "group_rest_requires_top_n"),
        ({"top_n": 1, "group_rest_as": "HKT", "sort": "value_desc"}, "group_label_conflict"),
        ({"chart_type": "pie", "aggregation": "avg"}, "pie_requires_sum_or_count"),
    ],
)
def test_invalid_display_combinations_are_rejected(spec: dict[str, Any], code: str) -> None:
    dataset = build_dataset_ir({"dataset_id": "p", "csv_text": CSV})
    base: dict[str, Any] = {"x_column": "region", "y_column": "amount", "aggregation": "sum"}
    with pytest.raises(ChartBuildError, match=code):
        build_chart_ir(dataset, ChartSpec(**{**base, **spec}))


def test_pie_renders_slices_legend_percentages_and_no_axes(tmp_path: Path) -> None:
    dataset, chart = _chart(chart_type="pie", data_labels=True)
    root = _svg(tmp_path, dataset, chart)
    assert len(root.findall(".//*[@class='pie-slice']")) == 5
    assert _texts(root, "legend-label") == ["BKK (27.6%)", "CNX (6.9%)", "HKT (57.5%)", "KKC (6.9%)", "LPG (1.1%)"]
    assert _texts(root, "x-axis-label") == [] and _texts(root, "y-tick") == []
    report = json.loads((tmp_path / "report.json").read_text(encoding="utf-8"))
    assert report["chart"]["percentages"] == ["27.6", "6.9", "57.5", "6.9", "1.1"]


def test_pie_rejects_too_many_slices_and_negative_values() -> None:
    many = build_dataset_ir({"dataset_id": "m", "csv_text": "k\n" + "".join(f"c{i}\n" for i in range(13))})
    with pytest.raises(ChartBuildError, match="pie_too_many_slices"):
        build_chart_ir(many, ChartSpec(x_column="k", aggregation="count", chart_type="pie"))
    chart = build_chart_ir(many, ChartSpec(x_column="k", aggregation="count", chart_type="pie", top_n=11, group_rest_as="Other"))
    assert len(chart.categories) == 12 and chart.values[-1] == 2
    negative = build_dataset_ir({"dataset_id": "n", "csv_text": "k,v\nA,-1\nB,3\n"})
    with pytest.raises(ChartBuildError, match="pie_requires_non_negative_values"):
        build_chart_ir(negative, ChartSpec(x_column="k", y_column="v", aggregation="sum", chart_type="pie"))


def test_horizontal_bar_has_value_ticks_category_labels_and_data_labels(tmp_path: Path) -> None:
    dataset, chart = _chart(chart_type="horizontal_bar", thousands_separator=True, value_decimals=0, data_labels=True)
    root = _svg(tmp_path, dataset, chart)
    assert _texts(root, "category-label") == ["BKK", "CNX", "HKT", "KKC", "LPG"]
    assert _texts(root, "x-tick-label") == ["0", "800", "1,600", "2,400", "3,200"]
    assert _texts(root, "data-label") == ["1,201", "300", "2,500", "300", "50"]
    assert _texts(root, "y-axis-label") == ["amount"]


@pytest.mark.parametrize(
    ("value", "spec", "text"),
    [
        ("1234.5", {"value_decimals": 2, "thousands_separator": True}, "1,234.50"),
        ("2.345", {"value_decimals": 2}, "2.35"),
        ("2.5", {"value_decimals": 0}, "3"),
        ("-2.5", {"value_decimals": 0}, "-3"),
        ("-0.004", {"value_decimals": 2}, "0.00"),
        ("1234567.250", {"thousands_separator": True}, "1,234,567.25"),
        ("10.50", {}, "10.5"),
    ],
)
def test_number_format_rounds_half_away_from_zero(value: str, spec: dict[str, Any], text: str) -> None:
    assert display_number(Decimal(value), ChartSpec(x_column="x", **spec)) == text


def test_excel_number_format_codes() -> None:
    assert excel_number_format(ChartSpec(x_column="x"), [Decimal(1)]) == "General"
    assert excel_number_format(ChartSpec(x_column="x", value_decimals=2, thousands_separator=True), []) == "#,##0.00"
    assert excel_number_format(ChartSpec(x_column="x", thousands_separator=True), [Decimal("1.25")]) == "#,##0.00"


def test_column_chart_data_labels_and_qa_detects_tampered_label(tmp_path: Path) -> None:
    dataset, chart = _chart(data_labels=True, value_decimals=1)
    root = _svg(tmp_path, dataset, chart)
    assert _texts(root, "data-label") == ["1200.5", "300.0", "2500.0", "300.0", "50.0"]
    svg = (tmp_path / "chart.svg").read_text(encoding="utf-8").replace(">2500.0<", ">2600.0<")
    (tmp_path / "chart.svg").write_text(svg, encoding="utf-8")
    assert "chart_data_labels_mismatch" in qa_outputs(dataset, chart, tmp_path)


def test_default_spec_has_no_data_labels(tmp_path: Path) -> None:
    dataset, chart = _chart()
    root = _svg(tmp_path, dataset, chart)
    assert _texts(root, "data-label") == []
    assert _texts(root, "y-tick-label") == ["0", "800", "1600", "2400", "3200"]
