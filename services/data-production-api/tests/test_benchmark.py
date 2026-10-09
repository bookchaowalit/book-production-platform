from __future__ import annotations

from pathlib import Path

from data_production.scripts.benchmark import run_benchmark


def test_benchmark_fixture_has_expected_quality(tmp_path: Path) -> None:
    summary = run_benchmark(Path(__file__).parent / "fixtures" / "datasets_20.json", tmp_path / "benchmark")
    assert summary["total"] == 20
    assert summary["accepted"] == 17
    assert summary["rejected"] == 3
    assert summary["aggregation_mismatches"] == 0
    assert summary["reopen_failures"] == 0
    assert summary["model_calls"] == 0


def test_benchmark_refuses_nonempty_output_directory(tmp_path: Path) -> None:
    output = tmp_path / "existing"
    output.mkdir()
    (output / "keep.txt").write_text("keep", encoding="utf-8")
    try:
        run_benchmark(Path(__file__).parent / "fixtures" / "datasets_20.json", output)
    except ValueError as exc:
        assert str(exc) == "benchmark_output_dir_must_be_empty"
    else:
        raise AssertionError("benchmark overwrote a non-empty output directory")
