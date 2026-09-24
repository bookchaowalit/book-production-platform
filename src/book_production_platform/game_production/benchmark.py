"""Acceptance benchmark for deterministic Game Scene Builder V1A jobs."""

from __future__ import annotations

import json
import math
import os
import tempfile
import time
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from book_production_platform.game_ir import (
    MAX_GAME_IR_BYTES,
    GameInputError,
    GameIR,
    parse_game_ir,
    validate_game_ir,
)
from book_production_platform.game_production.errors import GameJobError
from book_production_platform.game_production.jobs import (
    MAX_QA_MANIFEST_BYTES,
    GameJobResult,
    compose_game_scene,
)
from book_production_platform.game_production.qa import (
    TestRunSummary,
    verify_gameplay_trace,
)

BENCHMARK_SCHEMA = "game-production.benchmark.v1"
EXPECTED_ACCEPTED_FIXTURES = frozenset(
    {
        "minimal-room.json",
        "multi-wall-route.json",
        "two-objectives.json",
        "near-wall-movement.json",
        "larger-valid-room.json",
    }
)
EXPECTED_REJECTION_CODES = {
    "malformed-json.json": "invalid_game_ir",
    "oversized-input.json": "resource_cap_exceeded",
    "unknown-field.json": "invalid_game_ir",
    "unsupported-template.json": "invalid_game_ir",
    "duplicate-id.json": "invalid_game_geometry",
    "out-of-bounds.json": "invalid_game_geometry",
    "overlap.json": "invalid_game_geometry",
    "speed-out-of-range.json": "invalid_game_ir",
    "wall-cell-cap.json": "resource_cap_exceeded",
}
RUNTIME_BLOCKING_CODES = frozenset(
    {
        "unity_unavailable",
        "unsupported_unity_version",
        "deadline_exceeded",
        "unity_log_cap_exceeded",
        "unity_worker_failed",
    }
)
@dataclass(frozen=True)
class GameFixtureEvidence:
    fixture: str
    fingerprint: str
    artifact_dir: Path
    artifact_hashes: dict[str, str]
    elapsed_ms: float
    project_bytes: int
    unity_version: str
    unity_executable_sha256: str
    editmode: TestRunSummary
    playmode: TestRunSummary
    trace_events: tuple[str, ...]


def _json_files(directory: Path) -> dict[str, Path]:
    if directory.is_symlink() or not directory.is_dir():
        raise GameJobError("benchmark_fixture_error", "Benchmark fixture directory is unavailable")
    files: dict[str, Path] = {}
    try:
        for path in directory.iterdir():
            if path.suffix.lower() != ".json":
                continue
            if path.is_symlink() or not path.is_file() or path.name in files:
                raise GameJobError("benchmark_fixture_error", "Benchmark fixture file is unsafe")
            files[path.name] = path
    except OSError as error:
        raise GameJobError("benchmark_fixture_error", "Benchmark fixtures cannot be inspected") from error
    return files


def _fixture_sets(fixtures_dir: Path) -> tuple[list[Path], list[Path]]:
    root = Path(fixtures_dir).expanduser()
    if root.is_symlink() or not root.is_dir():
        raise GameJobError("benchmark_fixture_error", "Benchmark fixture root is unavailable")
    accepted = _json_files(root / "accepted")
    rejected = _json_files(root / "rejected")
    if set(accepted) != EXPECTED_ACCEPTED_FIXTURES or set(rejected) != set(EXPECTED_REJECTION_CODES):
        raise GameJobError("benchmark_fixture_error", "Benchmark fixture counts or names do not match the contract")
    return [accepted[name] for name in sorted(accepted)], [rejected[name] for name in sorted(rejected)]


def _read_manifest(artifact_dir: Path) -> dict[str, Any]:
    path = artifact_dir / "qa-manifest.json"
    if path.is_symlink() or not path.is_file():
        raise GameJobError("benchmark_integrity_error", "Accepted artifact manifest is unavailable")
    try:
        if path.stat().st_size > MAX_QA_MANIFEST_BYTES:
            raise GameJobError("benchmark_integrity_error", "Accepted artifact manifest exceeds its size limit")
        with path.open("rb") as source:
            raw = source.read(MAX_QA_MANIFEST_BYTES + 1)
        if len(raw) > MAX_QA_MANIFEST_BYTES:
            raise GameJobError("benchmark_integrity_error", "Accepted artifact manifest exceeds its size limit")
        value = json.loads(raw.decode("utf-8"))
    except GameJobError:
        raise
    except (OSError, UnicodeDecodeError, ValueError, RecursionError) as error:
        raise GameJobError("benchmark_integrity_error", "Accepted artifact manifest is invalid") from error
    if not isinstance(value, dict):
        raise GameJobError("benchmark_integrity_error", "Accepted artifact manifest has an invalid shape")
    return value


def _positive_test_summary(value: object) -> TestRunSummary:
    if not isinstance(value, dict):
        raise GameJobError("benchmark_integrity_error", "Accepted artifact test evidence is missing")
    total = value.get("total")
    passed = value.get("passed")
    if (
        isinstance(total, bool)
        or not isinstance(total, int)
        or isinstance(passed, bool)
        or not isinstance(passed, int)
        or total < 1
        or passed != total
    ):
        raise GameJobError("benchmark_integrity_error", "Accepted artifact test evidence did not pass")
    return TestRunSummary(total=total, passed=passed)


def _read_fixture_bytes(path: Path) -> bytes:
    fixture = Path(path)
    if fixture.is_symlink():
        raise GameJobError("benchmark_fixture_error", "Benchmark fixture file is unsafe")
    try:
        with fixture.open("rb") as source:
            payload = source.read(MAX_GAME_IR_BYTES + 1)
    except OSError as error:
        raise GameJobError("benchmark_fixture_error", "Benchmark fixture cannot be read") from error
    if len(payload) > MAX_GAME_IR_BYTES:
        raise GameJobError("benchmark_fixture_error", "Benchmark fixture exceeds size cap")
    return payload


def run_game_fixture(path: Path, output_root: Path, unity_bin: Path) -> GameFixtureEvidence:
    """Build and inspect one fresh accepted fixture using the real job API."""
    game_path = Path(path)
    try:
        source = _read_fixture_bytes(game_path)
        game_ir: GameIR = validate_game_ir(parse_game_ir(source))
    except GameJobError:
        raise
    except GameInputError as error:
        raise GameJobError("benchmark_fixture_error", "Accepted fixture does not satisfy GameIR") from error
    started = time.perf_counter()
    result: GameJobResult = compose_game_scene(game_path, output_root, unity_bin)
    elapsed_ms = (time.perf_counter() - started) * 1000
    if result.status != "accepted" or result.replay or result.model_calls != 0:
        raise GameJobError("benchmark_outcome_error", "Accepted fixture did not create a fresh model-free job")

    manifest = _read_manifest(result.artifact_dir)
    if (
        manifest.get("schema_version") != "game-production.qa.v1"
        or manifest.get("status") != "accepted"
        or manifest.get("model_calls") != 0
        or manifest.get("fingerprint") != result.fingerprint
    ):
        raise GameJobError("benchmark_integrity_error", "Accepted artifact manifest does not match the job")
    qa = manifest.get("qa")
    usage = manifest.get("resource_usage")
    if not isinstance(qa, dict) or not isinstance(usage, dict):
        raise GameJobError("benchmark_integrity_error", "Accepted artifact QA evidence is incomplete")
    editmode = _positive_test_summary(qa.get("editmode_tests"))
    playmode = _positive_test_summary(qa.get("playmode_tests"))
    project_bytes = usage.get("project_bytes")
    unity_version = manifest.get("unity_version")
    executable_hash = manifest.get("unity_executable_sha256")
    if (
        isinstance(project_bytes, bool)
        or not isinstance(project_bytes, int)
        or project_bytes < 1
        or not isinstance(unity_version, str)
        or not isinstance(executable_hash, str)
    ):
        raise GameJobError("benchmark_integrity_error", "Accepted artifact resource evidence is invalid")
    trace_path = result.artifact_dir / "gameplay-trace.json"
    trace_events, _ = verify_gameplay_trace(trace_path, game_ir)
    if qa.get("gameplay_events") != list(trace_events):
        raise GameJobError("benchmark_integrity_error", "Accepted artifact trace does not match QA evidence")
    return GameFixtureEvidence(
        fixture=game_path.name,
        fingerprint=result.fingerprint,
        artifact_dir=result.artifact_dir,
        artifact_hashes=result.artifact_hashes,
        elapsed_ms=elapsed_ms,
        project_bytes=project_bytes,
        unity_version=unity_version,
        unity_executable_sha256=executable_hash,
        editmode=editmode,
        playmode=playmode,
        trace_events=trace_events,
    )


def _wall_cell_cap_payload() -> bytes:
    cells = [[x, y] for y in range(2, 8) for x in range(32)] + [[0, 8]]
    value = {
        "schema_version": "game-ir.v1",
        "scene_id": "wall-cell-cap",
        "template": "top-down-collect.v1",
        "palette": "palette-minimal.v1",
        "world": {"width": 32, "height": 16, "tile_size": 1.0},
        "player": {"id": "runner", "spawn": [0, 0], "speed": 4.0},
        "walls": [{"id": "wall-cap", "cells": cells}],
        "objectives": [{"id": "key", "cell": [1, 0]}],
        "exit": {"id": "gate", "cell": [2, 0]},
    }
    return json.dumps(value, separators=(",", ":")).encode("utf-8")


def _rejection_input(path: Path) -> tuple[bytes | None, int | None]:
    try:
        descriptor = json.loads(_read_fixture_bytes(path).decode("utf-8"))
    except (UnicodeDecodeError, ValueError, RecursionError):
        return None, None
    if not isinstance(descriptor, dict):
        return None, None
    if descriptor.get("benchmark_case") == "oversized-input":
        return b" " * (MAX_GAME_IR_BYTES + 1), MAX_GAME_IR_BYTES + 1
    if descriptor.get("benchmark_case") == "wall-cell-cap":
        count = descriptor.get("count")
        if count != 193:
            raise GameJobError("benchmark_fixture_error", "Wall cap fixture must contain 193 cells")
        return _wall_cell_cap_payload(), None
    return None, None


def run_rejection_fixture(path: Path, output_root: Path, unity_bin: Path) -> str:
    """Run one invalid input and return its stable rejection code."""
    fixture = Path(path)
    expected = EXPECTED_REJECTION_CODES.get(fixture.name)
    if expected is None:
        raise GameJobError("benchmark_fixture_error", "Unknown rejection fixture")
    payload, exact_bytes = _rejection_input(fixture)
    if payload is None and exact_bytes is None:
        try:
            return _compose_rejection_code(fixture, output_root, unity_bin)
        except GameJobError as error:
            return error.code
    try:
        with tempfile.TemporaryDirectory(prefix="game-reject-", dir=output_root) as temporary:
            generated = Path(temporary) / fixture.name
            if exact_bytes is not None:
                generated.write_bytes(payload or b" " * exact_bytes)
            else:
                generated.write_bytes(payload or b"")
            return _compose_rejection_code(generated, output_root, unity_bin)
    except (GameInputError, GameJobError) as error:
        return error.code
    except OSError as error:
        raise GameJobError("benchmark_runtime_error", "Generated rejection input could not be staged") from error


def _compose_rejection_code(path: Path, output_root: Path, unity_bin: Path) -> str:
    try:
        compose_game_scene(path, output_root, unity_bin)
    except (GameInputError, GameJobError) as error:
        return error.code
    return "accepted_unexpectedly"


def run_tampered_replay_case(
    path: Path,
    artifact_dir: Path,
    output_root: Path,
    unity_bin: Path,
) -> str:
    """Mutate a scene temporarily and require replay to reject it without overwrite."""
    source = Path(path).read_bytes()
    try:
        game_ir = validate_game_ir(parse_game_ir(source))
    except GameInputError as error:
        raise GameJobError("benchmark_fixture_error", "Tamper case input is invalid") from error
    scene_path = Path(artifact_dir) / "project" / "Assets" / "Scenes" / f"{game_ir.scene_id}.unity"
    if scene_path.is_symlink() or not scene_path.is_file():
        raise GameJobError("benchmark_integrity_error", "Tamper case scene is unavailable")
    try:
        original = scene_path.read_bytes()
        scene_path.write_bytes(original + b"\nbenchmark-tamper")
        try:
            result = compose_game_scene(path, output_root, unity_bin)
        except (GameInputError, GameJobError) as error:
            return "tampered_replay_rejected" if error.code == "replay_integrity_error" else error.code
        return "tamper_accepted" if result.status == "replayed" else "unexpected_outcome"
    except OSError as error:
        raise GameJobError("benchmark_integrity_error", "Tamper case could not modify its own scene") from error
    finally:
        try:
            if "original" in locals():
                scene_path.write_bytes(original)
        except OSError as error:
            raise GameJobError("benchmark_integrity_error", "Tamper case scene could not be restored") from error


def summarize(outcomes: list[str]) -> dict[str, int]:
    """Count accepted, exact replay, expected rejection, tamper, and other results."""
    counts = Counter(outcomes)
    known = {"accepted", "replayed", "rejected", "tampered_replay_rejected"}
    return {
        "accepted": counts["accepted"],
        "replayed": counts["replayed"],
        "rejected": counts["rejected"],
        "tampered_replay_rejections": counts["tampered_replay_rejected"],
        "unexpected_outcomes": sum(count for status, count in counts.items() if status not in known),
    }


def _latency_summary(values: list[float]) -> dict[str, int | float | None]:
    ordered = sorted(values)
    if not ordered:
        return {"samples": 0, "min": None, "median": None, "p95": None, "max": None}
    p95_index = max(0, math.ceil(0.95 * len(ordered)) - 1)
    return {
        "samples": len(ordered),
        "min": round(ordered[0], 3),
        "median": round(ordered[len(ordered) // 2], 3),
        "p95": round(ordered[p95_index], 3),
        "max": round(ordered[-1], 3),
    }


def _validated_report_path(path: Path) -> Path:
    output = Path(path).expanduser()
    if output.is_symlink() or not output.name or output.is_dir():
        raise GameJobError("unsafe_output_path", "Benchmark report path must name a file")
    return output


def _write_report(path: Path, report: dict[str, object]) -> None:
    output = _validated_report_path(path)
    try:
        output.parent.mkdir(parents=True, exist_ok=True)
        temporary = output.with_name(f"{output.name}.{uuid4().hex}.tmp")
        with temporary.open("xb") as destination:
            destination.write((json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8"))
            destination.flush()
            os.fsync(destination.fileno())
        os.replace(temporary, output)
    except OSError as error:
        try:
            if "temporary" in locals():
                temporary.unlink(missing_ok=True)
        except OSError:
            pass
        raise GameJobError("publication_failed", "Benchmark report cannot be written") from error


def run_benchmark(fixtures_dir: Path, output_path: Path, unity_bin: Path) -> dict[str, object]:
    """Run five native builds, five exact replays, nine rejects, and one tamper case."""
    report_path = _validated_report_path(output_path)
    accepted_fixtures, rejected_fixtures = _fixture_sets(fixtures_dir)
    try:
        report_path.parent.mkdir(parents=True, exist_ok=True)
        artifact_root: Path | None = None
        for _ in range(10):
            candidate = report_path.parent / f".b-{uuid4().hex[:8]}"
            try:
                candidate.mkdir()
                artifact_root = candidate
                break
            except FileExistsError:
                continue
        if artifact_root is None:
            raise GameJobError("publication_failed", "Benchmark artifact directory cannot be created")
    except GameJobError:
        raise
    except OSError as error:
        raise GameJobError("publication_failed", "Benchmark output directory is unavailable") from error

    outcomes: list[str] = []
    unexpected: list[dict[str, str]] = []
    blocker_codes: set[str] = set()
    fresh_latencies: list[float] = []
    replay_latencies: list[float] = []
    evidence: list[GameFixtureEvidence] = []
    rejection_codes: dict[str, str] = {}
    outcome_records: list[dict[str, str]] = []

    for fixture in accepted_fixtures:
        try:
            fresh = run_game_fixture(fixture, artifact_root, unity_bin)
        except (GameInputError, GameJobError) as error:
            outcomes.append("runtime_blocked" if error.code in RUNTIME_BLOCKING_CODES else "unexpected")
            unexpected.append({"fixture": fixture.name, "error": error.code})
            outcome_records.append({"fixture": fixture.name, "status": "blocked", "error": error.code})
            if error.code in RUNTIME_BLOCKING_CODES:
                blocker_codes.add(error.code)
                break
            continue
        evidence.append(fresh)
        fresh_latencies.append(fresh.elapsed_ms)
        outcomes.append("accepted")
        outcome_records.append({"fixture": fixture.name, "status": "accepted"})

        replay_started = time.perf_counter()
        try:
            replay = compose_game_scene(fixture, artifact_root, unity_bin)
        except (GameInputError, GameJobError) as error:
            unexpected.append({"fixture": fixture.name, "error": error.code})
            outcomes.append("unexpected_replay")
            outcome_records.append({"fixture": fixture.name, "status": "replay_failed", "error": error.code})
            continue
        replay_elapsed = (time.perf_counter() - replay_started) * 1000
        if replay.status != "replayed" or not replay.replay or replay.artifact_hashes != fresh.artifact_hashes:
            unexpected.append({"fixture": fixture.name, "error": "replay_mismatch"})
            outcomes.append("unexpected_replay")
            outcome_records.append({"fixture": fixture.name, "status": "replay_mismatch"})
            continue
        replay_latencies.append(replay_elapsed)
        outcomes.append("replayed")
        outcome_records.append({"fixture": fixture.name, "status": "replayed"})

    for fixture in rejected_fixtures:
        try:
            actual_code = run_rejection_fixture(fixture, artifact_root, unity_bin)
        except GameJobError as error:
            actual_code = error.code
        expected_code = EXPECTED_REJECTION_CODES[fixture.name]
        rejection_codes[fixture.name] = actual_code
        if actual_code == expected_code:
            outcomes.append("rejected")
            outcome_records.append({"fixture": fixture.name, "status": "rejected", "error": actual_code})
        else:
            outcomes.append("unexpected_rejection")
            unexpected.append({"fixture": fixture.name, "error": actual_code})
            outcome_records.append({"fixture": fixture.name, "status": "unexpected", "error": actual_code})

    tamper_status = "not_run"
    if evidence:
        tamper_status = run_tampered_replay_case(
            accepted_fixtures[0],
            evidence[0].artifact_dir,
            artifact_root,
            unity_bin,
        )
        if tamper_status == "tampered_replay_rejected":
            outcomes.append(tamper_status)
            outcome_records.append({"fixture": "tampered-scene-replay", "status": tamper_status, "error": "replay_integrity_error"})
        else:
            outcomes.append("unexpected_tamper")
            unexpected.append({"fixture": "tampered-scene-replay", "error": tamper_status})
            outcome_records.append({"fixture": "tampered-scene-replay", "status": "unexpected", "error": tamper_status})

    counts = summarize(outcomes)
    counts["rejected_total"] = counts["rejected"] + counts["tampered_replay_rejections"]
    passed_counts = counts == {
        "accepted": 5,
        "replayed": 5,
        "rejected": 9,
        "tampered_replay_rejections": 1,
        "unexpected_outcomes": 0,
        "rejected_total": 10,
    }
    runtime_status = "blocked" if blocker_codes else ("passed" if passed_counts else "failed")
    total_project_bytes = sum(item.project_bytes for item in evidence)
    project_sizes = [item.project_bytes for item in evidence]
    test_totals = {
        "editmode": {
            "total": sum(item.editmode.total for item in evidence),
            "passed": sum(item.editmode.passed for item in evidence),
        },
        "playmode": {
            "total": sum(item.playmode.total for item in evidence),
            "passed": sum(item.playmode.passed for item in evidence),
        },
    }
    trace_counts: Counter[str] = Counter()
    for item in evidence:
        for event in item.trace_events:
            event_name = event.split(":", 1)[0]
            trace_counts[event_name] += 1
    report: dict[str, object] = {
        "schema_version": BENCHMARK_SCHEMA,
        "status": runtime_status,
        "runtime_acceptance": runtime_status,
        "runtime_blocker_codes": sorted(blocker_codes),
        "created_at_utc": datetime.now(UTC).isoformat(timespec="seconds"),
        "unity_version": evidence[0].unity_version if evidence else None,
        "unity_executable_sha256": evidence[0].unity_executable_sha256 if evidence else None,
        "artifact_root": artifact_root.name,
        "case_counts": counts,
        "stable_rejection_codes": rejection_codes,
        "outcomes": outcome_records,
        "unexpected_outcomes": unexpected,
        "tamper_case": {"status": tamper_status, "error": "replay_integrity_error" if tamper_status == "tampered_replay_rejected" else None},
        "accepted_jobs": [
            {
                "fixture": item.fixture,
                "fingerprint": item.fingerprint,
                "project_bytes": item.project_bytes,
                "fresh_ms": round(item.elapsed_ms, 3),
            }
            for item in evidence
        ],
        "latency_ms": {
            "fresh": _latency_summary(fresh_latencies),
            "replay": _latency_summary(replay_latencies),
        },
        "project_bytes": {
            "samples": len(project_sizes),
            "total": total_project_bytes,
            "min": min(project_sizes) if project_sizes else None,
            "max": max(project_sizes) if project_sizes else None,
        },
        "unity_tests": test_totals,
        "gameplay_trace": {
            "events_by_type": dict(sorted(trace_counts.items())),
            "objectives_collected": trace_counts["objective_collected"],
            "accepted_scenes": len(evidence),
        },
        "manual_scene_review": {"status": "pending", "fingerprint": evidence[0].fingerprint if evidence else None},
        "model_calls": 0,
        "monetary_cost": "unknown",
    }
    _write_report(report_path, report)
    return report


__all__ = [
    "BENCHMARK_SCHEMA",
    "EXPECTED_REJECTION_CODES",
    "GameFixtureEvidence",
    "run_benchmark",
    "run_game_fixture",
    "run_rejection_fixture",
    "run_tampered_replay_case",
    "summarize",
]
