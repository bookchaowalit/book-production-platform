"""Local runtime discovery and per-product readiness reporting."""

from __future__ import annotations

import json
import os
import platform
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from book_production_platform.bim_production.errors import BimProductionError
from book_production_platform.bim_production.runtime import (
    _VERSION_PATTERN,
    _host_platform,
    _load_runtime_lock,
)

PROBE_TIMEOUT_SECONDS = 15
FFMPEG_VERSION_PATTERN = re.compile(r"ffmpeg version (\S+)")
BLENDER_VERSION_PATTERN = re.compile(r"Blender (\S+)")

_RUNTIME_ORDER = ("python", "freecadcmd", "ffmpeg", "blender", "unity")
_LOCK_DETAIL_KEYS = (
    "freecad_version",
    "freecad_build",
    "ifcopenshell_version",
    "python_version",
)
_PRODUCT_REQUIRED: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("scene", ("blender",)),
    ("graphics", ("python",)),
    ("game", ("unity",)),
    ("spreadsheet", ("python",)),
    ("audio", ("ffmpeg",)),
    ("cad", ("freecadcmd",)),
    ("data", ("python",)),
    ("bim", ("freecadcmd",)),
)
_LOCAL_BLENDER = (
    Path.home()
    / ".codex"
    / "tools"
    / "blender-5.2.2"
    / "portable"
    / "blender-5.2.2-windows-x64"
    / "blender.exe"
)


@dataclass(frozen=True)
class RuntimeFinding:
    """One runtime observation safe to print; paths come from local discovery."""

    status: str
    path: str | None
    version: str | None
    version_source: str | None
    error_code: str | None
    runtime_lock: dict[str, str] | None


def default_blender() -> Path:
    """Resolve the benchmark Blender fallback used by the platform CLI."""

    configured = os.environ.get("BLENDER_BIN")
    if configured:
        return Path(configured)
    discovered = shutil.which("blender")
    if discovered:
        return Path(discovered)
    return _LOCAL_BLENDER


def _is_file(path: Path) -> bool:
    try:
        return path.is_file()
    except OSError:
        return False


def _resolve_executable(env_key: str, name: str) -> Path | None:
    candidate = os.environ.get(env_key) or shutil.which(name)
    if not candidate:
        return None
    direct = Path(candidate)
    if _is_file(direct):
        return direct
    discovered = shutil.which(candidate)
    if not discovered:
        return None
    discovered_path = Path(discovered)
    return discovered_path if _is_file(discovered_path) else None


def _command_version(
    executable: Path, arguments: tuple[str, ...], pattern: re.Pattern[str]
) -> str | None:
    """Run one bounded local version probe and return the first match."""

    try:
        completed = subprocess.run(
            [str(executable), *arguments],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=PROBE_TIMEOUT_SECONDS,
            shell=False,
            check=False,
        )
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return None
    if completed.returncode != 0:
        return None
    match = pattern.search(f"{completed.stdout or ''}\n{completed.stderr or ''}")
    if match is None:
        return None
    captured = match.group(1)
    return str(captured) if captured is not None else None


def _lock_details(lock: dict[str, Any]) -> dict[str, str]:
    return {key: str(lock[key]) for key in _LOCK_DETAIL_KEYS}


def _pinned_freecadcmd_default(lock: dict[str, Any]) -> Path | None:
    """Resolve the documented pinned .runtime/cad layout from the lock."""

    if lock["freecad_platform"] != _host_platform():
        return None
    release = str(lock["freecad_version"]).removeprefix("FreeCAD ")
    python_tag = str(lock["python_version"]).removeprefix("Python ").replace(".", "")
    if not release or not python_tag or "/" in release or "/" in python_tag:
        return None
    candidate = (
        Path(".runtime")
        / "cad"
        / f"freecad-{release}"
        / "extracted"
        / f"FreeCAD_{release}-Windows-x86_64-py{python_tag}"
        / "FreeCADCmd.exe"
    )
    return candidate if _is_file(candidate) else None


def _python_finding() -> RuntimeFinding:
    executable = sys.executable or None
    return RuntimeFinding(
        status="found",
        path=executable,
        version=platform.python_version(),
        version_source="python_runtime",
        error_code=None,
        runtime_lock=None,
    )


def _freecadcmd_finding(probe: bool) -> RuntimeFinding:
    path = _resolve_executable("FREECARDCMD_BIN", "freecadcmd")
    try:
        lock = _load_runtime_lock()
        details = _lock_details(lock)
    except BimProductionError as error:
        if path is not None:
            raise
        return RuntimeFinding("missing", None, None, None, error.code, None)
    if lock["freecad_platform"] != _host_platform():
        if path is not None:
            raise BimProductionError("unsupported_freecad_build")
        return RuntimeFinding("missing", None, None, None, "unsupported_freecad_build", details)
    if path is None:
        path = _pinned_freecadcmd_default(lock)
    if path is None or not _is_file(path):
        return RuntimeFinding("missing", None, None, None, None, details)
    version = str(lock["freecad_version"])
    version_source = "runtime_lock"
    error_code = None
    if probe:
        raw = _command_version(path, ("--version",), _VERSION_PATTERN)
        if raw is None:
            error_code = "freecad_unavailable"
        elif f"FreeCAD {raw}" != version:
            raise BimProductionError("unsupported_freecad_build")
        else:
            version_source = "probe"
    return RuntimeFinding("found", str(path), version, version_source, error_code, details)


def _probed_finding(
    path: Path, arguments: tuple[str, ...], pattern: re.Pattern[str]
) -> RuntimeFinding:
    version = _command_version(path, arguments, pattern)
    source = "probe" if version is not None else None
    return RuntimeFinding("found", str(path), version, source, None, None)


def _ffmpeg_finding() -> RuntimeFinding:
    path = _resolve_executable("FFMPEG_BIN", "ffmpeg")
    if path is None:
        return RuntimeFinding("missing", None, None, None, None, None)
    return _probed_finding(path, ("-version",), FFMPEG_VERSION_PATTERN)


def _blender_finding() -> RuntimeFinding:
    path = default_blender()
    if not _is_file(path):
        return RuntimeFinding("missing", None, None, None, None, None)
    return _probed_finding(path, ("--version",), BLENDER_VERSION_PATTERN)


def _unity_finding() -> RuntimeFinding:
    path = _resolve_executable("UNITY_BIN", "unity")
    if path is None:
        return RuntimeFinding("missing", None, None, None, None, None)
    return RuntimeFinding("found", str(path), None, None, None, None)


def _collect(probe: bool) -> dict[str, RuntimeFinding]:
    return {
        "python": _python_finding(),
        "freecadcmd": _freecadcmd_finding(probe),
        "ffmpeg": _ffmpeg_finding(),
        "blender": _blender_finding(),
        "unity": _unity_finding(),
    }


def _product_reports(
    findings: dict[str, RuntimeFinding],
) -> dict[str, dict[str, Any]]:
    products: dict[str, dict[str, Any]] = {}
    for name, required in _PRODUCT_REQUIRED:
        missing = [key for key in required if findings[key].status != "found"]
        products[name] = {
            "ready": not missing,
            "required": list(required),
            "missing": missing,
        }
    return products


def _finding_json(finding: RuntimeFinding) -> dict[str, Any]:
    return {
        "status": finding.status,
        "path": finding.path,
        "version": finding.version,
        "version_source": finding.version_source,
        "error_code": finding.error_code,
        "runtime_lock": finding.runtime_lock,
    }


def _render_text(
    findings: dict[str, RuntimeFinding], products: dict[str, dict[str, Any]]
) -> str:
    lines = ["book-production doctor", "", "runtime      status   version           path"]
    for key in _RUNTIME_ORDER:
        finding = findings[key]
        version = finding.version or "-"
        path = finding.path or "-"
        lines.append(f"{key:<12} {finding.status:<8} {version:<17} {path}")
    lines.append("")
    lines.append("product      status     required            missing")
    for name, _required in _PRODUCT_REQUIRED:
        entry = products[name]
        missing = ", ".join(entry["missing"]) if entry["missing"] else "-"
        status = "ready" if entry["ready"] else "not-ready"
        lines.append(
            f"{name:<12} {status:<10} {', '.join(entry['required']):<19} {missing}"
        )
    return "\n".join(lines)


def _emit(value: dict[str, Any]) -> None:
    print(json.dumps(value, ensure_ascii=False, sort_keys=True))


def run_doctor(output_format: str = "text", probe: bool = False) -> int:
    """Report local runtimes and per-product readiness; never runs a pipeline."""

    try:
        findings = _collect(probe=probe)
    except BimProductionError as error:
        _emit({"status": "rejected", "error": error.code})
        return 2
    except OSError:
        _emit({"status": "rejected", "error": "cli_io_error"})
        return 2
    products = _product_reports(findings)
    if output_format == "json":
        runtimes = {key: _finding_json(findings[key]) for key in _RUNTIME_ORDER}
        _emit({"status": "ok", "runtimes": runtimes, "products": products})
    else:
        print(_render_text(findings, products))
    return 0
