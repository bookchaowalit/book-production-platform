import json
from pathlib import Path

import pytest
from document_production.jobs import run_document_job
from document_production.scripts.benchmark import load_cases, run_benchmark

FIXTURE = Path(__file__).parent / "fixtures" / "structured_cases.json"
BRIEF_FIXTURE = Path(__file__).parent / "fixtures" / "brief_cases.json"


def test_structured_acceptance_cases_have_zero_model_calls(tmp_path):
    cases = load_cases(FIXTURE)
    valid = [case for case in cases if case["expected"] == "accepted"]
    assert len(valid) == 20
    results = [run_document_job(case["request"], tmp_path, f"case-{case['request']['document_id']}") for case in valid]
    assert all(result.status == "accepted" for result in results)
    assert all(result.model_calls == 0 for result in results)


def test_boundary_cases_reject_without_accepted_manifest(tmp_path):
    cases = load_cases(FIXTURE)
    invalid = [case for case in cases if case["expected"] == "rejected"]
    assert len(invalid) >= 3
    for index, case in enumerate(invalid):
        result = run_document_job(case["request"], tmp_path, f"invalid-{index}")
        assert result.status == "failed"
        assert result.manifest_path is None


def test_benchmark_reports_expected_statuses_without_hardcoded_case_count(tmp_path):
    report = run_benchmark(FIXTURE, tmp_path / "benchmark")
    assert report["expected_mismatch_count"] == 0


def test_benchmark_distinguishes_fresh_and_replay_runs(tmp_path):
    output = tmp_path / "benchmark"
    fresh = run_benchmark(FIXTURE, output, mode="fresh")
    assert fresh["fresh_count"] == 23
    assert fresh["replay_count"] == 0
    assert fresh["new_artifact_count"] == 40
    assert fresh["replay_miss_count"] == 0

    replay = run_benchmark(FIXTURE, output, mode="replay")
    assert replay["fresh_count"] == 3
    assert replay["replay_count"] == 20
    assert replay["replay_miss_count"] == 3
    assert replay["bytes_written"] == 0
    assert replay["new_artifact_count"] == 0

    with pytest.raises(ValueError, match="empty"):
        run_benchmark(FIXTURE, output, mode="fresh")


def test_brief_benchmark_reports_abstention_without_raw_briefs(tmp_path):
    report = run_benchmark(BRIEF_FIXTURE, tmp_path / "brief-benchmark", mode="brief")

    assert report["mode"] == "brief"
    assert report["case_count"] == 6
    assert report["accepted_count"] == 2
    assert report["needs_input_count"] == 3
    assert report["rejected_count"] == 1
    assert report["abstention_count"] == 3
    assert report["budget_failure_count"] == 0
    assert report["model_calls"] == 0
    assert report["expected_mismatch_count"] == 0
    raw = json.dumps(report, ensure_ascii=False)
    assert "Synthetic customer" not in raw
