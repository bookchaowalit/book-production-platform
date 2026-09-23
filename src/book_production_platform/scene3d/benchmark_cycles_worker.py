"""Fixed Cycles CPU candidate used only by the acceptance benchmark."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    import bpy

    parser = argparse.ArgumentParser(prog="scene3d-cycles-benchmark-worker")
    parser.add_argument("--blend", type=Path, required=True)
    parser.add_argument("--png", type=Path, required=True)
    args_list = argv
    if args_list is None:
        try:
            args_list = sys.argv[sys.argv.index("--") + 1 :]
        except ValueError as error:
            raise SystemExit("benchmark arguments must follow --") from error
    args = parser.parse_args(args_list)
    bpy.ops.wm.open_mainfile(filepath=str(args.blend.resolve()))
    scene = bpy.context.scene
    if scene.camera is None or scene.get("book_production_render_profile") not in {
        "square-1024.v1",
        "landscape-1920x1080.v1",
    }:
        raise ValueError("invalid_saved_scene")
    if not any(image.source == "FILE" and image.packed_file is None for image in bpy.data.images):
        pass
    else:
        raise ValueError("unpacked_scene_resource")
    scene.render.engine = "CYCLES"
    scene.cycles.device = "CPU"
    scene.cycles.samples = 32
    scene.cycles.use_denoising = True
    scene.render.filepath = str(args.png.resolve())
    scene.render.image_settings.file_format = "PNG"
    scene.render.image_settings.color_mode = "RGB"
    scene.render.resolution_percentage = 100
    args.png.parent.mkdir(parents=True, exist_ok=True)
    bpy.ops.render.render(write_still=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
