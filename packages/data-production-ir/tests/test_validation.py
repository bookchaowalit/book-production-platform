from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from data_ir import (
    DataContractError,
    DatasetRequest,
    infer_column_type,
    parse_csv_rows,
    parse_typed_value,
    validate_request,
)


def test_typed_values_and_inference_are_deterministic() -> None:
    assert parse_typed_value("12", "integer", "row[1].count") == 12
    assert parse_typed_value("12.50", "decimal", "row[1].amount") == Decimal("12.50")
    assert parse_typed_value("2026-09-22", "date", "row[1].day") == date(2026, 9, 22)
    assert parse_typed_value("yes", "boolean", "row[1].active") is True
    assert infer_column_type(["1", "2"]) == "integer"
    assert infer_column_type(["1.2", "2.3"]) == "decimal"
    assert infer_column_type(["001", "002"]) == "text"


def test_csv_parser_handles_quoted_commas_and_rejects_duplicate_headers() -> None:
    headers, rows = parse_csv_rows('name,amount\n"Bangkok, TH",12.5\n')
    assert headers == ["name", "amount"]
    assert rows == [["Bangkok, TH", "12.5"]]
    with pytest.raises(DataContractError, match="duplicate_header"):
        parse_csv_rows("name,name\na,b\n")


def test_csv_parser_rejects_unbounded_row_count() -> None:
    with pytest.raises(DataContractError, match="row_limit_exceeded"):
        parse_csv_rows("value\n" + "1\n" * 10001)


def test_request_validation_rejects_unknown_references() -> None:
    request = DatasetRequest(
        dataset_id="sales",
        csv_text="region,total\nA,10\n",
        columns=[{"key": "region", "label": "Region", "data_type": "text", "source_name": "region"}],
        transforms=[{"op": "trim_text", "column": "missing"}],
        chart={"x_column": "missing", "aggregation": "count"},
    )
    issues = validate_request(request)
    assert {issue.code for issue in issues} == {"unknown_transform_column", "unknown_chart_column"}


def test_chart_display_defaults_match_declared_defaults() -> None:
    from data_ir import ChartSpec
    from data_ir.models import CHART_DISPLAY_DEFAULTS

    spec = ChartSpec(x_column="x")
    assert {key: getattr(spec, key) for key in CHART_DISPLAY_DEFAULTS} == CHART_DISPLAY_DEFAULTS
    assert ChartSpec(x_column="x", chart_type="pie", top_n=3, group_rest_as="Other").chart_type == "pie"
