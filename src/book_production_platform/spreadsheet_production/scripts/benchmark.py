"""Synthetic 20-workbook benchmark for Spreadsheet V1A."""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from decimal import Decimal
from pathlib import Path
from typing import Any

from ..formulas import evaluate_workbook
from ..jobs import run_workbook_job
from ..recipe import build_workbook_ir


def run_benchmark(fixture_path: str | Path, output_dir: str | Path) -> dict[str, Any]:
    fixture = json.loads(Path(fixture_path).read_text(encoding="utf-8"))
    items = fixture.get("items") if isinstance(fixture, dict) else fixture
    if not isinstance(items, list) or not all(isinstance(item, dict) for item in items):
        raise ValueError("fixture must contain an items array of objects")
    accepted = 0
    rejected = 0
    formula_mismatches = 0
    reopen_failures = 0
    bytes_written = 0
    for index, item in enumerate(items):
        result = run_workbook_job(item, output_dir, f"spreadsheet-v1a-benchmark-{index + 1}")
        bytes_written += result.bytes_written
        if result.status != "accepted":
            rejected += 1
            continue
        accepted += 1
        if result.qa:
            reopen_failures += 1
        ir = build_workbook_ir(item)
        values = evaluate_workbook(ir).values
        rows = item["tables"][0]["rows"]
        expected_total = sum((Decimal(str(row.get("amount") or "0")) for row in rows), Decimal(0))
        if values.get("Summary!B4") != expected_total:
            formula_mismatches += 1
    return {
        "total": len(items),
        "accepted": accepted,
        "rejected": rejected,
        "formula_mismatches": formula_mismatches,
        "reopen_failures": reopen_failures,
        "model_calls": 0,
        "bytes_written": bytes_written,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="spreadsheet-production-benchmark")
    parser.add_argument("--fixture", required=True)
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args(argv)
    summary = run_benchmark(args.fixture, args.output_dir)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary["rejected"] == 0 and summary["formula_mismatches"] == 0 and summary["reopen_failures"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
