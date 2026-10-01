"""Parent-side helpers that generate synthetic IFC fixtures for BIM tests."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from book_production_platform.bim_production.runtime import (
    MAX_WORKER_TIMEOUT_SECONDS,
    BimWorkerResult,
    run_bim_worker,
)

TESTS_BIM_ROOT = Path(__file__).resolve().parent
TESTS_ROOT = TESTS_BIM_ROOT.parent
PRODUCT_ROOT = TESTS_BIM_ROOT.parents[1]
FIXTURE_REGISTRY_ROOT = TESTS_ROOT / "fixtures" / "bim"
FIXTURE_MACRO_PATH = TESTS_BIM_ROOT / "fixture_worker.FCMacro"
FIXTURE_PROTOCOL = "bim-fixture.v1"


def product_root() -> Path:
    """Return the owning product repository root."""

    return PRODUCT_ROOT


def pinned_runtime_paths() -> tuple[Path, Path]:
    """Return the verified FreeCADCmd and IfcOpenShell site-packages paths."""

    runtime_root = (
        product_root()
        / ".runtime"
        / "cad"
        / "freecad-1.1.3"
        / "extracted"
        / "FreeCAD_1.1.3-Windows-x86_64-py311"
    )
    return runtime_root / "FreeCADCmd.exe", runtime_root / "bin" / "Lib" / "site-packages"


def require_pinned_runtime() -> tuple[Path, Path]:
    """Return the pinned runtime paths or skip when this host does not have them.

    The pinned FreeCAD 1.1.3 + IfcOpenShell runtime is a local Windows build under
    ``.runtime/`` that is never committed, so hosts without it (CI, Linux, macOS)
    skip the real-worker tests instead of failing, matching the CAD test suite.
    """

    import pytest

    freecadcmd, site_packages = pinned_runtime_paths()
    if not freecadcmd.is_file() or not site_packages.is_dir():
        pytest.skip("the pinned local FreeCAD + IfcOpenShell runtime is unavailable")
    return freecadcmd, site_packages


def load_fixture_cases(filename: str) -> dict[str, dict[str, Any]]:
    """Load and minimally bound a descriptor registry of fixture cases."""

    path = FIXTURE_REGISTRY_ROOT / filename
    raw = path.read_bytes()
    if len(raw) > 256 * 1024:
        raise ValueError("fixture registry exceeds its bound")
    value = json.loads(raw.decode("utf-8"))
    if (
        not isinstance(value, dict)
        or value.get("protocol") != "bim-fixture-registry.v1"
        or not isinstance(value.get("cases"), list)
    ):
        raise ValueError("malformed fixture registry")
    cases: dict[str, dict[str, Any]] = {}
    for case in value["cases"]:
        if (
            not isinstance(case, dict)
            or case.get("protocol") != FIXTURE_PROTOCOL
            or not isinstance(case.get("case_id"), str)
        ):
            raise ValueError("malformed fixture case")
        cases[case["case_id"]] = case
    return cases


def run_fixture_case(
    case: dict[str, Any],
    freecadcmd: Path,
    ifc_site_packages: Path,
    output_root: Path,
    run_label: str,
    timeout_s: int = MAX_WORKER_TIMEOUT_SECONDS,
) -> tuple[BimWorkerResult, Path]:
    """Generate one synthetic IFC through the pinned worker's test macro."""

    if not isinstance(run_label, str) or not run_label.isidentifier():
        raise ValueError("run label must be a valid identifier")
    output_root.mkdir(parents=True, exist_ok=True)
    work_root = output_root / f"{run_label}-{case['case_id']}"
    work_root.mkdir()
    request_path = work_root / "fixture-request.bimreq"
    request_path.write_text(
        json.dumps({"protocol": FIXTURE_PROTOCOL, "case": case}, sort_keys=True),
        encoding="utf-8",
    )
    result = run_bim_worker(
        freecadcmd,
        ifc_site_packages,
        "fixture",
        request_path,
        work_root,
        timeout_s=timeout_s,
        worker_path=FIXTURE_MACRO_PATH,
    )
    return result, work_root


def generated_fixture_path(work_root: Path, case: dict[str, Any]) -> Path:
    """Return the generated IFC path for a successful fixture case."""

    return work_root / str(case["output_name"])
