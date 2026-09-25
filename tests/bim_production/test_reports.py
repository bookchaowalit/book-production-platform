from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest

from book_production_platform.bim_ir import parse_bim_takeoff_ir
from book_production_platform.bim_production.extract import (
    ExtractionFinding,
    ExtractionResult,
    QuantityRow,
)
from book_production_platform.bim_production.reports import (
    CSV_COLUMNS,
    REPORT_SCHEMA_VERSION,
    build_takeoff_report,
    csv_bytes,
    render_takeoff_json,
    write_takeoff_csv,
)

IR = parse_bim_takeoff_ir(
    {
        "schema_version": "bim-takeoff.v1",
        "takeoff_id": "report-demo",
        "element_classes": ["IfcWall"],
        "output_units": "SI",
    }
)
SUMMARY = {
    "schema": "IFC4",
    "project_identity_hash": "a" * 64,
    "total_instances": 21,
    "selected_elements": 2,
    "validation_findings": 0,
}
ROWS = (
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
    QuantityRow(
        global_id="0" * 21 + "1b",
        ifc_class="IfcWall",
        qto_name="Qto_WallBaseQuantities",
        quantity_name="NetArea",
        measure_kind="IfcQuantityArea",
        method=None,
        value=Decimal("17.5"),
        unit="m2",
    ),
)
GAPS = (
    ExtractionFinding("quantity_missing", "0" * 21 + "1c", "IfcWall", None, None),
)


def build_extraction() -> ExtractionResult:
    return ExtractionResult(rows=ROWS, findings=GAPS)


def test_report_is_deterministic_across_runs_and_key_order() -> None:
    first = build_takeoff_report(IR, "b" * 64, SUMMARY, build_extraction())
    second = build_takeoff_report(IR, "b" * 64, SUMMARY, build_extraction())

    assert render_takeoff_json(first) == render_takeoff_json(second)
    payload = render_takeoff_json(first)
    assert payload.endswith(b"\n") and payload.count(b"\n") == 1
    assert payload == json.dumps(
        json.loads(payload),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8") + b"\n"


def test_report_carries_schema_status_provenance_and_gaps() -> None:
    report = build_takeoff_report(IR, "b" * 64, SUMMARY, build_extraction())

    assert report["schema_version"] == REPORT_SCHEMA_VERSION
    assert report["takeoff_id"] == "report-demo"
    assert report["source_sha256"] == "b" * 64
    assert report["status"] == "partial"
    assert report["ifc_schema"] == "IFC4"
    assert len(report["rows"]) == 2
    assert report["rows"][0]["value"] == "2.5000"
    assert report["aggregates"][0]["value"] == "2.5000"
    assert report["coverage_gaps"] == [
        {
            "reason": "quantity_missing",
            "global_id": "0" * 21 + "1c",
            "ifc_class": "IfcWall",
            "qto_name": None,
            "quantity_name": None,
        }
    ]


def test_report_aggregates_never_merge_different_methods() -> None:
    extraction = ExtractionResult(
        rows=(
            ROWS[0],
            QuantityRow(
                global_id="0" * 21 + "1a",
                ifc_class="IfcWall",
                qto_name="Qto_WallBaseQuantities",
                quantity_name="Length",
                measure_kind="IfcQuantityLength",
                method="net",
                value=Decimal("1.0"),
                unit="m",
            ),
        ),
        findings=(),
    )
    report = build_takeoff_report(IR, "b" * 64, SUMMARY, extraction)

    assert len(report["aggregates"]) == 2
    assert {row["method"] for row in report["aggregates"]} == {"gross", "net"}


def test_csv_has_stable_headers_quoting_and_decimal_text(
    tmp_path: Path,
) -> None:
    rows = (
        ROWS[0],
        QuantityRow(
            global_id="0" * 21 + "2b",
            ifc_class="IfcWall",
            qto_name="Qto,Special",
            quantity_name='Name "quoted"',
            measure_kind="IfcQuantityLength",
            method=None,
            value=Decimal("1.0"),
            unit="m",
        ),
    )
    payload = csv_bytes(rows)
    assert payload.endswith(b"\r\n")
    lines = payload.decode("utf-8").split("\r\n")[:-1]
    assert lines[0] == ",".join(CSV_COLUMNS)
    assert '"Qto,Special"' in lines[2]
    assert '"Name ""quoted"""' in lines[2]

    write_takeoff_csv(rows, tmp_path / "takeoff.csv")
    assert (tmp_path / "takeoff.csv").read_bytes() == payload
    assert not (tmp_path / "takeoff.csv.tmp").exists()


def test_csv_row_parity_with_report_rows() -> None:
    import csv
    import io

    report = build_takeoff_report(IR, "b" * 64, SUMMARY, build_extraction())
    parsed = list(csv.DictReader(io.StringIO(csv_bytes(ROWS).decode("utf-8"), newline="")))

    assert len(parsed) == len(report["rows"])
    for record, csv_row in zip(report["rows"], parsed):
        for column in CSV_COLUMNS:
            expected = record[column] if record[column] is not None else ""
            assert csv_row[column] == str(expected)


def test_reports_never_contain_local_paths_or_source_text() -> None:
    report = build_takeoff_report(IR, "b" * 64, SUMMARY, build_extraction())
    payload = render_takeoff_json(report).decode("utf-8")

    for forbidden in ("C:\\", "/home/", "ISO-10303-21", "FILE_NAME", "site-packages"):
        assert forbidden not in payload
    csv_text = csv_bytes(ROWS).decode("utf-8")
    assert "C:\\" not in csv_text


def test_report_rejects_an_incomplete_source_hash() -> None:
    with pytest.raises(ValueError):
        build_takeoff_report(IR, "short", SUMMARY, build_extraction())
