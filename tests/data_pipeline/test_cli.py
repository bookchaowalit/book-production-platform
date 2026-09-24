from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import book_production_platform.cli as platform_cli
from book_production_platform.cli import main


def test_pipeline_cli_writes_a_local_plan_and_prints_metadata_only(
    tmp_path: Path,
    capsys,
    valid_pipeline_request: dict[str, Any],
) -> None:
    source = tmp_path / "pipeline.json"
    source.write_text(json.dumps(valid_pipeline_request), encoding="utf-8")
    output_root = tmp_path / "out"

    code = main(
        [
            "data",
            "pipeline",
            "build",
            "--input",
            str(source),
            "--output-root",
            str(output_root),
        ]
    )
    output = capsys.readouterr().out
    result = json.loads(output)

    assert code == 0
    assert result["status"] == "accepted"
    assert result["model_calls"] == 0
    assert result["artifact_names"] == ["parameters.json", "pipeline.sql", "qa-manifest.json", "report.json"]
    assert "BKK" not in output and "warehouse.orders" not in output
    assert (Path(result["artifact_dir"]) / "pipeline.sql").is_file()


def test_pipeline_cli_rejects_oversized_input_without_echoing_bytes(tmp_path: Path, capsys) -> None:
    source = tmp_path / "large.json"
    source.write_bytes(b"x" * 262_145)

    code = main(["data", "pipeline", "build", "--input", str(source), "--output-root", str(tmp_path / "out")])
    output = capsys.readouterr().out

    assert code == 2
    assert json.loads(output) == {"status": "rejected", "error": "pipeline_request_too_large"}
    assert "x" * 100 not in output
    assert not (tmp_path / "out" / "jobs").exists()


def test_pipeline_cli_maps_malformed_input_to_stable_code(tmp_path: Path, capsys) -> None:
    source = tmp_path / "bad.json"
    source.write_text('{"pipeline":"sensitive"}', encoding="utf-8")

    code = main(["data", "pipeline", "build", "--input", str(source), "--output-root", str(tmp_path / "out")])
    output = capsys.readouterr().out

    assert code == 2
    assert json.loads(output) == {"status": "rejected", "error": "invalid_pipeline_request"}
    assert "sensitive" not in output


def test_pipeline_cli_maps_io_errors_without_echoing_paths(tmp_path: Path, capsys) -> None:
    missing = tmp_path / "missing-secret.json"

    code = main(["data", "pipeline", "build", "--input", str(missing), "--output-root", str(tmp_path / "out")])
    output = capsys.readouterr().out

    assert code == 2
    assert json.loads(output) == {"status": "rejected", "error": "cli_io_error"}
    assert str(missing) not in output


def test_platform_cli_routes_data_commands(monkeypatch, capsys) -> None:
    received: list[list[str]] = []

    def fake_data_main(argv: list[str]) -> int:
        received.append(argv)
        return 6

    monkeypatch.setattr(platform_cli, "data_main", fake_data_main)
    args = ["pipeline", "build", "--input", "request.json", "--output-root", "jobs"]

    code = main(["data", *args])

    assert code == 6
    assert received == [args]
    assert capsys.readouterr().out == ""
