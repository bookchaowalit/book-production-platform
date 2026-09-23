"""Minimal Blender background-mode entry point; accepts data paths only."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

SOURCE_ROOT = Path(__file__).resolve().parents[2]
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from book_production_platform.scene3d.scene_builder import build_scene, render_saved_scene


def _worker_arguments() -> list[str]:
    try:
        separator = sys.argv.index("--")
    except ValueError as error:
        raise SystemExit("worker arguments must follow --") from error
    return sys.argv[separator + 1 :]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="book-production-blender-worker")
    subparsers = parser.add_subparsers(dest="mode", required=True)
    build_parser = subparsers.add_parser("build")
    build_parser.add_argument("--scene", type=Path, required=True)
    build_parser.add_argument("--asset", type=Path, required=True)
    build_parser.add_argument("--blend", type=Path, required=True)
    render_parser = subparsers.add_parser("render")
    render_parser.add_argument("--blend", type=Path, required=True)
    render_parser.add_argument("--png", type=Path, required=True)
    args = parser.parse_args(_worker_arguments() if argv is None else argv)
    if args.mode == "build":
        build_scene(args.scene, args.asset, args.blend)
    else:
        render_saved_scene(args.blend, args.png)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
