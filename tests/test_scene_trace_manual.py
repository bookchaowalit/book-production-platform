"""Scene3D job history, step trace, Blender manual guide, new presets, and turntable spec.

Blender itself is replaced by the fake workers of ``test_scene_jobs`` (Blender
is not installed in CI), so the new lighting/background/orbit presets are
verified at SceneIR, builder-allowlist, trace, and guide level only; whether
Blender renders them as intended is runtime-dependent.
"""

from __future__ import annotations

import json
import math
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
from typing import Any

import pytest

from book_production_platform.brief_adapter_kit.testing import ScriptedTransport
from book_production_platform.production_trace import (
    read_history,
    sha256_json,
    verify_trace,
)
from book_production_platform.scene3d import brief_cli, runtime
from book_production_platform.scene3d.errors import SceneInputError
from book_production_platform.scene3d.manual import (
    aim_rotation,
    build_blender_guide,
    fit_distance_factor,
    track_rotation_degrees,
)
from book_production_platform.scene3d.models import parse_scene_ir
from book_production_platform.scene3d.scene_builder import (
    _CAMERA_OFFSET,
    _LEGACY_TARGET,
    BACKGROUND_PRESETS,
    EEVEE_SAMPLES,
    FIT_DIRECTIONS,
    LIGHT_TARGET,
    LIGHTING_PRESETS,
    ORBIT_COMPOSITIONS,
    _load_validated_scene_ir,
)
from book_production_platform.scene3d.turntable import turntable_scenes, write_turntable
from tests.helpers.glb_factory import TRIANGLE_BINARY, triangle_document, write_glb
from tests.test_scene3d_brief_adapter import BRIEF, GOOD, adapter
from tests.test_scene_ir import valid_scene
from tests.test_scene_jobs import create_inputs, install_fake_workers


def _values(scene: dict[str, Any]) -> list[str]:
    return [step.value for step in build_blender_guide(parse_scene_ir(scene)).steps]


def test_accepted_job_is_traced_outside_the_replay_folder(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    scene_path, input_root, output_root, blender_bin = create_inputs(tmp_path)
    install_fake_workers(monkeypatch)

    result = runtime.compose_scene(scene_path, input_root, output_root, blender_bin)

    assert {item.name for item in result.artifact_dir.iterdir()} == {"scene.blend", "render.png", "qa-manifest.json"}
    history = read_history(output_root)
    assert len(history) == 1 and history[0]["status"] == "accepted"
    trace_path = output_root / history[0]["trace"]
    assert trace_path.parent.parent.name == "traces"
    trace = json.loads(trace_path.read_text(encoding="utf-8"))
    assert trace["tool_id"] == "scene3d-v1a"
    assert [step["operation"] for step in trace["steps"]] == [
        "parse_scene_ir", "glb_preflight", "blender_identity", "blender_build_scene", "blender_reopen_render", "verify_png", "publish_job",
    ]
    build = next(step for step in trace["steps"] if step["operation"] == "blender_build_scene")
    assert build["params"]["lights"][0] == {"name": "Key Softbox", "location": [-3.5, -4.0, 5.0], "energy_w": 720.0, "size_m": 4.0, "color": [1.0, 0.86, 0.72]}
    assert {artifact["file"] for artifact in trace["artifacts"]} == {f"../../{result.fingerprint}/{name}" for name in ("scene.blend", "render.png", "qa-manifest.json")}
    ir_document = json.loads((trace_path.parent / "ir.json").read_text(encoding="utf-8"))
    assert trace["ir"]["sha256"] == sha256_json(ir_document) and ir_document == valid_scene()
    assert trace["manual_guide"]["program"] == "Blender"
    assert (trace_path.parent / "MANUAL-STEPS.md").is_file()
    assert trace["ai"]["used"] is False


def test_verify_passes_then_fails_after_tamper_and_replay_writes_no_trace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    scene_path, input_root, output_root, blender_bin = create_inputs(tmp_path)
    install_fake_workers(monkeypatch)
    result = runtime.compose_scene(scene_path, input_root, output_root, blender_bin)
    replay = runtime.compose_scene(scene_path, input_root, output_root, blender_bin)
    assert replay.replay is True and len(read_history(output_root)) == 1
    job_id = read_history(output_root)[0]["job_id"]
    assert verify_trace(output_root, job_id)["ok"] is True

    (result.artifact_dir / "render.png").write_bytes(b"tampered")

    report = verify_trace(output_root, job_id)
    assert report["ok"] is False
    assert [item["file"] for item in report["artifacts"] if not item["ok"]] == [f"../../{result.fingerprint}/render.png"]


def test_failed_job_is_traced_with_the_failing_step(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import subprocess

    scene_path, input_root, output_root, blender_bin = create_inputs(tmp_path)
    monkeypatch.setattr(runtime, "_blender_identity", lambda *_: ("Blender 5.2.2 LTS", "d13f752e3b9c"))

    def time_out(command: list[str], *, cwd: Path, timeout_s: int) -> float:
        raise subprocess.TimeoutExpired(command, timeout_s)

    monkeypatch.setattr(runtime, "_run_worker", time_out)
    with pytest.raises(SceneInputError):
        runtime.compose_scene(scene_path, input_root, output_root, blender_bin, timeout_s=2)

    entry = read_history(output_root)[0]
    trace = json.loads((output_root / entry["trace"]).read_text(encoding="utf-8"))
    assert entry["status"] == "failed"
    build = next(step for step in trace["steps"] if step["operation"] == "blender_build_scene")
    assert build["status"] == "failed"
    assert trace["steps"][-1]["note"] == "deadline_exceeded"
    assert trace["artifacts"] == []


def test_symlinked_output_root_is_not_traced(tmp_path: Path) -> None:
    scene_path, input_root, _, blender_bin = create_inputs(tmp_path)
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    try:
        link.symlink_to(real, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("host does not permit symlink creation")
    with pytest.raises(SceneInputError):
        runtime.compose_scene(scene_path, input_root, link, blender_bin)
    assert list(real.iterdir()) == []


def test_ai_proposal_sidecar_is_traced_without_brief_text(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(brief_cli, "_provider_adapter", lambda path, root: adapter(tmp_path, ScriptedTransport(GOOD)))
    input_root = tmp_path / "input"
    (input_root / "products").mkdir(parents=True)
    write_glb(input_root / "products" / "speaker.glb", triangle_document(), TRIANGLE_BINARY)
    brief_path = tmp_path / "brief.json"
    brief_path.write_text(json.dumps({"brief": BRIEF}), encoding="utf-8")
    scene_path = input_root / "scene.json"
    with redirect_stdout(StringIO()), redirect_stderr(StringIO()):
        assert brief_cli.main(["brief", "--input", str(brief_path), "--output", str(scene_path), "--job-id", "trace-live"]) == 0
    sidecar = json.loads(runtime.proposal_path(scene_path).read_text(encoding="utf-8"))
    assert sidecar["model_calls"] >= 1 and "CANARY" not in json.dumps(sidecar)
    install_fake_workers(monkeypatch)
    blender_bin = tmp_path / "blender.exe"
    blender_bin.write_bytes(b"marker")

    runtime.compose_scene(scene_path, input_root, tmp_path / "out", blender_bin)

    entry = read_history(tmp_path / "out")[0]
    assert entry["ai_used"] is True
    trace_text = (tmp_path / "out" / entry["trace"]).read_text(encoding="utf-8")
    assert json.loads(trace_text)["ai"]["adapter"].startswith("provider-gateway@")
    assert "CANARY" not in trace_text and "CANARY" not in (tmp_path / "out" / "history.jsonl").read_text(encoding="utf-8")


def _apply_euler_xz(rotation: tuple[float, float, float], vector: tuple[float, float, float]) -> tuple[float, float, float]:
    x, _, z = (math.radians(value) for value in rotation)
    vx, vy, vz = vector
    vy, vz = vy * math.cos(x) - vz * math.sin(x), vy * math.sin(x) + vz * math.cos(x)
    vx, vy = vx * math.cos(z) - vy * math.sin(z), vx * math.sin(z) + vy * math.cos(z)
    return vx, vy, vz


@pytest.mark.parametrize("direction", [(-3.7, 6.0, -2.3), (0.0, 1.0, -0.18), (-1.0, 0.0, -0.18), (0.0, 0.3, -1.0), (3.5, 4.0, -4.2)])
def test_track_rotation_points_minus_z_along_the_direction_with_y_up(direction: tuple[float, float, float]) -> None:
    rotation = track_rotation_degrees(direction)
    length = math.sqrt(sum(value * value for value in direction))
    forward = _apply_euler_xz(rotation, (0.0, 0.0, -1.0))
    up = _apply_euler_xz(rotation, (0.0, 1.0, 0.0))
    for got, want in zip(forward, (value / length for value in direction), strict=True):
        assert got == pytest.approx(want, abs=1e-3)
    assert up[2] >= 0  # no roll: camera/light up axis stays above the horizon


def test_blender_guide_values_equal_the_builder_presets() -> None:
    values = _values(valid_scene())
    preset = BACKGROUND_PRESETS["studio-neutral.v1"]

    assert "0.52, 0.56, 0.62, 1" in values and f"{preset['world_strength']:g}" in values
    assert "Size = 200 m, Location = 0, 0, -0.04" in values
    assert "0.68, 0.71, 0.76, 1" in values and "0.72" in values
    assert "55 mm" in values
    assert "3.7, -6, 3.1" in values
    rotation = aim_rotation(_CAMERA_OFFSET, _LEGACY_TARGET)
    assert f"X={rotation[0]:g}°, Y=0°, Z={rotation[2]:g}°" in values
    for name, location, energy, size, color in LIGHTING_PRESETS["softbox-three-point.v1"]:
        assert name in values
        assert ", ".join(f"{v:g}" for v in location) in values
        assert f"Power = {energy:g} W, Shape = Disk, Size = {size:g} m" in values
        assert ", ".join(f"{v:g}" for v in color) in values
        light_rotation = aim_rotation(location, LIGHT_TARGET)
        assert f"X={light_rotation[0]:g}°, Y=0°, Z={light_rotation[2]:g}°" in values
    assert "EEVEE" in values and str(EEVEE_SAMPLES) in values and "AgX" in values
    assert "1024 × 1024, 100%" in values and "PNG, RGB, 8, 15%" in values and "off" in values
    assert "scene.blend" in values and "render.png" in values


def test_blender_guide_for_new_presets_and_fit_formula() -> None:
    scene = {**valid_scene(), "composition": "orbit-fit-090.v1", "lighting": "dramatic-rim.v1", "background": "transparent.v1", "render_profile": "landscape-1920x1080.v1"}
    guide = build_blender_guide(parse_scene_ir(scene))
    values = [step.value for step in guide.steps]

    assert "PNG, RGBA, 8, 15%" in values and "1920 × 1080, 100%" in values
    assert "Studio Ground" not in values
    assert [layer.name for layer in guide.layers if layer.kind.startswith("Light")] == ["Key Softbox", "Rim Left", "Rim Right"]
    factor = fit_distance_factor("landscape-1920x1080.v1")
    horizontal = 2 * math.atan(18 / 55)
    vertical = 2 * math.atan(math.tan(horizontal / 2) * 1080 / 1920)
    assert factor == pytest.approx(1.15 / math.sin(vertical / 2))
    assert any(f"× {factor:.4f}" in value and "1, 0, 0.33" in value for value in values)
    assert fit_distance_factor("square-1024.v1") == pytest.approx(1.15 / math.sin(math.atan(18 / 55)))


@pytest.mark.parametrize(
    ("field", "value"),
    [("lighting", "high-key-white.v1"), ("lighting", "dramatic-rim.v1"), ("background", "studio-white.v1"), ("background", "transparent.v1"), ("composition", "orbit-fit-225.v1")],
)
def test_new_presets_pass_scene_ir_and_the_blender_allowlist(tmp_path: Path, field: str, value: str) -> None:
    scene = {**valid_scene(), field: value}
    assert getattr(parse_scene_ir(scene), field) == value
    path = tmp_path / "scene.json"
    path.write_text(json.dumps(scene), encoding="utf-8")
    assert _load_validated_scene_ir(path)[field] == value


def test_orbit_compositions_circle_the_product() -> None:
    assert sorted(ORBIT_COMPOSITIONS) == [f"orbit-fit-{angle:03d}.v1" for angle in range(0, 360, 45)]
    assert FIT_DIRECTIONS["orbit-fit-000.v1"] == (0.0, -1.0, 0.33)
    assert FIT_DIRECTIONS["orbit-fit-090.v1"] == (1.0, 0.0, 0.33)
    assert FIT_DIRECTIONS["orbit-fit-180.v1"] == (0.0, 1.0, 0.33)


def test_turntable_spec_varies_only_the_composition(tmp_path: Path) -> None:
    base = {**valid_scene(), "lighting": "high-key-white.v1"}
    frames = turntable_scenes(base, steps=8)
    assert [frame["composition"] for frame in frames] == [f"orbit-fit-{angle:03d}.v1" for angle in range(0, 360, 45)]
    assert all({**frame, "composition": base["composition"]} == base for frame in frames)
    assert [frame["composition"] for frame in turntable_scenes(base, steps=4)] == ["orbit-fit-000.v1", "orbit-fit-090.v1", "orbit-fit-180.v1", "orbit-fit-270.v1"]
    with pytest.raises(SceneInputError):
        turntable_scenes(base, steps=3)
    with pytest.raises(SceneInputError):
        turntable_scenes({**base, "lighting": "neon.v1"})

    scene_path = tmp_path / "scene.json"
    scene_path.write_text(json.dumps(base), encoding="utf-8")
    spec = write_turntable(scene_path, tmp_path / "turntable", steps=8)
    assert spec["schema_version"] == "scene3d-turntable.v1"
    assert [entry["angle_degrees"] for entry in spec["frames"]] == list(range(0, 360, 45))
    for entry in spec["frames"]:
        parse_scene_ir(json.loads((tmp_path / "turntable" / entry["scene"]).read_text(encoding="utf-8")))


def test_turntable_frames_compose_as_separate_traced_jobs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _, input_root, output_root, blender_bin = create_inputs(tmp_path)
    install_fake_workers(monkeypatch)
    spec = write_turntable(input_root / "scene.json", input_root / "turntable", steps=4)

    fingerprints = {
        runtime.compose_scene(input_root / "turntable" / entry["scene"], input_root, output_root, blender_bin).fingerprint
        for entry in spec["frames"]
    }

    assert len(fingerprints) == 4
    assert [entry["status"] for entry in read_history(output_root)] == ["accepted"] * 4
