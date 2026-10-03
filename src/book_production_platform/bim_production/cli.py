"""JSON command line interface for local BIM quantity takeoff jobs."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any, NoReturn

from book_production_platform.bim_ir import BimTakeoffIRError
from book_production_platform.bim_production.benchmark import run_benchmark
from book_production_platform.bim_production.cost import build_cost_estimate_job
from book_production_platform.bim_production.errors import BimProductionError
from book_production_platform.bim_production.jobs import build_bim_takeoff_job
from book_production_platform.bim_production.runtime import MAX_WORKER_TIMEOUT_SECONDS


class _InvalidArguments(Exception):
    """An argparse error that should be emitted as stable JSON."""


class _HelpRequested(Exception):
    """Help was printed without starting a BIM engine."""


class _BimArgumentParser(argparse.ArgumentParser):
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
    parser = _BimArgumentParser(
        prog="book-production bim",
        description="ดึงปริมาณจากโมเดล IFC แบบ local พร้อม provenance และ replay",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    takeoff = commands.add_parser(
        "takeoff", help="สร้างหรือ replay ชุดผลลัพธ์ quantity takeoff"
    )
    takeoff.add_argument("--ir", type=Path, required=True)
    takeoff.add_argument("--ifc", type=Path, required=True)
    takeoff.add_argument("--output-root", type=Path, required=True)
    takeoff.add_argument("--freecadcmd", type=Path, required=True)
    takeoff.add_argument("--ifc-site-packages", type=Path, required=True)
    takeoff.add_argument("--timeout-s", type=int, default=MAX_WORKER_TIMEOUT_SECONDS)

    cost = commands.add_parser(
        "cost", help="คูณยอด takeoff กับตารางราคา bim-cost-rates.v1 เป็น cost-estimate.json/.csv"
    )
    cost.add_argument("--report", type=Path, required=True, help="takeoff.json ของงานที่เผยแพร่แล้ว")
    cost.add_argument("--rates", type=Path, required=True, help="ตารางราคา bim-cost-rates.v1 (JSON)")
    cost.add_argument("--output-dir", type=Path, required=True)

    benchmark = commands.add_parser("benchmark", help="รันชุด acceptance 20 กรณี")
    benchmark.add_argument("--fixtures", type=Path, required=True)
    benchmark.add_argument("--output", type=Path, required=True)
    benchmark.add_argument("--freecadcmd", type=Path, required=True)
    benchmark.add_argument("--ifc-site-packages", type=Path, required=True)
    benchmark.add_argument("--fixture-macro", type=Path, required=True)
    benchmark.add_argument("--timeout-s", type=int, default=MAX_WORKER_TIMEOUT_SECONDS)
    return parser


def _emit(value: dict[str, Any]) -> None:
    print(json.dumps(value, ensure_ascii=False, sort_keys=True))


def _emit_rejected(code: str) -> int:
    _emit({"status": "rejected", "error_code": code})
    return 2


def _takeoff_exit(status: str) -> int:
    if status in {"complete", "replayed"}:
        return 0
    if status == "partial":
        return 3
    if status == "needs_input":
        return 4
    return 2


def main(argv: Sequence[str] | None = None) -> int:
    arguments = list(argv) if argv is not None else sys.argv[1:]
    try:
        args = _parser().parse_args(arguments)
    except _HelpRequested:
        return 0
    except _InvalidArguments:
        _emit({"status": "rejected", "error_code": "invalid_arguments"})
        return 2
    try:
        if args.command == "takeoff":
            result = build_bim_takeoff_job(
                args.ir,
                args.ifc,
                args.output_root,
                args.freecadcmd,
                args.ifc_site_packages,
                timeout_s=args.timeout_s,
            )
            _emit(
                {
                    "status": result.status,
                    "fingerprint": result.fingerprint,
                    "row_count": result.row_count,
                    "coverage_gap_count": result.coverage_gap_count,
                    "model_calls": result.model_calls,
                    "trace_job_id": result.trace_job_id,
                }
            )
            return _takeoff_exit(result.status)
        if args.command == "cost":
            estimate = build_cost_estimate_job(args.report, args.rates, args.output_dir)
            _emit(
                {
                    "status": estimate.status,
                    "total": estimate.total,
                    "currency": estimate.currency,
                    "line_count": estimate.line_count,
                    "unpriced_count": estimate.unpriced_count,
                    "model_calls": 0,
                    "trace_job_id": estimate.trace_job_id,
                }
            )
            return 0 if estimate.status == "complete" else 3
        if args.command == "benchmark":
            report = run_benchmark(
                args.fixtures,
                args.output.parent.parent,
                args.freecadcmd,
                args.ifc_site_packages,
                args.fixture_macro,
                timeout_s=args.timeout_s,
                summary_path=args.output,
            )
            _emit(report)
            return 0 if report.get("status") == "passed" else 2
    except BimTakeoffIRError:
        return _emit_rejected("invalid_bim_takeoff_ir")
    except BimProductionError as error:
        return _emit_rejected(error.code)
    except (OSError, ValueError):
        return _emit_rejected("cli_io_error")
    _emit({"status": "rejected", "error_code": "unsupported_command"})
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
