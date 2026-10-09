from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest


def test_runtime_image_renders_six_thai_caption_goldens(tmp_path: Path):
    image = os.getenv("VIDEO_POSTPRODUCTION_GOLDEN_IMAGE")
    if not image:
        pytest.skip("set VIDEO_POSTPRODUCTION_GOLDEN_IMAGE to an already-built local runtime image")
    script = Path(__file__).parents[1] / "fixtures" / "generate_thai_caption_review.py"
    result = subprocess.run(
        ["docker", "run", "--rm", "--network", "none", "--interactive",
         "--volume", f"{tmp_path}:/golden", "--entrypoint", "python", image, "-", "/golden"],
        input=script.read_text(encoding="utf-8"), capture_output=True,
        text=True, encoding="utf-8", timeout=300, check=True,
    )
    records = json.loads(result.stdout)
    assert len(records) == 6
    assert {(record["width"], record["height"]) for record in records} == {(1080, 1920), (1920, 1080)}
    assert all(record["synthetic"] and record["frame_sha256"] for record in records)
