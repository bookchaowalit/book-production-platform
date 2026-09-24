import json
from pathlib import Path

from book_production_platform.spreadsheet_production.scripts.benchmark import (
    run_benchmark,
)


def test_twenty_workbook_benchmark_matches_golden_and_reopens(tmp_path: Path) -> None:
    fixture = Path(__file__).parent / "fixtures" / "workbooks_20.json"

    summary = run_benchmark(fixture, tmp_path)

    assert summary["total"] == 20
    assert summary["accepted"] == 20
    assert summary["rejected"] == 0
    assert summary["formula_mismatches"] == 0
    assert summary["reopen_failures"] == 0
    assert summary["model_calls"] == 0
    assert len(list(tmp_path.glob("job-*/workbook.xlsx"))) == 20
    assert json.loads(fixture.read_text(encoding="utf-8"))["items"][0]["tables"]
