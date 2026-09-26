"""Unit checks for the doctor runtime readiness command with injected fakes."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

from book_production_platform import doctor
from book_production_platform.bim_production.errors import BimProductionError
from book_production_platform.cli import main


def _fake_lock() -> dict[str, Any]:
    return {
        "schema_version": "bim.ifcopenshell-runtime.v1",
        "freecad_version": "FreeCAD 1.1.3",
        "freecad_build": "Revision: 20260725 (Git shallow)",
        "freecad_platform": "windows-x86_64",
        "freecadcmd_sha256": "0" * 64,
        "python_version": "3.11",
        "ifcopenshell_version": "0.8.4",
        "wrapper_relative_path": "ifcopenshell/_ifcopenshell_wrapper.cp311-win_amd64.pyd",
        "wrapper_sha256": "0" * 64,
        "worker_protocol": "bim-worker.v1",
    }


def _broken_lock() -> dict[str, Any]:
    raise BimProductionError("unsupported_ifcopenshell_runtime")


def _patch_all_missing(monkeypatch) -> None:
    monkeypatch.setattr(doctor, "_resolve_executable", lambda env_key, name: None)
    monkeypatch.setattr(
        doctor, "default_blender", lambda: Path("Z:\\nonexistent\\blender.exe")
    )
    monkeypatch.setattr(doctor, "_pinned_freecadcmd_default", lambda lock: None)


def _empty_binary(tmp_path: Path, name: str) -> Path:
    binary = tmp_path / name
    binary.write_bytes(b"")
    return binary


def test_doctor_reports_missing_runtimes_as_ok(monkeypatch, capsys) -> None:
    _patch_all_missing(monkeypatch)
    monkeypatch.setattr(doctor, "_load_runtime_lock", _fake_lock)

    exit_code = doctor.run_doctor("json")
    report = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert report["status"] == "ok"
    assert report["runtimes"]["python"]["status"] == "found"
    for key in ("freecadcmd", "ffmpeg", "blender", "unity"):
        assert report["runtimes"][key]["status"] == "missing"
    assert report["products"]["bim"] == {
        "ready": False,
        "required": ["freecadcmd"],
        "missing": ["freecadcmd"],
    }
    assert report["products"]["graphics"]["ready"] is True
    assert report["products"]["data"]["ready"] is True


def test_doctor_reports_versions_and_ready_products(monkeypatch, capsys, tmp_path) -> None:
    ffmpeg = _empty_binary(tmp_path, "ffmpeg.exe")
    blender = _empty_binary(tmp_path, "blender.exe")
    unity = _empty_binary(tmp_path, "unity.exe")
    freecadcmd = _empty_binary(tmp_path, "FreeCADCmd.exe")

    def fake_resolve(env_key: str, name: str) -> Path | None:
        found = {"ffmpeg": ffmpeg, "unity": unity, "freecadcmd": freecadcmd}
        return found.get(name)

    def fake_probe(executable: Path, arguments: tuple[str, ...], pattern: object) -> str | None:
        if executable == ffmpeg:
            return "9.0.1"
        if executable == blender:
            return "5.2.2"
        raise AssertionError("unexpected probe target")

    monkeypatch.setattr(doctor, "_resolve_executable", fake_resolve)
    monkeypatch.setattr(doctor, "default_blender", lambda: blender)
    monkeypatch.setattr(doctor, "_load_runtime_lock", _fake_lock)
    monkeypatch.setattr(doctor, "_host_platform", lambda: "windows-x86_64")
    monkeypatch.setattr(doctor, "_command_version", fake_probe)

    exit_code = doctor.run_doctor("json")
    report = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert report["runtimes"]["ffmpeg"] == {
        "status": "found",
        "path": str(ffmpeg),
        "version": "9.0.1",
        "version_source": "probe",
        "error_code": None,
        "runtime_lock": None,
    }
    freecad_finding = report["runtimes"]["freecadcmd"]
    assert freecad_finding["version"] == "FreeCAD 1.1.3"
    assert freecad_finding["version_source"] == "runtime_lock"
    assert freecad_finding["runtime_lock"]["ifcopenshell_version"] == "0.8.4"
    assert report["runtimes"]["unity"]["version"] is None
    for name in ("scene", "audio", "game", "bim", "cad"):
        assert report["products"][name]["ready"] is True


def test_doctor_rejects_when_lock_unusable_but_binary_found(monkeypatch, capsys, tmp_path) -> None:
    freecadcmd = _empty_binary(tmp_path, "FreeCADCmd.exe")
    monkeypatch.setattr(
        doctor,
        "_resolve_executable",
        lambda env_key, name: freecadcmd if name == "freecadcmd" else None,
    )
    monkeypatch.setattr(
        doctor, "default_blender", lambda: Path("Z:\\nonexistent\\blender.exe")
    )
    monkeypatch.setattr(doctor, "_load_runtime_lock", _broken_lock)

    exit_code = doctor.run_doctor("json")
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 2
    assert payload == {
        "status": "rejected",
        "error": "unsupported_ifcopenshell_runtime",
    }


def test_doctor_reports_missing_freecadcmd_when_lock_unusable(monkeypatch, capsys) -> None:
    _patch_all_missing(monkeypatch)
    monkeypatch.setattr(doctor, "_load_runtime_lock", _broken_lock)

    exit_code = doctor.run_doctor("json")
    report = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    freecad_finding = report["runtimes"]["freecadcmd"]
    assert freecad_finding["status"] == "missing"
    assert freecad_finding["error_code"] == "unsupported_ifcopenshell_runtime"
    assert report["products"]["cad"]["ready"] is False


def test_doctor_probe_reports_spawned_freecad_version(monkeypatch, capsys, tmp_path) -> None:
    freecadcmd = _empty_binary(tmp_path, "FreeCADCmd.exe")
    monkeypatch.setattr(
        doctor,
        "_resolve_executable",
        lambda env_key, name: freecadcmd if name == "freecadcmd" else None,
    )
    monkeypatch.setattr(
        doctor, "default_blender", lambda: Path("Z:\\nonexistent\\blender.exe")
    )
    monkeypatch.setattr(doctor, "_load_runtime_lock", _fake_lock)
    monkeypatch.setattr(doctor, "_host_platform", lambda: "windows-x86_64")
    monkeypatch.setattr(doctor, "_command_version", lambda *args: "1.1.3")

    exit_code = doctor.run_doctor("json", probe=True)
    report = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    freecad_finding = report["runtimes"]["freecadcmd"]
    assert freecad_finding["version"] == "FreeCAD 1.1.3"
    assert freecad_finding["version_source"] == "probe"


def test_doctor_probe_rejects_version_mismatch(monkeypatch, capsys, tmp_path) -> None:
    freecadcmd = _empty_binary(tmp_path, "FreeCADCmd.exe")
    monkeypatch.setattr(
        doctor,
        "_resolve_executable",
        lambda env_key, name: freecadcmd if name == "freecadcmd" else None,
    )
    monkeypatch.setattr(
        doctor, "default_blender", lambda: Path("Z:\\nonexistent\\blender.exe")
    )
    monkeypatch.setattr(doctor, "_load_runtime_lock", _fake_lock)
    monkeypatch.setattr(doctor, "_host_platform", lambda: "windows-x86_64")
    monkeypatch.setattr(doctor, "_command_version", lambda *args: "1.0.0")

    exit_code = doctor.run_doctor("json", probe=True)
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 2
    assert payload == {"status": "rejected", "error": "unsupported_freecad_build"}


def test_doctor_probe_failure_keeps_lock_version(monkeypatch, capsys, tmp_path) -> None:
    freecadcmd = _empty_binary(tmp_path, "FreeCADCmd.exe")
    monkeypatch.setattr(
        doctor,
        "_resolve_executable",
        lambda env_key, name: freecadcmd if name == "freecadcmd" else None,
    )
    monkeypatch.setattr(
        doctor, "default_blender", lambda: Path("Z:\\nonexistent\\blender.exe")
    )
    monkeypatch.setattr(doctor, "_load_runtime_lock", _fake_lock)
    monkeypatch.setattr(doctor, "_host_platform", lambda: "windows-x86_64")
    monkeypatch.setattr(doctor, "_command_version", lambda *args: None)

    exit_code = doctor.run_doctor("json", probe=True)
    report = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    freecad_finding = report["runtimes"]["freecadcmd"]
    assert freecad_finding["version"] == "FreeCAD 1.1.3"
    assert freecad_finding["version_source"] == "runtime_lock"
    assert freecad_finding["error_code"] == "freecad_unavailable"


def test_doctor_discovers_pinned_freecadcmd_from_lock(monkeypatch, capsys, tmp_path) -> None:
    freecadcmd = _empty_binary(tmp_path, "FreeCADCmd.exe")
    monkeypatch.setattr(doctor, "_resolve_executable", lambda env_key, name: None)
    monkeypatch.setattr(
        doctor, "default_blender", lambda: Path("Z:\\nonexistent\\blender.exe")
    )
    monkeypatch.setattr(doctor, "_load_runtime_lock", _fake_lock)
    monkeypatch.setattr(doctor, "_host_platform", lambda: "windows-x86_64")
    monkeypatch.setattr(doctor, "_pinned_freecadcmd_default", lambda lock: freecadcmd)

    exit_code = doctor.run_doctor("json")
    report = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    freecad_finding = report["runtimes"]["freecadcmd"]
    assert freecad_finding["status"] == "found"
    assert freecad_finding["path"] == str(freecadcmd)
    assert report["products"]["bim"]["ready"] is True


def test_pinned_freecadcmd_default_is_absent_without_runtime_dir(
    monkeypatch, tmp_path
) -> None:
    monkeypatch.chdir(tmp_path)

    assert doctor._pinned_freecadcmd_default(_fake_lock()) is None


def test_platform_cli_routes_doctor_json(monkeypatch, capsys) -> None:
    _patch_all_missing(monkeypatch)
    monkeypatch.setattr(doctor, "_load_runtime_lock", _fake_lock)

    exit_code = main(["doctor", "--format", "json"])
    report = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert report["status"] == "ok"
    assert set(report["products"]) == {
        "scene",
        "graphics",
        "game",
        "spreadsheet",
        "audio",
        "cad",
        "data",
        "bim",
    }


def test_doctor_text_output_is_deterministic(monkeypatch, capsys) -> None:
    _patch_all_missing(monkeypatch)
    monkeypatch.setattr(doctor, "_load_runtime_lock", _fake_lock)

    assert doctor.run_doctor("text") == 0
    first = capsys.readouterr().out
    assert doctor.run_doctor("text") == 0
    second = capsys.readouterr().out

    assert first == second
    assert "book-production doctor" in first
    assert "freecadcmd" in first
    assert "not-ready" in first


def test_command_version_parses_ffmpeg_output(monkeypatch) -> None:
    completed = subprocess.CompletedProcess(
        [],
        0,
        stdout="ffmpeg version 9.0.1-essentials_build Copyright\nlibavformat\n",
        stderr="",
    )
    monkeypatch.setattr(doctor.subprocess, "run", lambda *args, **kwargs: completed)

    result = doctor._command_version(
        Path("ffmpeg.exe"), ("-version",), doctor.FFMPEG_VERSION_PATTERN
    )

    assert result == "9.0.1-essentials_build"


def test_command_version_returns_none_when_probe_fails(monkeypatch, tmp_path) -> None:
    def raise_timeout(*args: object, **kwargs: object) -> object:
        raise subprocess.TimeoutExpired(cmd="tool", timeout=15)

    monkeypatch.setattr(doctor.subprocess, "run", raise_timeout)

    result = doctor._command_version(
        tmp_path / "tool.exe", ("--version",), doctor.BLENDER_VERSION_PATTERN
    )

    assert result is None


def test_command_version_rejects_nonzero_exit(monkeypatch, tmp_path) -> None:
    completed = subprocess.CompletedProcess([], 3, stdout="Blender 5.2.2\n", stderr="")
    monkeypatch.setattr(doctor.subprocess, "run", lambda *args, **kwargs: completed)

    result = doctor._command_version(
        tmp_path / "blender.exe", ("--version",), doctor.BLENDER_VERSION_PATTERN
    )

    assert result is None


def test_default_blender_prefers_env_then_falls_back(monkeypatch, tmp_path) -> None:
    binary = _empty_binary(tmp_path, "blender.exe")
    monkeypatch.setenv("BLENDER_BIN", str(binary))

    assert doctor.default_blender() == binary

    monkeypatch.delenv("BLENDER_BIN")
    monkeypatch.setattr(doctor.shutil, "which", lambda name: None)

    assert doctor.default_blender() == doctor._LOCAL_BLENDER
