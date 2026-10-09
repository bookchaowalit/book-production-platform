from __future__ import annotations

from pathlib import Path

from marketing_production.scripts.benchmark import run_benchmark


def test_twenty_campaign_benchmark_has_zero_model_calls(tmp_path: Path) -> None:
    fixture = Path(__file__).parent / "fixtures" / "campaigns_20.json"

    metrics = run_benchmark(fixture, tmp_path / "out")

    assert metrics["total"] == 20
    assert metrics["accepted"] == 20
    assert metrics["rejected"] == 0
    assert metrics["model_calls"] == 0
    assert metrics["replay_hits"] == 20
    assert metrics["lineage_failures"] == 0
