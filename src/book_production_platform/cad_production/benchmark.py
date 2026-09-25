"""Bounded 20-case acceptance benchmark for the CAD V1A adapter."""

from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
import time
from pathlib import Path
from typing import Any, NoReturn

from book_production_platform.cad_production.errors import CadProductionError
from book_production_platform.cad_production.jobs import (
    ARTIFACT_NAMES,
    MAX_ARTIFACT_BYTES,
    CadJobResult,
    build_cad_job,
)
from book_production_platform.cad_production.qa import CadQAResult
from book_production_platform.cad_production.runtime import (
    MAX_WORKER_TIMEOUT_SECONDS,
    _load_runtime_lock,
)

CASE_SCHEMA = "cad.acceptance-cases.v1"
BENCHMARK_SCHEMA = "cad.acceptance-benchmark.v1"
CASE_COUNTS = {"accept": 5, "replay": 5, "reject": 8, "tamper_replay": 2}
EXPECTED_ERROR_CODES = {
    "invalid_part_ir",
    "unsupported_template",
    "unsupported_units",
    "dimension_out_of_range",
    "feature_constraint_violation",
    "replay_integrity_error",
}
_CASE_ID = re.compile(r"[a-z0-9][a-z0-9-]{0,63}\Z")
_FIXTURE_NAME = re.compile(r"[a-z0-9][a-z0-9-]{0,63}\.json\Z")
_OVERSIZED_BYTES = 65_537
_OVERSIZED_DESCRIPTOR = {"benchmark_fixture": "oversized-input", "bytes": _OVERSIZED_BYTES}


def _fail(code: str) -> NoReturn:
    raise CadProductionError(code) from None


def _is_link(path: Path) -> bool:
    try:
        return path.is_symlink() or path.is_junction()
    except (OSError, AttributeError):
        return True


def _reject_link_components(path: Path, code: str) -> None:
    absolute = Path(os.path.abspath(path))
    current = Path(absolute.anchor)
    for component in absolute.parts[1:]:
        current = current / component
        if _is_link(current):
            _fail(code)


def _pairs_no_duplicates(pairs: list[tuple[str, object]]) -> dict[str, object]:
    value: dict[str, object] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate key")
        value[key] = item
    return value


def _load_cases(fixtures_dir: Path) -> list[dict[str, Any]]:
    if not isinstance(fixtures_dir, Path):
        _fail("invalid_benchmark_fixtures")
    _reject_link_components(fixtures_dir, "invalid_benchmark_fixtures")
    try:
        root = fixtures_dir.resolve(strict=True)
        cases_path = root / "cases.json"
        if not root.is_dir() or _is_link(cases_path) or cases_path.stat().st_size > 1024 * 1024:
            _fail("invalid_benchmark_fixtures")
        registry_bytes = cases_path.read_bytes()
        registry = json.loads(
            registry_bytes.decode("utf-8"), object_pairs_hook=_pairs_no_duplicates
        )
    except CadProductionError:
        raise
    except (OSError, UnicodeDecodeError, ValueError, TypeError, RecursionError):
        _fail("invalid_benchmark_fixtures")

    if (
        not isinstance(registry, dict)
        or set(registry) != {"schema_version", "cases"}
        or registry.get("schema_version") != CASE_SCHEMA
        or not isinstance(registry.get("cases"), list)
    ):
        _fail("invalid_benchmark_fixtures")

    cases: list[dict[str, Any]] = registry["cases"]
    counts: dict[str, int] = {}
    identifiers: set[str] = set()
    accept_ids: set[str] = set()
    input_names: set[str] = set()
    for item in cases:
        if not isinstance(item, dict) or not isinstance(item.get("id"), str):
            _fail("invalid_benchmark_fixtures")
        identifier = item["id"]
        if not _CASE_ID.fullmatch(identifier) or identifier in identifiers:
            _fail("invalid_benchmark_fixtures")
        identifiers.add(identifier)
        kind = item.get("kind")
        if not isinstance(kind, str):
            _fail("invalid_benchmark_fixtures")
        counts[kind] = counts.get(kind, 0) + 1

        if kind in {"accept", "reject"}:
            expected_keys = {"id", "kind", "input"}
            if kind == "reject":
                expected_keys.add("expected_error")
            if set(item) != expected_keys:
                _fail("invalid_benchmark_fixtures")
            filename = item.get("input")
            if (
                not isinstance(filename, str)
                or not _FIXTURE_NAME.fullmatch(filename)
                or filename in input_names
            ):
                _fail("invalid_benchmark_fixtures")
            input_names.add(filename)
            fixture_path = root / filename
            if _is_link(fixture_path) or not fixture_path.is_file():
                _fail("invalid_benchmark_fixtures")
            try:
                size = fixture_path.stat().st_size
            except OSError:
                _fail("invalid_benchmark_fixtures")
            if filename == "oversized-input.json":
                if size > 1024:
                    _fail("invalid_benchmark_fixtures")
                try:
                    descriptor = json.loads(
                        fixture_path.read_text(encoding="utf-8"),
                        object_pairs_hook=_pairs_no_duplicates,
                    )
                except (OSError, UnicodeDecodeError, ValueError, TypeError, RecursionError):
                    _fail("invalid_benchmark_fixtures")
                if descriptor != _OVERSIZED_DESCRIPTOR or identifier != "oversized-input":
                    _fail("invalid_benchmark_fixtures")
            elif size > 64 * 1024:
                _fail("invalid_benchmark_fixtures")
            if kind == "accept":
                accept_ids.add(identifier)
            else:
                expected_error = item.get("expected_error")
                if expected_error not in EXPECTED_ERROR_CODES - {"replay_integrity_error"}:
                    _fail("invalid_benchmark_fixtures")
        elif kind == "replay":
            if set(item) != {"id", "kind", "reference"}:
                _fail("invalid_benchmark_fixtures")
        elif kind == "tamper_replay":
            if (
                set(item) != {"id", "kind", "reference", "tamper_file", "expected_error"}
                or item.get("tamper_file") not in ARTIFACT_NAMES - {"qa-manifest.json"}
                or item.get("expected_error") != "replay_integrity_error"
            ):
                _fail("invalid_benchmark_fixtures")
        else:
            _fail("invalid_benchmark_fixtures")

    if len(cases) != 20 or counts != CASE_COUNTS or len(input_names) != 13:
        _fail("invalid_benchmark_fixtures")
    for item in cases:
        reference = item.get("reference")
        if item["kind"] in {"replay", "tamper_replay"} and (
            not isinstance(reference, str) or reference not in accept_ids
        ):
            _fail("invalid_benchmark_fixtures")
    expected_fixture_names = {f"{name}.json" for name in (
        "plate-asymmetric",
        "plate-wide",
        "plate-square",
        "plate-long-thin",
        "plate-compact-thick",
        "malformed-json",
        "unknown-field",
        "unsupported-template",
        "unsupported-units",
        "dimension-out-of-range",
        "web-constraint",
        "duplicate-key",
        "oversized-input",
    )}
    if input_names != expected_fixture_names:
        _fail("invalid_benchmark_fixtures")
    return cases


def _resolve_input(root: Path, filename: str, case_id: str, scratch: Path) -> Path:
    source = root / filename
    if _is_link(source) or not source.is_file():
        _fail("invalid_benchmark_fixtures")
    if filename != "oversized-input.json":
        return source
    try:
        descriptor = json.loads(
            source.read_text(encoding="utf-8"), object_pairs_hook=_pairs_no_duplicates
        )
    except (OSError, UnicodeDecodeError, ValueError, TypeError, RecursionError):
        _fail("invalid_benchmark_fixtures")
    if descriptor != _OVERSIZED_DESCRIPTOR or case_id != "oversized-input":
        _fail("invalid_benchmark_fixtures")
    oversized = scratch / "oversized-input-materialized.json"
    try:
        with oversized.open("xb") as stream:
            stream.write(b" " * _OVERSIZED_BYTES)
    except OSError:
        _fail("benchmark_io_error")
    return oversized


def _result_record(
    case: dict[str, Any],
    expected: str,
    actual: str,
    error_code: str | None,
    elapsed: float,
    artifact_bytes: int,
    model_calls: int,
    outcome: str,
) -> dict[str, object]:
    return {
        "id": case["id"],
        "kind": case["kind"],
        "expected": expected,
        "actual": actual,
        "error_code": error_code,
        "result": outcome,
        "latency_seconds": round(elapsed, 3),
        "artifact_bytes": artifact_bytes,
        "model_calls": model_calls,
    }


def _qa_passed(result: CadJobResult) -> bool:
    qa = result.qa
    if not isinstance(qa, CadQAResult):
        return False
    return (
        qa.solid_count == 1
        and qa.bore_count == 4
        and qa.volume_mm3 > 0
        and qa.step_relative_volume_error <= 0.001
        and qa.stl_triangle_count > 0
        and qa.tessellation_faces > 0
        and qa.preview_profile == "isometric-1024x768.v1"
    )


def _published_bytes(result: CadJobResult) -> int:
    artifact_dir = result.artifact_dir
    if artifact_dir is None or _is_link(artifact_dir) or not artifact_dir.is_dir():
        _fail("artifact_read_error")
    try:
        if {entry.name for entry in artifact_dir.iterdir()} != ARTIFACT_NAMES:
            _fail("artifact_set_mismatch")
        total = 0
        for name in ARTIFACT_NAMES:
            artifact = artifact_dir / name
            if _is_link(artifact) or not artifact.is_file():
                _fail("artifact_read_error")
            size = artifact.stat().st_size
            if not 0 < size <= MAX_ARTIFACT_BYTES:
                _fail("artifact_read_error")
            total += size
        return total
    except OSError:
        _fail("artifact_read_error")


def _tampered_replay(
    input_path: Path,
    result: CadJobResult,
    tamper_file: str,
    freecadcmd: Path,
    timeout_s: int,
    parent: Path,
) -> CadJobResult:
    if result.fingerprint is None or result.artifact_dir is None:
        _fail("benchmark_outcome_mismatch")
    fingerprint = result.fingerprint
    source_root = result.artifact_dir.parent
    source_receipt = source_root / ".receipts" / f"{fingerprint}.json"
    if _is_link(source_receipt) or not source_receipt.is_file():
        _fail("benchmark_outcome_mismatch")
    try:
        with tempfile.TemporaryDirectory(prefix=".cad-benchmark-tamper-", dir=parent) as name:
            sandbox = Path(name)
            tampered_root = sandbox / "jobs"
            tampered_artifact = tampered_root / fingerprint
            shutil.copytree(result.artifact_dir, tampered_artifact)
            tampered_receipt = tampered_root / ".receipts" / source_receipt.name
            tampered_receipt.parent.mkdir()
            shutil.copyfile(source_receipt, tampered_receipt)
            target = tampered_artifact / tamper_file
            with target.open("ab") as stream:
                stream.write(b"\nCAD benchmark tamper")
            return build_cad_job(input_path, tampered_root, freecadcmd, timeout_s)
    except CadProductionError:
        raise
    except OSError:
        _fail("benchmark_io_error")


def _atomic_report(path: Path, report: dict[str, object]) -> None:
    payload = (
        json.dumps(report, ensure_ascii=False, allow_nan=False, sort_keys=True, indent=2).encode("utf-8")
        + b"\n"
    )
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb", prefix=f".{path.name}.", suffix=".tmp", dir=path.parent, delete=False
        ) as stream:
            temporary = Path(stream.name)
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        if _is_link(path) or path.exists():
            _fail("benchmark_output_exists")
        os.link(temporary, path)
    except CadProductionError:
        raise
    except OSError:
        _fail("benchmark_io_error")
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass


def run_benchmark(
    fixtures_dir: Path,
    output_path: Path,
    freecadcmd: Path,
    timeout_s: int = MAX_WORKER_TIMEOUT_SECONDS,
) -> dict[str, object]:
    """Run five fresh jobs, five replays, eight input rejects, and two tamper probes."""

    if (
        isinstance(timeout_s, bool)
        or not isinstance(timeout_s, int)
        or not 1 <= timeout_s <= MAX_WORKER_TIMEOUT_SECONDS
    ):
        _fail("invalid_timeout")
    cases = _load_cases(fixtures_dir)
    if not isinstance(output_path, Path) or not isinstance(freecadcmd, Path):
        _fail("unsafe_output_path")
    _reject_link_components(output_path, "unsafe_output_path")
    absolute_output = Path(os.path.abspath(output_path.expanduser()))
    parent = absolute_output.parent
    try:
        parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        _fail("unsafe_output_path")
    _reject_link_components(parent, "unsafe_output_path")
    jobs_root = parent / f"{absolute_output.stem}.jobs"
    if _is_link(absolute_output) or absolute_output.exists() or _is_link(jobs_root) or jobs_root.exists():
        _fail("benchmark_output_exists")

    fixture_root = fixtures_dir.resolve(strict=True)
    runtime_lock = _load_runtime_lock()
    runtime = {
        key: runtime_lock[key]
        for key in ("version", "build", "platform", "executable_sha256")
    }
    records: list[dict[str, object]] = []
    accepted: dict[str, tuple[Path, CadJobResult]] = {}
    summary = {
        "fresh_accepted": 0,
        "replayed": 0,
        "input_rejected": 0,
        "tamper_rejected": 0,
        "qa_failures": 0,
        "unexpected": 0,
    }
    unexpected_cases: list[str] = []
    fresh_latencies: list[float] = []
    replay_latencies: list[float] = []
    bytes_written = 0
    bytes_reused = 0
    model_calls = 0
    rejected_by_code: dict[str, int] = {}
    started = time.perf_counter()

    try:
        with tempfile.TemporaryDirectory(prefix=".cad-benchmark-inputs-", dir=parent) as temp_name:
            input_scratch = Path(temp_name)
            for case in cases:
                case_started = time.perf_counter()
                kind = case["kind"]
                expected = (
                    "accepted"
                    if kind == "accept"
                    else "replayed"
                    if kind == "replay"
                    else str(case["expected_error"])
                )
                error_code: str | None = None
                actual = "rejected"
                case_bytes = 0
                case_model_calls = 0
                passed = False
                result: CadJobResult | None = None

                try:
                    if kind == "accept":
                        input_path = _resolve_input(fixture_root, case["input"], case["id"], input_scratch)
                        result = build_cad_job(input_path, jobs_root, freecadcmd, timeout_s)
                        actual = result.status
                        error_code = result.error_code
                        case_model_calls = result.model_calls
                        if result.status == "accepted" and not result.replay:
                            accepted[case["id"]] = (input_path, result)
                            summary["fresh_accepted"] += 1
                            fresh_latencies.append(time.perf_counter() - case_started)
                            case_bytes = result.bytes_written
                            bytes_written += case_bytes
                            if not _qa_passed(result):
                                summary["qa_failures"] += 1
                            passed = _qa_passed(result)
                    elif kind == "replay":
                        reference = accepted.get(case["reference"])
                        if reference is None:
                            _fail("benchmark_outcome_mismatch")
                        input_path, _accepted_result = reference
                        result = build_cad_job(input_path, jobs_root, freecadcmd, timeout_s)
                        actual = result.status
                        error_code = result.error_code
                        case_model_calls = result.model_calls
                        if result.status == "replayed" and result.replay:
                            summary["replayed"] += 1
                            replay_latencies.append(time.perf_counter() - case_started)
                            case_bytes = _published_bytes(result)
                            bytes_reused += case_bytes
                            if not _qa_passed(result):
                                summary["qa_failures"] += 1
                            passed = _qa_passed(result)
                    elif kind == "reject":
                        input_path = _resolve_input(fixture_root, case["input"], case["id"], input_scratch)
                        result = build_cad_job(input_path, jobs_root, freecadcmd, timeout_s)
                        actual = result.status
                        error_code = result.error_code
                        case_model_calls = result.model_calls
                        if result.status == "rejected" and result.error_code == expected:
                            summary["input_rejected"] += 1
                            rejected_by_code[expected] = rejected_by_code.get(expected, 0) + 1
                            passed = True
                    else:
                        reference = accepted.get(case["reference"])
                        if reference is None:
                            _fail("benchmark_outcome_mismatch")
                        input_path, accepted_result = reference
                        result = _tampered_replay(
                            input_path,
                            accepted_result,
                            case["tamper_file"],
                            freecadcmd,
                            timeout_s,
                            parent,
                        )
                        actual = result.status
                        error_code = result.error_code
                        case_model_calls = result.model_calls
                        if result.status == "rejected" and result.error_code == expected:
                            summary["tamper_rejected"] += 1
                            rejected_by_code[expected] = rejected_by_code.get(expected, 0) + 1
                            passed = True
                except CadProductionError as error:
                    error_code = error.code
                    actual = "rejected"

                model_calls += case_model_calls
                passed = passed and case_model_calls == 0
                outcome = "pass" if passed else "unexpected"
                if not passed:
                    summary["unexpected"] += 1
                    unexpected_cases.append(case["id"])
                records.append(
                    _result_record(
                        case,
                        expected,
                        actual,
                        error_code,
                        time.perf_counter() - case_started,
                        case_bytes,
                        case_model_calls,
                        outcome,
                    )
                )
                if not passed:
                    break

    except CadProductionError:
        raise
    except OSError:
        _fail("benchmark_io_error")

    passed = (
        len(records) == 20
        and summary == {
            "fresh_accepted": 5,
            "replayed": 5,
            "input_rejected": 8,
            "tamper_rejected": 2,
            "qa_failures": 0,
            "unexpected": 0,
        }
        and model_calls == 0
    )
    report: dict[str, object] = {
        "schema_version": BENCHMARK_SCHEMA,
        "status": "passed" if passed else "failed",
        "cases_total": 20,
        "cases_completed": len(records),
        "summary": summary,
        "unexpected_cases": unexpected_cases,
        "rejected_by_code": dict(sorted(rejected_by_code.items())),
        "runtime": runtime,
        "timings_seconds": {
            "fresh_builds": [round(value, 3) for value in fresh_latencies],
            "fresh_mean": round(sum(fresh_latencies) / len(fresh_latencies), 3)
            if fresh_latencies
            else None,
            "replays": [round(value, 3) for value in replay_latencies],
            "replay_mean": round(sum(replay_latencies) / len(replay_latencies), 3)
            if replay_latencies
            else None,
            "wall": round(time.perf_counter() - started, 3),
        },
        "bytes_written": bytes_written,
        "bytes_reused": bytes_reused,
        "model_calls": model_calls,
        "monetary_cost": "unknown",
        "cases": records,
    }
    _atomic_report(absolute_output, report)
    return report


__all__ = ["BENCHMARK_SCHEMA", "CASE_SCHEMA", "run_benchmark"]
