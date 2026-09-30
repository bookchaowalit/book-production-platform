"""JSON command-line interface for Game Scene Builder V1A."""

from __future__ import annotations

import argparse
import json
from collections.abc import Callable
from importlib import import_module
from pathlib import Path
from typing import Any, NoReturn

from book_production_platform.game_ir import GameInputError
from book_production_platform.game_production.errors import GameJobError
from book_production_platform.game_production.export import (
    DEFAULT_EXPORT_TIMEOUT_SECONDS,
    EXPORT_TARGETS,
    export_game_player,
)
from book_production_platform.game_production.jobs import compose_game_scene


class _JsonArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        del message
        _emit({"status": "rejected", "error": "invalid_cli_arguments"})
        raise SystemExit(2)


def _parser() -> argparse.ArgumentParser:
    parser = _JsonArgumentParser(
        prog="book-production game",
        description="Build editable Unity scenes from validated GameIR and export playable players.",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    build = commands.add_parser("build", help="build and verify one Unity project")
    build.add_argument("--game", type=Path, required=True)
    build.add_argument("--output-root", type=Path, required=True)
    build.add_argument("--unity", type=Path, required=True)
    build.add_argument("--timeout-s", type=int, default=120)
    build.add_argument("--asset-root", type=Path, default=None, help="folder holding GameIR v2 audio assets")

    export = commands.add_parser("export", help="build a playable player from an accepted project")
    export.add_argument("--artifact", type=Path, required=True, help="accepted `game build` artifact directory")
    export.add_argument("--target", choices=sorted(EXPORT_TARGETS), required=True)
    export.add_argument("--output-root", type=Path, required=True)
    export.add_argument("--unity", type=Path, required=True)
    export.add_argument("--timeout-s", type=int, default=DEFAULT_EXPORT_TIMEOUT_SECONDS)

    benchmark = commands.add_parser("benchmark", help="run the 20-case local acceptance suite")
    benchmark.add_argument("--fixtures", type=Path, required=True)
    benchmark.add_argument("--output", type=Path, required=True)
    benchmark.add_argument("--unity", type=Path, required=True)
    return parser


def _emit(value: dict[str, object]) -> None:
    print(json.dumps(value, ensure_ascii=False, sort_keys=True))


def game_main(argv: list[str]) -> int:
    """Run one Game Scene Builder command and return its process exit code."""
    try:
        args = _parser().parse_args(argv)
    except SystemExit as error:
        return int(error.code or 0)

    if args.command == "build":
        try:
            result = compose_game_scene(
                args.game,
                args.output_root,
                args.unity,
                timeout_s=args.timeout_s,
                asset_root=args.asset_root,
            )
        except (GameInputError, GameJobError) as error:
            _emit({"status": "rejected", "error": error.code})
            return 2
        except OSError:
            _emit({"status": "rejected", "error": "cli_io_error"})
            return 2
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

    if args.command == "export":
        try:
            exported = export_game_player(
                args.artifact,
                args.target,
                args.output_root,
                args.unity,
                timeout_s=args.timeout_s,
            )
        except (GameInputError, GameJobError) as error:
            _emit({"status": "rejected", "error": error.code})
            return 2
        except OSError:
            _emit({"status": "rejected", "error": "cli_io_error"})
            return 2
        _emit(
            {
                "status": exported.status,
                "fingerprint": exported.fingerprint,
                "target": exported.target,
                "artifact_dir": str(exported.artifact_dir),
                "entrypoint": exported.entrypoint,
                "source_fingerprint": exported.source_fingerprint,
                "artifact_hashes": exported.artifact_hashes,
                "replay": exported.replay,
                "model_calls": exported.model_calls,
            }
        )
        return 0

    if args.command == "benchmark":
        try:
            benchmark_module: Any = import_module("book_production_platform.game_production.benchmark")
        except ModuleNotFoundError as error:
            if error.name != "book_production_platform.game_production.benchmark":
                raise
            _emit({"status": "rejected", "error": "benchmark_not_available"})
            return 2
        run_benchmark: Callable[[Path, Path, Path], dict[str, object]] = benchmark_module.run_benchmark

        try:
            report = run_benchmark(args.fixtures, args.output, args.unity)
        except (GameInputError, GameJobError) as error:
            _emit({"status": "rejected", "error": error.code})
            return 2
        except OSError:
            _emit({"status": "rejected", "error": "cli_io_error"})
            return 2
        _emit(report)
        return 0 if report.get("status") == "passed" else 3

    _emit({"status": "rejected", "error": "unsupported_command"})
    return 2
