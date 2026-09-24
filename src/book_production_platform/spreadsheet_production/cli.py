"""CLI entry point for the local Spreadsheet V1A product."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from .jobs import IdempotencyConflict, run_workbook_job
from .scripts.benchmark import run_benchmark


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="spreadsheet-production")
    commands = parser.add_subparsers(dest="command", required=True)
    build = commands.add_parser("build", help="build a validated XLSX workbook")
    build.add_argument("--input", required=True)
    build.add_argument("--output", required=True)
    build.add_argument("--idempotency-key", required=True)
    benchmark = commands.add_parser("benchmark", help="run the 20-workbook acceptance suite")
    benchmark.add_argument("--fixture", required=True)
    benchmark.add_argument("--output-dir", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "benchmark":
        try:
            summary = run_benchmark(args.fixture, args.output_dir)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            print(f"benchmark error: {exc}", file=sys.stderr)
            return 2
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0 if summary["rejected"] == 0 and summary["formula_mismatches"] == 0 and summary["reopen_failures"] == 0 else 1
    try:
        payload: Any = json.loads(Path(args.input).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"input error: {exc}", file=sys.stderr)
        return 2
    try:
        result = run_workbook_job(payload, args.output, args.idempotency_key)
    except IdempotencyConflict as exc:
        print(f"idempotency error: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result.to_dict(), ensure_ascii=False, default=str, indent=2))
    return 0 if result.status == "accepted" else 2


if __name__ == "__main__":
    raise SystemExit(main())
