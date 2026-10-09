"""CLI for the deterministic cross-tool production pipelines.

Run from the service directory (or with the service on ``PYTHONPATH``):

.. code-block:: text

    python -m production_pipelines.cli monthly_report --config job.json --output .runtime/monthly-report

Exit codes: ``0`` published, ``2`` input or validation error, ``3`` stage
failure, ``4`` boundary contract violation, ``5`` output already exists.
Failures print one stable JSON error object to stderr and publish nothing.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from .runner import EXIT_OK, run_monthly_report


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="production-pipelines")
    commands = parser.add_subparsers(dest="command", required=True)
    monthly = commands.add_parser("monthly_report", help="run the T6.3 monthly report chain end to end")
    monthly.add_argument("--config", required=True, help="path to a pipeline-report-config.v1 JSON job file")
    monthly.add_argument("--output", required=True, help="run output directory (must not already exist)")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "monthly_report":
        result = run_monthly_report(args.config, Path(args.output))
        if result.exit_code == EXIT_OK:
            print(result.manifest_path)
            return EXIT_OK
        payload = {"command": "monthly_report", **(result.error or {"error": "unknown"})}
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True), file=sys.stderr)
        return result.exit_code
    print(f"unknown command: {args.command}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
