"""Tests for the inter-stage handoff request builders."""

from __future__ import annotations

import json

import pytest
from synthetic_fixtures import synthetic_config_payload, synthetic_csv_text

from production_pipelines.contracts import ContractError, parse_config
from production_pipelines.stage_requests import (
    build_data_stage_request,
    build_document_stage_request,
    build_viz_stage_request,
    build_workbook_stage_request,
)

VIZ_REPORT: dict = {
    "schema_version": "data-report.v1",
    "dataset_id": "sales-2026-09",
    "rows": 5,
    "chart": {
        "chart_type": "bar",
        "aggregation": "sum",
        "x_column": "category",
        "y_column": "amount",
        "x_axis_label": "หมวด",
        "y_axis_label": "ยอดขาย",
        "value_unit": "THB",
        "categories": ["อาหาร", "เครื่องดื่ม", "ของใช้"],
        "values": ["2000.50", "750.75", "220"],
        "aggregate_total": "2971.25",
        "row_count": 5,
        "source_refs": [],
    },
}

COLUMNS_REPORT: list[dict] = [
    {"key": "category", "label": "หมวด", "data_type": "text", "source_name": "หมวด", "required": False},
    {"key": "amount", "label": "ยอดขาย", "data_type": "decimal", "source_name": "ยอดขาย", "required": False},
    {"key": "sold_on", "label": "วันที่", "data_type": "date", "source_name": "วันที่", "required": False},
]


def _config():
    return parse_config(synthetic_config_payload())


def test_data_stage_request_is_schema_valid_and_has_no_chart():
    payload = build_data_stage_request(_config())
    assert payload["schema_version"] == "dataset-request.v1"
    assert "chart" not in payload
    assert payload["columns"][0]["source_name"] == "หมวด"


def test_viz_stage_request_embeds_cleaned_csv_and_drops_source_name():
    payload = build_viz_stage_request(_config(), synthetic_csv_text(), "pipeline:p:stage:data_pipeline:cleaned.csv")
    assert payload["chart"]["x_column"] == "category"
    assert payload["csv_text"].startswith("หมวด")
    assert all("source_name" not in column for column in payload["columns"])
    assert payload["transforms"] == []


def test_workbook_stage_request_passes_values_through_and_maps_keys():
    payload = build_workbook_stage_request(
        _config(),
        COLUMNS_REPORT,
        "category,amount,sold_on\nอาหาร,1200.50,2026-09-01\nเครื่องดื่ม,,2026-09-02\n",
        "pipeline:p:stage:data_pipeline:cleaned.csv",
    )
    table = payload["tables"][0]
    assert [column["key"] for column in table["columns"]] == ["category", "amount", "sold_on"]
    assert table["rows"][0] == {"category": "อาหาร", "amount": "1200.50", "sold_on": "2026-09-01"}
    assert table["rows"][1]["amount"] is None  # empty CSV cell becomes a null cell
    assert table["source_ref"].startswith("pipeline:")


def test_workbook_stage_request_uses_configured_key_mapping():
    config = synthetic_config_payload()
    config["workbook"]["column_keys"] = {"category": "group", "amount": "value", "sold_on": "day"}
    payload = build_workbook_stage_request(
        parse_config(config),
        COLUMNS_REPORT,
        "category,amount,sold_on\nอาหาร,1,2026-09-01\n",
        "pipeline:p",
    )
    assert [column["key"] for column in payload["tables"][0]["columns"]] == ["group", "value", "day"]


def test_workbook_stage_request_rejects_unexpected_cleaned_headers():
    with pytest.raises(Exception) as excinfo:
        build_workbook_stage_request(
            _config(),
            COLUMNS_REPORT,
            "หมวด,ยอดขาย,วันที่\nอาหาร,1,2026-09-01\n",
            "pipeline:p",
        )
    assert "not dataset column keys" in str(excinfo.value)


def test_document_stage_request_derives_table_and_totals_from_viz_report():
    payload = build_document_stage_request(
        _config(),
        VIZ_REPORT,
        workbook_name="workbook.xlsx",
        workbook_media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        workbook_hash="a" * 64,
        workbook_size=100,
        chart_name="chart.svg",
        chart_media_type="image/svg+xml",
        chart_hash="b" * 64,
        chart_size=10,
    )
    assert payload["table"]["headers"] == ["หมวด", "ยอดขาย (THB)"]
    assert payload["table"]["rows"] == [["อาหาร", "2000.50"], ["เครื่องดื่ม", "750.75"], ["ของใช้", "220"]]
    assert payload["totals"] == {"subtotal": "2971.25", "tax_total": "0", "grand_total": "2971.25"}
    assert payload["charts"][0]["content_hash"] == "b" * 64


def test_document_stage_request_rejects_inconsistent_chart_block():
    broken = json.loads(json.dumps(VIZ_REPORT))
    broken["chart"]["values"] = ["1.00"]
    with pytest.raises(ContractError) as excinfo:
        build_document_stage_request(
            _config(),
            broken,
            "workbook.xlsx",
            "application/octet-stream",
            "a" * 64,
            100,
            "chart.svg",
            "image/svg+xml",
            "b" * 64,
            10,
        )
    assert "chart block" in str(excinfo.value)


def test_document_stage_request_rejects_missing_chart_block():
    with pytest.raises(ContractError):
        build_document_stage_request(_config(), {}, "w", "m", "a" * 64, 1, "c", "m", "b" * 64, 1)
