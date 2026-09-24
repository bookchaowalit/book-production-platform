"""Run the synthetic, plan-only acceptance benchmark for Data Pipeline V1A."""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from book_production_platform.data_pipeline.jobs import (
    PipelineJobResult,
    run_pipeline_job,
)
from book_production_platform.data_pipeline_ir.models import PipelineRequest

MAX_FIXTURE_BYTES = 1_048_576
EXPECTED_CASES = 20
EXPECTED_ACCEPTED = 12
EXPECTED_REJECTED = 8
SUMMARY_KEYS = (
    "total",
    "accepted",
    "rejected",
    "unexpected",
    "query_plan_failures",
    "lineage_failures",
    "replay_hits",
    "model_calls",
    "bytes_written",
)


def _load_cases(fixture_path: Path) -> list[dict[str, Any]]:
    try:
        if fixture_path.is_symlink():
            raise ValueError("invalid_pipeline_benchmark_fixture")
        with fixture_path.open("rb") as fixture:
            raw_fixture = fixture.read(MAX_FIXTURE_BYTES + 1)
        if len(raw_fixture) > MAX_FIXTURE_BYTES:
            raise ValueError("invalid_pipeline_benchmark_fixture")
        parsed = json.loads(raw_fixture.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, RecursionError):
        raise ValueError("invalid_pipeline_benchmark_fixture") from None
    if not isinstance(parsed, list) or len(parsed) != EXPECTED_CASES:
        raise ValueError("invalid_pipeline_benchmark_fixture")

    seen: set[str] = set()
    cases: list[dict[str, Any]] = []
    for case in parsed:
        if not isinstance(case, dict) or set(case) != {"id", "request", "expected_status"}:
            raise ValueError("invalid_pipeline_benchmark_fixture")
        case_id = case["id"]
        request = case["request"]
        expected_status = case["expected_status"]
        if (
            not isinstance(case_id, str)
            or not case_id
            or case_id in seen
            or not isinstance(request, dict)
            or not isinstance(expected_status, str)
            or expected_status not in {"accepted", "rejected"}
        ):
            raise ValueError("invalid_pipeline_benchmark_fixture")
        try:
            case["request"] = PipelineRequest.model_validate_json(
                json.dumps(request, ensure_ascii=False, allow_nan=False)
            )
        except (TypeError, ValueError, RecursionError):
            raise ValueError("invalid_pipeline_benchmark_fixture") from None
        seen.add(case_id)
        cases.append(case)
    if len(seen) != EXPECTED_CASES:
        raise ValueError("invalid_pipeline_benchmark_fixture")
    if sum(case["expected_status"] == "accepted" for case in cases) != EXPECTED_ACCEPTED:
        raise ValueError("invalid_pipeline_benchmark_fixture")
    if sum(case["expected_status"] == "rejected" for case in cases) != EXPECTED_REJECTED:
        raise ValueError("invalid_pipeline_benchmark_fixture")
    return cases


def _ensure_empty_output_root(output_root: Path) -> None:
    try:
        if output_root.is_symlink():
            raise ValueError("benchmark_output_directory_unavailable")
        if output_root.exists() and not output_root.is_dir():
            raise ValueError("benchmark_output_directory_unavailable")
        if output_root.exists() and any(output_root.iterdir()):
            raise ValueError("benchmark_output_directory_not_empty")
    except ValueError:
        raise
    except OSError:
        raise ValueError("benchmark_output_directory_unavailable") from None


def _opened_report(result: PipelineJobResult) -> dict[str, Any] | None:
    if result.artifact_dir is None or result.artifact_dir.is_symlink():
        return None
    path = result.artifact_dir / "report.json"
    try:
        if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_FIXTURE_BYTES:
            return None
        report = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, RecursionError):
        return None
    return report if isinstance(report, dict) else None


def _has_output_lineage(report: dict[str, Any]) -> bool:
    output_schema = report.get("output_schema")
    lineage = report.get("output_lineage")
    if not isinstance(output_schema, list) or not output_schema or not isinstance(lineage, dict):
        return False
    output_keys = {column.get("key") for column in output_schema if isinstance(column, dict)}
    if len(output_keys) != len(output_schema) or None in output_keys or set(lineage) != output_keys:
        return False
    for references in lineage.values():
        if not isinstance(references, list) or not references:
            return False
        for reference in references:
            if not isinstance(reference, dict):
                return False
            if not isinstance(reference.get("source_ref"), str) or not isinstance(reference.get("table_name"), str):
                return False
            if reference.get("column_key") is not None and not isinstance(reference.get("column_key"), str):
                return False
    return True


def run_pipeline_benchmark(fixture_path: Path, output_root: Path) -> dict[str, int]:
    """Run all fixture cases, reopen accepted reports, and replay accepted plans.

    The returned metadata contains aggregate counts only. Source references,
    query parameters, SQL, artifact paths, and request values stay in the
    private job artifacts and are never copied into the benchmark summary.
    """
    cases = _load_cases(fixture_path)
    _ensure_empty_output_root(output_root)
    summary = {key: 0 for key in SUMMARY_KEYS}
    summary["total"] = len(cases)

    for case in cases:
        result = run_pipeline_job(case["request"], output_root)
        summary[result.status] += 1 if result.status in {"accepted", "rejected"} else 0
        summary["unexpected"] += int(result.status != case["expected_status"])
        summary["model_calls"] += result.model_calls
        summary["bytes_written"] += result.bytes_written

        if result.status != "accepted":
            continue
        report = _opened_report(result)
        plan_verified = result.metadata.get("query_plan_verified") is True
        query_plan = report.get("query_plan") if report is not None else None
        if not plan_verified or not isinstance(query_plan, list) or not query_plan:
            summary["query_plan_failures"] += 1
        if report is None or not _has_output_lineage(report):
            summary["lineage_failures"] += 1

        try:
            replay = run_pipeline_job(case["request"], output_root)
        except (OSError, ValueError):
            summary["unexpected"] += 1
            continue
        summary["model_calls"] += replay.model_calls
        if replay.status == "accepted" and replay.replay and replay.bytes_written == 0:
            summary["replay_hits"] += 1
        else:
            summary["unexpected"] += 1

    return summary


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="book-production data benchmark")
    parser.add_argument("--fixtures", required=True, type=Path)
    parser.add_argument("--output-root", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        summary = run_pipeline_benchmark(args.fixtures, args.output_root)
    except ValueError as error:
        code = error.args[0] if error.args and isinstance(error.args[0], str) else "benchmark_failed"
        print(json.dumps({"status": "rejected", "error": code}, sort_keys=True))
        return 2
    print(json.dumps(summary, sort_keys=True))
    passed = (
        summary["accepted"] == EXPECTED_ACCEPTED
        and summary["rejected"] == EXPECTED_REJECTED
        and summary["unexpected"] == 0
        and summary["query_plan_failures"] == 0
        and summary["lineage_failures"] == 0
        and summary["replay_hits"] == EXPECTED_ACCEPTED
        and summary["model_calls"] == 0
        and summary["accepted"] + summary["rejected"] == summary["total"]
    )
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["main", "run_pipeline_benchmark"]
