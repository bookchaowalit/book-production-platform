from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from typing import Any

import pytest

from book_production_platform.bim_production import cli as bim_cli
from book_production_platform.bim_production.jobs import BimJobResult


def _job_result(status: str) -> BimJobResult:
    return BimJobResult(
        status=status,
        fingerprint="e" * 64,
        error_code=None if status != "rejected" else "worker_failed",
        row_count=1,
        coverage_gap_count=0,
        model_calls=0,
    )


def _run(capsys: pytest.CaptureFixture[str], argv: list[str]) -> tuple[int, str]:
    exit_code = bim_cli.main(argv)
    captured = capsys.readouterr()
    return exit_code, captured.out.strip()


def test_module_exposes_a_stable_main_entry_point() -> None:
    spec = importlib.util.find_spec("book_production_platform.bim_production.cli")
    assert spec is not None, "the BIM domain CLI must exist for platform dispatch"


def test_takeoff_maps_statuses_to_documented_exit_codes(
    capsys: pytest.CaptureFixture[str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    documented = {"complete": 0, "replayed": 0, "partial": 3, "needs_input": 4}
    for status, expected_exit in documented.items():
        monkeypatch.setattr(
            bim_cli,
            "build_bim_takeoff_job",
            lambda *a, bound=status, **k: _job_result(bound),
        )
        exit_code, output = _run(
            capsys,
            [
                "takeoff",
                "--ir", str(tmp_path / "ir.bimreq"),
                "--ifc", str(tmp_path / "m.ifc"),
                "--output-root", str(tmp_path / "out"),
                "--freecadcmd", str(tmp_path / "FreeCADCmd.exe"),
                "--ifc-site-packages", str(tmp_path / "sp"),
            ],
        )
        assert exit_code == expected_exit
        payload = json.loads(output)
        assert payload["status"] == status
        assert payload["model_calls"] == 0


def test_rejected_jobs_exit_two_with_stable_error_json(
    capsys: pytest.CaptureFixture[str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(bim_cli, "build_bim_takeoff_job", _raising)
    exit_code, output = _run(
        capsys,
        [
            "takeoff",
            "--ir", str(tmp_path / "ir.bimreq"),
            "--ifc", str(tmp_path / "m.ifc"),
            "--output-root", str(tmp_path / "out"),
            "--freecadcmd", str(tmp_path / "FreeCADCmd.exe"),
            "--ifc-site-packages", str(tmp_path / "sp"),
        ],
    )
    assert exit_code == 2
    assert json.loads(output) == {"status": "rejected", "error_code": "ifc_parse_invalid"}


def _raising(*args: object, **kwargs: object) -> BimJobResult:
    from book_production_platform.bim_production.errors import BimProductionError

    raise BimProductionError("ifc_parse_invalid")


@pytest.mark.parametrize(
    "argv",
    [
        [],
        ["takeoff"],
        ["takeoff", "--ir"],
        ["takeoff", "--ir", "x", "--ifc", "y"],
        ["unknown"],
    ],
)
def test_malformed_arguments_exit_two(
    capsys: pytest.CaptureFixture[str], argv: list[str]
) -> None:
    exit_code, output = _run(capsys, argv)

    assert exit_code == 2
    assert json.loads(output) == {"status": "rejected", "error_code": "invalid_arguments"}


def test_help_exits_zero_without_running_a_worker(
    capsys: pytest.CaptureFixture[str],
) -> None:
    exit_code, output = _run(capsys, ["--help"])

    assert exit_code == 0
    assert "takeoff" in output


def test_invalid_timeout_is_rejected(capsys: pytest.CaptureFixture[str]) -> None:
    exit_code, output = _run(
        capsys,
        [
            "takeoff",
            "--ir", "x",
            "--ifc", "y",
            "--output-root", "z",
            "--freecadcmd", "f",
            "--ifc-site-packages", "s",
            "--timeout-s", "500",
        ],
    )

    assert exit_code == 2
    payload: dict[str, Any] = json.loads(output)
    assert payload["status"] == "rejected"


def test_platform_cli_delegates_bim_arguments(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    import book_production_platform.cli as platform_cli

    seen: list[list[str]] = []

    def fake_bim_main(argv: list[str] | None = None) -> int:
        seen.append(list(argv or []))
        return 0

    monkeypatch.setattr(platform_cli, "bim_main", fake_bim_main)
    exit_code = platform_cli.main(["bim", "takeoff", "--ir", "x"])

    assert exit_code == 0
    assert seen == [["takeoff", "--ir", "x"]]
