from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

import pytest

from book_production_platform.game_production import benchmark, jobs
from book_production_platform.game_production.errors import GameJobError

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "game_production"
ACCEPTED_NAMES = {
    "minimal-room.json",
    "multi-wall-route.json",
    "two-objectives.json",
    "near-wall-movement.json",
    "larger-valid-room.json",
}


def install_fake_job_runner(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str]]:
    calls: list[tuple[str, str]] = []

    def fake_compose(game_path: Path, output_root: Path, unity_bin: Path) -> jobs.GameJobResult:
        del unity_bin
        if game_path.name not in ACCEPTED_NAMES:
            return jobs.compose_game_scene(game_path, output_root, Path("unused-unity.exe"))

        source = game_path.read_bytes()
        value = json.loads(source)
        fingerprint = hashlib.sha256(source).hexdigest()
        artifact_dir = output_root / fingerprint
        project = artifact_dir / "project"
        scene = project / "Assets" / "Scenes" / f"{value['scene_id']}.unity"
        trace_path = artifact_dir / "gameplay-trace.json"
        manifest_path = artifact_dir / "qa-manifest.json"
        receipt_path = artifact_dir / ".receipt.json"

        if artifact_dir.exists():
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
            scene_hash = hashlib.sha256(scene.read_bytes()).hexdigest()
            if receipt["scene_sha256"] != scene_hash:
                raise GameJobError("replay_integrity_error")
            manifest_hash = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
            hashes = {"scene_sha256": scene_hash, "qa_manifest_sha256": manifest_hash}
            calls.append((game_path.name, "replayed"))
            return jobs.GameJobResult("replayed", fingerprint, artifact_dir, hashes, True, 0)

        scene.parent.mkdir(parents=True)
        scene_bytes = f"native scene for {value['scene_id']}".encode()
        scene.write_bytes(scene_bytes)
        objectives = sorted(value["objectives"], key=lambda item: item["id"])
        events = [f"exit_blocked:{value['exit']['id']}", "player_moved"]
        events.extend(f"objective_collected:{item['id']}" for item in objectives)
        events.append(f"game_won:{value['exit']['id']}")
        trace = {"events": events}
        trace_bytes = json.dumps(trace, separators=(",", ":")).encode("utf-8")
        artifact_dir.mkdir(exist_ok=True)
        trace_path.write_bytes(trace_bytes)
        project_bytes = scene.stat().st_size
        manifest = {
            "schema_version": "game-production.qa.v1",
            "status": "accepted",
            "fingerprint": fingerprint,
            "unity_version": "6000.6.2f1",
            "unity_executable_sha256": "a" * 64,
            "outputs": {"scene": {"sha256": hashlib.sha256(scene_bytes).hexdigest()}},
            "qa": {
                "editmode_tests": {"total": 2, "passed": 2},
                "playmode_tests": {"total": 1, "passed": 1},
                "gameplay_events": events,
            },
            "resource_usage": {"project_bytes": project_bytes},
            "model_calls": 0,
        }
        manifest_bytes = json.dumps(manifest, sort_keys=True).encode("utf-8")
        manifest_path.write_bytes(manifest_bytes)
        scene_hash = hashlib.sha256(scene_bytes).hexdigest()
        manifest_hash = hashlib.sha256(manifest_bytes).hexdigest()
        receipt_path.write_text(json.dumps({"scene_sha256": scene_hash}), encoding="utf-8")
        hashes = {"scene_sha256": scene_hash, "qa_manifest_sha256": manifest_hash}
        calls.append((game_path.name, "accepted"))
        return jobs.GameJobResult("accepted", fingerprint, artifact_dir, hashes, False, 0)

    monkeypatch.setattr(benchmark, "compose_game_scene", fake_compose)
    return calls


def test_benchmark_requires_exact_case_counts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls = install_fake_job_runner(monkeypatch)
    report_path = tmp_path / "benchmark.json"

    report = benchmark.run_benchmark(FIXTURES, report_path, tmp_path / "unused-unity.exe")

    counts = report["case_counts"]
    assert counts == {
        "accepted": 5,
        "replayed": 5,
        "rejected": 9,
        "tampered_replay_rejections": 1,
        "unexpected_outcomes": 0,
        "rejected_total": 10,
    }
    assert report["status"] == "passed"
    assert report["runtime_acceptance"] == "passed"
    assert report["model_calls"] == 0
    assert report["artifact_root"].startswith(".b-")
    assert len(report["artifact_root"]) <= 11
    assert report["stable_rejection_codes"] == benchmark.EXPECTED_REJECTION_CODES
    assert report["latency_ms"]["fresh"]["samples"] == 5
    assert report["latency_ms"]["replay"]["samples"] == 5
    assert report["project_bytes"]["samples"] == 5
    assert report["unity_tests"]["editmode"] == {"total": 10, "passed": 10}
    assert report["unity_tests"]["playmode"] == {"total": 5, "passed": 5}
    assert report["gameplay_trace"]["objectives_collected"] == 9
    assert json.loads(report_path.read_text(encoding="utf-8")) == report
    assert str(tmp_path) not in json.dumps(report)
    assert len(calls) == 10
    assert calls.count(("minimal-room.json", "accepted")) == 1
    assert calls.count(("minimal-room.json", "replayed")) == 1


def test_benchmark_rejects_incomplete_fixture_sets(tmp_path: Path) -> None:
    fixture_copy = tmp_path / "fixtures"
    shutil.copytree(FIXTURES, fixture_copy)
    (fixture_copy / "accepted" / "larger-valid-room.json").unlink()

    with pytest.raises(GameJobError) as error:
        benchmark.run_benchmark(fixture_copy, tmp_path / "benchmark.json", tmp_path / "Unity.exe")

    assert error.value.code == "benchmark_fixture_error"
    assert not (tmp_path / "benchmark.json").exists()


def test_oversized_accepted_fixture_is_rejected_before_job_runner(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = tmp_path / "minimal-room.json"
    fixture.write_bytes(b" " * (benchmark.MAX_GAME_IR_BYTES * 4))

    def unexpected_job(*args, **kwargs):
        pytest.fail("oversized fixture reached the Unity job runner")

    monkeypatch.setattr(benchmark, "compose_game_scene", unexpected_job)

    with pytest.raises(GameJobError) as error:
        benchmark.run_game_fixture(fixture, tmp_path / "output", tmp_path / "Unity.exe")

    assert error.value.code == "benchmark_fixture_error"
    assert str(error.value) == "Benchmark fixture exceeds size cap"


def test_oversized_rejection_descriptor_is_rejected_before_job_runner(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = tmp_path / "duplicate-id.json"
    descriptor = {"benchmark_case": "wall-cell-cap", "count": 193, "padding": "x" * (benchmark.MAX_GAME_IR_BYTES * 4)}
    fixture.write_text(json.dumps(descriptor), encoding="utf-8")

    def unexpected_job(*args, **kwargs):
        pytest.fail("oversized descriptor reached the Unity job runner")

    monkeypatch.setattr(benchmark, "compose_game_scene", unexpected_job)

    with pytest.raises(GameJobError) as error:
        benchmark.run_rejection_fixture(fixture, tmp_path / "output", tmp_path / "Unity.exe")

    assert error.value.code == "benchmark_fixture_error"
    assert str(error.value) == "Benchmark fixture exceeds size cap"


def test_unavailable_unity_is_reported_as_blocked_not_passed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_runtime(game_path: Path, output_root: Path, unity_bin: Path) -> jobs.GameJobResult:
        if game_path.name in ACCEPTED_NAMES:
            raise GameJobError("unity_unavailable")
        return jobs.compose_game_scene(game_path, output_root, unity_bin)

    monkeypatch.setattr(benchmark, "compose_game_scene", fail_runtime)

    report = benchmark.run_benchmark(FIXTURES, tmp_path / "benchmark.json", tmp_path / "MissingUnity.exe")

    assert report["status"] == "blocked"
    assert report["runtime_acceptance"] == "blocked"
    assert report["runtime_blocker_codes"] == ["unity_unavailable"]
    assert report["case_counts"]["accepted"] == 0
    assert report["case_counts"]["rejected"] == 9
    assert report["model_calls"] == 0


def test_summarize_counts_only_recognized_outcomes() -> None:
    assert benchmark.summarize(
        ["accepted", "replayed", "rejected", "tampered_replay_rejected", "unexpected"]
    ) == {
        "accepted": 1,
        "replayed": 1,
        "rejected": 1,
        "tampered_replay_rejections": 1,
        "unexpected_outcomes": 1,
    }
