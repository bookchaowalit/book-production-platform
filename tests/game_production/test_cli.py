from __future__ import annotations

import json
from pathlib import Path

from book_production_platform.game_production import cli
from book_production_platform.game_production.errors import GameJobError
from book_production_platform.game_production.jobs import GameJobResult


def _game_input(path: Path) -> Path:
    value = {
        "schema_version": "game-ir.v1",
        "scene_id": "cli-room",
        "template": "top-down-collect.v1",
        "palette": "palette-minimal.v1",
        "world": {"width": 8, "height": 6, "tile_size": 1.0},
        "player": {"id": "runner", "spawn": [1, 1], "speed": 4.0},
        "walls": [{"id": "wall-a", "cells": [[3, 1]]}],
        "objectives": [{"id": "shard", "cell": [4, 1]}],
        "exit": {"id": "portal", "cell": [6, 1]},
    }
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def test_build_reports_json_and_model_calls_zero(tmp_path: Path, monkeypatch, capsys) -> None:
    artifact_dir = tmp_path / "artifact"
    expected = GameJobResult("accepted", "a" * 64, artifact_dir, {"scene_sha256": "b" * 64}, False, 0)
    calls: list[tuple[Path, Path, Path, int]] = []

    def fake_compose(game_path: Path, output_root: Path, unity_bin: Path, timeout_s: int) -> GameJobResult:
        calls.append((game_path, output_root, unity_bin, timeout_s))
        return expected

    monkeypatch.setattr(cli, "compose_game_scene", fake_compose)
    game_path = _game_input(tmp_path / "room.json")

    exit_code = cli.game_main(
        ["build", "--game", str(game_path), "--output-root", str(tmp_path / "jobs"), "--unity", "Unity.exe"]
    )

    result = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert result == {
        "status": "accepted",
        "fingerprint": "a" * 64,
        "artifact_dir": str(artifact_dir),
        "artifact_hashes": {"scene_sha256": "b" * 64},
        "replay": False,
        "model_calls": 0,
    }
    assert calls == [(game_path, tmp_path / "jobs", Path("Unity.exe"), 120)]


def test_build_rejection_uses_stable_json_error_code(monkeypatch, capsys) -> None:
    def reject(*args, **kwargs):
        raise GameJobError("unity_unavailable")

    monkeypatch.setattr(cli, "compose_game_scene", reject)

    exit_code = cli.game_main(
        ["build", "--game", "room.json", "--output-root", "jobs", "--unity", "Unity.exe"]
    )

    assert exit_code == 2
    assert json.loads(capsys.readouterr().out) == {"status": "rejected", "error": "unity_unavailable"}


def test_missing_arguments_return_stable_cli_error_json(capsys) -> None:
    exit_code = cli.game_main(["build", "--game", "room.json"])

    assert exit_code == 2
    assert json.loads(capsys.readouterr().out) == {"status": "rejected", "error": "invalid_cli_arguments"}


def test_benchmark_requires_all_inputs(capsys) -> None:
    exit_code = cli.game_main(["benchmark", "--fixtures", "fixtures"])

    assert exit_code == 2
    assert json.loads(capsys.readouterr().out) == {"status": "rejected", "error": "invalid_cli_arguments"}


def test_timeout_over_cap_is_rejected_before_file_or_unity_access(capsys) -> None:
    exit_code = cli.game_main(
        ["build", "--game", "missing.json", "--output-root", "jobs", "--unity", "Missing.exe", "--timeout-s", "121"]
    )

    assert exit_code == 2
    assert json.loads(capsys.readouterr().out) == {"status": "rejected", "error": "invalid_timeout"}


def test_missing_unity_executable_is_a_stable_rejection(tmp_path: Path, capsys) -> None:
    game_path = _game_input(tmp_path / "room.json")
    missing_unity = tmp_path / "MissingUnity.exe"

    exit_code = cli.game_main(
        ["build", "--game", str(game_path), "--output-root", str(tmp_path / "jobs"), "--unity", str(missing_unity)]
    )

    assert exit_code == 2
    assert json.loads(capsys.readouterr().out) == {"status": "rejected", "error": "unity_unavailable"}
