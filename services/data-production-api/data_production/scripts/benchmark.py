"""Synthetic 20-job benchmark for Data + Data Viz V1A."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from ..jobs import run_data_job


def run_benchmark(fixture_path: str | Path, output_dir: str | Path) -> dict[str, int]:
    fixture = json.loads(Path(fixture_path).read_text(encoding="utf-8"))
    cases = fixture["cases"]
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    if any(root.iterdir()):
        raise ValueError("benchmark_output_dir_must_be_empty")
    summary = {"total": len(cases), "accepted": 0, "rejected": 0, "aggregation_mismatches": 0, "reopen_failures": 0, "model_calls": 0, "bytes_written": 0}
    for index, case in enumerate(cases, start=1):
        result = run_data_job(case["request"], root, f"data-v1a-benchmark-{index}")
        expected_status = case.get("expected_status", "accepted")
        if result.status == "accepted":
            summary["accepted"] += 1
            summary["bytes_written"] += result.bytes_written
            report_path = root / f"job-{hashlib.sha256(f'data-v1a-benchmark-{index}'.encode()).hexdigest()[:16]}" / "report.json"
            try:
                report = json.loads(report_path.read_text(encoding="utf-8"))
                expected_total = case.get("expected_total")
                if expected_total is not None and report.get("chart", {}).get("aggregate_total") != expected_total:
                    summary["aggregation_mismatches"] += 1
                if not report_path.exists() or report.get("rows") is None:
                    summary["reopen_failures"] += 1
            except (OSError, json.JSONDecodeError, TypeError):
                summary["reopen_failures"] += 1
        else:
            summary["rejected"] += 1
        summary["model_calls"] += result.model_calls
        if (result.status == "accepted") != (expected_status == "accepted"):
            summary["reopen_failures"] += 1
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(prog="data-production-benchmark")
    parser.add_argument("--fixture", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()
    print(json.dumps(run_benchmark(args.fixture, args.output_dir), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
