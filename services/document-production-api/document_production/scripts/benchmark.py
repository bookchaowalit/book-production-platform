"""Run the deterministic Documents V1A acceptance and cost benchmark."""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any, Literal

from ..jobs import run_document_brief_job, run_document_job


def load_cases(path: str | Path) -> list[dict[str, Any]]:
    raw_cases = json.loads(Path(path).read_text(encoding="utf-8"))
    cases: list[dict[str, Any]] = []
    for raw in raw_cases:
        request = dict(raw["request"])
        if "line_item_repeat" in raw:
            request["line_items"] = request.get("line_items", []) * int(raw["line_item_repeat"])
        cases.append({"id": raw["id"], "expected": raw["expected"], "request": request})
    return cases


def load_brief_cases(path: str | Path) -> list[dict[str, Any]]:
    raw_cases = json.loads(Path(path).read_text(encoding="utf-8"))
    cases: list[dict[str, Any]] = []
    for raw in raw_cases:
        request = dict(raw["request"])
        if "brief_repeat" in raw:
            request["brief"] = str(request.get("brief", "")) * int(raw["brief_repeat"])
        cases.append({"id": raw["id"], "expected": raw["expected"], "request": request})
    return cases


def _percentile(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, round((percentile / 100) * (len(ordered) - 1)))
    return round(ordered[index], 6)


def run_brief_benchmark(fixture: str | Path, output: str | Path) -> dict[str, Any]:
    cases = load_brief_cases(fixture)
    output_path = Path(output)
    if output_path.exists() and any(output_path.iterdir()):
        raise ValueError("brief benchmark output must be empty")
    durations: list[float] = []
    accepted = 0
    needs_input = 0
    rejected = 0
    model_calls = 0
    bytes_written = 0
    cpu_seconds = 0.0
    abstentions = 0
    budget_failures = 0
    expected_mismatch_count = 0
    results: list[dict[str, Any]] = []
    for case in cases:
        started = time.perf_counter()
        result = run_document_brief_job(case["request"], output_path, f"brief-benchmark-{case['id']}")
        durations.append(time.perf_counter() - started)
        status = result.status if result.status in {"accepted", "needs_input"} else "rejected"
        accepted += status == "accepted"
        needs_input += status == "needs_input"
        rejected += status == "rejected"
        abstentions += status == "needs_input"
        model_calls += result.model_calls
        bytes_written += int(result.cost_event["bytes_written"])
        cpu_seconds += float(result.cost_event["cpu_seconds"])
        budget_failures += any(issue.get("code") == "budget_exceeded" for issue in result.qa.get("issues", []))
        expected_mismatch_count += status != case["expected"]
        results.append(
            {
                "id": case["id"],
                "expected": case["expected"],
                "actual": status,
                "model_calls": result.model_calls,
                "missing": result.metadata.get("extraction", {}).get("missing", []),
            }
        )
    return {
        "mode": "brief",
        "case_count": len(cases),
        "accepted_count": accepted,
        "needs_input_count": needs_input,
        "rejected_count": rejected,
        "abstention_count": abstentions,
        "budget_failure_count": budget_failures,
        "p50_latency_seconds": _percentile(durations, 50),
        "p95_latency_seconds": _percentile(durations, 95),
        "cpu_seconds": round(cpu_seconds, 6),
        "bytes_written": bytes_written,
        "model_calls": model_calls,
        "expected_mismatch_count": expected_mismatch_count,
        "cost_status": "unknown",
        "cost_per_accepted_output_usd": None,
        "results": results,
    }


def run_benchmark(fixture: str | Path, output: str | Path, *, mode: Literal["fresh", "replay", "brief"] = "fresh") -> dict[str, Any]:
    if mode == "brief":
        return run_brief_benchmark(fixture, output)
    cases = load_cases(fixture)
    output_path = Path(output)
    if mode not in {"fresh", "replay"}:
        raise ValueError(f"unsupported benchmark mode: {mode}")
    if mode == "fresh" and output_path.exists() and any(output_path.iterdir()):
        raise ValueError("fresh benchmark output must be empty")
    if mode == "replay" and not (output_path / "jobs.sqlite3").exists():
        raise ValueError("replay benchmark requires an existing fresh output")
    durations: list[float] = []
    accepted = 0
    failed = 0
    model_calls = 0
    bytes_written = 0
    cpu_seconds = 0.0
    expected_mismatch_count = 0
    fresh_count = 0
    replay_count = 0
    replay_miss_count = 0
    new_artifact_count = 0
    results: list[dict[str, Any]] = []
    for case in cases:
        started = time.perf_counter()
        result = run_document_job(case["request"], output_path, f"benchmark-{case['id']}")
        durations.append(time.perf_counter() - started)
        accepted += result.status == "accepted"
        failed += result.status != "accepted"
        model_calls += result.model_calls
        bytes_written += int(result.cost_event["bytes_written"])
        cpu_seconds += float(result.cost_event["cpu_seconds"])
        if bool(result.cost_event.get("replayed")):
            replay_count += 1
        else:
            fresh_count += 1
            replay_miss_count += mode == "replay"
            new_artifact_count += len(result.artifacts)
        actual = "accepted" if result.status == "accepted" else "rejected"
        expected_mismatch_count += actual != case["expected"]
        results.append({"id": case["id"], "expected": case["expected"], "actual": actual, "model_calls": result.model_calls})

    return {
        "case_count": len(cases),
        "accepted_count": accepted,
        "failed_count": failed,
        "p50_latency_seconds": _percentile(durations, 50),
        "p95_latency_seconds": _percentile(durations, 95),
        "cpu_seconds": round(cpu_seconds, 6),
        "bytes_written": bytes_written,
        "model_calls": model_calls,
        "mode": mode,
        "fresh_count": fresh_count,
        "replay_count": replay_count,
        "replay_miss_count": replay_miss_count,
        "new_artifact_count": new_artifact_count,
        "expected_mismatch_count": expected_mismatch_count,
        "cost_status": "unknown",
        "cost_per_accepted_output_usd": None,
        "results": results,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="documents-benchmark")
    parser.add_argument("--fixture", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--mode", choices=("fresh", "replay", "brief"), default="fresh")
    parser.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    args = parser.parse_args(argv)
    report = run_benchmark(args.fixture, args.output, mode=args.mode)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(json.dumps({key: value for key, value in report.items() if key != "results"}, ensure_ascii=False, indent=2))
    return 0 if report["expected_mismatch_count"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
