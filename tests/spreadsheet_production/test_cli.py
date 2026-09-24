import json
from pathlib import Path

from test_recipe import request

from book_production_platform.spreadsheet_production.cli import main


def test_cli_build_prints_json_summary(tmp_path: Path, capsys: object) -> None:
    input_path = tmp_path / "workbook.json"
    input_path.write_text(json.dumps(request(), ensure_ascii=False, default=str), encoding="utf-8")

    exit_code = main(["build", "--input", str(input_path), "--output", str(tmp_path / "out"), "--idempotency-key", "cli-1"])

    assert exit_code == 0
    assert json.loads(capsys.readouterr().out)["status"] == "accepted"  # type: ignore[attr-defined]
