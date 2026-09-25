from __future__ import annotations

import hashlib
import json
from decimal import Decimal
from pathlib import Path

import pytest

from book_production_platform.bim_ir import parse_bim_takeoff_ir
from book_production_platform.bim_production.errors import BimProductionError
from book_production_platform.bim_production.extract import (
    ExtractionFinding,
    ExtractionResult,
    QuantityRow,
)
from book_production_platform.bim_production.qa import (
    build_qa_manifest,
    render_qa_manifest_json,
    verify_takeoff_artifacts,
)
from book_production_platform.bim_production.reports import (
    build_takeoff_report,
    csv_bytes,
    render_takeoff_json,
    write_takeoff_csv,
)

RUNTIME = {
    "freecad_version": "FreeCAD 1.1.3",
    "freecad_build": "Revision: 20260725 (Git shallow)",
    "ifcopenshell_version": "0.8.4",
    "python_version": "3.11",
    "platform": "windows-x86_64",
    "freecadcmd_sha256": "a" * 64,
    "wrapper_sha256": "b" * 64,
}


def sample_rows() -> tuple[QuantityRow, ...]:
    return (
        QuantityRow(
            global_id="0" * 21 + "1a",
            ifc_class="IfcWall",
            qto_name="Qto_WallBaseQuantities",
            quantity_name="Length",
            measure_kind="IfcQuantityLength",
            method="gross",
            value=Decimal("2.5000"),
            unit="m",
        ),
    )


def sample_extraction() -> ExtractionResult:
    return ExtractionResult(rows=sample_rows(), findings=())


def stage_artifacts(
    tmp_path: Path,
    status: str | None = None,
    rows: tuple[QuantityRow, ...] | None = None,
    findings: tuple[ExtractionFinding, ...] = (),
    ir: dict[str, object] | None = None,
) -> tuple[Path, bytes, bytes, bytes]:
    ir_model = parse_bim_takeoff_ir(
        ir
        or {
            "schema_version": "bim-takeoff.v1",
            "takeoff_id": "qa-demo",
            "element_classes": ["IfcWall"],
            "output_units": "SI",
        }
    )
    extraction = ExtractionResult(
        rows=rows if rows is not None else sample_rows(), findings=findings
    )
    report = build_takeoff_report(ir_model, "c" * 64, SUMMARY, extraction)
    if status is not None:
        report["status"] = status
    report_payload = render_takeoff_json(report)
    csv_payload = csv_bytes(extraction.rows)
    manifest = build_qa_manifest(
        ir_model,
        RUNTIME,
        "c" * 64,
        SUMMARY,
        extraction,
        report_payload,
        csv_payload,
        report,
    )
    manifest["status"] = report["status"]
    manifest_payload = render_qa_manifest_json(manifest)
    staging = tmp_path / "staging"
    staging.mkdir()
    (staging / "takeoff.json").write_bytes(report_payload)
    write_takeoff_csv(extraction.rows, staging / "takeoff.csv")
    (staging / "qa-manifest.json").write_bytes(manifest_payload)
    return staging, report_payload, csv_payload, manifest_payload


SUMMARY = {
    "schema": "IFC4",
    "project_identity_hash": "a" * 64,
    "total_instances": 21,
    "selected_elements": 2,
    "validation_findings": 0,
}


def test_complete_artifacts_pass_qa(tmp_path: Path) -> None:
    staging, report_payload, csv_payload, manifest_payload = stage_artifacts(tmp_path)

    result = verify_takeoff_artifacts(staging)

    assert result.status == "complete"
    assert result.row_count == 1
    assert result.coverage_gap_count == 0
    assert result.artifact_bytes["takeoff.json"] == len(report_payload)
    assert result.artifact_bytes["takeoff.csv"] == len(csv_payload)
    assert result.artifact_hashes["qa-manifest.json"] == hashlib.sha256(
        manifest_payload
    ).hexdigest()


def test_partial_artifacts_pass_qa_with_gap_counts(tmp_path: Path) -> None:
    staging, _, _, _ = stage_artifacts(
        tmp_path,
        rows=sample_rows(),
        findings=(ExtractionFinding("quantity_missing", "0" * 21 + "1c", "IfcWall", None, None),),
    )

    result = verify_takeoff_artifacts(staging)

    assert result.status == "partial"
    assert result.row_count == 1
    assert result.coverage_gap_count == 1


def test_manifest_is_deterministic_and_privacy_safe(tmp_path: Path) -> None:
    _, report_payload, _, manifest_payload = stage_artifacts(tmp_path)
    manifest = json.loads(manifest_payload.decode("utf-8"))

    assert manifest["model_calls"] == 0
    assert manifest["monetary_cost"] == "unknown"
    assert manifest["artifacts"]["takeoff.json"]["sha256"] == hashlib.sha256(
        report_payload
    ).hexdigest()
    assert manifest["coverage_reasons"] == {}
    assert "C:\\" not in manifest_payload.decode("utf-8")
    second_root = tmp_path / "determinism"
    second_root.mkdir()
    _, _, _, second_manifest = stage_artifacts(second_root)
    assert manifest_payload == second_manifest


def test_extra_or_missing_files_fail_qa(tmp_path: Path) -> None:
    staging, _, _, _ = stage_artifacts(tmp_path)
    (staging / "extra.txt").write_bytes(b"nope")

    with pytest.raises(BimProductionError) as error:
        verify_takeoff_artifacts(staging)
    assert error.value.code == "takeoff_integrity_error"

    second_root = tmp_path / "second"
    second_root.mkdir()
    staging, _, _, _ = stage_artifacts(second_root)
    (staging / "qa-manifest.json").unlink()
    with pytest.raises(BimProductionError):
        verify_takeoff_artifacts(staging)


def test_tampered_report_bytes_fail_qa(tmp_path: Path) -> None:
    staging, report_payload, _, _ = stage_artifacts(tmp_path)
    tampered = report_payload.replace(b"2.5000", b"9.9999")
    (staging / "takeoff.json").write_bytes(tampered)

    with pytest.raises(BimProductionError) as error:
        verify_takeoff_artifacts(staging)

    assert error.value.code == "takeoff_integrity_error"


def test_csv_report_parity_break_fails_qa(tmp_path: Path) -> None:
    staging, _, _, _ = stage_artifacts(tmp_path)
    payload = (staging / "takeoff.csv").read_bytes().replace(b"2.5000", b"2.5001")
    (staging / "takeoff.csv").write_bytes(payload)

    with pytest.raises(BimProductionError):
        verify_takeoff_artifacts(staging)


def test_status_inconsistency_fails_qa(tmp_path: Path) -> None:
    staging, report_payload, _, _ = stage_artifacts(tmp_path)
    report = json.loads(report_payload.decode("utf-8"))
    report["status"] = "partial"
    forged = json.dumps(report, sort_keys=True, separators=(",", ":")).encode() + b"\n"
    (staging / "takeoff.json").write_bytes(forged)

    with pytest.raises(BimProductionError):
        verify_takeoff_artifacts(staging)


def test_nonzero_model_calls_fail_qa(tmp_path: Path) -> None:
    staging, _, _, manifest_payload = stage_artifacts(tmp_path)
    manifest = json.loads(manifest_payload.decode("utf-8"))
    manifest["model_calls"] = 2
    forged = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode() + b"\n"
    (staging / "qa-manifest.json").write_bytes(forged)

    with pytest.raises(BimProductionError) as error:
        verify_takeoff_artifacts(staging)
    assert error.value.code == "takeoff_integrity_error"


def test_duplicate_json_keys_fail_qa(tmp_path: Path) -> None:
    staging, report_payload, _, _ = stage_artifacts(tmp_path)
    forged = report_payload[:-2] + b',"takeoff_id":"other"}\n'
    (staging / "takeoff.json").write_bytes(forged)

    with pytest.raises(BimProductionError):
        verify_takeoff_artifacts(staging)


def test_empty_and_oversized_artifacts_fail_qa(tmp_path: Path) -> None:
    staging, _, _, _ = stage_artifacts(tmp_path)
    (staging / "takeoff.csv").write_bytes(b"")

    with pytest.raises(BimProductionError):
        verify_takeoff_artifacts(staging)
