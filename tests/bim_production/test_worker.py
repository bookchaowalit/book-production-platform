from __future__ import annotations

import importlib
import importlib.util
import io
import json
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
    assert spec is not None, "BIM worker must validate bounded requests and fail closed"
    return importlib.import_module("book_production_platform.bim_production.runtime")


def test_absent_worker_output_is_not_misclassified_as_a_link(tmp_path: Path) -> None:
    worker = importlib.import_module("book_production_platform.bim_production.worker")

    assert worker._is_link(tmp_path / "not-created-yet.json") is False


class FakeProcess:
    def __init__(
        self, output: bytes, *, timeout: bool = False, returncode: int = 0
    ) -> None:
        self.stdout = io.BytesIO(output)
        self.timeout = timeout
        self.killed = False
        self.returncode = returncode
        self.command: list[str] = []
        self.pid: int | None = None

    def poll(self) -> int | None:
        return None if self.timeout and not self.killed else self.returncode

    def wait(self, timeout: float | None = None) -> int:
        if self.timeout and not self.killed:
            raise subprocess.TimeoutExpired(self.command, timeout)
        return self.returncode

    def kill(self) -> None:
        self.killed = True
        self.returncode = -9


def test_worker_rejects_a_non_allowlisted_mode_before_starting_process(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    runtime = _runtime_module()
    monkeypatch.setattr(
        runtime,
        "inspect_bim_runtime",
        lambda *args: pytest.fail("an invalid worker mode must not start the runtime"),
    )

    with pytest.raises(runtime.BimProductionError) as error:
        runtime.run_bim_worker(
            tmp_path / "FreeCADCmd.exe",
            tmp_path / "site-packages",
            "arbitrary-code",
            tmp_path / "request.json",
            tmp_path,
            timeout_s=10,
        )

    assert error.value.code == "invalid_worker_mode"


@pytest.mark.parametrize("timeout_s", [0, 121, True, 1.5])
def test_worker_rejects_invalid_timeout(
    tmp_path: Path, timeout_s: object
) -> None:
    runtime = _runtime_module()

    with pytest.raises(runtime.BimProductionError) as error:
        runtime.run_bim_worker(
            tmp_path / "FreeCADCmd.exe",
            tmp_path / "site-packages",
            "preflight",
            tmp_path / "request.json",
            tmp_path,
            timeout_s=timeout_s,  # type: ignore[arg-type]
        )

    assert error.value.code == "invalid_timeout"


@pytest.mark.parametrize(
    "payload",
    [
        b"not-json",
        b'{"protocol":"bim-worker.v1","protocol":"bim-worker.v1"}',
        b"x" * 16_385,
    ],
)
def test_worker_result_reader_rejects_malformed_duplicate_or_oversized_json(
    tmp_path: Path, payload: bytes
) -> None:
    runtime = _runtime_module()
    result_path = tmp_path / "worker-result-preflight.json"
    result_path.write_bytes(payload)

    assert runtime._read_worker_result(result_path, "preflight") is None


def _prepare_worker_runner(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, worker_path: Path
) -> tuple[Any, Path, Path, Path]:
    runtime = _runtime_module()
    freecadcmd = tmp_path / "FreeCADCmd.exe"
    freecadcmd.write_bytes(b"test executable")
    site_packages = tmp_path / "site-packages"
    site_packages.mkdir()
    work_root = tmp_path / "work"
    work_root.mkdir()
    worker_path.write_text("# fixed test worker\n", encoding="utf-8")
    monkeypatch.setattr(runtime, "WORKER_PATH", worker_path)
    identity = runtime.BimRuntimeIdentity(
        freecad_version="FreeCAD 1.1.3",
        freecad_build="Revision: 20260725 (Git shallow)",
        ifcopenshell_version="0.8.4",
        python_version="3.11",
        platform="windows-x86_64",
        freecadcmd_sha256="a" * 64,
        wrapper_sha256="b" * 64,
    )
    monkeypatch.setattr(runtime, "inspect_bim_runtime", lambda *args: identity)
    return runtime, freecadcmd, site_packages, work_root


def _preflight_result() -> dict[str, object]:
    return {
        "protocol": "bim-worker.v1",
        "mode": "preflight",
        "status": "ok",
        "gui_loaded": False,
        "freecad_version": "FreeCAD 1.1.3",
        "freecad_build": "Revision: 20260725 (Git shallow)",
        "python_version": "3.11",
        "ifcopenshell_version": "0.8.4",
        "wrapper_sha256": "b" * 64,
        "schemas": ["IFC2X3", "IFC4"],
        "open_callable": True,
        "validation_api": True,
        "unit_api": True,
        "qto_api": True,
        "malformed_spf_rejected": True,
        "valid_ifc4_validation_findings": 0,
    }


def test_worker_runner_uses_closed_stdin_and_shell_false(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    runtime, freecadcmd, site_packages, work_root = _prepare_worker_runner(
        monkeypatch, tmp_path, tmp_path / "worker.FCMacro"
    )
    calls: list[dict[str, object]] = []

    def fake_popen(command: list[str], **kwargs: object) -> FakeProcess:
        calls.append(kwargs)
        result_path = work_root / "worker-result-preflight.json"
        result_path.write_text(json.dumps(_preflight_result()), encoding="utf-8")
        return FakeProcess(b"diagnostic output")

    monkeypatch.setattr(runtime.subprocess, "Popen", fake_popen)
    result = runtime.run_bim_worker(
        freecadcmd,
        site_packages,
        "preflight",
        tmp_path / "unused.json",
        work_root,
        timeout_s=10,
    )

    assert result.error_code is None
    assert calls[0]["stdin"] is subprocess.DEVNULL
    assert calls[0]["shell"] is False


def test_worker_runner_rejects_a_result_with_the_wrong_ifcopenshell_version(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    runtime, freecadcmd, site_packages, work_root = _prepare_worker_runner(
        monkeypatch, tmp_path, tmp_path / "worker.FCMacro"
    )

    def fake_popen(command: list[str], **kwargs: object) -> FakeProcess:
        result = _preflight_result()
        result["ifcopenshell_version"] = "0.8.5"
        (work_root / "worker-result-preflight.json").write_text(
            json.dumps(result), encoding="utf-8"
        )
        return FakeProcess(b"")

    monkeypatch.setattr(runtime.subprocess, "Popen", fake_popen)
    result = runtime.run_bim_worker(
        freecadcmd,
        site_packages,
        "preflight",
        tmp_path / "unused.json",
        work_root,
        timeout_s=10,
    )

    assert result.error_code == "unsupported_ifcopenshell_runtime"


def test_worker_runner_reports_a_deadline_and_kills_the_process(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    runtime, freecadcmd, site_packages, work_root = _prepare_worker_runner(
        monkeypatch, tmp_path, tmp_path / "worker.FCMacro"
    )
    processes: list[FakeProcess] = []

    def fake_popen(command: list[str], **kwargs: object) -> FakeProcess:
        process = FakeProcess(b"", timeout=True)
        process.command = command
        processes.append(process)
        return process

    monkeypatch.setattr(runtime.subprocess, "Popen", fake_popen)
    result = runtime.run_bim_worker(
        freecadcmd,
        site_packages,
        "preflight",
        tmp_path / "unused.json",
        work_root,
        timeout_s=1,
    )

    assert result.error_code == "worker_deadline_exceeded"
    assert processes[0].killed is True


def test_worker_runner_rejects_a_nonzero_exit_without_diagnostics(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    runtime, freecadcmd, site_packages, work_root = _prepare_worker_runner(
        monkeypatch, tmp_path, tmp_path / "worker.FCMacro"
    )
    monkeypatch.setattr(
        runtime.subprocess,
        "Popen",
        lambda command, **kwargs: FakeProcess(b"private diagnostic", returncode=1),
    )

    result = runtime.run_bim_worker(
        freecadcmd,
        site_packages,
        "preflight",
        tmp_path / "unused.json",
        work_root,
        timeout_s=10,
    )

    assert result.error_code == "worker_failed"
    assert result.diagnostics_bytes == len(b"private diagnostic")
    assert result.result_json is None
