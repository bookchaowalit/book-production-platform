"""Stable JSON command line interface for local production capabilities."""

from __future__ import annotations

import argparse
import json
import os
import shutil
from pathlib import Path
from typing import Any

from book_production_platform.graphics_production.cli import main as graphics_main
from book_production_platform.scene3d.benchmark import run_benchmark
from book_production_platform.scene3d.errors import SceneInputError
from book_production_platform.scene3d.runtime import MAX_TIMEOUT_SECONDS, compose_scene
from book_production_platform.spreadsheet_production.cli import main as spreadsheet_main

_LOCAL_BLENDER = (
    Path.home()
    / ".codex"
    / "tools"
    / "blender-5.2.2"
    / "portable"
    / "blender-5.2.2-windows-x64"
    / "blender.exe"
)


def _default_blender() -> Path:
    configured = os.environ.get("BLENDER_BIN")
    if configured:
        return Path(configured)
    discovered = shutil.which("blender")
    if discovered:
        return Path(discovered)
    return _LOCAL_BLENDER


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="book-production",
        description="Build deterministic local products from validated domain representations.",
    )
    groups = parser.add_subparsers(dest="family", required=True)
    scene = groups.add_parser("scene", help="3D product scene operations")
    commands = scene.add_subparsers(dest="command", required=True)

    compose = commands.add_parser("compose", help="compose an editable Blender scene from SceneIR and a local GLB")
    compose.add_argument("--scene", type=Path, required=True)
    compose.add_argument("--input-root", type=Path, required=True)
    compose.add_argument("--output-root", type=Path, required=True)
    compose.add_argument("--blender", type=Path, required=True)
    compose.add_argument("--timeout-s", type=int, default=MAX_TIMEOUT_SECONDS)

    benchmark = commands.add_parser("benchmark", help="run the 20-case local acceptance suite")
    benchmark.add_argument("--fixtures", type=Path, required=True)
    benchmark.add_argument("--output", type=Path, required=True)
    benchmark.add_argument("--blender", type=Path, default=None)
    benchmark.add_argument("--timeout-s", type=int, default=MAX_TIMEOUT_SECONDS)

    graphics = groups.add_parser("graphics", help="Graphics V1A template batch operations")
    graphics.add_argument("graphics_args", nargs=argparse.REMAINDER)
    spreadsheet = groups.add_parser("spreadsheet", help="Spreadsheet V1A workbook operations")
    spreadsheet.add_argument("spreadsheet_args", nargs=argparse.REMAINDER)
    return parser


def _emit(value: dict[str, Any]) -> None:
    print(json.dumps(value, ensure_ascii=False, sort_keys=True))


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.family == "graphics":
        return graphics_main(args.graphics_args)
    if args.family == "spreadsheet":
        return spreadsheet_main(args.spreadsheet_args)
    try:
        if args.family == "scene" and args.command == "compose":
            result = compose_scene(
                args.scene,
                args.input_root,
                args.output_root,
                args.blender,
                timeout_s=args.timeout_s,
            )
            _emit(
                {
                    "status": result.status,
                    "fingerprint": result.fingerprint,
                    "artifact_dir": str(result.artifact_dir),
                    "artifact_hashes": result.artifact_hashes,
                    "replay": result.replay,
                    "model_calls": result.model_calls,
                }
            )
            return 0
        if args.family == "scene" and args.command == "benchmark":
            report = run_benchmark(
                args.fixtures,
                args.output,
                args.blender or _default_blender(),
                timeout_s=args.timeout_s,
            )
            _emit(report)
            return 0 if report.get("status") == "passed" else 3
    except SceneInputError as error:
        _emit({"status": "rejected", "error": error.code})
        return 2
    except OSError:
        _emit({"status": "rejected", "error": "cli_io_error"})
        return 2
    _emit({"status": "rejected", "error": "unsupported_command"})
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
