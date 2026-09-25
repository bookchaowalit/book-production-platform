from __future__ import annotations

import hashlib
import json
import threading
from pathlib import Path
from typing import Any

import pytest

from book_production_platform.bim_ir import parse_bim_takeoff_ir
from book_production_platform.bim_production import jobs as jobs_module
from book_production_platform.bim_production.errors import BimProductionError
from book_production_platform.bim_production.jobs import (
    JOB_ARTIFACTS,
    BimJobResult,
    build_bim_takeoff_job,
    compute_fingerprint,
)
from book_production_platform.bim_production.runtime import (
    BimRuntimeIdentity,
    BimWorkerResult,
    validate_ifc_source,
)
from tests.bim_production.fixtures import (
    load_fixture_cases,
    pinned_runtime_paths,
    run_fixture_case,
)

_LOCK: dict[str, Any] = {
    "freecad_version": "FreeCAD 1.1.3",
    "freecad_build": "Revision: 20260725 (Git shallow)",
    "python_version": "3.11",
    "ifcopenshell_version": "0.8.4",
    "freecadcmd_sha256": "a" * 64,
    "wrapper_sha256": "b" * 64,
}

IDENTITY = BimRuntimeIdentity(
    freecad_version="FreeCAD 1.1.3",
    freecad_build="Revision: 20260725 (Git shallow)",
    ifcopenshell_version="0.8.4",
    python_version="3.11",
    platform="windows-x86_64",
    freecadcmd_sha256="a" * 64,
    wrapper_sha256="b" * 64,
)
IR_JSON = {
    "schema_version": "bim-takeoff.v1",
    "takeoff_id": "job-demo",
    "element_classes": ["IfcWall"],
    "output_units": "SI",
}


def _identity(freecadcmd: Path, ifc_site_packages: Path) -> BimRuntimeIdentity:
    del freecadcmd, ifc_site_packages
    return IDENTITY


def _records_complete() -> list[dict[str, Any]]:
    return [
        {
            "global_id": "0" * 21 + "1a",
            "ifc_class": "IfcWall",
            "qto_name": "Qto_WallBaseQuantities",
            "quantity_name": "Length",
            "measure_kind": "IfcQuantityLength",
            "method": "gross",
            "raw_value": "2500.0",
            "unit": {
                "kind": "si",
                "name": "METRE",
                "prefix": "MILLI",
                "scale": "0.001",
                "dimension": 1,
            },
            "gap": None,
        },
        {
            "global_id": "0" * 21 + "1a",
            "ifc_class": "IfcWall",
            "qto_name": "Qto_WallBaseQuantities",
            "quantity_name": "NumberOfElements",
            "measure_kind": "IfcQuantityCount",
            "method": None,
            "raw_value": "2.0",
            "unit": None,
            "gap": None,
        },
    ]


def _records_needs_input() -> list[dict[str, Any]]:
    return [
        {
            "global_id": "0" * 21 + "1a",
            "ifc_class": "IfcWall",
            "qto_name": None,
            "quantity_name": None,
            "measure_kind": None,
            "method": None,
            "raw_value": None,
            "unit": None,
            "gap": "quantity_missing",
        }
    ]


def _worker_result(records: list[dict[str, Any]]) -> BimWorkerResult:
    return BimWorkerResult(
        exit_code=0,
        elapsed_seconds=0.1,
        diagnostics_bytes=0,
        error_code=None,
        result_json={
            "protocol": "bim-worker.v1",
            "mode": "takeoff",
            "status": "ok",
            "gui_loaded": False,
            "validation": {
                "schema": "IFC4",
                "project_identity_hash": "a" * 64,
                "total_instances": 21,
                "selected_elements": 1,
                "validation_findings": 0,
            },
            "extraction": {"records": records},
        },
    )


@pytest.fixture(scope="module")
def real_ifc(tmp_path_factory: pytest.TempPathFactory) -> Path:
    freecadcmd, site_packages = pinned_runtime_paths()
    assert freecadcmd.is_file() and site_packages.is_dir()
    cases = load_fixture_cases("ifc-validation-cases.json")
    result, work_root = run_fixture_case(
        cases["ifc4-extraction-rich"], freecadcmd, site_packages,
        tmp_path_factory.mktemp("jobs-fixtures"), "jobgen",
    )
    assert result.error_code is None
    return work_root / str(cases["ifc4-extraction-rich"]["output_name"])


@pytest.fixture
def inputs(tmp_path: Path, real_ifc: Path) -> tuple[Path, Path]:
    ir_path = tmp_path / "ir.bimreq"
    ir_path.write_text(json.dumps(IR_JSON), encoding="utf-8")
    return ir_path, real_ifc


@pytest.fixture
def fast_worker(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(jobs_module, "inspect_bim_runtime", _identity)
    monkeypatch.setattr(
        jobs_module, "run_bim_worker", lambda *args, **kwargs: _worker_result(_records_complete())
    )


def test_fingerprint_covers_every_semantic_input(
    monkeypatch: pytest.MonkeyPatch, real_ifc: Path
) -> None:
    ir = parse_bim_takeoff_ir(IR_JSON)
    ir_other = parse_bim_takeoff_ir({**IR_JSON, "takeoff_id": "other"})
    base = compute_fingerprint(ir, "c" * 64, "IFC4")
    assert base == compute_fingerprint(ir, "c" * 64, "IFC4")
    assert base != compute_fingerprint(ir_other, "c" * 64, "IFC4")
    assert base != compute_fingerprint(ir, "d" * 64, "IFC4")
    assert base != compute_fingerprint(ir, "c" * 64, "IFC2X3")

    other_lock = dict(_LOCK)
    other_lock["freecadcmd_sha256"] = "f" * 64
    monkeypatch.setattr(jobs_module, "_load_runtime_lock", lambda: other_lock)
    changed_lock = compute_fingerprint(ir, "c" * 64, "IFC4")
    assert changed_lock != base
    assert len(base) == 64


def test_fresh_complete_job_publishes_exactly_three_artifacts(
    inputs: tuple[Path, Path], tmp_path: Path, fast_worker: None
) -> None:
    ir_path, ifc_path = inputs
    result = build_bim_takeoff_job(ir_path, ifc_path, tmp_path / "out", Path("FreeCADCmd.exe"), Path("sp"))

    assert result.status == "complete"
    assert result.error_code is None
    assert result.model_calls == 0
    job_dir = tmp_path / "out" / "jobs" / str(result.fingerprint)
    assert sorted(entry.name for entry in job_dir.iterdir()) == sorted(JOB_ARTIFACTS)
    assert (tmp_path / "out" / ".receipts" / f"{result.fingerprint}.json").is_file()
    assert not (tmp_path / "out" / "staging").exists() or not any(
        (tmp_path / "out" / "staging").iterdir()
    )


def test_exact_replay_starts_no_worker_and_preserves_bytes(
    inputs: tuple[Path, Path], tmp_path: Path, fast_worker: None
) -> None:
    ir_path, ifc_path = inputs
    output = tmp_path / "out"
    first = build_bim_takeoff_job(ir_path, ifc_path, output, Path("FreeCADCmd.exe"), Path("sp"))
    job_dir = output / "jobs" / str(first.fingerprint)
    before = {entry.name: hashlib.sha256(entry.read_bytes()).hexdigest() for entry in job_dir.iterdir()}

    calls: list[object] = []

    def spy(*args: object, **kwargs: object) -> BimWorkerResult:
        calls.append(args)
        raise AssertionError("replay must not start the worker")

    monkey_spy = pytest.MonkeyPatch()
    monkey_spy.setattr(jobs_module, "run_bim_worker", spy)
    try:
        second = build_bim_takeoff_job(ir_path, ifc_path, output, Path("FreeCADCmd.exe"), Path("sp"))
    finally:
        monkey_spy.undo()

    assert second.status == "replayed"
    assert second.model_calls == 0
    assert second.fingerprint == first.fingerprint
    assert not calls
    after = {entry.name: hashlib.sha256(entry.read_bytes()).hexdigest() for entry in job_dir.iterdir()}
    assert before == after


def test_needs_input_never_publishes(
    inputs: tuple[Path, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(jobs_module, "inspect_bim_runtime", _identity)
    monkeypatch.setattr(
        jobs_module,
        "run_bim_worker",
        lambda *args, **kwargs: _worker_result(_records_needs_input()),
    )
    ir_path, ifc_path = inputs
    output = tmp_path / "out"

    result = build_bim_takeoff_job(ir_path, ifc_path, output, Path("FreeCADCmd.exe"), Path("sp"))

    assert result.status == "needs_input"
    assert not (output / "jobs").exists() or not any((output / "jobs").iterdir())
    receipts = output / ".receipts"
    assert not receipts.exists() or not list(receipts.glob("*.json"))


def test_partial_jobs_publish_with_gaps(
    inputs: tuple[Path, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    records = _records_complete() + _records_needs_input()
    monkeypatch.setattr(jobs_module, "inspect_bim_runtime", _identity)
    monkeypatch.setattr(
        jobs_module, "run_bim_worker", lambda *args, **kwargs: _worker_result(records)
    )
    ir_path, ifc_path = inputs

    result = build_bim_takeoff_job(ir_path, ifc_path, tmp_path / "out", Path("FreeCADCmd.exe"), Path("sp"))

    assert result.status == "partial"
    assert result.coverage_gap_count == 1


def test_changed_inputs_change_the_fingerprint(
    inputs: tuple[Path, Path], tmp_path: Path, real_ifc: Path, fast_worker: None
) -> None:
    ir_path, ifc_path = inputs
    output = tmp_path / "out"
    first = build_bim_takeoff_job(ir_path, ifc_path, output, Path("FreeCADCmd.exe"), Path("sp"))

    ir_path2 = tmp_path / "ir2.bimreq"
    ir_path2.write_text(json.dumps({**IR_JSON, "takeoff_id": "other"}), encoding="utf-8")
    second = build_bim_takeoff_job(ir_path2, ifc_path, output, Path("FreeCADCmd.exe"), Path("sp"))

    assert first.fingerprint is not None and second.fingerprint is not None
    assert first.fingerprint != second.fingerprint
    assert second.status == "complete"


def test_forged_receipt_fails_without_overwrite(
    inputs: tuple[Path, Path], tmp_path: Path, fast_worker: None
) -> None:
    ir_path, ifc_path = inputs
    output = tmp_path / "out"
    first = build_bim_takeoff_job(ir_path, ifc_path, output, Path("FreeCADCmd.exe"), Path("sp"))
    job_dir = output / "jobs" / str(first.fingerprint)
    before = {entry.name: hashlib.sha256(entry.read_bytes()).hexdigest() for entry in job_dir.iterdir()}
    receipt_path = output / ".receipts" / f"{first.fingerprint}.json"
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    receipt["row_count"] = 999
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")

    with pytest.raises(BimProductionError) as error:
        build_bim_takeoff_job(ir_path, ifc_path, output, Path("FreeCADCmd.exe"), Path("sp"))

    assert error.value.code == "takeoff_integrity_error"
    after = {entry.name: hashlib.sha256(entry.read_bytes()).hexdigest() for entry in job_dir.iterdir()}
    assert before == after


def test_missing_receipt_fails_closed(
    inputs: tuple[Path, Path], tmp_path: Path, fast_worker: None
) -> None:
    ir_path, ifc_path = inputs
    output = tmp_path / "out"
    first = build_bim_takeoff_job(ir_path, ifc_path, output, Path("FreeCADCmd.exe"), Path("sp"))
    (output / ".receipts" / f"{first.fingerprint}.json").unlink()

    with pytest.raises(BimProductionError) as error:
        build_bim_takeoff_job(ir_path, ifc_path, output, Path("FreeCADCmd.exe"), Path("sp"))

    assert error.value.code == "takeoff_integrity_error"


def test_tampered_artifact_fails_replay(
    inputs: tuple[Path, Path], tmp_path: Path, fast_worker: None
) -> None:
    ir_path, ifc_path = inputs
    output = tmp_path / "out"
    first = build_bim_takeoff_job(ir_path, ifc_path, output, Path("FreeCADCmd.exe"), Path("sp"))
    report = output / "jobs" / str(first.fingerprint) / "takeoff.json"
    payload = report.read_bytes().replace(b"2.5000", b"2.5001")
    report.write_bytes(payload)

    with pytest.raises(BimProductionError) as error:
        build_bim_takeoff_job(ir_path, ifc_path, output, Path("FreeCADCmd.exe"), Path("sp"))

    assert error.value.code == "takeoff_integrity_error"
    assert report.read_bytes() == payload, "a rejected replay never repairs artifacts"


def test_pre_existing_directory_without_receipt_is_never_overwritten(
    inputs: tuple[Path, Path], tmp_path: Path, real_ifc: Path
) -> None:
    ir_path, ifc_path = inputs
    output = tmp_path / "out"
    fingerprint = compute_fingerprint(
        parse_bim_takeoff_ir(IR_JSON),
        validate_ifc_source(ifc_path),
        "IFC4",
    )
    job_dir = output / "jobs" / fingerprint
    job_dir.mkdir(parents=True)
    (job_dir / "takeoff.json").write_bytes(b"prior user data")

    with pytest.raises(BimProductionError) as error:
        build_bim_takeoff_job(ir_path, ifc_path, output, Path("FreeCADCmd.exe"), Path("sp"))

    assert error.value.code == "takeoff_integrity_error"
    assert (job_dir / "takeoff.json").read_bytes() == b"prior user data"


def test_invalid_inputs_fail_before_any_worker_start(
    inputs: tuple[Path, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ir_path, ifc_path = inputs
    output = tmp_path / "out"

    def spy(*args: object, **kwargs: object) -> BimWorkerResult:
        raise AssertionError("no worker may start for invalid input")

    monkeypatch.setattr(jobs_module, "run_bim_worker", spy)
    monkeypatch.setattr(jobs_module, "inspect_bim_runtime", _identity)

    bad_ir = tmp_path / "bad.bimreq"
    bad_ir.write_text('{"schema_version":"bim-takeoff.v1"}', encoding="utf-8")
    with pytest.raises(BimProductionError) as error:
        build_bim_takeoff_job(bad_ir, ifc_path, output, Path("FreeCADCmd.exe"), Path("sp"))
    assert error.value.code == "invalid_bim_takeoff_ir"

    with pytest.raises(BimProductionError) as error:
        build_bim_takeoff_job(ir_path, tmp_path / "missing.ifc", output, Path("FreeCADCmd.exe"), Path("sp"))
    assert error.value.code == "ifc_path_invalid"

    with pytest.raises(BimProductionError) as error:
        build_bim_takeoff_job(ir_path, ir_path, output, Path("FreeCADCmd.exe"), Path("sp"))
    assert error.value.code == "ifc_path_invalid"


def test_worker_failure_leaves_no_receipt_and_cleans_staging(
    inputs: tuple[Path, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(jobs_module, "inspect_bim_runtime", _identity)
    monkeypatch.setattr(
        jobs_module,
        "run_bim_worker",
        lambda *args, **kwargs: BimWorkerResult(0, 1.0, 10, "worker_deadline_exceeded", None),
    )
    ir_path, ifc_path = inputs
    output = tmp_path / "out"

    result = build_bim_takeoff_job(ir_path, ifc_path, output, Path("FreeCADCmd.exe"), Path("sp"))

    assert result.status == "rejected"
    assert result.error_code == "worker_deadline_exceeded"
    assert not (output / "jobs").exists() or not any((output / "jobs").iterdir())
    assert not (output / ".receipts").exists() or not list(
        (output / ".receipts").glob("*.json")
    )
    staging = output / "staging"
    assert not staging.exists() or not any(staging.iterdir())


def test_concurrent_identical_jobs_publish_once(
    inputs: tuple[Path, Path], tmp_path: Path, real_ifc: Path
) -> None:
    freecadcmd, site_packages = pinned_runtime_paths()
    ir_path, ifc_path = inputs
    output = tmp_path / "out"
    results: list[BimJobResult] = []
    errors: list[BaseException] = []

    def runner() -> None:
        try:
            results.append(
                build_bim_takeoff_job(
                    ir_path, ifc_path, output, freecadcmd, site_packages, timeout_s=120
                )
            )
        except BaseException as error:  # noqa: BLE001
            errors.append(error)

    threads = [threading.Thread(target=runner) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(300)

    assert not errors
    assert len(results) == 2
    assert results[0].fingerprint == results[1].fingerprint
    statuses = sorted(result.status for result in results)
    assert statuses == ["complete", "replayed"]
    job_dir = output / "jobs" / str(results[0].fingerprint)
    assert sorted(entry.name for entry in job_dir.iterdir()) == sorted(JOB_ARTIFACTS)


def test_real_worker_values_reach_the_published_report(
    inputs: tuple[Path, Path], tmp_path: Path, real_ifc: Path
) -> None:
    freecadcmd, site_packages = pinned_runtime_paths()
    ir_path, ifc_path = inputs
    output = tmp_path / "out"

    result = build_bim_takeoff_job(ir_path, ifc_path, output, freecadcmd, site_packages, timeout_s=120)

    assert result.status == "complete"
    report = json.loads(
        (output / "jobs" / str(result.fingerprint) / "takeoff.json").read_text(encoding="utf-8")
    )
    assert report["status"] == "complete"
    assert report["rows"][0]["value"] == "2.5000"
    assert any(
        row["quantity_name"] == "Length" and row["method"] == "net"
        for row in report["aggregates"]
    )
