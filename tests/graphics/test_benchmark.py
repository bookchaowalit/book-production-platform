import json
from pathlib import Path

from book_production_platform.graphics_production.scripts.benchmark import run_benchmark
from tests.graphics.browser import requires_browser


@requires_browser
def test_batch_30_benchmark_has_zero_model_calls_and_complete_outputs(tmp_path: Path) -> None:
    fixture = Path(__file__).parent / "fixtures" / "batch_30.json"

    summary = run_benchmark(fixture, tmp_path)

    assert summary["total"] == 30
    assert summary["accepted"] == 30
    assert summary["rejected"] == 0
    assert summary["model_calls"] == 0
    assert summary["unsafe_svg_issues"] == 0
    manifest = json.loads((tmp_path / summary["manifest"]).read_text(encoding="utf-8"))
    assert len(manifest["items"]) == 30
    assert all(len(item["artifacts"]) == 2 for item in manifest["items"])
