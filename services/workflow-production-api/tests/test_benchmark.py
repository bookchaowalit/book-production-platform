from __future__ import annotations

from pathlib import Path

from workflow_production.scripts.benchmark import run_benchmark


def test_twenty_workflow_benchmark_is_deterministic(tmp_path: Path) -> None:
    fixture = Path(__file__).parent / "fixtures" / "workflows_20.json"

    metrics = run_benchmark(fixture, tmp_path / "out")

    assert metrics == {"total": 20, "accepted": 20, "rejected": 0, "model_calls": 0, "replay_hits": 20, "qa_failures": 0}
