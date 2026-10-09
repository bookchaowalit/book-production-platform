from __future__ import annotations

from decimal import Decimal

from data_ir import ChartSpec, DatasetRequest
from data_production.aggregation import build_chart_ir
from data_production.recipe import build_dataset_ir


def test_chart_aggregation_uses_normalized_rows_and_lineage() -> None:
    dataset = build_dataset_ir(
        DatasetRequest(
            dataset_id="orders",
            csv_text="region,amount\n BKK ,10\nBKK,5\nCNX,2\n",
            columns=[
                {"key": "region", "label": "Region", "data_type": "text"},
                {"key": "amount", "label": "Amount", "data_type": "integer"},
            ],
            transforms=[{"op": "trim_text", "column": "region"}],
        )
    )
    chart = build_chart_ir(dataset, ChartSpec(x_column="region", y_column="amount", aggregation="sum", title="Sales"))
    assert chart.categories == ["BKK", "CNX"]
    assert chart.values == [Decimal(15), Decimal(2)]
    assert chart.aggregate_total == Decimal(17)
    assert len(chart.source_refs) == 6


def test_count_chart_does_not_require_numeric_y_column() -> None:
    dataset = build_dataset_ir({"dataset_id": "counts", "csv_text": "kind\nA\nA\nB\n"})
    chart = build_chart_ir(dataset, ChartSpec(x_column="kind", aggregation="count"))
    assert chart.values == [Decimal(2), Decimal(1)]
    assert chart.spec.x_axis_label == "kind"
    assert chart.spec.y_axis_label == "Count"
    assert chart.spec.value_unit is None


def test_chart_axis_labels_default_to_typed_schema_labels() -> None:
    dataset = build_dataset_ir(
        DatasetRequest(
            dataset_id="axis-defaults",
            csv_text="area,net\nNorth,10\nSouth,20\n",
            columns=[
                {"key": "area", "label": "Sales region", "data_type": "text"},
                {"key": "net", "label": "Net revenue", "data_type": "integer"},
            ],
        )
    )

    chart = build_chart_ir(dataset, ChartSpec(x_column="area", y_column="net", aggregation="sum"))

    assert chart.spec.x_axis_label == "Sales region"
    assert chart.spec.y_axis_label == "Net revenue"
    assert chart.spec.value_unit is None
