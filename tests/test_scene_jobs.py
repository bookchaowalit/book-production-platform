import hashlib
import json
import os
import subprocess
from pathlib import Path
from typing import Any

import pytest
from PIL import Image, ImageDraw

from book_production_platform.production_trace import read_history
from book_production_platform.scene3d import runtime
from book_production_platform.scene3d.errors import SceneInputError
from tests.helpers.glb_factory import TRIANGLE_BINARY, triangle_document, write_glb
from tests.test_scene_ir import valid_scene


BLENDER_IDENTITY = ("Blender 5.2.2 LTS", "d13f752e3b9c")


def create_inputs(
    root: Path,
    *,
    document: dict[str, Any] | None = None,
    render_profile: str = "square-1024.v1",
) -> tuple[Path, Path, Path, Path]:
    input_root = root / "input"
    input_root.mkdir(parents=True, exist_ok=True)
    asset = write_glb(input_root / "product.glb", document or triangle_document(), TRIANGLE_BINARY)
    scene_value = valid_scene()
    scene_value["render_profile"] = render_profile
    scene_path = input_root / "scene.json"
    scene_path.write_text(json.dumps(scene_value), encoding="utf-8")
    output_root = root / "output"
    blender_bin = root / "blender.exe"
    blender_bin.write_bytes(b"test executable marker")
    return scene_path, input_root, output_root, blender_bin


def install_fake_workers(monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    calls: list[list[str]] = []
    monkeypatch.setattr(runtime, "_blender_identity", lambda *_: BLENDER_IDENTITY)

    def fake_worker(command: list[str], *, cwd: Path, timeout_s: int) -> float:
        assert timeout_s > 0
        calls.append(command)
        separator = command.index("--")
        mode = command[separator + 1]
        options = command[separator + 2 :]

        def value(flag: str) -> Path:
            return Path(options[options.index(flag) + 1])

        if mode == "build":
            scene_value = json.loads(value("--scene").read_text(encoding="utf-8"))
            value("--blend").write_bytes(b"synthetic-blend\0" + scene_value["render_profile"].encode())
        else:
            profile = value("--blend").read_bytes().decode("utf-8").split("\0", 1)[1]
            dimensions = (1024, 1024) if profile == "square-1024.v1" else (1920, 1080)
            image = Image.new("RGB", dimensions, color=(225, 230, 235))
            ImageDraw.Draw(image).rectangle((30, 30, dimensions[0] // 2, dimensions[1] // 2), fill=(20, 80, 150))
            image.save(value("--png"), format="PNG")
        return 0.001

    monkeypatch.setattr(runtime, "_run_worker", fake_worker)
    return calls


def test_compose_scene_publishes_exact_verified_artifacts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    scene_path, input_root, output_root, blender_bin = create_inputs(tmp_path)
    install_fake_workers(monkeypatch)

    result = runtime.compose_scene(scene_path, input_root, output_root, blender_bin)

    assert result.status == "accepted"
    assert result.replay is False
    assert result.model_calls == 0
    assert set(result.artifact_hashes) == {"scene.blend", "render.png", "qa-manifest.json"}
    for name, expected_hash in result.artifact_hashes.items():
        assert hashlib.sha256((result.artifact_dir / name).read_bytes()).hexdigest() == expected_hash
    assert {item.name for item in result.artifact_dir.iterdir()} == {
        "scene.blend",
        "render.png",
        "qa-manifest.json",
    }
    manifest = json.loads((result.artifact_dir / "qa-manifest.json").read_text(encoding="utf-8"))
    assert manifest["model_calls"] == 0
    assert manifest["status"] == "accepted"
    assert manifest["fingerprint"] == result.fingerprint
    assert set(manifest["outputs"]) == {"scene.blend", "render.png"}
    assert str(tmp_path) not in json.dumps(manifest)


def test_exact_replay_verifies_hashes_without_running_blender(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    scene_path, input_root, output_root, blender_bin = create_inputs(tmp_path)
    calls = install_fake_workers(monkeypatch)
    first = runtime.compose_scene(scene_path, input_root, output_root, blender_bin)
    first_hashes = first.artifact_hashes.copy()

    def unexpected_worker(*_: Any, **__: Any) -> float:
        pytest.fail("exact replay must not invoke Blender workers")

    monkeypatch.setattr(runtime, "_run_worker", unexpected_worker)
    second = runtime.compose_scene(scene_path, input_root, output_root, blender_bin)

    assert len(calls) == 2
    assert second.status == "replayed"
    assert second.replay is True
    assert second.fingerprint == first.fingerprint
    assert second.artifact_hashes == first_hashes
    receipt = output_root / ".receipts" / f"{first.fingerprint}.json"
    assert json.loads(receipt.read_text(encoding="utf-8"))["hashes"] == first_hashes


def test_changed_source_bytes_create_a_new_fingerprint(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    scene_path, input_root, output_root, blender_bin = create_inputs(tmp_path)
    calls = install_fake_workers(monkeypatch)
    first = runtime.compose_scene(scene_path, input_root, output_root, blender_bin)

    changed = triangle_document()
    changed["asset"]["generator"] = "changed-source-fixture"
    write_glb(input_root / "product.glb", changed, TRIANGLE_BINARY)
    second = runtime.compose_scene(scene_path, input_root, output_root, blender_bin)

    assert first.fingerprint != second.fingerprint
    assert second.replay is False
    assert len(calls) == 4
    assert (first.artifact_dir / "scene.blend").is_file()
    assert (second.artifact_dir / "scene.blend").is_file()


def test_tampered_replay_is_rejected_without_overwriting_existing_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    scene_path, input_root, output_root, blender_bin = create_inputs(tmp_path)
    calls = install_fake_workers(monkeypatch)
    first = runtime.compose_scene(scene_path, input_root, output_root, blender_bin)
    render_path = first.artifact_dir / "render.png"
    render_path.write_bytes(b"tampered-output")

    with pytest.raises(SceneInputError) as error:
        runtime.compose_scene(scene_path, input_root, output_root, blender_bin)

    assert error.value.code == "replay_integrity_error"
    assert render_path.read_bytes() == b"tampered-output"
    assert len(calls) == 2


def test_tampered_replay_manifest_is_rejected_without_rerendering(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    scene_path, input_root, output_root, blender_bin = create_inputs(tmp_path)
    calls = install_fake_workers(monkeypatch)
    first = runtime.compose_scene(scene_path, input_root, output_root, blender_bin)
    manifest_path = first.artifact_dir / "qa-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["timing_seconds"]["wall"] = 999
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(SceneInputError) as error:
        runtime.compose_scene(scene_path, input_root, output_root, blender_bin)

    assert error.value.code == "replay_integrity_error"
    assert len(calls) == 2
    assert json.loads(manifest_path.read_text(encoding="utf-8"))["timing_seconds"]["wall"] == 999


def test_missing_replay_receipt_is_rejected_without_rerendering(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    scene_path, input_root, output_root, blender_bin = create_inputs(tmp_path)
    calls = install_fake_workers(monkeypatch)
    first = runtime.compose_scene(scene_path, input_root, output_root, blender_bin)
    (output_root / ".receipts" / f"{first.fingerprint}.json").unlink()

    with pytest.raises(SceneInputError) as error:
        runtime.compose_scene(scene_path, input_root, output_root, blender_bin)

    assert error.value.code == "replay_integrity_error"
    assert len(calls) == 2


def test_deeply_nested_replay_manifest_has_stable_error_without_rerendering(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    scene_path, input_root, output_root, blender_bin = create_inputs(tmp_path)
    calls = install_fake_workers(monkeypatch)
    first = runtime.compose_scene(scene_path, input_root, output_root, blender_bin)
    manifest_path = first.artifact_dir / "qa-manifest.json"
    original = manifest_path.read_text(encoding="utf-8").rstrip()
    nested = ",\"nested\":" + "[" * 10000 + "null" + "]" * 10000
    manifest_path.write_text(original[:-1] + nested + "}\n", encoding="utf-8")
    receipt_path = output_root / ".receipts" / f"{first.fingerprint}.json"
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    receipt["hashes"]["qa-manifest.json"] = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")

    with pytest.raises(SceneInputError) as error:
        runtime.compose_scene(scene_path, input_root, output_root, blender_bin)

    assert error.value.code == "replay_integrity_error"
    assert len(calls) == 2


def test_timeout_removes_staging_and_never_publishes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    scene_path, input_root, output_root, blender_bin = create_inputs(tmp_path)
    monkeypatch.setattr(runtime, "_blender_identity", lambda *_: BLENDER_IDENTITY)

    def time_out(command: list[str], *, cwd: Path, timeout_s: int) -> float:
        raise subprocess.TimeoutExpired(command, timeout_s)

    monkeypatch.setattr(runtime, "_run_worker", time_out)
    with pytest.raises(SceneInputError) as error:
        runtime.compose_scene(scene_path, input_root, output_root, blender_bin, timeout_s=2)

    assert error.value.code == "deadline_exceeded"
    assert not any("staging" in child.name for child in output_root.iterdir())
    # Only the lock folder and the failure trace (traces/<job_id>/) remain.
    assert not any(child.is_dir() and child.name not in {".locks", "traces"} for child in output_root.iterdir())
    assert [entry["status"] for entry in read_history(output_root)] == ["failed"]


@pytest.mark.parametrize("accessor_type", [[], {}], ids=["array-type", "object-type"])
def test_malformed_glb_accessor_is_rejected_before_blender(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    accessor_type: object,
) -> None:
    document = triangle_document()
    document["accessors"][0]["type"] = accessor_type
    scene_path, input_root, output_root, blender_bin = create_inputs(tmp_path, document=document)

    def unexpected_runtime(*_: Any, **__: Any) -> Any:
        pytest.fail("invalid GLB must be rejected before Blender starts")

    monkeypatch.setattr(runtime, "_blender_identity", unexpected_runtime)
    monkeypatch.setattr(runtime, "_run_worker", unexpected_runtime)
    with pytest.raises(SceneInputError) as error:
        runtime.compose_scene(scene_path, input_root, output_root, blender_bin)

    assert error.value.code == "invalid_glb"
    # Nothing is published; only the failure trace and history line are written.
    assert sorted(child.name for child in output_root.iterdir()) == ["history.jsonl", "traces"]
    assert [entry["status"] for entry in read_history(output_root)] == ["failed"]


def test_symlinked_asset_is_rejected_before_worker_invocation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    scene_path, input_root, output_root, blender_bin = create_inputs(tmp_path)
    target = input_root / "product.glb"
    alias = input_root / "alias.glb"
    try:
        os.symlink(target, alias)
    except (OSError, NotImplementedError):
        pytest.skip("host does not permit symlink creation")
    scene_value = valid_scene()
    scene_value["asset"]["path"] = alias.name
    scene_path.write_text(json.dumps(scene_value), encoding="utf-8")
    calls = install_fake_workers(monkeypatch)

    with pytest.raises(SceneInputError) as error:
        runtime.compose_scene(scene_path, input_root, output_root, blender_bin)

    assert error.value.code == "unsafe_glb_resource"
    assert calls == []
