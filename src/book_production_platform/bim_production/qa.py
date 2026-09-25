"""Staged artifact QA and the privacy-safe QA manifest for takeoff jobs."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

from book_production_platform.bim_ir import BimTakeoffIR
from book_production_platform.bim_production.errors import BimProductionError
from book_production_platform.bim_production.extract import (
    MAX_EXTRACTION_RECORDS,
    ExtractionResult,
)
from book_production_platform.bim_production.reports import (
    CSV_COLUMNS,
    REPORT_SCHEMA_VERSION,
    _canonical_json_bytes,
)
from book_production_platform.bim_production.units import COVERAGE_REASONS

QA_SCHEMA_VERSION = "bim-takeoff-qa.v1"
ARTIFACT_NAMES = ("takeoff.csv", "takeoff.json", "qa-manifest.json")
MAX_ARTIFACT_BYTES = 32 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class BimQAResult:
    """Verified staged artifact facts used for publication and the receipt."""

    status: str
    row_count: int
    coverage_gap_count: int
    artifact_hashes: dict[str, str]
    artifact_bytes: dict[str, int]


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _reject_non_finite(value: str) -> None:
    raise ValueError(f"non-finite JSON constant: {value}")


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON object key")
        result[key] = value
    return result


def _load_bounded_json(path: Path, limit: int) -> dict[str, Any]:
    payload = path.read_bytes()
    if len(payload) > limit:
        raise BimProductionError("takeoff_integrity_error")
    try:
        value = json.loads(
            payload.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_non_finite,
        )
    except (UnicodeDecodeError, ValueError) as error:
        raise BimProductionError("takeoff_integrity_error") from error
    if not isinstance(value, dict):
        raise BimProductionError("takeoff_integrity_error")
    return value


def build_coverage_gaps(
    extraction: ExtractionResult,
) -> list[dict[str, str | None]]:
    return [
        {
            "reason": finding.reason,
            "global_id": finding.global_id,
            "ifc_class": finding.ifc_class,
            "qto_name": finding.qto_name,
            "quantity_name": finding.quantity_name,
        }
        for finding in extraction.findings
    ]


def build_qa_manifest(
    ir: BimTakeoffIR,
    runtime: dict[str, str],
    source_sha256: str,
    ifc_summary: dict[str, Any],
    extraction: ExtractionResult,
    report_bytes: bytes,
    csv_bytes_payload: bytes,
    report: dict[str, Any],
) -> dict[str, object]:
    """Assemble the QA manifest over the pre-publication staged artifacts."""

    runtime_keys = {
        "freecad_version",
        "freecad_build",
        "ifcopenshell_version",
        "python_version",
        "platform",
        "freecadcmd_sha256",
        "wrapper_sha256",
    }
    if set(runtime) != runtime_keys or not all(
        isinstance(runtime[key], str) and runtime[key] for key in runtime_keys
    ):
        raise ValueError("runtime identity is incomplete")
    reasons: dict[str, int] = {}
    for finding in extraction.findings:
        reasons[finding.reason] = reasons.get(finding.reason, 0) + 1
    return {
        "schema_version": QA_SCHEMA_VERSION,
        "report_schema_version": REPORT_SCHEMA_VERSION,
        "takeoff_id": ir.takeoff_id,
        "runtime": dict(runtime),
        "source_sha256": source_sha256,
        "ifc_schema": str(ifc_summary["schema"]),
        "total_instances": int(ifc_summary["total_instances"]),
        "selected_elements": int(ifc_summary["selected_elements"]),
        "status": report["status"],
        "row_count": len(extraction.rows),
        "coverage_gap_count": len(extraction.findings),
        "coverage_reasons": dict(sorted(reasons.items())),
        "artifacts": {
            "takeoff.json": {
                "bytes": len(report_bytes),
                "sha256": _sha256_bytes(report_bytes),
            },
            "takeoff.csv": {
                "bytes": len(csv_bytes_payload),
                "sha256": _sha256_bytes(csv_bytes_payload),
            },
        },
        "model_calls": 0,
        "monetary_cost": "unknown",
    }


def render_qa_manifest_json(manifest: dict[str, object]) -> bytes:
    if manifest.get("schema_version") != QA_SCHEMA_VERSION:
        raise ValueError("manifest does not carry the supported schema version")
    return _canonical_json_bytes(manifest)


def _parse_csv(payload: bytes) -> list[dict[str, str]]:
    import csv
    import io

    text = payload.decode("utf-8")
    reader = csv.DictReader(io.StringIO(text, newline=""), delimiter=",", quotechar='"')
    if reader.fieldnames != list(CSV_COLUMNS):
        raise BimProductionError("takeoff_integrity_error")
    rows: list[dict[str, str]] = []
    for row in reader:
        if None in row.values() or any(value is None for value in row.values()):
            raise BimProductionError("takeoff_integrity_error")
        rows.append({key: str(value) for key, value in row.items()})
    return rows


def verify_takeoff_artifacts(staging: Path) -> BimQAResult:
    """Verify exactly three staged artifacts before a job may publish."""

    present = sorted(entry.name for entry in staging.iterdir() if entry.is_file())
    if present != sorted(ARTIFACT_NAMES):
        raise BimProductionError("takeoff_integrity_error")

    report_payload = (staging / "takeoff.json").read_bytes()
    csv_payload = (staging / "takeoff.csv").read_bytes()
    manifest_payload = (staging / "qa-manifest.json").read_bytes()
    for name, payload in (
        ("takeoff.json", report_payload),
        ("takeoff.csv", csv_payload),
        ("qa-manifest.json", manifest_payload),
    ):
        if not payload or len(payload) > MAX_ARTIFACT_BYTES:
            raise BimProductionError("takeoff_integrity_error")

    report = _load_bounded_json(staging / "takeoff.json", MAX_ARTIFACT_BYTES)
    manifest = _load_bounded_json(staging / "qa-manifest.json", MAX_ARTIFACT_BYTES)
    if report.get("schema_version") != REPORT_SCHEMA_VERSION:
        raise BimProductionError("takeoff_integrity_error")
    if manifest.get("schema_version") != QA_SCHEMA_VERSION:
        raise BimProductionError("takeoff_integrity_error")
    if manifest.get("report_schema_version") != REPORT_SCHEMA_VERSION:
        raise BimProductionError("takeoff_integrity_error")
    if manifest.get("takeoff_id") != report.get("takeoff_id"):
        raise BimProductionError("takeoff_integrity_error")
    if manifest.get("source_sha256") != report.get("source_sha256"):
        raise BimProductionError("takeoff_integrity_error")
    if manifest.get("ifc_schema") != report.get("ifc_schema"):
        raise BimProductionError("takeoff_integrity_error")
    if manifest.get("model_calls") != 0:
        raise BimProductionError("takeoff_integrity_error")
    if manifest.get("monetary_cost") != "unknown":
        raise BimProductionError("takeoff_integrity_error")

    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, dict) or set(artifacts) != {"takeoff.json", "takeoff.csv"}:
        raise BimProductionError("takeoff_integrity_error")
    for name, payload in (("takeoff.json", report_payload), ("takeoff.csv", csv_payload)):
        entry = artifacts[name]
        if entry.get("bytes") != len(payload) or entry.get("sha256") != _sha256_bytes(payload):
            raise BimProductionError("takeoff_integrity_error")

    rows = report.get("rows")
    if not isinstance(rows, list) or len(rows) > MAX_EXTRACTION_RECORDS:
        raise BimProductionError("takeoff_integrity_error")
    csv_rows = _parse_csv(csv_payload)
    if len(csv_rows) != len(rows):
        raise BimProductionError("takeoff_integrity_error")
    for record, csv_row in zip(rows, csv_rows):
        if set(csv_row) != set(CSV_COLUMNS):
            raise BimProductionError("takeoff_integrity_error")
        for column in CSV_COLUMNS:
            expected = record.get(column)
            expected_text = "" if expected is None else str(expected)
            if csv_row[column] != expected_text:
                raise BimProductionError("takeoff_integrity_error")

    status = str(report.get("status"))
    gaps = report.get("coverage_gaps")
    if not isinstance(gaps, list):
        raise BimProductionError("takeoff_integrity_error")
    for gap in gaps:
        if not isinstance(gap, dict) or gap.get("reason") not in COVERAGE_REASONS:
            raise BimProductionError("takeoff_integrity_error")
    if status == "complete" and (gaps or not rows):
        raise BimProductionError("takeoff_integrity_error")
    if status == "partial" and (not rows or not gaps):
        raise BimProductionError("takeoff_integrity_error")
    if status not in {"complete", "partial"}:
        raise BimProductionError("takeoff_integrity_error")
    if manifest.get("status") != status:
        raise BimProductionError("takeoff_integrity_error")
    if manifest.get("row_count") != len(rows):
        raise BimProductionError("takeoff_integrity_error")
    if manifest.get("coverage_gap_count") != len(gaps):
        raise BimProductionError("takeoff_integrity_error")

    aggregates = report.get("aggregates")
    if not isinstance(aggregates, list) or not aggregates:
        raise BimProductionError("takeoff_integrity_error")
    _verify_aggregate_totals(rows, aggregates)

    return BimQAResult(
        status=status,
        row_count=len(rows),
        coverage_gap_count=len(gaps),
        artifact_hashes={
            "takeoff.json": _sha256_bytes(report_payload),
            "takeoff.csv": _sha256_bytes(csv_payload),
            "qa-manifest.json": _sha256_bytes(manifest_payload),
        },
        artifact_bytes={
            "takeoff.json": len(report_payload),
            "takeoff.csv": len(csv_payload),
            "qa-manifest.json": len(manifest_payload),
        },
    )


def _verify_aggregate_totals(
    rows: list[dict[str, Any]], aggregates: list[dict[str, Any]]
) -> None:
    totals: dict[tuple[object, ...], Decimal] = {}
    key: tuple[object, ...]
    for row in rows:
        key = (
            row["ifc_class"],
            row["qto_name"],
            row["quantity_name"],
            row["measure_kind"],
            row["unit"],
            row.get("method"),
        )
        totals[key] = totals.get(key, Decimal(0)) + Decimal(str(row["value"]))
    rebuilt: dict[tuple[object, ...], Decimal] = {}
    for aggregate in aggregates:
        if not isinstance(aggregate, dict) or set(aggregate) != {
            "ifc_class",
            "qto_name",
            "quantity_name",
            "measure_kind",
            "unit",
            "method",
            "value",
        }:
            raise BimProductionError("takeoff_integrity_error")
        key = (
            aggregate["ifc_class"],
            aggregate["qto_name"],
            aggregate["quantity_name"],
            aggregate["measure_kind"],
            aggregate["unit"],
            aggregate["method"],
        )
        rebuilt[key] = rebuilt.get(key, Decimal(0)) + Decimal(str(aggregate["value"]))
    if set(totals) != set(rebuilt):
        raise BimProductionError("takeoff_integrity_error")
    for key, total in totals.items():
        if rebuilt[key] != total:
            raise BimProductionError("takeoff_integrity_error")
