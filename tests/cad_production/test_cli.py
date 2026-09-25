from __future__ import annotations

import json
from pathlib import Path

import book_production_platform.cli as platform_cli
from book_production_platform.cad_production import cli


def _valid_part_ir(path: Path) -> Path:
    path.write_text(
        json.dumps(
            {
                "schema_version": "part-ir.v1",
                "part_id": "cli-plate",
                "template": "mounting-plate.v1",
                "units": "mm",
                "parameters": {
                    "width_mm": "137",
                    "height_mm": "83",
                    "thickness_mm": "7.5",
                    "hole_diameter_mm": "8",
                    "hole_offset_x_mm": "13",
                    "hole_offset_y_mm": "17",
                },
                "hole_pattern": "four-corner.v1",
            }
        ),
        encoding="utf-8",
    )
    return path


def test_help_is_available_without_starting_freecad(capsys) -> None:
    assert cli.main(["--help"]) == 0
    output = capsys.readouterr().out
    assert "build" in output and "benchmark" in output


def test_missing_arguments_emit_stable_json(capsys) -> None:
    assert cli.main(["build", "--input", "part.json"]) == 2
    assert json.loads(capsys.readouterr().out) == {
        "status": "rejected",
        "error": "invalid_cli_arguments",
    }


def test_top_level_cad_cli_delegates_arguments(monkeypatch, capsys) -> None:
    received: list[list[str]] = []

    def fake_cad_main(argv: list[str]) -> int:
        received.append(argv)
        return 7

    monkeypatch.setattr(platform_cli, "cad_main", fake_cad_main)
    arguments = ["build", "--input", "part.json", "--output-root", "jobs"]

    assert platform_cli.main(["cad", *arguments]) == 7
    assert received == [arguments]
    assert capsys.readouterr().out == ""


def test_unsupported_freecad_runtime_has_stable_error_json(tmp_path: Path, capsys) -> None:
    source = _valid_part_ir(tmp_path / "part.json")

    code = cli.main(
        [
            "build",
            "--input",
            str(source),
            "--output-root",
            str(tmp_path / "jobs"),
            "--freecadcmd",
            str(tmp_path / "FreeCADCmd.exe"),
        ]
    )

    output = capsys.readouterr().out
    assert code == 2
    assert json.loads(output) == {"status": "rejected", "error": "freecad_unavailable"}
    assert str(tmp_path) not in output


def test_accepted_build_emits_job_metadata_and_zero_model_calls(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    source = _valid_part_ir(tmp_path / "part.json")
    artifact_dir = tmp_path / "jobs" / ("a" * 64)
    result = {
        "fingerprint": "a" * 64,
        "status": "accepted",
        "artifact_dir": str(artifact_dir),
        "artifact_names": [
            "part.FCStd",
            "part.step",
            "part.stl",
            "preview.png",
            "qa-manifest.json",
        ],
        "artifact_hashes": {},
        "qa": {"bore_count": 4, "solid_count": 1},
        "replay": False,
        "bytes_written": 4096,
        "model_calls": 0,
        "error_code": None,
    }
    calls: list[tuple[Path, Path, Path, int]] = []

    class FakeJobResult:
        status = "accepted"

        @staticmethod
        def to_dict() -> dict[str, object]:
            return result

    def fake_build(
        input_path: Path, output_root: Path, freecadcmd: Path, timeout_s: int
    ) -> FakeJobResult:
        calls.append((input_path, output_root, freecadcmd, timeout_s))
        return FakeJobResult()

    monkeypatch.setattr(cli, "build_cad_job", fake_build)
    output_root = tmp_path / "jobs"
    freecadcmd = tmp_path / "FreeCADCmd.exe"

    code = cli.main(
        [
            "build",
            "--input",
            str(source),
            "--output-root",
            str(output_root),
            "--freecadcmd",
            str(freecadcmd),
            "--timeout-s",
            "45",
        ]
    )

    assert code == 0
    assert json.loads(capsys.readouterr().out) == result
    assert calls == [(source, output_root, freecadcmd, 45)]


def test_oversized_cli_input_is_rejected_before_engine_call(tmp_path: Path, monkeypatch, capsys) -> None:
    source = tmp_path / "large.json"
    source.write_bytes(b" " * (64 * 1024 + 1))

    def unexpected_build(*args, **kwargs):
        raise AssertionError("oversized PartIR must not reach FreeCAD")

    monkeypatch.setattr(cli, "build_cad_job", unexpected_build)
    code = cli.main(
        [
            "build",
            "--input",
            str(source),
            "--output-root",
            str(tmp_path / "jobs"),
            "--freecadcmd",
            "FreeCADCmd.exe",
        ]
    )

    output = capsys.readouterr().out
    assert code == 2
    assert json.loads(output) == {"status": "rejected", "error": "invalid_part_ir"}
    assert str(tmp_path) not in output
