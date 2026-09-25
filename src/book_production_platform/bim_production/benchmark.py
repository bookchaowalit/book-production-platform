"""The pinned 20-case synthetic acceptance benchmark for BIM takeoff."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from book_production_platform.bim_ir import BimTakeoffIRError
from book_production_platform.bim_production.errors import BimProductionError
from book_production_platform.bim_production.jobs import (
    BimJobResult,
    build_bim_takeoff_job,
)
from book_production_platform.bim_production.runtime import run_bim_worker

BENCHMARK_PROTOCOL = "bim-benchmark-cases.v1"
BENCHMARK_SUMMARY_VERSION = "bim-benchmark-summary.v1"
FIXTURE_PROTOCOL = "bim-fixture.v1"
FRESH_CASES = 5
FRESH_COMPLETE = 4
FRESH_PARTIAL = 1
REPLAY_CASES = 5
REJECT_CASES = 8
TAMPER_CASES = 2
TOTAL_CASES = FRESH_CASES + REPLAY_CASES + REJECT_CASES + TAMPER_CASES
MAX_CASES_BYTES = 256 * 1024


def load_benchmark_cases(path: Path) -> dict[str, Any]:
    """Validate the pinned registry shape and exact 20-case composition."""

    raw = path.read_bytes()
    if len(raw) > MAX_CASES_BYTES:
        raise BimProductionError("invalid_bim_takeoff_ir")
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as error:
        raise BimProductionError("invalid_bim_takeoff_ir") from error
    fresh = value.get("fresh") if isinstance(value, dict) else None
    rejects = value.get("rejects") if isinstance(value, dict) else None
    if value.get("protocol") != BENCHMARK_PROTOCOL or value.get("fixture_protocol") != FIXTURE_PROTOCOL:
        raise BimProductionError("invalid_bim_takeoff_ir")
    if set(value) != {"protocol", "fixture_protocol", "fresh", "rejects"}:
        raise BimProductionError("invalid_bim_takeoff_ir")
    if not isinstance(fresh, list) or not isinstance(rejects, list):
        raise BimProductionError("invalid_bim_takeoff_ir")
    if len(fresh) != FRESH_CASES or len(rejects) != REJECT_CASES:
        raise BimProductionError("invalid_bim_takeoff_ir")
    complete = sum(1 for case in fresh if case.get("expected") == "complete")
    partial = sum(1 for case in fresh if case.get("expected") == "partial")
    if complete != FRESH_COMPLETE or partial != FRESH_PARTIAL:
        raise BimProductionError("invalid_bim_takeoff_ir")
    ids = [str(case.get("id")) for case in fresh + rejects]
    if len(set(ids)) != len(ids) or any(not case_id for case_id in ids):
        raise BimProductionError("invalid_bim_takeoff_ir")
    for case in rejects:
        if not isinstance(case.get("expected_code"), str):
            raise BimProductionError("invalid_bim_takeoff_ir")
    registry: dict[str, Any] = value
    return registry


def _write_ir(directory: Path, takeoff_id: str, classes: list[str]) -> Path:
    path = directory / f"{takeoff_id}.bimreq"
    document = {
        "schema_version": "bim-takeoff.v1",
        "takeoff_id": takeoff_id,
        "element_classes": classes,
        "output_units": "SI",
    }
    path.write_text(json.dumps(document, sort_keys=True), encoding="utf-8")
    return path


def _generate_fixture(
    descriptor: dict[str, Any],
    workspace: Path,
    run_label: str,
    freecadcmd: Path,
    ifc_site_packages: Path,
    fixture_macro: Path,
) -> Path:
    work_root = workspace / f"fixture-{run_label}"
    work_root.mkdir()
    request = work_root / "fixture-request.bimreq"
    request.write_text(
        json.dumps({"protocol": FIXTURE_PROTOCOL, "case": descriptor}, sort_keys=True),
        encoding="utf-8",
    )
    result = run_bim_worker(
        freecadcmd,
        ifc_site_packages,
        "fixture",
        request,
        work_root,
        timeout_s=120,
        worker_path=fixture_macro,
    )
    if result.error_code is not None or result.result_json is None:
        raise BimProductionError(result.error_code or "worker_failed")
    return work_root / str(descriptor["output_name"])


def _outcome_of(result: BimJobResult) -> tuple[str, str | None]:
    if result.status == "rejected":
        return "rejected", result.error_code
    return result.status, None


def run_benchmark(
    cases_path: Path,
    output_root: Path,
    freecadcmd: Path,
    ifc_site_packages: Path,
    fixture_macro: Path,
    timeout_s: int = 120,
    summary_path: Path | None = None,
) -> dict[str, Any]:
    """Run the pinned 20 cases; ``output_root`` is the local job area."""

    registry = load_benchmark_cases(cases_path)
    output_root.mkdir(parents=True, exist_ok=True)
    workspace = output_root / "benchmark-work"
    workspace.mkdir(exist_ok=True)
    case_results: list[dict[str, Any]] = []
    passed = 0

    def record(case_id: str, outcome: str, error_code: str | None, started: float) -> None:
        case_results.append(
            {
                "id": case_id,
                "outcome": outcome,
                "error_code": error_code,
                "latency_ms": int((time.monotonic() - started) * 1000),
                "model_calls": 0,
            }
        )

    def record_pass(
        case_id: str, ok: bool, outcome: str, error_code: str | None, started: float
    ) -> None:
        nonlocal passed
        if ok:
            passed += 1
            record(case_id, outcome, None, started)
        else:
            record(case_id, outcome, error_code or "unexpected_outcome", started)

    fresh_inputs: dict[str, tuple[Path, Path]] = {}

    for case in registry["fresh"]:
        case_id = str(case["id"])
        started = time.monotonic()
        ir_path = _write_ir(workspace, case_id, case["element_classes"])
        try:
            ifc_path = _generate_fixture(
                case["fixture"], workspace, case_id, freecadcmd, ifc_site_packages, fixture_macro
            )
            result = build_bim_takeoff_job(
                ir_path,
                ifc_path,
                output_root,
                freecadcmd,
                ifc_site_packages,
                timeout_s=timeout_s,
            )
            outcome, error_code = _outcome_of(result)
            record_pass(case_id, outcome == case["expected"], outcome, error_code, started)
            fresh_inputs[case_id] = (ir_path, ifc_path)
        except BimProductionError as error:
            record(case_id, "rejected", error.code, started)
        except BimTakeoffIRError:
            record(case_id, "rejected", "invalid_bim_takeoff_ir", started)

    for case in registry["fresh"]:
        case_id = f"replay-{case['id']}"
        started = time.monotonic()
        inputs = fresh_inputs.get(str(case["id"]))
        if inputs is None:
            record(case_id, "skipped", "fresh_case_failed", started)
            continue
        try:
            result = build_bim_takeoff_job(
                inputs[0],
                inputs[1],
                output_root,
                freecadcmd,
                ifc_site_packages,
                timeout_s=timeout_s,
            )
            outcome, _ = _outcome_of(result)
            record_pass(case_id, outcome == "replayed", outcome, None, started)
        except BimProductionError as error:
            record(case_id, "rejected", error.code, started)

    for case in registry["rejects"]:
        case_id = f"reject-{case['id']}"
        started = time.monotonic()
        try:
            result = _run_reject_case(
                case, workspace, output_root, freecadcmd, ifc_site_packages, fixture_macro, timeout_s
            )
            outcome, error_code = _outcome_of(result)
            record_pass(
                case_id,
                outcome == "rejected" and error_code == case["expected_code"],
                outcome,
                error_code or case["expected_code"],
                started,
            )
        except BimProductionError as error:
            record_pass(
                case_id,
                error.code == case["expected_code"],
                "rejected",
                error.code,
                started,
            )
        except BimTakeoffIRError:
            record_pass(
                case_id,
                case["expected_code"] == "invalid_bim_takeoff_ir",
                "rejected",
                "invalid_bim_takeoff_ir",
                started,
            )

    tamper_specs = (
        ("tamper-artifact-" + str(registry["fresh"][0]["id"]), "artifact"),
        ("tamper-receipt-" + str(registry["fresh"][1]["id"]), "receipt"),
    )
    for case_id, tamper_kind in tamper_specs:
        started = time.monotonic()
        fresh_id = case_id.split("-", 2)[2]
        inputs = fresh_inputs.get(fresh_id)
        if inputs is None:
            record(case_id, "skipped", "fresh_case_failed", started)
            continue
        ir_path, ifc_path = inputs
        try:
            probe = build_bim_takeoff_job(
                ir_path, ifc_path, output_root, freecadcmd, ifc_site_packages, timeout_s=timeout_s
            )
            if probe.fingerprint is None:
                record(case_id, "benchmark_error", "missing_job", started)
                continue
            original = ""
            if tamper_kind == "artifact":
                target = output_root / "jobs" / str(probe.fingerprint) / "takeoff.json"
                original = target.read_bytes().decode("utf-8")
                target.write_bytes((original + " ").encode("utf-8"))
            else:
                receipt_path = output_root / ".receipts" / f"{probe.fingerprint}.json"
                original = receipt_path.read_text(encoding="utf-8")
                receipt = json.loads(original)
                receipt["row_count"] = 424242
                receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
            try:
                build_bim_takeoff_job(
                    ir_path, ifc_path, output_root, freecadcmd, ifc_site_packages, timeout_s=timeout_s
                )
                record_pass(case_id, False, "accepted", "expected_rejection", started)
            except BimProductionError as error:
                record_pass(
                    case_id,
                    error.code == "takeoff_integrity_error",
                    "rejected",
                    error.code,
                    started,
                )
            finally:
                if tamper_kind == "artifact":
                    target.write_bytes(original.encode("utf-8"))
                else:
                    receipt_path.write_text(original, encoding="utf-8")
        except BimProductionError as error:
            record(case_id, "rejected", error.code, started)

    summary = {
        "schema_version": BENCHMARK_SUMMARY_VERSION,
        "status": "passed" if passed == TOTAL_CASES else "failed",
        "total": TOTAL_CASES,
        "passed": passed,
        "model_calls_total": 0,
        "monetary_cost": "unknown",
        "cases": case_results,
    }
    if summary_path is not None:
        summary_path.parent.mkdir(parents=True, exist_ok=True)
        summary_path.write_text(
            json.dumps(summary, ensure_ascii=False, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    return summary


def _run_reject_case(
    case: dict[str, Any],
    workspace: Path,
    output_root: Path,
    freecadcmd: Path,
    ifc_site_packages: Path,
    fixture_macro: Path,
    timeout_s: int,
) -> BimJobResult:
    ir_id = f"reject-{case['id']}"
    ir_path = _write_ir(workspace, ir_id, case.get("element_classes") or ["IfcWall"])
    kind = str(case["ifc_kind"])
    if kind == "invalid-ir":
        ir_path.write_text('{"schema_version":"bim-takeoff.v1"}', encoding="utf-8")
        return build_bim_takeoff_job(
            ir_path, workspace / "unused.ifc", output_root, freecadcmd, ifc_site_packages, timeout_s
        )
    if kind == "missing-ifc":
        return build_bim_takeoff_job(
            ir_path, workspace / "missing.ifc", output_root, freecadcmd, ifc_site_packages, timeout_s
        )
    if kind == "wrong-extension":
        return build_bim_takeoff_job(
            ir_path, ir_path, output_root, freecadcmd, ifc_site_packages, timeout_s
        )
    descriptor = case["fixture"]
    ifc_path = _generate_fixture(
        descriptor, workspace, ir_id, freecadcmd, ifc_site_packages, fixture_macro
    )
    return build_bim_takeoff_job(
        ir_path, ifc_path, output_root, freecadcmd, ifc_site_packages, timeout_s
    )
