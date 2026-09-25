from __future__ import annotations

import io
import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

from book_production_platform.cad_production import runtime
from book_production_platform.cad_production.errors import CadProductionError
from book_production_platform.cad_production.runtime import (
    MAX_DIAGNOSTIC_BYTES,
    MAX_WORKER_RESULT_BYTES,
    FreeCADIdentity,
    run_freecad_worker,
)
from tests.cad_production.conftest import write_worker_result


class FakeProcess:
    def __init__(
        self,
        output: bytes,
        *,
        returncode: int = 0,
        timeout: bool = False,
    ) -> None:
        self.stdout = io.BytesIO(output)
        self.returncode = returncode
        self.timeout = timeout
        self.killed = False
        self.command: list[str] = []

    def poll(self) -> int | None:
        return None if self.timeout and not self.killed else self.returncode

    def wait(self, timeout: float | None = None) -> int:
        if self.timeout and not self.killed:
            raise subprocess.TimeoutExpired(self.command, timeout)
        return self.returncode

    def kill(self) -> None:
        self.killed = True
        self.returncode = -9


def install_fake_worker(
    monkeypatch: pytest.MonkeyPatch,
    request_file: Path,
    request_root: Path,
    valid_worker_result: dict[str, object],
    *,
    output: bytes = b"worker output",
    returncode: int = 0,
    timeout: bool = False,
    response: object | None = None,
) -> tuple[list[list[str]], list[dict[str, Any]], list[FakeProcess]]:
    (request_root / "FreeCADCmd.exe").write_bytes(b"fake executable")
    calls: list[list[str]] = []
    process_kwargs: list[dict[str, Any]] = []
    processes: list[FakeProcess] = []
    monkeypatch.setattr(
        runtime,
        "inspect_freecad_runtime",
        lambda _: FreeCADIdentity(
            version="FreeCAD 1.1.3",
            build="Revision: 20260725 (Git shallow)",
            platform="windows-x86_64",
            executable_sha256="a" * 64,
        ),
    )

    def fake_popen(argv: list[str], **kwargs: Any) -> FakeProcess:
        calls.append(argv)
        process_kwargs.append(kwargs)
        process = FakeProcess(output, returncode=returncode, timeout=timeout)
        process.command = argv
        processes.append(process)
        if response is not None:
            mode = argv[argv.index("--") + 1]
            (request_root / f"worker-result-{mode}.json").write_text(
                json.dumps(response), encoding="utf-8"
            )
        elif returncode == 0 and not timeout:
            write_worker_result(request_root, valid_worker_result)
        return process

    monkeypatch.setattr(runtime.subprocess, "Popen", fake_popen)
    return calls, process_kwargs, processes


def test_worker_uses_fixed_macro_argv_and_no_shell(
    request_file: Path,
    request_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    valid_worker_result: dict[str, object],
) -> None:
    calls, kwargs, _ = install_fake_worker(
        monkeypatch, request_file, request_root, valid_worker_result
    )
    binary = request_root / "FreeCADCmd.exe"
    binary.write_bytes(b"fake")

    result = run_freecad_worker(binary, "build", request_file, request_root, timeout_s=120)

    assert result.error_code is None
    assert result.exit_code == 0
    assert result.result_json == valid_worker_result
    assert calls[0][0] == str(binary.resolve())
    assert calls[0][1] == "--console"
    assert calls[0][3:] == [
        "--",
        "build",
        "--request",
        str(request_file.resolve()),
        "--output-root",
        str(request_root.resolve()),
    ]
    assert kwargs[0]["shell"] is False
    assert kwargs[0]["stdin"] is subprocess.DEVNULL
    assert not kwargs[0]["cwd"].is_relative_to(request_root.resolve())


def test_worker_rejects_non_allowlisted_mode(
    request_file: Path, request_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(runtime, "inspect_freecad_runtime", lambda _: pytest.fail("must not run"))
    binary = request_root / "FreeCADCmd.exe"

    with pytest.raises(CadProductionError) as error:
        run_freecad_worker(binary, "arbitrary-code", request_file, request_root, timeout_s=10)

    assert error.value.code == "invalid_worker_mode"


@pytest.mark.parametrize("timeout_s", [0, 121, True, 1.5])
def test_worker_rejects_invalid_timeout(
    request_file: Path, request_root: Path, timeout_s: object
) -> None:
    with pytest.raises(CadProductionError) as error:
        run_freecad_worker(
            request_root / "FreeCADCmd.exe",
            "build",
            request_file,
            request_root,
            timeout_s=timeout_s,  # type: ignore[arg-type]
        )

    assert error.value.code == "invalid_timeout"


def test_worker_kills_timed_out_process_and_reports_stable_code(
    request_file: Path,
    request_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    valid_worker_result: dict[str, object],
) -> None:
    _, _, processes = install_fake_worker(
        monkeypatch,
        request_file,
        request_root,
        valid_worker_result,
        timeout=True,
    )

    result = run_freecad_worker(
        request_root / "FreeCADCmd.exe", "build", request_file, request_root, timeout_s=1
    )

    assert result.error_code == "worker_deadline_exceeded"
    assert processes[0].killed
    assert not (request_root / "part.FCStd").exists()


def test_worker_bounds_diagnostics_and_result_json(
    request_file: Path,
    request_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    valid_worker_result: dict[str, object],
) -> None:
    calls, _, _ = install_fake_worker(
        monkeypatch,
        request_file,
        request_root,
        valid_worker_result,
        output=b"x" * (MAX_DIAGNOSTIC_BYTES + 1024),
    )

    result = run_freecad_worker(
        request_root / "FreeCADCmd.exe", "build", request_file, request_root, timeout_s=10
    )

    assert result.error_code is None
    assert result.diagnostics_bytes == MAX_DIAGNOSTIC_BYTES
    assert len(json.dumps(result.result_json).encode("utf-8")) <= MAX_WORKER_RESULT_BYTES
    assert calls


@pytest.mark.parametrize(
    ("returncode", "response"),
    [
        (17, None),
        (0, "not-json"),
        (0, {"protocol": "wrong", "status": "ok"}),
    ],
)
def test_worker_maps_nonzero_or_malformed_response_to_stable_error(
    request_file: Path,
    request_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    valid_worker_result: dict[str, object],
    returncode: int,
    response: object,
) -> None:
    install_fake_worker(
        monkeypatch,
        request_file,
        request_root,
        valid_worker_result,
        returncode=returncode,
        response=response,
    )

    result = run_freecad_worker(
        request_root / "FreeCADCmd.exe", "build", request_file, request_root, timeout_s=10
    )

    assert result.error_code == "worker_failed"
    assert result.result_json is None


def test_worker_rejects_oversized_result_file(
    request_file: Path,
    request_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    valid_worker_result: dict[str, object],
) -> None:
    install_fake_worker(
        monkeypatch,
        request_file,
        request_root,
        valid_worker_result,
        response={"x": "y" * MAX_WORKER_RESULT_BYTES},
    )

    result = run_freecad_worker(
        request_root / "FreeCADCmd.exe", "build", request_file, request_root, timeout_s=10
    )

    assert result.error_code == "worker_failed"
    assert result.result_json is None


def test_real_freecad_build_reopen_and_step_export(
    tmp_path: Path, request_file: Path, request_root: Path
) -> None:
    repository_root = Path(__file__).parents[2]
    freecad = (
        repository_root
        / ".runtime"
        / "cad"
        / "freecad-1.1.3"
        / "extracted"
        / "FreeCAD_1.1.3-Windows-x86_64-py311"
        / "FreeCADCmd.exe"
    )
    if not freecad.is_file():
        pytest.skip("the pinned local FreeCAD runtime is unavailable")
    output_root = tmp_path / "integration-output"
    output_root.mkdir()
    integration_request = output_root / "request.json"
    integration_request.write_bytes(request_file.read_bytes())

    build = run_freecad_worker(freecad, "build", integration_request, output_root, 120)
    assert build.error_code is None
    assert build.result_json is not None
    assert build.result_json["feature_names"] == [
        "Body",
        "PlateSketch",
        "Pad",
        "HoleSketch",
        "Pocket",
    ]
    assert (output_root / "part.FCStd").stat().st_size > 0
    assert (output_root / "part.step").stat().st_size > 0
    assert (output_root / "part.stl").stat().st_size > 0
    assert (output_root / "tessellation.json").stat().st_size > 0

    reopened = run_freecad_worker(
        freecad, "verify-native", integration_request, output_root, 120
    )
    assert reopened.error_code is None
    exchanged = run_freecad_worker(freecad, "verify-step", integration_request, output_root, 120)
    assert exchanged.error_code is None
