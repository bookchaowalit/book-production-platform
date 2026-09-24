from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from book_production_platform.data_pipeline.cli import main as data_main
from book_production_platform.data_pipeline.jobs import run_pipeline_job
from book_production_platform.data_pipeline.main import app
from book_production_platform.data_pipeline.scripts import pipeline_benchmark

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "pipelines_20.json"
SQL_SHAPED_VALUE = "north'; DROP TABLE synthetic_orders; --"


def _cases() -> list[dict[str, Any]]:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def test_benchmark_fixture_contains_twelve_accepts_and_eight_rejections() -> None:
    cases = _cases()
    expected_ids = {
        "select",
        "filter-text",
        "filter-null",
        "sort",
        "sort-limit",
        "count-rows",
        "count-column",
        "sum-decimal",
        "avg-decimal",
        "union-compatible",
        "sql-shaped-filter",
        "typed-date-decimal-filters",
        "cycle",
        "unknown-parent",
        "dead-node",
        "unknown-column",
        "filter-type-mismatch",
        "sum-text",
        "union-schema-mismatch",
        "limit-without-sort",
    }
    first_source = cases[0]["request"]["source"]
    expected_columns = [
        ("region", "text"),
        ("amount", "decimal"),
        ("created", "date"),
        ("active", "boolean"),
        ("units", "integer"),
    ]

    assert len(cases) == 20
    assert len({case["id"] for case in cases}) == 20
    assert {case["id"] for case in cases} == expected_ids
    assert all(case["request"]["source"] == first_source for case in cases)
    assert [(column["key"], column["data_type"]) for column in first_source["columns"]] == expected_columns
    assert all(isinstance(case.get("request"), dict) for case in cases)
    assert sum(case["expected_status"] == "accepted" for case in cases) == 12
    assert sum(case["expected_status"] == "rejected" for case in cases) == 8


def test_pipeline_benchmark_matches_all_expected_outcomes(tmp_path: Path) -> None:
    summary = pipeline_benchmark.run_pipeline_benchmark(FIXTURE_PATH, tmp_path)

    assert summary["total"] == 20
    assert summary["accepted"] == 12
    assert summary["rejected"] == 8
    assert summary["unexpected"] == 0
    assert summary["query_plan_failures"] == 0
    assert summary["lineage_failures"] == 0
    assert summary["replay_hits"] == 12
    assert summary["model_calls"] == 0
    assert summary["bytes_written"] > 0


def test_pipeline_benchmark_refuses_a_nonempty_output_root(tmp_path: Path) -> None:
    (tmp_path / "keep.txt").write_text("existing", encoding="utf-8")

    with pytest.raises(ValueError, match="benchmark_output_directory_not_empty"):
        pipeline_benchmark.run_pipeline_benchmark(FIXTURE_PATH, tmp_path)


def test_benchmark_cli_fails_when_an_acceptance_quality_gate_fails(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    failed_summary = {
        "total": 20,
        "accepted": 12,
        "rejected": 8,
        "unexpected": 0,
        "query_plan_failures": 1,
        "lineage_failures": 0,
        "replay_hits": 12,
        "model_calls": 0,
        "bytes_written": 100,
    }
    monkeypatch.setattr(
        "book_production_platform.data_pipeline.scripts.pipeline_benchmark.run_pipeline_benchmark",
        lambda *_args: failed_summary,
    )

    code = pipeline_benchmark.main(["--fixtures", str(FIXTURE_PATH), "--output-root", str(tmp_path / "out")])

    assert code == 1
    assert json.loads(capsys.readouterr().out) == failed_summary


def test_sql_shaped_filter_is_kept_only_in_private_parameters_and_not_responses(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = next(case for case in _cases() if case["id"] == "sql-shaped-filter")
    request = case["request"]
    direct = run_pipeline_job(request, tmp_path / "direct")
    assert direct.artifact_dir is not None
    artifacts = {
        name: (direct.artifact_dir / name).read_text(encoding="utf-8")
        for name in ("pipeline.sql", "parameters.json", "report.json", "qa-manifest.json")
    }
    assert SQL_SHAPED_VALUE in artifacts["parameters.json"]
    assert all(SQL_SHAPED_VALUE not in content for name, content in artifacts.items() if name != "parameters.json")

    input_path = tmp_path / "sql-shaped-request.json"
    input_path.write_text(json.dumps(request), encoding="utf-8")
    cli_root = tmp_path / "cli-output"
    code = data_main(["pipeline", "build", "--input", str(input_path), "--output-root", str(cli_root)])
    cli_response = capsys.readouterr().out
    assert code == 0
    assert SQL_SHAPED_VALUE not in cli_response

    monkeypatch.setenv("DATA_PIPELINE_OUTPUT_ROOT", str(tmp_path / "api-output"))
    api_response = TestClient(app).post("/v1/data/pipelines", json={"pipeline": request})
    assert api_response.status_code == 200
    assert SQL_SHAPED_VALUE not in api_response.text

    summary = pipeline_benchmark.run_pipeline_benchmark(FIXTURE_PATH, tmp_path / "benchmark-output")
    assert SQL_SHAPED_VALUE not in json.dumps(summary)
    assert "synthetic.orders.v1" not in json.dumps(summary)
