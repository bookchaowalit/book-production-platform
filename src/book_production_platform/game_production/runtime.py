"""Bounded Unity Editor process runner for deterministic game-scene jobs."""

from __future__ import annotations

import hashlib
import json
import math
import subprocess
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

from book_production_platform.game_production.errors import GameJobError

EXPECTED_UNITY_VERSION = "6000.6.2f1"
MAX_UNITY_TIMEOUT_SECONDS = 120
MAX_UNITY_LOG_BYTES = 8 * 1024 * 1024
MAX_TEST_XML_BYTES = 8 * 1024 * 1024
POLL_INTERVAL_SECONDS = 0.1

_STAGE_METHODS = {
    "compile": "BookProduction.Editor.SceneCompiler.BuildFromGameIR",
    "reopen": "BookProduction.Editor.SceneVerifier.VerifyGeneratedScene",
}
_TEST_PLATFORMS = {"editmode": "EditMode", "playmode": "PlayMode"}


@dataclass(frozen=True)
class UnityProcessResult:
    version: str
    executable_sha256: str
    returncode: int
    log_bytes: int
    elapsed_seconds: float
    test_xml: Path | None


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _resolve_paths(unity_bin: Path, project_path: Path) -> tuple[Path, Path]:
    try:
        executable = Path(unity_bin).expanduser().resolve(strict=True)
        project = Path(project_path).expanduser().resolve(strict=True)
    except (OSError, RuntimeError) as error:
        raise GameJobError("unity_unavailable", "Unity executable or project is unavailable") from error
    if not executable.is_file() or not project.is_dir() or Path(project_path).is_symlink():
        raise GameJobError("unity_unavailable", "Unity executable or project is unavailable")
    return executable, project


def _probe_unity_version(unity_bin: Path, project_path: Path, deadline: float) -> str:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise GameJobError("deadline_exceeded", "Unity job exceeded its deadline")
    try:
        result = subprocess.run(
            [str(unity_bin), "-version"],
            cwd=project_path,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=remaining,
            shell=False,
            check=False,
        )
    except subprocess.TimeoutExpired as error:
        raise GameJobError("deadline_exceeded", "Unity version check exceeded its deadline") from error
    except OSError as error:
        raise GameJobError("unity_unavailable", "Unity version check could not start") from error
    if result.returncode != 0:
        raise GameJobError("unity_unavailable", "Unity version check failed")
    lines = (result.stdout or "").splitlines()
    version = lines[0].strip() if lines else ""
    if version != EXPECTED_UNITY_VERSION:
        raise GameJobError("unsupported_unity_version", "Unity 6000.6.2f1 is required")
    return version


def _prepare_stage_paths(project_path: Path, stage: str) -> tuple[Path, Path, Path | None]:
    private_root = project_path / ".book-production"
    if private_root.is_symlink():
        raise GameJobError("unity_worker_failed", "Unity staging output path is unsafe")
    try:
        private_root.mkdir(exist_ok=True)
        if not private_root.is_dir():
            raise GameJobError("unity_worker_failed", "Unity staging output path is invalid")
        log_root = private_root / "logs"
        if log_root.is_symlink():
            raise GameJobError("unity_worker_failed", "Unity log path is unsafe")
        log_root.mkdir(exist_ok=True)
        if not log_root.is_dir():
            raise GameJobError("unity_worker_failed", "Unity log path is invalid")
    except OSError as error:
        raise GameJobError("unity_worker_failed", "Unity staging output path is unavailable") from error

    log_path = log_root / f"{stage}.log"
    test_xml = log_root / f"{stage}.xml" if stage in _TEST_PLATFORMS else None
    generated_outputs: list[Path | None] = [log_path, test_xml]
    if stage == "compile":
        generated_outputs.append(private_root / "worker-report.json")
    elif stage == "playmode":
        generated_outputs.append(private_root / "gameplay-trace.json")
    for output_path in generated_outputs:
        if output_path is None:
            continue
        if output_path.is_symlink():
            raise GameJobError("unity_worker_failed", "Unity output path is unsafe")
        try:
            output_path.unlink(missing_ok=True)
        except OSError as error:
            raise GameJobError("unity_worker_failed", "Unity output path could not be prepared") from error
    return private_root, log_path, test_xml


def _stage_argv(
    unity_bin: Path,
    project_path: Path,
    stage: str,
    log_path: Path,
    test_xml: Path | None,
) -> list[str]:
    argv = [
        str(unity_bin),
        "-batchmode",
        "-nographics",
    ]
    if stage in _STAGE_METHODS:
        argv.extend(("-quit", "-projectPath", str(project_path), "-executeMethod", _STAGE_METHODS[stage]))
    elif stage in _TEST_PLATFORMS:
        assert test_xml is not None
        argv.extend(
            (
                "-projectPath",
                str(project_path),
                "-runTests",
                "-testPlatform",
                _TEST_PLATFORMS[stage],
                "-testResults",
                str(test_xml),
            )
        )
    else:
        raise ValueError("stage must be compile, editmode, playmode, or reopen")
    argv.extend(("-logFile", str(log_path)))
    return argv


def _read_log_size(log_path: Path) -> int:
    try:
        if log_path.is_symlink():
            raise GameJobError("unity_worker_failed", "Unity log path is unsafe")
        return log_path.stat().st_size if log_path.exists() else 0
    except OSError as error:
        raise GameJobError("unity_worker_failed", "Unity log could not be inspected") from error


def _stop_process(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is None:
        try:
            process.kill()
        except OSError:
            pass
    try:
        process.wait()
    except OSError:
        pass


def _verify_worker_report(private_root: Path, version: str) -> None:
    report_path = private_root / "worker-report.json"
    if report_path.is_symlink() or not report_path.is_file():
        raise GameJobError("unity_worker_failed", "Unity worker report is missing")
    try:
        if report_path.stat().st_size > 64 * 1024:
            raise GameJobError("unity_worker_failed", "Unity worker report exceeds its size limit")
        report = json.loads(report_path.read_text(encoding="utf-8"))
    except GameJobError:
        raise
    except (OSError, UnicodeDecodeError, ValueError, RecursionError) as error:
        raise GameJobError("unity_worker_failed", "Unity worker report is invalid") from error
    if not isinstance(report, dict) or report.get("status") != "verified":
        raise GameJobError("unity_worker_failed", "Unity worker report did not verify the scene")
    if report.get("unity_version") != version:
        raise GameJobError("unsupported_unity_version", "Unity worker report version did not match the Editor")


def _verify_test_xml(test_xml: Path | None) -> None:
    if test_xml is None or test_xml.is_symlink() or not test_xml.is_file():
        raise GameJobError("unity_test_failed", "Unity test result is missing")
    try:
        if test_xml.stat().st_size > MAX_TEST_XML_BYTES:
            raise GameJobError("unity_test_failed", "Unity test result exceeds its size limit")
        root = ET.parse(test_xml).getroot()
        cases = list(root.iter("test-case"))
        total = int(root.attrib.get("total", root.attrib.get("testcasecount", "0")))
        failed = int(root.attrib.get("failed", "0"))
        skipped = int(root.attrib.get("skipped", "0"))
        inconclusive = int(root.attrib.get("inconclusive", "0"))
        not_run = int(root.attrib.get("not-run", "0"))
    except GameJobError:
        raise
    except (OSError, ET.ParseError, ValueError, RecursionError) as error:
        raise GameJobError("unity_test_failed", "Unity test result is invalid") from error
    if (
        root.tag != "test-run"
        or root.attrib.get("result") != "Passed"
        or total < 1
        or not cases
        or failed != 0
        or skipped != 0
        or inconclusive != 0
        or not_run != 0
        or len(cases) != total
        or any(case.attrib.get("result") != "Passed" or case.attrib.get("runstate") != "Runnable" for case in cases)
    ):
        raise GameJobError("unity_test_failed", "Unity tests failed, were skipped, or did not run")


def run_unity_stage(
    unity_bin: Path,
    project_path: Path,
    stage: str,
    deadline: float,
) -> UnityProcessResult:
    """Run one fixed Unity stage under a shared monotonic deadline and output cap."""
    if stage not in {*_STAGE_METHODS, *_TEST_PLATFORMS}:
        raise ValueError("stage must be compile, editmode, playmode, or reopen")
    if not math.isfinite(deadline):
        raise GameJobError("deadline_exceeded", "Unity job deadline is invalid")
    started = time.perf_counter()
    executable, project = _resolve_paths(unity_bin, project_path)
    if deadline - time.monotonic() > MAX_UNITY_TIMEOUT_SECONDS:
        raise GameJobError("deadline_exceeded", "Unity job deadline exceeds 120 seconds")
    version = _probe_unity_version(executable, project, deadline)
    executable_sha256 = _sha256_file(executable)
    if deadline <= time.monotonic():
        raise GameJobError("deadline_exceeded", "Unity job exceeded its deadline")
    private_root, log_path, test_xml = _prepare_stage_paths(project, stage)
    argv = _stage_argv(executable, project, stage, log_path, test_xml)
    try:
        process = subprocess.Popen(
            argv,
            cwd=project,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            shell=False,
        )
    except OSError as error:
        raise GameJobError("unity_unavailable", "Unity worker could not start") from error

    while process.poll() is None:
        if deadline <= time.monotonic():
            _stop_process(process)
            raise GameJobError("deadline_exceeded", "Unity worker exceeded its deadline")
        if _read_log_size(log_path) > MAX_UNITY_LOG_BYTES:
            _stop_process(process)
            raise GameJobError("unity_log_cap_exceeded", "Unity log exceeded its size limit")
        time.sleep(min(POLL_INTERVAL_SECONDS, max(0.0, deadline - time.monotonic())))

    returncode = process.returncode
    if returncode is None:
        returncode = process.wait()
    log_bytes = _read_log_size(log_path)
    if log_bytes > MAX_UNITY_LOG_BYTES:
        raise GameJobError("unity_log_cap_exceeded", "Unity log exceeded its size limit")
    if returncode != 0:
        raise GameJobError("unity_worker_failed", "Unity worker exited with an error")
    if stage in _STAGE_METHODS:
        _verify_worker_report(private_root, version)
    if test_xml is not None:
        _verify_test_xml(test_xml)

    return UnityProcessResult(
        version=version,
        executable_sha256=executable_sha256,
        returncode=returncode,
        log_bytes=log_bytes,
        elapsed_seconds=time.perf_counter() - started,
        test_xml=test_xml,
    )
