from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from book_production_platform.game_production import jobs, runtime
from book_production_platform.game_production.errors import GameJobError


def game_ir(*, scene_id: str = "amber-room", speed: float = 4.0) -> dict[str, Any]:
    return {
        "schema_version": "game-ir.v1",
        "scene_id": scene_id,
        "template": "top-down-collect.v1",
        "palette": "palette-minimal.v1",
        "world": {"width": 8, "height": 6, "tile_size": 1.0},
        "player": {"id": "runner", "spawn": [1, 1], "speed": speed},
        "walls": [{"id": "wall-a", "cells": [[3, 1]]}],
        "objectives": [
            {"id": "amber-shard", "cell": [4, 1]},
            {"id": "blue-shard", "cell": [5, 3]},
        ],
        "exit": {"id": "portal", "cell": [6, 1]},
    }


def write_game(path: Path, value: dict[str, Any] | None = None) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value or game_ir(), ensure_ascii=False), encoding="utf-8")
    return path


def install_fake_stages(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    fail_stage: str | None = None,
    failure_code: str = "unity_worker_failed",
) -> tuple[Path, list[str]]:
    unity_bin = tmp_path / "Unity.exe"
    unity_bin.write_bytes(b"pinned fake Unity executable")
    calls: list[str] = []

    def fake_stage(
        executable: Path,
        project: Path,
        stage: str,
        deadline: float,
    ) -> runtime.UnityProcessResult:
        del deadline
        calls.append(stage)
        if stage == fail_stage:
            raise GameJobError(failure_code)

        private_root = project / ".book-production"
        log_root = private_root / "logs"
        log_root.mkdir(parents=True, exist_ok=True)
        test_xml: Path | None = None
        if stage == "compile":
            worker_input = json.loads((private_root / "worker-input.json").read_text(encoding="utf-8"))
            scene_path = project / "Assets" / "Scenes" / f"{worker_input['scene_id']}.unity"
            scene_path.parent.mkdir(parents=True, exist_ok=True)
            scene_path.write_bytes(
                b"fake-native-unity-scene\0"
                + json.dumps(worker_input, sort_keys=True, separators=(",", ":")).encode("utf-8")
            )
            scene_path.with_name(scene_path.name + ".meta").write_text(
                "fileFormatVersion: 2\nguid: " + hashlib.sha256(worker_input["scene_id"].encode()).hexdigest()[:32] + "\n",
                encoding="utf-8",
            )
            wall_count = sum(len(wall["cells"]) for wall in worker_input["walls"])
            report = {
                "status": "verified",
                "unity_version": runtime.EXPECTED_UNITY_VERSION,
                "scene_path": f"Assets/Scenes/{worker_input['scene_id']}.unity",
                "player_count": 1,
                "wall_collider_count": wall_count,
                "objective_count": len(worker_input["objectives"]),
                "exit_count": 1,
                "unique_entity_ids": True,
                "external_resource_count": 0,
                "disallowed_component_count": 0,
                "trusted_template_no_network_references": True,
            }
            (private_root / "worker-report.json").write_text(json.dumps(report), encoding="utf-8")
        if stage in {"editmode", "playmode"}:
            test_xml = log_root / f"{stage}.xml"
            test_xml.write_text(
                '<test-run result="Passed" total="1" passed="1" failed="0" skipped="0">'
                '<test-case result="Passed" runstate="Runnable" /></test-run>',
                encoding="utf-8",
            )
        if stage == "playmode":
            worker_input = json.loads((private_root / "worker-input.json").read_text(encoding="utf-8"))
            events = [f"exit_blocked:{worker_input['exit']['id']}", "player_moved"]
            events.extend(
                f"objective_collected:{item['id']}"
                for item in sorted(worker_input["objectives"], key=lambda item: item["id"])
            )
            events.append(f"game_won:{worker_input['exit']['id']}")
            (private_root / "gameplay-trace.json").write_text(
                json.dumps({"events": events}, separators=(",", ":")),
                encoding="utf-8",
            )

        return runtime.UnityProcessResult(
            version=runtime.EXPECTED_UNITY_VERSION,
            executable_sha256=hashlib.sha256(executable.read_bytes()).hexdigest(),
            returncode=0,
            log_bytes=64,
            elapsed_seconds=0.01,
            test_xml=test_xml,
        )

    monkeypatch.setattr(jobs.runtime, "run_unity_stage", fake_stage)
    return unity_bin, calls


def build_job(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    value: dict[str, Any] | None = None,
    output_name: str = "output",
    fail_stage: str | None = None,
    failure_code: str = "unity_worker_failed",
) -> tuple[jobs.GameJobResult, list[str], Path, Path, Path]:
    unity_bin, calls = install_fake_stages(
        monkeypatch,
        tmp_path,
        fail_stage=fail_stage,
        failure_code=failure_code,
    )
    game_path = write_game(tmp_path / "input" / "room.json", value)
    output_root = tmp_path / output_name
    result = jobs.compose_game_scene(game_path, output_root, unity_bin)
    return result, calls, game_path, output_root, unity_bin


def test_first_build_publishes_verified_project_manifest_and_trace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result, calls, game_path, output_root, _ = build_job(tmp_path, monkeypatch)

    assert result.status == "accepted"
    assert result.replay is False
    assert result.model_calls == 0
    assert calls == ["compile", "editmode", "playmode", "reopen"]
    assert result.artifact_dir == output_root / result.fingerprint
    assert set(result.artifact_hashes) == {
        "project_tree_sha256",
        "scene_sha256",
        "gameplay_trace_sha256",
        "qa_manifest_sha256",
    }
    assert {item.name for item in result.artifact_dir.iterdir()} == {
        "project",
        "gameplay-trace.json",
        "qa-manifest.json",
        ".receipt.json",
    }

    project = result.artifact_dir / "project"
    manifest_path = result.artifact_dir / "qa-manifest.json"
    trace_path = result.artifact_dir / "gameplay-trace.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    trace = json.loads(trace_path.read_text(encoding="utf-8"))
    assert manifest["schema_version"] == "game-production.qa.v1"
    assert manifest["status"] == "accepted"
    assert manifest["model_calls"] == 0
    assert manifest["monetary_cost"] == "unknown"
    assert manifest["source_game_ir_sha256"] == hashlib.sha256(game_path.read_bytes()).hexdigest()
    assert all(manifest["checks"].values())
    assert manifest["qa"]["worker_report"]["objective_count"] == 2
    assert trace["events"][-1] == "game_won:portal"
    assert not any(str(tmp_path) in json.dumps(item) for item in (manifest, trace))
    assert (project / "Assets" / "BookProduction" / "Source" / "game-ir.json").is_file()
    assert (project / "Assets" / "BookProduction" / "Source" / "scene-composer-template.unity").is_file()
    assert (project / "Assets" / "Scenes" / "amber-room.unity").is_file()
    assert not (project / ".book-production").exists()
    assert not (project / "Library").exists()
    assert not (project / "Temp").exists()
    assert jobs.receipt_exists(result.artifact_dir)
    for name, digest in result.artifact_hashes.items():
        if name == "project_tree_sha256":
            assert jobs.project_tree_sha256(project) == digest
        elif name == "scene_sha256":
            assert hashlib.sha256((project / "Assets/Scenes/amber-room.unity").read_bytes()).hexdigest() == digest
        elif name == "gameplay_trace_sha256":
            assert hashlib.sha256(trace_path.read_bytes()).hexdigest() == digest
        else:
            assert hashlib.sha256(manifest_path.read_bytes()).hexdigest() == digest


def test_exact_replay_verifies_receipt_without_running_unity_again(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first, calls, game_path, output_root, unity_bin = build_job(tmp_path, monkeypatch)

    second = jobs.compose_game_scene(game_path, output_root, unity_bin)

    assert second.status == "replayed"
    assert second.replay is True
    assert second.fingerprint == first.fingerprint
    assert second.artifact_hashes == first.artifact_hashes
    assert calls == ["compile", "editmode", "playmode", "reopen"]


def test_semantically_changed_source_creates_a_new_fingerprint(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first, calls, game_path, output_root, unity_bin = build_job(tmp_path, monkeypatch)
    changed = game_ir(speed=6.0)
    write_game(game_path, changed)

    second = jobs.compose_game_scene(game_path, output_root, unity_bin)

    assert first.fingerprint != second.fingerprint
    assert second.replay is False
    assert calls == ["compile", "editmode", "playmode", "reopen"] * 2


def test_template_change_creates_a_new_fingerprint(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    template = tmp_path / "template"
    template.mkdir()
    marker = template / "template-marker.txt"
    marker.write_text("version one", encoding="utf-8")
    monkeypatch.setattr(jobs, "TEMPLATE_PROJECT", template)
    parsed_game = jobs.parse_game_ir(json.dumps(game_ir()).encode("utf-8"))

    first = jobs.game_fingerprint(parsed_game, "a" * 64)
    marker.write_text("version two", encoding="utf-8")
    second = jobs.game_fingerprint(parsed_game, "a" * 64)

    assert first != second


@pytest.mark.parametrize("tamper", ["scene", "trace", "manifest", "receipt", "extra-file"])
def test_corrupt_replay_artifacts_fail_closed_without_overwrite(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    tamper: str,
) -> None:
    first, calls, game_path, output_root, unity_bin = build_job(tmp_path, monkeypatch)
    if tamper == "scene":
        target = first.artifact_dir / "project/Assets/Scenes/amber-room.unity"
        target.write_bytes(b"changed")
    elif tamper == "trace":
        target = first.artifact_dir / "gameplay-trace.json"
        target.write_text('{"events":[]}', encoding="utf-8")
    elif tamper == "manifest":
        target = first.artifact_dir / "qa-manifest.json"
        target.write_bytes(target.read_bytes() + b" ")
    elif tamper == "receipt":
        target = first.artifact_dir / ".receipt.json"
        target.write_text('{"fingerprint":"wrong"}', encoding="utf-8")
    else:
        target = first.artifact_dir / "unexpected.txt"
        target.write_text("unexpected", encoding="utf-8")
    original_bytes = target.read_bytes()

    with pytest.raises(GameJobError) as error:
        jobs.compose_game_scene(game_path, output_root, unity_bin)

    assert error.value.code == "replay_integrity_error"
    assert target.read_bytes() == original_bytes
    assert calls == ["compile", "editmode", "playmode", "reopen"]


def test_invalid_source_is_rejected_before_unity_launch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    unity_bin, calls = install_fake_stages(monkeypatch, tmp_path)
    game_path = tmp_path / "input.json"
    game_path.write_text('{"schema_version":"game-ir.v1","unexpected":true}', encoding="utf-8")

    with pytest.raises(ValueError):
        jobs.compose_game_scene(game_path, tmp_path / "output", unity_bin)

    assert calls == []


def test_output_root_symlink_is_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    unity_bin, calls = install_fake_stages(monkeypatch, tmp_path)
    game_path = write_game(tmp_path / "input.json")
    target = tmp_path / "target"
    target.mkdir()
    link = tmp_path / "output-link"
    try:
        link.symlink_to(target, target_is_directory=True)
    except OSError as error:
        pytest.skip(f"symlink creation is unavailable: {error}")

    with pytest.raises(GameJobError) as error:
        jobs.compose_game_scene(game_path, link, unity_bin)

    assert error.value.code == "unsafe_output_path"
    assert calls == []


def test_staging_cap_rejects_before_unity_and_leaves_no_accepted_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    unity_bin, calls = install_fake_stages(monkeypatch, tmp_path)
    game_path = write_game(tmp_path / "input.json")
    output_root = tmp_path / "output"
    monkeypatch.setattr(jobs, "MAX_STAGING_BYTES", 1)

    with pytest.raises(GameJobError) as error:
        jobs.compose_game_scene(game_path, output_root, unity_bin)

    assert error.value.code == "resource_cap_exceeded"
    assert calls == []
    assert list((output_root / ".staging").iterdir()) == []
    assert not list(output_root.glob("[0-9a-f]*"))


def test_published_project_cap_rejects_after_qa_without_publishing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    unity_bin, calls = install_fake_stages(monkeypatch, tmp_path)
    game_path = write_game(tmp_path / "input.json")
    output_root = tmp_path / "output"
    monkeypatch.setattr(jobs, "MAX_PROJECT_BYTES", 1)

    with pytest.raises(GameJobError) as error:
        jobs.compose_game_scene(game_path, output_root, unity_bin)

    assert error.value.code == "resource_cap_exceeded"
    assert calls == ["compile", "editmode", "playmode", "reopen"]
    assert list((output_root / ".staging").iterdir()) == []
    assert not list(output_root.glob("[0-9a-f]*"))


@pytest.mark.parametrize(
    ("stage", "code"),
    [("editmode", "unity_test_failed"), ("playmode", "deadline_exceeded")],
)
def test_failed_stage_cleans_only_its_staging_and_publishes_nothing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    stage: str,
    code: str,
) -> None:
    unity_bin, calls = install_fake_stages(monkeypatch, tmp_path, fail_stage=stage, failure_code=code)
    game_path = write_game(tmp_path / "input.json")
    output_root = tmp_path / "output"

    with pytest.raises(GameJobError) as error:
        jobs.compose_game_scene(game_path, output_root, unity_bin)

    assert error.value.code == code
    assert not list((output_root / ".staging").iterdir())
    assert not list(output_root.glob("[0-9a-f]*"))
    assert calls[-1] == stage


def test_staging_cleanup_leaves_sibling_job_untouched(tmp_path: Path) -> None:
    root = tmp_path / "output"
    root.mkdir()
    current = jobs.create_job_staging(root, "a" * 64)
    sibling = jobs.create_job_staging(root, "b" * 64)
    (current / "partial.txt").write_text("partial", encoding="utf-8")
    (sibling / "partial.txt").write_text("keep", encoding="utf-8")

    jobs.remove_only_job_staging(current)

    assert not current.exists()
    assert (sibling / "partial.txt").read_text(encoding="utf-8") == "keep"


def test_atomic_publish_refuses_to_replace_an_existing_destination(tmp_path: Path) -> None:
    root = tmp_path / "output"
    root.mkdir()
    staging = jobs.create_job_staging(root, "c" * 64)
    destination = root / ("c" * 64)
    destination.mkdir()
    (destination / "keep.txt").write_text("keep", encoding="utf-8")

    with pytest.raises(GameJobError) as error:
        jobs.atomic_publish(staging, destination)

    assert error.value.code == "publication_conflict"
    assert (destination / "keep.txt").read_text(encoding="utf-8") == "keep"
    jobs.remove_only_job_staging(staging)
