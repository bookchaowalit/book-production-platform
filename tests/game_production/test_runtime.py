from __future__ import annotations

import hashlib
import json
import subprocess
import time
from pathlib import Path
from typing import Any

import pytest

from book_production_platform.game_production import runtime
from book_production_platform.game_production.errors import GameJobError

EXPECTED_UNITY_VERSION = "6000.6.2f1"


class FakeProcess:
    def __init__(self, returncode: int = 0, *, running: bool = False) -> None:
        self.returncode = returncode
        self.running = running
        self.killed = False

    def poll(self) -> int | None:
        return None if self.running else self.returncode

    def kill(self) -> None:
        self.killed = True
        self.running = False
        self.returncode = -9

    def wait(self, timeout: float | None = None) -> int:
        del timeout
        self.running = False
        return self.returncode


def install_fake_unity(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    version: str = EXPECTED_UNITY_VERSION,
    process_returncode: int = 0,
    write_test_xml: bool = True,
    worker_report_version: str = EXPECTED_UNITY_VERSION,
    write_worker_report: bool = True,
) -> tuple[Path, Path, list[list[str]], list[FakeProcess]]:
    unity_bin = tmp_path / "Unity.exe"
    unity_bin.write_bytes(b"unity executable")
    project_path = tmp_path / "project"
    project_path.mkdir()
    calls: list[list[str]] = []
    processes: list[FakeProcess] = []

    def fake_run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        assert kwargs["shell"] is False
        assert kwargs["cwd"] == project_path.resolve()
        return subprocess.CompletedProcess(argv, 0, stdout=version, stderr="")

    def fake_popen(argv: list[str], **kwargs: Any) -> FakeProcess:
        assert kwargs["shell"] is False
        assert kwargs["cwd"] == project_path.resolve()
        calls.append(argv)
        command = FakeProcess(process_returncode)
        processes.append(command)
        output_root = project_path / ".book-production" / "logs"
        if "-testResults" in argv and write_test_xml:
            test_xml = Path(argv[argv.index("-testResults") + 1])
            test_xml.parent.mkdir(parents=True, exist_ok=True)
            test_xml.write_text(
                '<test-run result="Passed" total="1" passed="1" failed="0" skipped="0">'
                '<test-case result="Passed" runstate="Runnable" /></test-run>',
                encoding="utf-8",
            )
        if (
            write_worker_report
            and "-executeMethod" in argv
            and argv[argv.index("-executeMethod") + 1].endswith("BuildFromGameIR")
        ):
            output_root.parent.mkdir(parents=True, exist_ok=True)
            (output_root.parent / "worker-report.json").write_text(
                json.dumps({"status": "verified", "unity_version": worker_report_version}),
                encoding="utf-8",
            )
        return command

    monkeypatch.setattr(runtime.subprocess, "run", fake_run)
    monkeypatch.setattr(runtime.subprocess, "Popen", fake_popen)
    return unity_bin, project_path, calls, processes


def run_compile(unity_bin: Path, project_path: Path) -> runtime.UnityProcessResult:
    return runtime.run_unity_stage(
        unity_bin,
        project_path,
        "compile",
        time.monotonic() + 10,
    )


def test_runner_uses_fixed_argv_without_shell_and_reports_binary_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    unity_bin, project_path, calls, _ = install_fake_unity(monkeypatch, tmp_path)

    result = run_compile(unity_bin, project_path)

    assert calls == [
        [
            str(unity_bin.resolve()),
            "-batchmode",
            "-nographics",
            "-quit",
            "-projectPath",
            str(project_path.resolve()),
            "-executeMethod",
            "BookProduction.Editor.SceneCompiler.BuildFromGameIR",
            "-logFile",
            str(project_path.resolve() / ".book-production" / "logs" / "compile.log"),
        ]
    ]
    assert result.version == EXPECTED_UNITY_VERSION
    assert result.executable_sha256 == hashlib.sha256(b"unity executable").hexdigest()
    assert result.returncode == 0
    assert result.log_bytes == 0
    assert result.elapsed_seconds >= 0
    assert result.test_xml is None


def test_runner_rejects_unsupported_unity_version_before_worker_start(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    unity_bin, project_path, calls, _ = install_fake_unity(monkeypatch, tmp_path, version="2022.3.0f1")

    with pytest.raises(GameJobError) as error:
        run_compile(unity_bin, project_path)

    assert error.value.code == "unsupported_unity_version"
    assert calls == []


def test_compile_rejects_worker_report_version_mismatch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    unity_bin, project_path, _, _ = install_fake_unity(
        monkeypatch,
        tmp_path,
        worker_report_version="2022.3.0f1",
    )

    with pytest.raises(GameJobError) as error:
        run_compile(unity_bin, project_path)

    assert error.value.code == "unsupported_unity_version"


def test_compile_does_not_accept_a_stale_worker_report(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    unity_bin, project_path, _, _ = install_fake_unity(monkeypatch, tmp_path, write_worker_report=False)
    private_root = project_path / ".book-production"
    private_root.mkdir()
    (private_root / "worker-report.json").write_text(
        json.dumps({"status": "verified", "unity_version": EXPECTED_UNITY_VERSION}),
        encoding="utf-8",
    )

    with pytest.raises(GameJobError) as error:
        run_compile(unity_bin, project_path)

    assert error.value.code == "unity_worker_failed"
    assert not (private_root / "worker-report.json").exists()


def test_runner_rejects_missing_unity_executable(tmp_path: Path) -> None:
    with pytest.raises(GameJobError) as error:
        runtime.run_unity_stage(
            tmp_path / "missing-Unity.exe",
            tmp_path,
            "compile",
            time.monotonic() + 10,
        )

    assert error.value.code == "unity_unavailable"


def test_runner_kills_worker_when_job_deadline_expires(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    unity_bin, project_path, _, processes = install_fake_unity(monkeypatch, tmp_path)

    def running_worker(argv: list[str], **kwargs: Any) -> FakeProcess:
        process = FakeProcess(running=True)
        processes.append(process)
        return process

    monkeypatch.setattr(runtime.subprocess, "Popen", running_worker)
    ticks = iter((0.0, 0.0, 0.0, 2.0, 2.0))
    monkeypatch.setattr(runtime.time, "monotonic", lambda: next(ticks))
    monkeypatch.setattr(runtime.time, "sleep", lambda _: None)

    with pytest.raises(GameJobError) as error:
        runtime.run_unity_stage(unity_bin, project_path, "compile", deadline=1.0)

    assert error.value.code == "deadline_exceeded"
    assert processes[-1].killed


def test_runner_kills_worker_when_log_exceeds_cap(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    unity_bin, project_path, _, processes = install_fake_unity(monkeypatch, tmp_path)

    def oversized_log(argv: list[str], **kwargs: Any) -> FakeProcess:
        log_path = Path(argv[argv.index("-logFile") + 1])
        log_path.write_bytes(b"x" * (runtime.MAX_UNITY_LOG_BYTES + 1))
        process = FakeProcess(running=True)
        processes.append(process)
        return process

    monkeypatch.setattr(runtime.subprocess, "Popen", oversized_log)
    monkeypatch.setattr(runtime.time, "sleep", lambda _: None)

    with pytest.raises(GameJobError) as error:
        runtime.run_unity_stage(unity_bin, project_path, "compile", time.monotonic() + 10)

    assert error.value.code == "unity_log_cap_exceeded"
    assert processes[-1].killed


def test_runner_rejects_nonzero_unity_exit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    unity_bin, project_path, _, _ = install_fake_unity(monkeypatch, tmp_path, process_returncode=17)

    with pytest.raises(GameJobError) as error:
        run_compile(unity_bin, project_path)

    assert error.value.code == "unity_worker_failed"


def test_test_stage_requires_a_test_result_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    unity_bin, project_path, _, _ = install_fake_unity(monkeypatch, tmp_path, write_test_xml=False)

    with pytest.raises(GameJobError) as error:
        runtime.run_unity_stage(unity_bin, project_path, "editmode", time.monotonic() + 10)

    assert error.value.code == "unity_test_failed"


@pytest.mark.parametrize(
    "test_xml",
    [
        (
            '<test-run result="Failed" total="1" passed="0" failed="1" skipped="0">'
            '<test-case result="Failed" runstate="Runnable" /></test-run>'
        ),
        (
            '<test-run result="Passed" total="1" passed="0" failed="0" skipped="0">'
            '<test-case result="NotRun" runstate="NotRunnable" /></test-run>'
        ),
        '<test-run result="Passed" total="0" passed="0" failed="0" skipped="0" />',
    ],
)
def test_test_stage_rejects_failed_skipped_or_not_run_cases(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    test_xml: str,
) -> None:
    unity_bin, project_path, _, _ = install_fake_unity(monkeypatch, tmp_path)
    original_popen = runtime.subprocess.Popen

    def invalid_xml(argv: list[str], **kwargs: Any) -> FakeProcess:
        process = original_popen(argv, **kwargs)
        result_path = Path(argv[argv.index("-testResults") + 1])
        result_path.write_text(test_xml, encoding="utf-8")
        return process

    monkeypatch.setattr(runtime.subprocess, "Popen", invalid_xml)

    with pytest.raises(GameJobError) as error:
        runtime.run_unity_stage(unity_bin, project_path, "playmode", time.monotonic() + 10)

    assert error.value.code == "unity_test_failed"


def test_test_stage_returns_validated_xml_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    unity_bin, project_path, _, _ = install_fake_unity(monkeypatch, tmp_path)

    result = runtime.run_unity_stage(unity_bin, project_path, "editmode", time.monotonic() + 10)

    assert result.test_xml == project_path.resolve() / ".book-production" / "logs" / "editmode.xml"
    assert result.test_xml.is_file()
