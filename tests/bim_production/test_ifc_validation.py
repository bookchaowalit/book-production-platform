from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from book_production_platform.bim_production.runtime import (
    BimWorkerResult,
    run_bim_worker,
    stage_ifc_input,
)
from tests.bim_production.fixtures import (
    load_fixture_cases,
    require_pinned_runtime,
    run_fixture_case,
)

CASES = load_fixture_cases("ifc-validation-cases.json")


def case(case_id: str) -> dict[str, Any]:
    return CASES[case_id]


@pytest.fixture(scope="module")
def runtime() -> tuple[Path, Path]:
    return require_pinned_runtime()


@pytest.fixture(scope="module")
def generated(
    tmp_path_factory: pytest.TempPathFactory, runtime: tuple[Path, Path]
) -> dict[str, Path]:
    freecadcmd, site_packages = runtime
    output_root = tmp_path_factory.mktemp("fixtures")
    generated_paths: dict[str, Path] = {}
    for index, case_id in enumerate(
        (
            "ifc4-walls-valid",
            "ifc2x3-walls-valid",
            "ifc4-no-units",
        )
    ):
        result, work_root = run_fixture_case(
            case(case_id), freecadcmd, site_packages, output_root, f"gen{index}"
        )
        assert result.error_code is None, f"fixture {case_id} must generate cleanly"
        assert result.result_json is not None
        assert result.result_json.get("gui_loaded") is False
        generated_paths[case_id] = work_root / str(case(case_id)["output_name"])
        assert generated_paths[case_id].is_file()
    return generated_paths


def takeoff_run(
    runtime: tuple[Path, Path],
    tmp_path: Path,
    source: Path,
    run_label: str,
    element_classes: list[str] | None = None,
    expected_schemas: list[str] | None = None,
    max_instances: int = 250_000,
) -> tuple[BimWorkerResult, Path, str]:
    """Stage one private copy and run the takeoff validation mode once."""

    freecadcmd, site_packages = runtime
    work_root = tmp_path / f"run-{run_label}"
    staged, source_hash = stage_ifc_input(source, work_root / "worker")
    request: dict[str, Any] = {
        "protocol": "bim-worker.v1",
        "mode": "takeoff",
        "takeoff_id": "validation-check",
        "ifc_path": str(staged),
        "element_classes": element_classes or ["IfcWall"],
        "expected_schemas": expected_schemas or ["IFC2X3", "IFC4"],
        "max_instances": max_instances,
    }
    request_path = work_root / "worker" / "takeoff-request.bimreq"
    request_path.write_text(json.dumps(request, sort_keys=True), encoding="utf-8")
    result = run_bim_worker(
        freecadcmd,
        site_packages,
        "takeoff",
        request_path,
        work_root / "worker",
        timeout_s=120,
    )
    return result, staged, source_hash


def generate(
    runtime: tuple[Path, Path], tmp_path: Path, case_id: str, run_label: str
) -> Path:
    freecadcmd, site_packages = runtime
    result, work_root = run_fixture_case(
        case(case_id), freecadcmd, site_packages, tmp_path, run_label
    )
    assert result.error_code is None, f"fixture {case_id} must generate cleanly"
    return work_root / str(case(case_id)["output_name"])


def test_generated_fixtures_are_deterministic(
    runtime: tuple[Path, Path], tmp_path: Path
) -> None:
    first = generate(runtime, tmp_path, "ifc4-walls-valid", "detA")
    second = generate(runtime, tmp_path, "ifc4-walls-valid", "detB")

    assert hashlib.sha256(first.read_bytes()).digest() == hashlib.sha256(
        second.read_bytes()
    ).digest()


def test_valid_ifc4_and_ifc2x3_models_validate_cleanly(
    runtime: tuple[Path, Path],
    generated: dict[str, Path],
    tmp_path: Path,
) -> None:
    for index, case_id in enumerate(("ifc4-walls-valid", "ifc2x3-walls-valid")):
        result, staged, source_hash = takeoff_run(
            runtime, tmp_path, generated[case_id], f"valid{index}"
        )

        assert result.exit_code == 0
        assert result.error_code is None
        assert result.result_json is not None
        assert result.result_json.get("gui_loaded") is False
        validation = result.result_json["validation"]
        assert validation["schema"] == case(case_id)["schema"]
        assert validation["validation_findings"] == 0
        assert validation["selected_elements"] == len(case(case_id)["elements"])
        assert validation["total_instances"] > len(case(case_id)["elements"])
        assert len(validation["project_identity_hash"]) == 64
        staged_bytes = staged.read_bytes()
        source_bytes = generated[case_id].read_bytes()
        assert staged_bytes == source_bytes
        assert source_hash == hashlib.sha256(source_bytes).hexdigest()


def test_malformed_spf_is_rejected_without_artifacts(
    runtime: tuple[Path, Path], tmp_path: Path
) -> None:
    malformed = generate(runtime, tmp_path, "ifc4-malformed-spf", "mal")
    result, _, _ = takeoff_run(runtime, tmp_path, malformed, "malrun")

    assert result.error_code == "ifc_parse_invalid"
    assert result.result_json is not None
    assert result.result_json["status"] == "error"
    jobs_root = tmp_path / "jobs"
    assert not jobs_root.exists() or not list(jobs_root.glob("*"))


def test_ifc4x3_schema_is_rejected(runtime: tuple[Path, Path], tmp_path: Path) -> None:
    model = generate(runtime, tmp_path, "ifc4x3-unsupported", "x3")
    result, _, _ = takeoff_run(runtime, tmp_path, model, "x3run")

    assert result.error_code == "unsupported_ifc_schema"


def test_schema_mismatch_against_expected_schemas_is_rejected(
    runtime: tuple[Path, Path], generated: dict[str, Path], tmp_path: Path
) -> None:
    result, _, _ = takeoff_run(
        runtime,
        tmp_path,
        generated["ifc4-walls-valid"],
        "mismatch",
        expected_schemas=["IFC2X3"],
    )

    assert result.error_code == "unsupported_ifc_schema"


def test_duplicate_global_ids_fail_the_identity_gate(
    runtime: tuple[Path, Path], tmp_path: Path
) -> None:
    model = generate(runtime, tmp_path, "ifc4-duplicate-globalid", "dup")
    result, _, _ = takeoff_run(runtime, tmp_path, model, "duprun")

    assert result.error_code == "ifc_identity_invalid"


def test_short_global_ids_fail_the_identity_gate(
    runtime: tuple[Path, Path], tmp_path: Path
) -> None:
    model = generate(runtime, tmp_path, "ifc4-short-globalid", "short")
    result, _, _ = takeoff_run(runtime, tmp_path, model, "shortrun")

    assert result.error_code == "ifc_identity_invalid"


def test_malformed_unit_assignments_fail_closed(
    runtime: tuple[Path, Path], tmp_path: Path
) -> None:
    model = generate(runtime, tmp_path, "ifc4-malformed-unit", "unit")
    result, _, _ = takeoff_run(runtime, tmp_path, model, "unitrun")

    assert result.error_code == "ifc_schema_invalid"


def test_instance_bound_rejects_models_above_the_limit(
    runtime: tuple[Path, Path], generated: dict[str, Path], tmp_path: Path
) -> None:
    probe, _, _ = takeoff_run(
        runtime, tmp_path, generated["ifc4-walls-valid"], "probe"
    )
    total = int(probe.result_json["validation"]["total_instances"])
    below, _, _ = takeoff_run(
        runtime, tmp_path, generated["ifc4-walls-valid"], "below", max_instances=total + 1
    )
    at, _, _ = takeoff_run(
        runtime, tmp_path, generated["ifc4-walls-valid"], "at", max_instances=total
    )
    above, _, _ = takeoff_run(
        runtime,
        tmp_path,
        generated["ifc4-walls-valid"],
        "above",
        max_instances=total - 1,
    )

    assert below.error_code is None
    assert at.error_code is None
    assert above.error_code == "ifc_input_too_large"


def test_request_bound_rejects_limits_above_250k(
    runtime: tuple[Path, Path], generated: dict[str, Path], tmp_path: Path
) -> None:
    result, _, _ = takeoff_run(
        runtime,
        tmp_path,
        generated["ifc4-walls-valid"],
        "cap",
        max_instances=250_001,
    )

    assert result.error_code in {"invalid_bim_request", "worker_failed"}


def test_source_bytes_and_results_respect_privacy(
    runtime: tuple[Path, Path], generated: dict[str, Path], tmp_path: Path
) -> None:
    result, staged, _ = takeoff_run(
        runtime, tmp_path, generated["ifc4-walls-valid"], "privacy"
    )

    assert staged.read_bytes() == generated["ifc4-walls-valid"].read_bytes()
    assert result.result_json is not None
    serialized = json.dumps(result.result_json)
    assert str(generated["ifc4-walls-valid"]) not in serialized
    assert str(staged) not in serialized
    assert "FixtureProject" not in serialized
    assert "ISO-10303-21" not in serialized


def test_no_units_model_validates_without_findings(
    runtime: tuple[Path, Path], generated: dict[str, Path], tmp_path: Path
) -> None:
    result, _, _ = takeoff_run(
        runtime, tmp_path, generated["ifc4-no-units"], "nounits"
    )

    assert result.exit_code == 0
    assert result.error_code is None
    assert result.result_json is not None
    assert result.result_json["validation"]["validation_findings"] == 0
