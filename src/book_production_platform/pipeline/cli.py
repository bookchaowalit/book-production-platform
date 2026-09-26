"""CLI entry point for cross-product pipelines."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from book_production_platform.scene3d.errors import SceneInputError
from book_production_platform.scene3d.runtime import MAX_TIMEOUT_SECONDS

from .errors import PipelineError
from .jobs import run_scene_to_graphics


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="book-production pipeline")
    commands = parser.add_subparsers(dest="command", required=True)
    scene_to_graphics = commands.add_parser(
        "scene-to-graphics",
        help="render one scene with Blender, then batch multi-size graphics from its PNG",
    )
    scene_to_graphics.add_argument("--scene", type=Path, required=True)
    scene_to_graphics.add_argument("--input-root", type=Path, required=True)
    scene_to_graphics.add_argument("--spec", type=Path, required=True)
    scene_to_graphics.add_argument("--output-root", type=Path, required=True)
    scene_to_graphics.add_argument("--blender", type=Path, required=True)
    scene_to_graphics.add_argument("--timeout-s", type=int, default=MAX_TIMEOUT_SECONDS)
    return parser


def _emit(value: dict[str, object]) -> None:
    print(json.dumps(value, ensure_ascii=False, sort_keys=True))


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        result = run_scene_to_graphics(
            args.scene,
            args.input_root,
            args.output_root,
            args.blender,
            args.spec,
            timeout_s=args.timeout_s,
        )
    except PipelineError as error:
        print(json.dumps({"status": "rejected", "error": error.code}, sort_keys=True), file=sys.stderr)
        return 2
    except SceneInputError as error:
        print(json.dumps({"status": "rejected", "error": error.code}, sort_keys=True), file=sys.stderr)
        return 2
    except OSError:
        print(json.dumps({"status": "rejected", "error": "cli_io_error"}, sort_keys=True), file=sys.stderr)
        return 2
    _emit(
        {
            "status": result.status,
            "pipeline_id": result.pipeline_id,
            "pipeline_dir": str(result.pipeline_dir),
            "scene_status": result.scene_status,
            "scene_fingerprint": result.scene_fingerprint,
            "batch_id": result.batch_id,
            "graphics_status": result.graphics_status,
            "model_calls": result.model_calls,
        }
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
