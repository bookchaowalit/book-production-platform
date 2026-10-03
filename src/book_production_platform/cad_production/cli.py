"""JSON command line interface for local CAD production jobs."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any, NoReturn

from book_production_platform.cad_ir import MAX_PART_IR_BYTES, PartIRError, load_part_ir
from book_production_platform.cad_production.benchmark import run_benchmark
from book_production_platform.cad_production.brief_cli import BRIEF_COMMANDS, brief_main
from book_production_platform.cad_production.errors import CadProductionError
from book_production_platform.cad_production.jobs import build_cad_job
from book_production_platform.cad_production.runtime import MAX_WORKER_TIMEOUT_SECONDS


class _InvalidArguments(Exception):
    """An argparse error that should be emitted as stable JSON."""


class _HelpRequested(Exception):
    """Help was printed without starting a CAD engine."""


class _CadArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        del message
        raise _InvalidArguments

    def exit(self, status: int = 0, message: str | None = None) -> NoReturn:
        if message:
            self._print_message(message, sys.stdout if status == 0 else sys.stderr)
        if status == 0:
            raise _HelpRequested
        raise _InvalidArguments


def _parser() -> argparse.ArgumentParser:
    parser = _CadArgumentParser(
        prog="book-production cad",
        description="สร้างชิ้นงาน CAD แบบ parametric จาก PartIR ที่ตรวจสอบแล้ว",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    build = commands.add_parser("build", help="สร้างและตรวจ mounting plate ใน FreeCAD")
    build.add_argument("--input", type=Path, required=True)
    build.add_argument("--output-root", type=Path, required=True)
    build.add_argument("--freecadcmd", type=Path, required=True)
    build.add_argument("--timeout-s", type=int, default=MAX_WORKER_TIMEOUT_SECONDS)

    benchmark = commands.add_parser("benchmark", help="รันชุด acceptance 20 กรณี")
    benchmark.add_argument("--fixtures", type=Path, required=True)
    benchmark.add_argument("--output", type=Path, required=True)
    benchmark.add_argument("--freecadcmd", type=Path, required=True)
    benchmark.add_argument("--timeout-s", type=int, default=MAX_WORKER_TIMEOUT_SECONDS)
    return parser


def _emit(value: dict[str, Any]) -> None:
    print(json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True))


def _read_part_ir(path: Path) -> None:
    try:
        if path.is_symlink() or path.is_junction():
            raise PartIRError("invalid_part_ir")
        with path.open("rb") as stream:
            payload = stream.read(MAX_PART_IR_BYTES + 1)
        if len(payload) > MAX_PART_IR_BYTES:
            raise PartIRError("invalid_part_ir")
        # Validate before calling the job API, which independently reloads the bounded file.
        load_part_ir(path)
    except PartIRError:
        raise
    except (OSError, AttributeError):
        raise PartIRError("invalid_part_ir") from None


def main(argv: Sequence[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if arguments and arguments[0] in BRIEF_COMMANDS:
        return brief_main(arguments)
    try:
        args = _parser().parse_args(argv)
    except _HelpRequested:
        return 0
    except _InvalidArguments:
        _emit({"status": "rejected", "error": "invalid_cli_arguments"})
        return 2

    if args.command == "build":
        try:
            _read_part_ir(args.input)
        except PartIRError as error:
            _emit({"status": "rejected", "error": error.code})
            return 2
        result = build_cad_job(
            args.input,
            args.output_root,
            args.freecadcmd,
            timeout_s=args.timeout_s,
        )
        if result.status not in {"accepted", "replayed"}:
            _emit({"status": "rejected", "error": result.error_code or "cad_job_failed"})
            return 2
        _emit(result.to_dict())
        return 0

    try:
        report = run_benchmark(
            args.fixtures,
            args.output,
            args.freecadcmd,
            timeout_s=args.timeout_s,
        )
    except CadProductionError as error:
        _emit({"status": "rejected", "error": error.code})
        return 2
    except OSError:
        _emit({"status": "rejected", "error": "cli_io_error"})
        return 2
    _emit(report)
    return 0 if report.get("status") == "passed" else 3


if __name__ == "__main__":
    raise SystemExit(main())
