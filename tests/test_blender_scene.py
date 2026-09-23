import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest
from PIL import Image

from tests.helpers.glb_factory import with_texture, write_glb
from tests.test_scene_ir import valid_scene


BLENDER_CANDIDATE = (
    Path.home()
    / ".codex"
    / "tools"
    / "blender-5.2.2"
    / "portable"
    / "blender-5.2.2-windows-x64"
    / "blender.exe"
)


def blender_executable() -> Path:
    configured = os.environ.get("BLENDER_BIN")
    found = Path(configured) if configured else Path(shutil.which("blender") or "")
    if found.is_file():
        return found
    if BLENDER_CANDIDATE.is_file():
        return BLENDER_CANDIDATE
    pytest.skip("Blender 5.2.2 LTS is required; install it locally or set BLENDER_BIN")


def test_blender_builds_editable_scene_and_renders_after_fresh_reopen(tmp_path: Path) -> None:
    blender = blender_executable()
    version = subprocess.run([str(blender), "--version"], capture_output=True, text=True, timeout=15)
    assert version.returncode == 0, version.stdout + version.stderr
    assert "Blender 5.2.2 LTS" in version.stdout

    document, binary = with_texture()
    asset = write_glb(tmp_path / "product.glb", document, binary)
    scene_ir = tmp_path / "scene.json"
    scene_ir.write_text(json.dumps(valid_scene()), encoding="utf-8")
    blend_file = tmp_path / "scene.blend"
    render_file = tmp_path / "render.png"
    worker = (
        Path(__file__).parents[1]
        / "src"
        / "book_production_platform"
        / "scene3d"
        / "blender_worker.py"
    )

    build = subprocess.run(
        [
            str(blender),
            "--background",
            "--factory-startup",
            "--python",
            str(worker),
            "--",
            "build",
            "--scene",
            str(scene_ir),
            "--asset",
            str(asset),
            "--blend",
            str(blend_file),
        ],
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert build.returncode == 0, (build.stdout + build.stderr)[-6000:]
    assert blend_file.is_file() and blend_file.stat().st_size > 0
    asset.unlink()

    render = subprocess.run(
        [
            str(blender),
            "--background",
            "--factory-startup",
            "--python",
            str(worker),
            "--",
            "render",
            "--blend",
            str(blend_file),
            "--png",
            str(render_file),
        ],
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert render.returncode == 0, (render.stdout + render.stderr)[-6000:]
    with Image.open(render_file) as image:
        assert image.size == (1024, 1024)
        assert image.getbbox() is not None
