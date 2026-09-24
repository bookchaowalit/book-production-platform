"""Integration checks for the shared platform's family command router."""

from __future__ import annotations

import json
from pathlib import Path

import book_production_platform.cli as platform_cli
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


def test_platform_cli_routes_game_commands(monkeypatch, capsys) -> None:
    received: list[list[str]] = []

    def fake_game_main(argv: list[str]) -> int:
        received.append(argv)
        return 7

    monkeypatch.setattr(platform_cli, "game_main", fake_game_main)
    args = ["build", "--game", "room.json", "--output-root", "jobs", "--unity", "Unity.exe"]

    exit_code = main(["game", *args])

    assert exit_code == 7
    assert received == [args]
    assert capsys.readouterr().out == ""
