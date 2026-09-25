from __future__ import annotations

import hashlib
import importlib
import importlib.util
import subprocess
from pathlib import Path
from typing import Any

import pytest


def _runtime_module() -> Any:
    try:
        spec = importlib.util.find_spec(
            "book_production_platform.bim_production.runtime"
        )
    except ModuleNotFoundError:
        spec = None
    assert spec is not None, "BIM runtime must shorten and verify the pinned IFC wrapper path"
    return importlib.import_module("book_production_platform.bim_production.runtime")


def test_windows_short_path_resolves_the_existing_wrapper_below_260_characters() -> None:
    runtime = _runtime_module()
    product_root = Path(__file__).resolve().parents[2]
    site_packages = (
        product_root
        / ".runtime"
        / "cad"
        / "freecad-1.1.3"
        / "extracted"
        / "FreeCAD_1.1.3-Windows-x86_64-py311"
        / "bin"
        / "Lib"
        / "site-packages"
    )
    wrapper_name = "_ifcopenshell_wrapper.cp311-win_amd64.pyd"
    original_wrapper = site_packages / "ifcopenshell" / wrapper_name

    assert original_wrapper.is_file()
    assert len(str(original_wrapper)) >= 260

    short_site_packages = runtime.windows_short_path(site_packages)
    short_wrapper = short_site_packages / "ifcopenshell" / wrapper_name

    assert short_wrapper.is_file()
    assert short_wrapper.resolve() == original_wrapper.resolve()
    assert len(str(short_wrapper)) < 260


def test_windows_short_path_fails_closed_when_windows_returns_no_alias(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    runtime = _runtime_module()
    monkeypatch.setattr(runtime, "_get_short_path_name", lambda path: "")

    with pytest.raises(runtime.BimProductionError) as error:
        runtime.windows_short_path(tmp_path)

    assert error.value.code == "bim_runtime_path_unsupported"


def test_windows_short_path_fails_if_native_module_path_stays_too_long(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    runtime = _runtime_module()
    monkeypatch.setattr(runtime, "_get_short_path_name", lambda path: str(path))
    long_site_packages = tmp_path / ("site-" + "x" * 230)

    with pytest.raises(runtime.BimProductionError) as error:
        runtime.windows_short_path(long_site_packages)

    assert error.value.code == "bim_runtime_path_unsupported"


def test_windows_short_path_rejects_a_missing_site_packages_directory(
    tmp_path: Path,
) -> None:
    runtime = _runtime_module()

    with pytest.raises(runtime.BimProductionError) as error:
        runtime.windows_short_path(tmp_path / "missing-site-packages")

    assert error.value.code == "bim_runtime_path_unsupported"


def test_runtime_identity_accepts_the_existing_pinned_freecad_and_wrapper(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = _runtime_module()
    product_root = Path(__file__).resolve().parents[2]
    runtime_root = (
        product_root
        / ".runtime"
        / "cad"
        / "freecad-1.1.3"
        / "extracted"
        / "FreeCAD_1.1.3-Windows-x86_64-py311"
    )
    freecadcmd = runtime_root / "FreeCADCmd.exe"
    site_packages = runtime_root / "bin" / "Lib" / "site-packages"
    wrapper = site_packages / "ifcopenshell" / "_ifcopenshell_wrapper.cp311-win_amd64.pyd"
    assert hashlib.sha256(freecadcmd.read_bytes()).hexdigest() == (
        "8feb06052673cdcd56df60d856cc5e6fbf8cf862d56f1419e80a558f505a7b23"
    )
    assert hashlib.sha256(wrapper.read_bytes()).hexdigest() == (
        "c505947c7d8c1342195498a0e48f38a5da35581104df8b5556f930fbcf297b78"
    )
    monkeypatch.setattr(runtime, "_host_platform", lambda: "windows-x86_64")
    run_calls: list[dict[str, object]] = []

    def fake_run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        run_calls.append(kwargs)
        return subprocess.CompletedProcess(
            argv,
            0,
            stdout="FreeCAD 1.1.3 Revision: 20260725 (Git shallow)\n",
            stderr="",
        )

    monkeypatch.setattr(runtime.subprocess, "run", fake_run)

    identity = runtime.inspect_bim_runtime(freecadcmd, site_packages)

    assert identity.freecad_version == "FreeCAD 1.1.3"
    assert identity.ifcopenshell_version == "0.8.4"
    assert identity.platform == "windows-x86_64"
    assert identity.freecadcmd_sha256 == (
        "8feb06052673cdcd56df60d856cc5e6fbf8cf862d56f1419e80a558f505a7b23"
    )
    assert identity.wrapper_sha256 == (
        "c505947c7d8c1342195498a0e48f38a5da35581104df8b5556f930fbcf297b78"
    )
    assert run_calls[0]["stdin"] is subprocess.DEVNULL
    assert run_calls[0]["shell"] is False


def test_runtime_identity_rejects_wrapper_hash_mismatch(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    runtime = _runtime_module()
    freecadcmd = tmp_path / "FreeCADCmd.exe"
    freecadcmd.write_bytes(b"pinned test executable")
    site_packages = tmp_path / "site-packages"
    ifcopenshell = site_packages / "ifcopenshell"
    ifcopenshell.mkdir(parents=True)
    wrapper = ifcopenshell / "_ifcopenshell_wrapper.cp311-win_amd64.pyd"
    wrapper.write_bytes(b"wrapper bytes")
    monkeypatch.setattr(runtime, "_host_platform", lambda: "windows-x86_64")
    monkeypatch.setattr(runtime, "windows_short_path", lambda path: path)
    monkeypatch.setattr(
        runtime,
        "_load_runtime_lock",
        lambda: {
            "schema_version": "bim.ifcopenshell-runtime.v1",
            "freecad_version": "FreeCAD 1.1.3",
            "freecad_build": "Revision: 20260725 (Git shallow)",
            "freecad_platform": "windows-x86_64",
            "freecadcmd_sha256": hashlib.sha256(freecadcmd.read_bytes()).hexdigest(),
            "python_version": "3.11",
            "ifcopenshell_version": "0.8.4",
            "wrapper_relative_path": "ifcopenshell/_ifcopenshell_wrapper.cp311-win_amd64.pyd",
            "wrapper_sha256": "0" * 64,
            "worker_protocol": "bim-worker.v1",
        },
    )

    with pytest.raises(runtime.BimProductionError) as error:
        runtime.inspect_bim_runtime(freecadcmd, site_packages)

    assert error.value.code == "unsupported_ifcopenshell_runtime"


def test_runtime_identity_rejects_a_freecad_version_mismatch(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    runtime = _runtime_module()
    freecadcmd = tmp_path / "FreeCADCmd.exe"
    freecadcmd.write_bytes(b"pinned test executable")
    site_packages = tmp_path / "site-packages"
    wrapper = site_packages / "ifcopenshell" / "_ifcopenshell_wrapper.cp311-win_amd64.pyd"
    wrapper.parent.mkdir(parents=True)
    wrapper.write_bytes(b"wrapper bytes")
    monkeypatch.setattr(runtime, "_host_platform", lambda: "windows-x86_64")
    monkeypatch.setattr(runtime, "windows_short_path", lambda path: path)
    monkeypatch.setattr(
        runtime,
        "_load_runtime_lock",
        lambda: {
            "schema_version": "bim.ifcopenshell-runtime.v1",
            "freecad_version": "FreeCAD 1.1.3",
            "freecad_build": "Revision: 20260725 (Git shallow)",
            "freecad_platform": "windows-x86_64",
            "freecadcmd_sha256": hashlib.sha256(freecadcmd.read_bytes()).hexdigest(),
            "python_version": "3.11",
            "ifcopenshell_version": "0.8.4",
            "wrapper_relative_path": "ifcopenshell/_ifcopenshell_wrapper.cp311-win_amd64.pyd",
            "wrapper_sha256": hashlib.sha256(wrapper.read_bytes()).hexdigest(),
            "worker_protocol": "bim-worker.v1",
        },
    )
    monkeypatch.setattr(
        runtime.subprocess,
        "run",
        lambda argv, **kwargs: subprocess.CompletedProcess(
            argv, 0, stdout="FreeCAD 1.1.2 Revision: 20260725 (Git shallow)\n", stderr=""
        ),
    )

    with pytest.raises(runtime.BimProductionError) as error:
        runtime.inspect_bim_runtime(freecadcmd, site_packages)

    assert error.value.code == "unsupported_freecad_build"


def test_runtime_identity_rejects_a_missing_executable(tmp_path: Path) -> None:
    runtime = _runtime_module()

    with pytest.raises(runtime.BimProductionError) as error:
        runtime.inspect_bim_runtime(
            tmp_path / "FreeCADCmd.exe", tmp_path / "site-packages"
        )

    assert error.value.code == "freecad_unavailable"
