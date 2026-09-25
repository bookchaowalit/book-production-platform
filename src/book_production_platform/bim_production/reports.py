"""Deterministic takeoff report and CSV rendering with canonical bytes."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import os
from decimal import Decimal
from pathlib import Path
from typing import Any

from book_production_platform.bim_ir import BimTakeoffIR
from book_production_platform.bim_production.extract import (
    ExtractionResult,
    QuantityRow,
    aggregate_rows,
    report_status,
)

REPORT_SCHEMA_VERSION = "bim-takeoff-report.v1"
CSV_COLUMNS = (
    "global_id",
    "ifc_class",
    "qto_name",
    "quantity_name",
    "measure_kind",
    "method",
    "value",
    "unit",
)


def _canonical_json_bytes(value: object) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        + b"\n"
    )


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _decimal_text(value: Decimal) -> str:
    if not value.is_finite():
        raise ValueError("cannot serialize a non-finite quantity")
    return str(value)


def _validation_payload(ifc_summary: dict[str, Any]) -> dict[str, object]:
    expected = {
        "schema",
        "project_identity_hash",
        "total_instances",
        "selected_elements",
        "validation_findings",
    }
    if set(ifc_summary) != expected:
        raise ValueError("ifc summary is missing or carries unknown fields")
    if not isinstance(ifc_summary["validation_findings"], int):
        raise TypeError("validation findings must be an integer count")
    return {
        "ifc_schema": str(ifc_summary["schema"]),
        "project_identity_hash": str(ifc_summary["project_identity_hash"]),
        "total_instances": int(ifc_summary["total_instances"]),
        "selected_elements": int(ifc_summary["selected_elements"]),
        "validation_findings": int(ifc_summary["validation_findings"]),
    }


def build_takeoff_report(
    ir: BimTakeoffIR,
    source_sha256: str,
    ifc_summary: dict[str, Any],
    extraction: ExtractionResult,
) -> dict[str, object]:
    """Assemble the deterministic report document from validated inputs."""

    if len(source_sha256) != 64:
        raise ValueError("source hash must be a SHA-256 hex digest")
    validation = _validation_payload(ifc_summary)
    aggregates = aggregate_rows(extraction.rows)
    return {
        "schema_version": REPORT_SCHEMA_VERSION,
        "takeoff_id": ir.takeoff_id,
        "element_classes": list(ir.element_classes),
        "output_units": ir.output_units,
        "source_sha256": source_sha256,
        **validation,
        "status": report_status(extraction),
        "rows": [
            {
                "global_id": row.global_id,
                "ifc_class": row.ifc_class,
                "qto_name": row.qto_name,
                "quantity_name": row.quantity_name,
                "measure_kind": row.measure_kind,
                "method": row.method,
                "value": _decimal_text(row.value),
                "unit": row.unit,
            }
            for row in extraction.rows
        ],
        "aggregates": [
            {
                "ifc_class": aggregate.ifc_class,
                "qto_name": aggregate.qto_name,
                "quantity_name": aggregate.quantity_name,
                "measure_kind": aggregate.measure_kind,
                "unit": aggregate.unit,
                "method": aggregate.method,
                "value": _decimal_text(aggregate.value),
            }
            for aggregate in aggregates
        ],
        "coverage_gaps": [
            {
                "reason": finding.reason,
                "global_id": finding.global_id,
                "ifc_class": finding.ifc_class,
                "qto_name": finding.qto_name,
                "quantity_name": finding.quantity_name,
            }
            for finding in extraction.findings
        ],
    }


def render_takeoff_json(report: dict[str, object]) -> bytes:
    """Render canonical report bytes: UTF-8, key-sorted, compact, one newline."""

    if report.get("schema_version") != REPORT_SCHEMA_VERSION:
        raise ValueError("report does not carry the supported schema version")
    return _canonical_json_bytes(report)


def report_content_hash(report_bytes: bytes) -> str:
    return _sha256_bytes(report_bytes)


def csv_bytes(rows: tuple[QuantityRow, ...] | list[QuantityRow]) -> bytes:
    """Render stable RFC 4180 CSV bytes for the given rows."""

    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer, delimiter=",", quotechar='"', lineterminator="\r\n")
    writer.writerow(CSV_COLUMNS)
    for row in rows:
        writer.writerow(
            [
                row.global_id,
                row.ifc_class,
                row.qto_name,
                row.quantity_name,
                row.measure_kind,
                row.method if row.method is not None else "",
                _decimal_text(row.value),
                row.unit,
            ]
        )
    return buffer.getvalue().encode("utf-8")


def write_takeoff_csv(rows: tuple[QuantityRow, ...] | list[QuantityRow], path: Path) -> None:
    """Write the CSV bytes to staging, replacing any previous temporary file."""

    payload = csv_bytes(rows)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_bytes(payload)
    os.replace(temporary, path)
