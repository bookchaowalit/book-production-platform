from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from book_production_platform.bim_production.extract import (
    aggregate_rows,
    build_extraction,
    report_status,
)
from book_production_platform.bim_production.runtime import (
    BimWorkerResult,
    run_bim_worker,
    stage_ifc_input,
)
from tests.bim_production.fixtures import (
    load_fixture_cases,
    pinned_runtime_paths,
    run_fixture_case,
)

CASES = load_fixture_cases("ifc-validation-cases.json")


def case(case_id: str) -> dict[str, Any]:
    return CASES[case_id]


@pytest.fixture(scope="module")
def runtime() -> tuple[Path, Path]:
    freecadcmd, site_packages = pinned_runtime_paths()
    assert freecadcmd.is_file(), "the pinned FreeCADCmd runtime must exist locally"
    assert site_packages.is_dir(), "the pinned IfcOpenShell site-packages must exist locally"
    return freecadcmd, site_packages


def generate(
    runtime: tuple[Path, Path], tmp_path: Path, case_id: str, run_label: str
) -> Path:
    freecadcmd, site_packages = runtime
    result, work_root = run_fixture_case(
        case(case_id), freecadcmd, site_packages, tmp_path, run_label
    )
    assert result.error_code is None, f"fixture {case_id} must generate cleanly"
    return work_root / str(case(case_id)["output_name"])


def takeoff_extraction(
    runtime: tuple[Path, Path],
    tmp_path: Path,
    source: Path,
    run_label: str,
    element_classes: list[str] | None = None,
) -> tuple[BimWorkerResult, Any]:
    freecadcmd, site_packages = runtime
    work_root = tmp_path / f"run-{run_label}" / "worker"
    staged, _ = stage_ifc_input(source, work_root)
    request: dict[str, Any] = {
        "protocol": "bim-worker.v1",
        "mode": "takeoff",
        "takeoff_id": "extraction-check",
        "ifc_path": str(staged),
        "element_classes": element_classes or ["IfcWall"],
        "expected_schemas": ["IFC2X3", "IFC4"],
        "max_instances": 250_000,
    }
    request_path = work_root / "takeoff-request.bimreq"
    request_path.write_text(json.dumps(request, sort_keys=True), encoding="utf-8")
    result = run_bim_worker(
        freecadcmd,
        site_packages,
        "takeoff",
        request_path,
        work_root,
        timeout_s=120,
    )
    records = None
    if result.result_json is not None and result.result_json.get("extraction"):
        extraction = build_extraction(result.result_json["extraction"]["records"])
        records = extraction
    return result, records


def test_extracts_all_four_quantity_kinds_with_si_conversion(
    runtime: tuple[Path, Path], tmp_path: Path
) -> None:
    source = generate(runtime, tmp_path, "ifc4-extraction-rich", "rich")
    result, extraction = takeoff_extraction(runtime, tmp_path, source, "richrun")

    assert result.error_code is None
    assert extraction is not None
    assert report_status(extraction) == "complete"
    rows = {
        (row.global_id[-4:], row.qto_name, row.quantity_name, row.method): row
        for row in extraction.rows
    }
    by_id = sorted(rows)
    assert len(extraction.rows) == 7

    gross_length = [
        row
        for row in extraction.rows
        if row.quantity_name == "Length" and row.method == "gross"
    ]
    assert sorted(Decimal(str(row.value)) for row in gross_length) == [
        Decimal("2.0"),
        Decimal("2.5000"),
    ]
    net_length = [
        row
        for row in extraction.rows
        if row.quantity_name == "Length" and row.method == "net"
    ]
    assert net_length[0].value == Decimal("2.4000")
    assert net_length[0].unit == "m"

    area_rows = [row for row in extraction.rows if row.measure_kind == "IfcQuantityArea"]
    assert sum((row.value for row in area_rows), Decimal(0)) == Decimal("17.5")
    assert all(row.unit == "m2" for row in area_rows)
    volume_rows = [
        row for row in extraction.rows if row.measure_kind == "IfcQuantityVolume"
    ]
    assert volume_rows[0].value == Decimal("3.125")
    assert volume_rows[0].unit == "m3"
    count_rows = [row for row in extraction.rows if row.measure_kind == "IfcQuantityCount"]
    assert count_rows[0].value == Decimal("4.0")
    assert count_rows[0].unit == "count"

    gross_keys = [key for key in by_id if key[3] == "gross"]
    assert gross_keys, "gross method must be preserved from the source QTO"


def test_aggregates_separate_gross_and_net_methods() -> None:
    from book_production_platform.bim_production.extract import QuantityRow

    rows = (
        QuantityRow(
            global_id="a", ifc_class="IfcWall", qto_name="Q", quantity_name="Length",
            measure_kind="IfcQuantityLength", method="gross",
            value=Decimal("2.5"), unit="m",
        ),
        QuantityRow(
            global_id="b", ifc_class="IfcWall", qto_name="Q", quantity_name="Length",
            measure_kind="IfcQuantityLength", method="gross",
            value=Decimal("1.0"), unit="m",
        ),
        QuantityRow(
            global_id="a", ifc_class="IfcWall", qto_name="Q", quantity_name="Length",
            measure_kind="IfcQuantityLength", method="net",
            value=Decimal("2.4"), unit="m",
        ),
    )
    aggregates = aggregate_rows(rows)

    assert len(aggregates) == 2
    gross = next(row for row in aggregates if row.method == "gross")
    net = next(row for row in aggregates if row.method == "net")
    assert gross.value == Decimal("3.5")
    assert net.value == Decimal("2.4")


def test_duplicate_quantity_names_become_ambiguous_gaps(
    runtime: tuple[Path, Path], tmp_path: Path
) -> None:
    source = generate(runtime, tmp_path, "ifc4-duplicate-quantity", "dup")
    result, extraction = takeoff_extraction(runtime, tmp_path, source, "duprun")

    assert result.error_code is None
    assert extraction is not None
    assert not extraction.rows
    assert [finding.reason for finding in extraction.findings] == ["quantity_ambiguous"]
    assert report_status(extraction) == "needs_input"


def test_elements_without_qtos_are_unreported_and_never_zero(
    runtime: tuple[Path, Path], tmp_path: Path
) -> None:
    source = generate(runtime, tmp_path, "ifc4-needs-input", "needs")
    result, extraction = takeoff_extraction(runtime, tmp_path, source, "needsrun")

    assert result.error_code is None
    assert extraction is not None
    assert not extraction.rows
    assert len(extraction.findings) == 2
    assert all(finding.reason == "quantity_missing" for finding in extraction.findings)
    assert report_status(extraction) == "needs_input"


def test_type_level_quantities_fail_closed_at_validation(
    runtime: tuple[Path, Path], tmp_path: Path
) -> None:
    """The pinned schema validation rejects type-level QTO constructs outright."""

    source = generate(runtime, tmp_path, "ifc4-type-only-qto", "type")
    result, extraction = takeoff_extraction(runtime, tmp_path, source, "typerun")

    assert result.error_code == "ifc_schema_invalid"
    assert extraction is None


def test_partial_status_when_some_rows_and_some_gaps(
    runtime: tuple[Path, Path], tmp_path: Path
) -> None:
    source = generate(runtime, tmp_path, "ifc4-no-units", "nounits")
    result, extraction = takeoff_extraction(runtime, tmp_path, source, "nounitsrun")

    assert result.error_code is None
    assert extraction is not None
    assert report_status(extraction) == "partial"
    length_rows = [row for row in extraction.rows if row.unit == "m"]
    assert length_rows and length_rows[0].value == Decimal("3.0")
    missing = [finding for finding in extraction.findings]
    assert [finding.reason for finding in missing] == ["quantity_unit_missing"]
    area_rows = [row for row in extraction.rows if row.unit == "m2"]
    assert not area_rows


def test_ifc2x3_extraction_matches_expected_totals(
    runtime: tuple[Path, Path], tmp_path: Path
) -> None:
    source = generate(runtime, tmp_path, "ifc2x3-walls-valid", "x23")
    result, extraction = takeoff_extraction(runtime, tmp_path, source, "x23run")

    assert result.error_code is None
    assert extraction is not None
    assert report_status(extraction) == "complete"
    aggregates = aggregate_rows(extraction.rows)
    length_total = [
        row for row in aggregates if row.quantity_name == "Length" and row.unit == "m"
    ]
    assert length_total[0].value == Decimal(5000)
    count_total = [row for row in aggregates if row.unit == "count"]
    assert count_total[0].value == Decimal("1.0")
