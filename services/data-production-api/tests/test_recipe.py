from __future__ import annotations

from decimal import Decimal

import pytest
from data_ir import DatasetRequest
from data_production.recipe import DatasetBuildError, build_dataset_ir


def test_cleaner_applies_trim_fill_filter_sort_and_preserves_lineage() -> None:
    request = DatasetRequest(
        dataset_id="sales",
        title="Sales",
        csv_text="region,amount\n  BKK ,10.5\nCNX,\nBKK,5.5\n",
        columns=[
            {"key": "region", "label": "Region", "data_type": "text"},
            {"key": "amount", "label": "Amount", "data_type": "decimal"},
        ],
        transforms=[
            {"op": "trim_text", "column": "region"},
            {"op": "fill_null", "column": "amount", "value": "0"},
            {"op": "filter_equals", "column": "region", "value": "BKK"},
            {"op": "sort_by", "column": "amount", "direction": "desc"},
        ],
    )
    dataset = build_dataset_ir(request)
    assert dataset.rows == [{"region": "BKK", "amount": Decimal("10.5")}, {"region": "BKK", "amount": Decimal("5.5")}]
    assert dataset.transform_log == ["trim_text:region", "fill_null:amount", "filter_equals:region", "sort_by:amount"]
    assert dataset.lineage["rows[0].amount"].startswith("input.csv:row:2")


def test_cleaner_rejects_bad_typed_input_and_empty_filter() -> None:
    with pytest.raises(DatasetBuildError, match="invalid_decimal"):
        build_dataset_ir({"dataset_id": "bad", "csv_text": "amount\nnope\n", "columns": [{"key": "amount", "label": "Amount", "data_type": "decimal"}]})
    with pytest.raises(DatasetBuildError, match="empty_dataset"):
        build_dataset_ir({"dataset_id": "empty", "csv_text": "region\nBKK\n", "columns": [{"key": "region", "label": "Region", "data_type": "text"}], "transforms": [{"op": "filter_equals", "column": "region", "value": "CNX"}]})


def test_inferred_schema_supports_unicode_and_space_headers() -> None:
    dataset = build_dataset_ir({"dataset_id": "headers", "csv_text": "สินค้า, sales region\nกาแฟ, BKK\n"})
    assert [column.key for column in dataset.columns] == ["สินค้า", "sales_region"]
    assert dataset.rows == [{"สินค้า": "กาแฟ", "sales_region": " BKK"}]
