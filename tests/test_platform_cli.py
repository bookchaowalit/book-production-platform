"""Integration checks for the shared platform's family command router."""

from __future__ import annotations

import json
from pathlib import Path

from book_production_platform.cli import main


def test_platform_cli_routes_spreadsheet_build(tmp_path: Path, capsys: object) -> None:
    root = Path(__file__).resolve().parents[1]
    input_path = root / "examples" / "spreadsheet" / "workbook.json"
    output_path = tmp_path / "workbooks"

    exit_code = main(
        [
            "spreadsheet",
            "build",
            "--input",
            str(input_path),
            "--output",
            str(output_path),
            "--idempotency-key",
            "platform-cli-test",
        ]
    )

    result = json.loads(capsys.readouterr().out)  # type: ignore[attr-defined]
    assert exit_code == 0
    assert result["status"] == "accepted"
    assert result["model_calls"] == 0
    manifest_path = Path(result["manifest_path"])
    assert (manifest_path.parent / "workbook.xlsx").is_file()
