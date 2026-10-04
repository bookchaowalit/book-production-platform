"""Trace, history, Unity manual guide, level metrics, and level map for game jobs.

Unity is not installed here: compose runs through the fake stages from
``test_jobs`` and export through a fake player build. Every Unity Editor
step in the guide is checked against GameIR only (runtime-unverified).
"""

from __future__ import annotations

import itertools
import json
import random
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from book_production_platform.game_ir import (
    GameInputError,
    parse_game_ir,
    validate_game_ir,
)
from book_production_platform.game_production import export, jobs
from book_production_platform.game_production.brief_cli import _write_proposal_sidecar
from book_production_platform.game_production.errors import GameJobError
from book_production_platform.game_production.level_map import (
    ascii_map,
    render_level_svg,
)
from book_production_platform.game_production.manual import (
    build_unity_guide,
    num,
    world_position,
)
from book_production_platform.game_production.metrics import (
    EXACT_ROUTE_LIMIT,
    level_metrics,
)
from book_production_platform.production_trace import (
    read_history,
    render_manual_markdown,
    sha256_json,
    verify_trace,
)
from tests.game_production.test_jobs import (
    build_job,
    game_ir,
    install_fake_stages,
    write_game,
)

CANARY = "CANARY-studio-secret-55d1"


def _ir(value: dict[str, Any] | None = None):
    return validate_game_ir(parse_game_ir(json.dumps(value or game_ir()).encode("utf-8")))


def _trace(root: Path, job_id: str) -> dict[str, Any]:
    return json.loads((root / ".trace" / "jobs" / job_id / "trace.json").read_text(encoding="utf-8"))


def test_accepted_compose_records_trace_manual_map_and_verifies(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    result, _, _, output_root, _ = build_job(tmp_path, monkeypatch)

    assert result.status == "accepted" and result.trace_job_id is not None
    job_dir = output_root / ".trace" / "jobs" / result.trace_job_id
    assert {item.name for item in job_dir.iterdir()} == {"trace.json", "MANUAL-STEPS.md", "manual-facts.json", "ir.json", "level-map.svg", "level-metrics.json"}
    trace = _trace(output_root, result.trace_job_id)
    assert trace["tool_id"] == "game-v1a" and trace["status"] == "accepted"
    assert [step["stage"] for step in trace["steps"]] == [
        "validate", "assets", "runtime", "fingerprint", "stage", "unity", "qa", "publish", "analyze",
    ]
    unity = next(step for step in trace["steps"] if step["stage"] == "unity")
    assert unity["params"]["stages"] == ["compile", "editmode", "playmode", "reopen"]
    assert unity["params"]["playmode"] == {"total": 1, "passed": 1}
    canonical = json.loads(jobs.canonical_game_ir_bytes(_ir()))
    assert json.loads((job_dir / "ir.json").read_text(encoding="utf-8")) == canonical
    assert trace["ir"]["sha256"] == sha256_json(canonical)
    names = {Path(item["file"]).name for item in trace["artifacts"]}
    assert names == {"ir.json", "amber-room.unity", "gameplay-trace.json", "qa-manifest.json", ".receipt.json", "level-metrics.json", "level-map.svg"}
    [entry] = read_history(output_root / ".trace")
    assert entry["status"] == "accepted"
    # The content-addressed artifact directory keeps exactly its verified entries.
    assert {item.name for item in result.artifact_dir.iterdir()} == jobs.ARTIFACT_ROOT_ENTRIES

    assert verify_trace(output_root / ".trace", result.trace_job_id)["ok"] is True
    (result.artifact_dir / "gameplay-trace.json").write_text("{}", encoding="utf-8")
    assert verify_trace(output_root / ".trace", result.trace_job_id)["ok"] is False


def test_replay_is_traced(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    first, _, game_path, output_root, unity_bin = build_job(tmp_path, monkeypatch)
    replay = jobs.compose_game_scene(game_path, output_root, unity_bin)
    assert replay.replay is True and replay.trace_job_id not in {None, first.trace_job_id}
    assert _trace(output_root, str(replay.trace_job_id))["status"] == "replayed"
    assert [item["status"] for item in read_history(output_root / ".trace")] == ["accepted", "replayed"]


def test_unity_failure_is_traced_and_reraised(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(GameJobError) as error:
        build_job(tmp_path, monkeypatch, fail_stage="playmode", failure_code="unity_tests_failed")
    assert error.value.code == "unity_tests_failed"
    output_root = tmp_path / "output"
    [entry] = read_history(output_root / ".trace")
    trace = _trace(output_root, entry["job_id"])
    assert trace["status"] == "failed"
    assert next(step for step in trace["steps"] if step["stage"] == "unity")["status"] == "failed"
    assert trace["steps"][-2]["note"] == "unity_tests_failed"
    assert (output_root / ".trace" / "jobs" / entry["job_id"] / "MANUAL-STEPS.md").is_file()
    assert not [item for item in output_root.iterdir() if not item.name.startswith(".")]


def test_invalid_game_ir_writes_no_trace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    unity_bin, _ = install_fake_stages(monkeypatch, tmp_path)
    bad = game_ir()
    bad["exit"]["cell"] = [3, 1]  # on a wall
    path = write_game(tmp_path / "input" / "bad.json", bad)
    with pytest.raises(GameInputError):
        jobs.compose_game_scene(path, tmp_path / "output", unity_bin)
    assert not (tmp_path / "output" / ".trace").exists()


def test_trace_and_history_hold_no_private_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    unity_bin, _ = install_fake_stages(monkeypatch, tmp_path)
    path = write_game(tmp_path / CANARY / f"{CANARY}.json")
    output_root = tmp_path / "output"
    result = jobs.compose_game_scene(path, output_root, unity_bin)
    for file in (output_root / ".trace" / "history.jsonl", output_root / ".trace" / "jobs" / str(result.trace_job_id) / "trace.json"):
        text = file.read_text(encoding="utf-8")
        assert CANARY not in text and str(tmp_path) not in text


def test_ai_proposal_sidecar_is_recorded_only_when_it_matches(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    unity_bin, _ = install_fake_stages(monkeypatch, tmp_path)
    path = write_game(tmp_path / "input" / "room.json")
    _write_proposal_sidecar(path, SimpleNamespace(game_ir=game_ir(), adapter="provider-gateway", adapter_version="game-brief-adapter.v1", model_calls=2), mode="live")
    output_root = tmp_path / "output"
    result = jobs.compose_game_scene(path, output_root, unity_bin)
    assert _trace(output_root, str(result.trace_job_id))["ai"]["model_calls"] == 2

    write_game(path, game_ir(speed=6.0))
    edited = jobs.compose_game_scene(path, output_root, unity_bin)
    trace = _trace(output_root, str(edited.trace_job_id))
    assert trace["ai"]["used"] is False
    assert any(step["operation"] == "proposal_edited_by_human" for step in trace["steps"])


def _fake_export(executable: Path, work: Path, target: str, deadline: float) -> tuple[str, float]:
    del executable, deadline
    request = json.loads((work / ".book-production" / "export-input.json").read_text(encoding="utf-8"))
    player = Path(request["output_dir"])
    (player / "Build").mkdir(parents=True, exist_ok=True)
    (player / "index.html").write_text("<html></html>", encoding="utf-8")
    for name in ("player.loader.js", "player.data", "player.framework.js", "player.wasm"):
        (player / "Build" / name).write_bytes(name.encode())
    (work / ".book-production" / "export-report.json").write_text(
        json.dumps({"result": "Succeeded", "total_errors": 0, "total_warnings": 0, "scenes": ["amber-room"]}), encoding="utf-8"
    )
    assert target == "web"
    return "6000.6.2f1", 0.5


def test_export_is_traced_with_build_profile_guide(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    result, _, _, _, unity_bin = build_job(tmp_path, monkeypatch)
    monkeypatch.setattr(export, "_run_unity_export", _fake_export)
    exports = tmp_path / "exports"

    exported = export.export_game_player(result.artifact_dir, "web", exports, unity_bin)

    assert exported.status == "accepted" and exported.trace_job_id is not None
    trace = _trace(exports, exported.trace_job_id)
    assert trace["tool_id"] == "game-v1a-export"
    assert [step["stage"] for step in trace["steps"]] == ["validate", "unity", "publish"]
    assert {Path(item["file"]).name for item in trace["artifacts"]} == {"ir.json", "export-manifest.json", ".receipt.json", "index.html"}
    manual = (exports / ".trace" / "jobs" / exported.trace_job_id / "MANUAL-STEPS.md").read_text(encoding="utf-8")
    assert "File ▸ Build Profiles" in manual and "WebGL" in manual and "amber-room" in manual
    assert verify_trace(exports / ".trace", exported.trace_job_id)["ok"] is True


def test_guide_places_every_entity_at_its_exact_cell_and_position() -> None:
    ir = _ir(game_ir())
    guide = build_unity_guide(ir)
    text = render_manual_markdown(guide)

    assert guide.program == "Unity Editor" and "6000.6.2f1" in guide.program_version
    cells = [("player", ir.player.spawn)] + [(f"wall:{w.id}", c) for w in ir.walls for c in w.cells]
    cells += [(o.id, o.cell) for o in ir.objectives] + [(ir.exit.id, ir.exit.cell)]
    for _, cell in cells:
        x, y = world_position(cell, ir)
        assert f"cell ({cell[0]}, {cell[1]})" in text
        assert f"X {num(x)}, Y {num(y)}, Z 0" in text
    assert world_position((1, 1), ir) == (-2.5, -1.5)
    values = [step.value for step in guide.steps]
    assert "Move Speed = 4" in values
    assert "4.8" in values  # camera size = max(8, 6) * 1 * 0.6
    assert any("Cell Size X 1, Y 1" in value for value in values)
    assert "Total Objectives" in text and "Is Trigger = on" in text
    route = level_metrics(ir).route_order
    collected = [value for value in values if value.startswith("event objective_collected:")]
    assert collected == [f"event objective_collected:{item}" for item in route]
    assert values[-1] == "event game_won:portal"
    layer_names = [layer.name for layer in guide.layers]
    assert "Player-runner" in layer_names and "Wall-wall-a-3-1" in layer_names and "Exit-portal" in layer_names
    assert guide.estimated_minutes is not None and guide.estimated_minutes > 0


def test_guide_handles_fractional_tiles_and_audio() -> None:
    value = game_ir()
    value["world"]["tile_size"] = 0.5
    ir = _ir(value)
    guide = build_unity_guide(ir)
    assert world_position(ir.player.spawn, ir) == (-1.25, -0.75)
    assert any("X -1.25, Y -0.75, Z 0" in step.value for step in guide.steps)


def test_route_is_optimal_against_brute_force() -> None:
    rng = random.Random(7)
    for _ in range(25):
        width, height = 7, 6
        cells = [(x, y) for x in range(width) for y in range(height)]
        rng.shuffle(cells)
        spawn, exit_cell, *rest = cells
        objectives = rest[:4]
        walls = [cell for cell in rest[4:14]]
        value = {
            **game_ir(),
            "world": {"width": width, "height": height, "tile_size": 1.0},
            "player": {"id": "runner", "spawn": list(spawn), "speed": 4.0},
            "walls": [{"id": "w", "cells": [list(c) for c in walls]}] if walls else [],
            "objectives": [{"id": f"o{i}", "cell": list(c)} for i, c in enumerate(objectives)],
            "exit": {"id": "portal", "cell": list(exit_cell)},
        }
        try:
            ir = _ir(value)
        except GameInputError:  # random layout left a goal unreachable
            continue
        metrics = level_metrics(ir)
        from book_production_platform.game_production.metrics import _bfs, _path

        wall_set = frozenset(walls)
        nodes = [ir.player.spawn, *(o.cell for o in ir.objectives), ir.exit.cell]
        dist = [[len(_path(_bfs(a, width, height, wall_set), b)) - 1 for b in nodes] for a in nodes]
        best = min(
            dist[0][p[0]] + sum(dist[a][b] for a, b in itertools.pairwise(p)) + dist[p[-1]][len(nodes) - 1]
            for p in itertools.permutations(range(1, len(nodes) - 1))
        )
        assert metrics.route_steps == best and metrics.route_method == "exact"
        assert len(metrics.route_cells) == best + 1
        assert all(abs(a[0] - b[0]) + abs(a[1] - b[1]) == 1 for a, b in itertools.pairwise(metrics.route_cells))
        assert not set(metrics.route_cells) & wall_set


def test_metrics_values_and_heuristic_fallback() -> None:
    metrics = level_metrics(_ir())
    # Both orders cost 11 grid steps (amber first: 5 + 3 + 3; blue first: 6 + 3 + 2);
    # ties resolve deterministically to the lower (cost, last-objective index).
    assert metrics.route_order == ("blue-shard", "amber-shard")
    assert metrics.route_steps == 11
    assert metrics.estimated_seconds == 2.75
    assert metrics.wall_cells == 1 and metrics.walkable_cells == 47 and metrics.reachable_cells == 47
    assert metrics.difficulty_band in {"easy", "medium", "hard"}
    assert metrics.to_dict() == level_metrics(_ir()).to_dict()

    many = game_ir()
    many["world"] = {"width": 16, "height": 3, "tile_size": 1.0}
    many["walls"] = []
    many["objectives"] = [{"id": f"o{i:02d}", "cell": [i + 1, 2]} for i in range(EXACT_ROUTE_LIMIT + 1)]
    many["exit"] = {"id": "portal", "cell": [15, 0]}
    big = level_metrics(_ir(many))
    assert big.route_method == "heuristic" and big.objective_count == EXACT_ROUTE_LIMIT + 1


def test_level_map_is_deterministic_and_marks_every_entity() -> None:
    ir = _ir()
    svg = render_level_svg(ir).decode("utf-8")
    assert svg == render_level_svg(ir).decode("utf-8")
    assert 'data-player="runner" data-cell="1,1"' in svg
    assert 'data-wall="wall-a" data-cell="3,1"' in svg
    assert 'data-objective="amber-shard" data-cell="4,1"' in svg
    assert 'data-exit="portal" data-cell="6,1"' in svg
    rows = ascii_map(ir).splitlines()
    assert len(rows) == ir.world.height + 2
    assert rows[-2] == "  0 ........" and rows[-3] == "  1 .P.#o.E."
