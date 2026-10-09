"""Deterministic builders for the inter-stage handoff payloads.

Each builder consumes only validated contract data (the pipeline config plus
hash-verified artifacts of previous stages) and emits the next stage's
versioned request payload. No stage logic is re-implemented here: typed value
parsing stays with the receiving services.
"""

from __future__ import annotations

import csv
import io
from typing import Any

from .contracts import (
    CONTRACT_DATASET_REQUEST,
    CONTRACT_REPORT_REQUEST,
    CONTRACT_WORKBOOK_REQUEST,
    ContractError,
    PipelineReportConfig,
    schema_issues,
    workbook_key_for,
)

__all__ = [
    "build_data_stage_request",
    "build_document_stage_request",
    "build_viz_stage_request",
    "build_workbook_stage_request",
]


def _require_valid(contract: str, payload: Any) -> None:
    issues = schema_issues(contract, payload)
    if issues:
        raise ContractError(contract, issues)


def build_data_stage_request(config: PipelineReportConfig) -> dict[str, Any]:
    """Data pipeline stage request: cleaning only, no chart."""

    payload: dict[str, Any] = {
        "schema_version": CONTRACT_DATASET_REQUEST,
        "dataset_id": config.dataset.dataset_id,
        "title": config.dataset.title,
        "delimiter": config.dataset.delimiter,
        "columns": [column.model_dump(mode="json", exclude_none=True) for column in config.dataset.columns],
        "transforms": [transform.model_dump(mode="json", exclude_none=True) for transform in config.dataset.transforms],
        "csv_text": config.dataset.csv_text,
        "source_ref": config.dataset.source_ref,
    }
    _require_valid(CONTRACT_DATASET_REQUEST, payload)
    return payload


def build_viz_stage_request(config: PipelineReportConfig, cleaned_csv_text: str, source_ref: str) -> dict[str, Any]:
    """Data viz stage request: chart build over the cleaned stage artifact.

    The cleaned CSV headers are the dataset column keys (rendered by the
    data stage), so the viz request columns deliberately omit
    ``source_name`` and let the receiving service resolve keys directly.
    """

    payload: dict[str, Any] = {
        "schema_version": CONTRACT_DATASET_REQUEST,
        "dataset_id": config.dataset.dataset_id,
        "title": config.dataset.title,
        "delimiter": config.dataset.delimiter,
        "columns": [
            {key: value for key, value in column.model_dump(mode="json").items() if key != "source_name"}
            for column in config.dataset.columns
        ],
        "transforms": [],
        "chart": config.chart.model_dump(mode="json", exclude_none=True),
        "csv_text": cleaned_csv_text,
        "source_ref": source_ref,
    }
    _require_valid(CONTRACT_DATASET_REQUEST, payload)
    return payload


def build_workbook_stage_request(config: PipelineReportConfig, columns: list[dict[str, Any]], cleaned_csv_text: str, source_ref: str) -> dict[str, Any]:
    """Spreadsheet stage request built from the cleaned CSV and typed columns.

    Cell values pass through as CSV strings; the receiving spreadsheet
    recipe performs the typed parsing against the declared column types.
    Empty CSV cells become ``None`` (null cells).
    """

    key_by_data_key = {
        column["key"]: workbook_key_for(column["key"], index, config.workbook.column_keys)
        for index, column in enumerate(columns, start=1)
    }
    if len(set(key_by_data_key.values())) != len(key_by_data_key):
        raise ContractError(CONTRACT_WORKBOOK_REQUEST, ["workbook column keys must be unique after mapping"])

    table_columns = [
        {
            "key": key_by_data_key[column["key"]],
            "label": column["label"],
            "data_type": column["data_type"],
            "required": bool(column.get("required", False)),
        }
        for column in columns
    ]
    reader = csv.DictReader(io.StringIO(cleaned_csv_text))
    if reader.fieldnames is None or not reader.fieldnames:
        raise ContractError(CONTRACT_WORKBOOK_REQUEST, ["cleaned csv has no header row"])
    unknown_headers = [header for header in reader.fieldnames if header not in key_by_data_key]
    if unknown_headers:
        raise ContractError(CONTRACT_WORKBOOK_REQUEST, [f"cleaned csv headers are not dataset column keys: {', '.join(unknown_headers)}"])
    rows: list[dict[str, Any]] = []
    for raw_row in reader:
        row: dict[str, Any] = {}
        for data_key, value in raw_row.items():
            row[key_by_data_key[data_key]] = None if value == "" else value
        rows.append(row)

    payload: dict[str, Any] = {
        "schema_version": CONTRACT_WORKBOOK_REQUEST,
        "workbook_id": config.workbook.workbook_id,
        "title": config.workbook.title,
        "locale": config.workbook.locale,
        "tables": [
            {
                "name": "DetailSource",
                "columns": table_columns,
                "rows": rows,
                "source_ref": source_ref,
            }
        ],
        "formulas": [],
        "template_parameters": {},
    }
    _require_valid(CONTRACT_WORKBOOK_REQUEST, payload)
    return payload


def build_document_stage_request(
    config: PipelineReportConfig,
    chart_report: dict[str, Any],
    workbook_name: str,
    workbook_media_type: str,
    workbook_hash: str,
    workbook_size: int,
    chart_name: str,
    chart_media_type: str,
    chart_hash: str,
    chart_size: int,
) -> dict[str, Any]:
    """Document stage request: tabular report referencing charts and totals.

    The table is the chart aggregation (category, value) taken from the data
    viz stage ``report.json``; ``totals.subtotal`` is the chart
    ``aggregate_total`` so the document QA arithmetic re-derives it exactly.
    """

    chart = chart_report.get("chart")
    if not isinstance(chart, dict):
        raise ContractError(CONTRACT_DATASET_REQUEST, ["viz report payload has no chart block"])
    categories = chart.get("categories")
    values = chart.get("values")
    aggregate_total = chart.get("aggregate_total")
    if not isinstance(categories, list) or not isinstance(values, list) or not isinstance(aggregate_total, str):
        raise ContractError(CONTRACT_DATASET_REQUEST, ["viz report chart block is incomplete"])
    if len(categories) != len(values) or not categories:
        raise ContractError(CONTRACT_DATASET_REQUEST, ["viz report chart block categories/values are inconsistent"])

    value_unit = chart.get("value_unit")
    value_header = str(chart.get("y_axis_label") or "Value")
    if value_unit:
        value_header = f"{value_header} ({value_unit})"
    headers = [str(chart.get("x_axis_label") or "Category"), value_header]
    rows = [[str(category), str(value)] for category, value in zip(categories, values)]

    payload: dict[str, Any] = {
        "schema_version": "report-document-request.v1",
        "document_id": config.document.document_id,
        "title": config.document.title,
        "locale": config.document.locale,
        "currency": config.document.currency,
        "customer_name": config.document.customer_name,
        "issue_date": config.document.issue_date.isoformat(),
        "summary_lines": list(config.document.summary_lines),
        "table": {"headers": headers, "rows": rows},
        "totals": {
            "subtotal": aggregate_total,
            "tax_total": "0",
            "grand_total": aggregate_total,
        },
        "charts": [
            {
                "name": chart_name,
                "media_type": chart_media_type,
                "content_hash": chart_hash,
                "byte_size": chart_size,
            }
        ],
        "workbook": {
            "name": workbook_name,
            "media_type": workbook_media_type,
            "content_hash": workbook_hash,
            "byte_size": workbook_size,
        },
    }
    report_issues = schema_issues(CONTRACT_REPORT_REQUEST, payload)
    if report_issues:
        raise ContractError(CONTRACT_REPORT_REQUEST, report_issues)
    return payload
