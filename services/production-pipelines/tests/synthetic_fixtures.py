"""Synthetic monthly report fixtures shared by the pipeline tests.

The fixture data is synthetic sales data; it never carries private content.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
from pathlib import Path
from typing import Any


def synthetic_csv_text() -> str:
    """Five synthetic sales rows with Thai headers and a decimal amount."""

    rows = [
        ("หมวด", "ยอดขาย", "วันที่"),
        ("อาหาร", "1200.50", "2026-09-01"),
        ("อาหาร", "800", "2026-09-02"),
        ("เครื่องดื่ม", "450.75", "2026-09-01"),
        ("เครื่องดื่ม", "300", "2026-09-03"),
        ("ของใช้", "220", "2026-09-02"),
    ]
    buffer = io.StringIO()
    csv.writer(buffer, lineterminator="\n").writerows(rows)
    return buffer.getvalue()


def synthetic_config_payload() -> dict[str, Any]:
    """Return a valid ``pipeline-report-config.v1`` payload."""

    return {
        "schema_version": "pipeline-report-config.v1",
        "pipeline_id": "monthly-report-2026-09",
        "dataset": {
            "dataset_id": "sales-2026-09",
            "title": "ยอดขาย กันยายน 2026",
            "delimiter": ",",
            "columns": [
                {"key": "category", "label": "หมวด", "data_type": "text", "source_name": "หมวด"},
                {"key": "amount", "label": "ยอดขาย", "data_type": "decimal", "source_name": "ยอดขาย"},
                {"key": "sold_on", "label": "วันที่", "data_type": "date", "source_name": "วันที่"},
            ],
            "transforms": [{"op": "sort_by", "column": "amount", "direction": "desc"}],
            "csv_text": synthetic_csv_text(),
            "source_ref": "sales-2026-09.csv",
        },
        "chart": {
            "chart_type": "bar",
            "x_column": "category",
            "y_column": "amount",
            "aggregation": "sum",
            "title": "ยอดขายตามหมวด",
            "value_unit": "THB",
        },
        "workbook": {
            "workbook_id": "sales-2026-09-workbook",
            "title": "สมุดรายงาน กันยายน 2026",
            "locale": "th-TH",
        },
        "document": {
            "document_id": "monthly-report-2026-09",
            "title": "รายงานรายเดือน กันยายน 2026",
            "locale": "th-TH",
            "currency": "THB",
            "customer_name": "ฝ่ายขาย",
            "issue_date": "2026-09-30",
            "summary_lines": ["ช่วงข้อมูล: กันยายน 2026", "แหล่งข้อมูล: sales-2026-09.csv"],
        },
    }


def write_config(path: Path, overrides: dict[str, Any] | None = None) -> Path:
    """Write the synthetic config (optionally deep-merged with overrides)."""

    payload = synthetic_config_payload()
    if overrides:
        for key, value in overrides.items():
            if isinstance(value, dict) and isinstance(payload.get(key), dict):
                payload[key].update(value)
            else:
                payload[key] = value
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def sha256_of(data: bytes) -> str:
    """Deterministic content hash helper for fake stage artifacts."""

    return hashlib.sha256(data).hexdigest()
