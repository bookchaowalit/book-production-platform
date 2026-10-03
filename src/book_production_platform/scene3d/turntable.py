"""Turntable batch spec: one SceneIR per orbit angle around the same product.

``turntable_scenes`` turns one validated ``scene-ir.v1`` into a list of
``scene-ir.v1`` values that differ only in ``composition`` (``orbit-fit-000.v1``
… ``orbit-fit-315.v1``, every 45 degrees), so each still is an ordinary,
replay-safe ``book-production scene compose`` job with its own trace. The
product, lighting, background, material, and render profile stay identical,
which keeps the frames consistent for a 360° product viewer or a GIF made
from the stills. Encoding the stills into a video/animated file is not part of
this engine (see ``docs/parity/scene3d.md``).

CLI::

    python -m book_production_platform.scene3d.turntable \\
        --scene scene.json --output-dir turntable --steps 8
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from .errors import SceneInputError
from .models import parse_scene_ir
from .scene_builder import ORBIT_COMPOSITIONS, ORBIT_STEP_DEGREES

TURNTABLE_SCHEMA = "scene3d-turntable.v1"
ALLOWED_STEPS = (1, 2, 4, 8)


def turntable_scenes(scene: object, *, steps: int = 8) -> list[dict[str, Any]]:
    """Return ``steps`` SceneIR values evenly spaced around the product (8 = every 45°)."""

    if steps not in ALLOWED_STEPS:
        raise SceneInputError("invalid_turntable_steps", f"steps must be one of {ALLOWED_STEPS}")
    base = parse_scene_ir(scene).model_dump(mode="json")
    stride = 360 // steps
    scenes = []
    for angle in range(0, 360, stride):
        composition = f"orbit-fit-{angle:03d}.v1"
        if composition not in ORBIT_COMPOSITIONS:  # pragma: no cover - guarded by ALLOWED_STEPS
            raise SceneInputError("invalid_turntable_steps", "orbit angle is not registered")
        scenes.append(parse_scene_ir({**base, "composition": composition}).model_dump(mode="json"))
    return scenes


def write_turntable(scene_path: Path, output_dir: Path, *, steps: int = 8) -> dict[str, Any]:
    """Write ``frame-NNN.json`` SceneIR files plus a ``turntable.json`` spec."""

    try:
        scene = json.loads(scene_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError) as error:
        raise SceneInputError("invalid_scene_ir", "scene description is unavailable or invalid") from error
    frames = turntable_scenes(scene, steps=steps)
    output_dir.mkdir(parents=True, exist_ok=True)
    entries = []
    for index, frame in enumerate(frames):
        name = f"frame-{index:03d}.json"
        (output_dir / name).write_text(json.dumps(frame, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        entries.append({"index": index, "angle_degrees": index * (360 // steps), "composition": frame["composition"], "scene": name})
    spec = {
        "schema_version": TURNTABLE_SCHEMA,
        "steps": steps,
        "orbit_step_degrees": ORBIT_STEP_DEGREES,
        "render_profile": frames[0]["render_profile"],
        "frames": entries,
    }
    (output_dir / "turntable.json").write_text(json.dumps(spec, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return spec


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="scene3d-turntable", description="write one SceneIR per orbit angle")
    parser.add_argument("--scene", type=Path, required=True, help="a scene-ir.v1 JSON file")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--steps", type=int, default=8, choices=ALLOWED_STEPS)
    args = parser.parse_args(argv)
    try:
        spec = write_turntable(args.scene, args.output_dir, steps=args.steps)
    except SceneInputError as error:
        print(json.dumps({"status": "rejected", "error": error.code}), file=sys.stderr)
        return 2
    print(json.dumps(spec, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
