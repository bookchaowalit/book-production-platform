"""Pinned FreeCAD process inspection and bounded worker execution."""

from __future__ import annotations

import hashlib
import json
import math
import os
import platform
import re
import subprocess
import tempfile
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from typing import Any

from book_production_platform.cad_ir import PartIRError, load_part_ir
from book_production_platform.cad_production.errors import CadProductionError

MAX_WORKER_TIMEOUT_SECONDS = 120
MAX_DIAGNOSTIC_BYTES = 64 * 1024
MAX_WORKER_RESULT_BYTES = 16 * 1024
WORKER_PROTOCOL = "freecad-worker.v1"
WORKER_MODES = frozenset({"build", "verify-native", "verify-step"})
EXPECTED_FEATURE_NAMES = ["Body", "PlateSketch", "Pad", "HoleSketch", "Pocket"]
WORKER_PATH = Path(__file__).with_name("freecad_worker.FCMacro")
_VERSION_PATTERN = re.compile(
    r"(?m)^FreeCAD (?P<version>[0-9]+\.[0-9]+\.[0-9]+) Revision: (?P<build>[^\r\n]+)$"
)
_SAFE_ENVIRONMENT_KEYS = ("SYSTEMROOT", "WINDIR")


@dataclass(frozen=True, slots=True)
class FreeCADIdentity:
    version: str
    build: str
    platform: str
    executable_sha256: str


@dataclass(frozen=True, slots=True)
class WorkerResult:
    exit_code: int | None
    elapsed_seconds: float
    diagnostics_bytes: int
    error_code: str | None
    result_json: dict[str, object] | None


def _load_runtime_lock() -> dict[str, Any]:
    try:
        resource = resources.files("book_production_platform.cad_production").joinpath(
            "freecad-runtime.v1.json"
        )
        value = json.loads(resource.read_text(encoding="utf-8"))
    except (OSError, TypeError, ValueError, ModuleNotFoundError):
        raise CadProductionError("unsupported_freecad_build") from None
    required = {
        "schema_version",
        "source_url",
        "archive_sha256",
        "version",
        "build",
        "platform",
        "executable_sha256",
        "worker_protocol",
        "preview_profile",
    }
    if (
        not isinstance(value, dict)
        or set(value) != required
        or value.get("schema_version") != "freecad-runtime.v1"
        or value.get("worker_protocol") != WORKER_PROTOCOL
        or not all(isinstance(value.get(key), str) for key in required)
    ):
        raise CadProductionError("unsupported_freecad_build") from None
    return value


def _host_platform() -> str:
    if platform.system() == "Windows" and platform.machine().upper() in {
        "AMD64",
        "X86_64",
    }:
        return "windows-x86_64"
    return f"{platform.system().lower()}-{platform.machine().lower()}"


def _safe_environment(workspace: Path, executable: Path) -> dict[str, str]:
    environment = {
        key: os.environ[key]
        for key in _SAFE_ENVIRONMENT_KEYS
        if key in os.environ
    }
    for name in ("appdata", "localappdata", "profile"):
        (workspace / name).mkdir(parents=True, exist_ok=True)
    system_root = environment.get("SYSTEMROOT") or environment.get("WINDIR")
    path_entries = [executable.parent, executable.parent / "bin"]
    if system_root:
        path_entries.append(Path(system_root) / "System32")
    environment["PATH"] = os.pathsep.join(str(path) for path in path_entries)
    environment["TEMP"] = str(workspace)
    environment["TMP"] = str(workspace)
    environment["APPDATA"] = str(workspace / "appdata")
    environment["LOCALAPPDATA"] = str(workspace / "localappdata")
    environment["USERPROFILE"] = str(workspace / "profile")
    environment["HOME"] = str(workspace / "profile")
    return environment


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _is_link(path: Path) -> bool:
    try:
        return path.is_symlink() or path.is_junction()
    except OSError:
        return True


def inspect_freecad_runtime(freecadcmd: Path) -> FreeCADIdentity:
    """Verify one regular FreeCADCmd executable against the packaged lock."""

    if not isinstance(freecadcmd, Path) or _is_link(freecadcmd):
        raise CadProductionError("unsupported_freecad_build") from None
    if freecadcmd.name.casefold() != "freecadcmd.exe" or not freecadcmd.is_file():
        raise CadProductionError("freecad_unavailable") from None
    if _host_platform() != "windows-x86_64":
        raise CadProductionError("unsupported_freecad_build") from None
    try:
        executable = freecadcmd.resolve(strict=True)
        executable_hash = _sha256_file(executable)
    except OSError:
        raise CadProductionError("freecad_unavailable") from None
    lock = _load_runtime_lock()
    if lock["platform"] != _host_platform() or lock["executable_sha256"] != executable_hash:
        raise CadProductionError("unsupported_freecad_build") from None

    try:
        with tempfile.TemporaryDirectory(prefix="cad-runtime-") as temporary:
            workspace = Path(temporary)
            result = subprocess.run(
                [str(executable), "--version"],
                cwd=executable.parent,
                env=_safe_environment(workspace, executable),
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=15,
                shell=False,
                check=False,
            )
    except (OSError, subprocess.TimeoutExpired):
        raise CadProductionError("freecad_unavailable") from None
    output = f"{result.stdout}\n{result.stderr}"
    match = _VERSION_PATTERN.search(output)
    if result.returncode != 0 or match is None:
        raise CadProductionError("unsupported_freecad_build") from None
    version = f"FreeCAD {match.group('version')}"
    build = f"Revision: {match.group('build')}"
    if version != lock["version"] or build != lock["build"]:
        raise CadProductionError("unsupported_freecad_build") from None
    return FreeCADIdentity(
        version=version,
        build=build,
        platform=lock["platform"],
        executable_sha256=executable_hash,
    )


def _reject_duplicate_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    value: dict[str, object] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate key")
        value[key] = item
    return value


def _read_worker_result(path: Path, mode: str) -> dict[str, object] | None:
    try:
        if _is_link(path) or not path.is_file():
            return None
        with path.open("rb") as stream:
            payload = stream.read(MAX_WORKER_RESULT_BYTES + 1)
        if len(payload) > MAX_WORKER_RESULT_BYTES:
            return None
        value = json.loads(
            payload.decode("utf-8"), object_pairs_hook=_reject_duplicate_pairs
        )
    except (OSError, UnicodeDecodeError, ValueError, TypeError, RecursionError):
        return None
    if not isinstance(value, dict):
        return None
    allowed = {
        "protocol",
        "mode",
        "status",
        "gui_loaded",
        "shape_valid",
        "solid_count",
        "volume_positive",
        "feature_names",
        "bounds_match",
        "step_valid",
        "relative_volume_error",
        "tessellation_vertices",
        "tessellation_faces",
        "volume_mm3",
        "bounds_mm",
        "cylindrical_bores",
        "units",
    }
    if (
        set(value) - allowed
        or value.get("protocol") != WORKER_PROTOCOL
        or value.get("mode") != mode
        or value.get("status") != "ok"
        or value.get("gui_loaded") is not False
        or value.get("shape_valid") is not True
        or type(value.get("solid_count")) is not int
        or value.get("solid_count") != 1
        or value.get("volume_positive") is not True
    ):
        return None
    if mode in {"build", "verify-native"} and value.get("feature_names") != EXPECTED_FEATURE_NAMES:
        return None
    if mode == "build":
        for key in ("tessellation_vertices", "tessellation_faces"):
            count = value.get(key)
            if type(count) is not int or not 1 <= count <= 100_000:
                return None
    if mode in {"verify-native", "verify-step"} and value.get("bounds_match") is not True:
        return None
    if mode in {"verify-native", "verify-step"}:
        volume = value.get("volume_mm3")
        bounds = value.get("bounds_mm")
        if (
            isinstance(volume, bool)
            or not isinstance(volume, (int, float))
            or not math.isfinite(volume)
            or not 0 < volume <= 12_500_000
            or not isinstance(bounds, list)
            or len(bounds) != 6
            or any(
                isinstance(item, bool)
                or not isinstance(item, (int, float))
                or not math.isfinite(item)
                or not -0.01 <= item <= 500.01
                for item in bounds
            )
        ):
            return None
    if mode == "verify-native":
        bores = value.get("cylindrical_bores")
        if not isinstance(bores, list) or len(bores) != 4:
            return None
        for bore in bores:
            if not isinstance(bore, dict) or set(bore) != {
                "center_x_mm",
                "center_y_mm",
                "radius_mm",
                "through",
            }:
                return None
            coordinates = (bore["center_x_mm"], bore["center_y_mm"], bore["radius_mm"])
            if (
                bore.get("through") is not True
                or any(
                    isinstance(item, bool)
                    or not isinstance(item, (int, float))
                    or not math.isfinite(item)
                    for item in coordinates
                )
                or not 0 <= coordinates[0] <= 500
                or not 0 <= coordinates[1] <= 500
                or not 0 < coordinates[2] <= 12.5
            ):
                return None
    if mode == "verify-step" and value.get("units") != "mm":
        return None
    if mode == "verify-step":
        if value.get("step_valid") is not True:
            return None
        relative_error = value.get("relative_volume_error")
        if (
            isinstance(relative_error, bool)
            or not isinstance(relative_error, (int, float))
            or not 0 <= relative_error <= 0.001
        ):
            return None
    return dict(value)


def _bounded_diagnostics(
    process: subprocess.Popen[bytes],
) -> tuple[threading.Thread | None, Callable[[], int]]:
    stdout = process.stdout
    if stdout is None:
        return None, lambda: 0
    state = {"total": 0}
    state_lock = threading.Lock()

    def drain() -> None:
        retained = bytearray()
        while True:
            block = stdout.read(8192)
            if not block:
                return
            with state_lock:
                state["total"] += len(block)
            retained.extend(block)
            if len(retained) > MAX_DIAGNOSTIC_BYTES:
                del retained[: len(retained) - MAX_DIAGNOSTIC_BYTES]

    reader = threading.Thread(target=drain, name="freecad-output-reader", daemon=True)
    reader.start()
    return reader, lambda: min(state["total"], MAX_DIAGNOSTIC_BYTES)


def _terminate_worker(process: subprocess.Popen[bytes]) -> int | None:
    """Stop the FreeCAD launcher and, on Windows, its child process tree."""

    process_id = getattr(process, "pid", None)
    system_root = os.environ.get("SYSTEMROOT") or os.environ.get("WINDIR")
    taskkill = Path(system_root) / "System32" / "taskkill.exe" if system_root else None
    if (
        os.name == "nt"
        and isinstance(process_id, int)
        and taskkill is not None
        and taskkill.is_file()
        and process.poll() is None
    ):
        try:
            subprocess.run(
                [str(taskkill), "/PID", str(process_id), "/T", "/F"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=5,
                shell=False,
                check=False,
                env={key: os.environ[key] for key in _SAFE_ENVIRONMENT_KEYS if key in os.environ},
            )
        except (OSError, subprocess.TimeoutExpired):
            pass
    if process.poll() is None:
        try:
            process.kill()
        except OSError:
            pass
    try:
        return process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        try:
            process.kill()
        except OSError:
            pass
        try:
            return process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            return None


def run_freecad_worker(
    freecadcmd: Path,
    mode: str,
    request_path: Path,
    result_root: Path,
    timeout_s: int,
) -> WorkerResult:
    """Run one fixed worker mode and return only bounded, schema-checked metadata."""

    if mode not in WORKER_MODES:
        raise CadProductionError("invalid_worker_mode")
    if (
        isinstance(timeout_s, bool)
        or not isinstance(timeout_s, int)
        or not 1 <= timeout_s <= MAX_WORKER_TIMEOUT_SECONDS
    ):
        raise CadProductionError("invalid_timeout")
    if not isinstance(result_root, Path) or _is_link(result_root):
        raise CadProductionError("unsafe_output_path")
    if not isinstance(request_path, Path) or _is_link(request_path):
        raise CadProductionError("invalid_part_ir")
    try:
        root = result_root.resolve(strict=True)
        request = request_path.resolve(strict=True)
        if not root.is_dir() or not request.is_file() or request.parent != root:
            raise CadProductionError("invalid_part_ir")
        load_part_ir(request)
    except PartIRError:
        raise CadProductionError("invalid_part_ir") from None
    except OSError:
        raise CadProductionError("unsafe_output_path") from None
    if not WORKER_PATH.is_file() or _is_link(WORKER_PATH):
        raise CadProductionError("freecad_unavailable")
    expected_files = {
        "part.FCStd",
        "part.step",
        "part.stl",
        "tessellation.json",
    }
    if mode == "build" and any((root / name).exists() or _is_link(root / name) for name in expected_files):
        raise CadProductionError("publication_failed")
    if mode == "verify-native" and not (root / "part.FCStd").is_file():
        raise CadProductionError("native_document_invalid")
    if mode == "verify-step" and not (root / "part.step").is_file():
        raise CadProductionError("step_export_invalid")

    inspect_freecad_runtime(freecadcmd)
    result_path = root / f"worker-result-{mode}.json"
    if _is_link(result_path):
        raise CadProductionError("unsafe_output_path")
    if result_path.exists():
        raise CadProductionError("publication_failed")
    started = time.perf_counter()
    diagnostics_total = 0
    exit_code: int | None = None
    # Keep the process CWD short even when users choose a deeply nested output
    # root; Windows rejects long CWD paths before FreeCAD can start.
    with tempfile.TemporaryDirectory(prefix=".freecad-worker-") as temporary:
        workspace = Path(temporary)
        command = [
            str(Path(freecadcmd).resolve(strict=True)),
            "--console",
            str(WORKER_PATH.resolve(strict=True)),
            "--",
            mode,
            "--request",
            str(request),
            "--output-root",
            str(root),
        ]
        try:
            process = subprocess.Popen(
                command,
                cwd=workspace,
                env=_safe_environment(workspace, Path(freecadcmd).resolve(strict=True)),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                shell=False,
            )
        except OSError:
            return WorkerResult(
                exit_code=None,
                elapsed_seconds=time.perf_counter() - started,
                diagnostics_bytes=0,
                error_code="freecad_unavailable",
                result_json=None,
            )
        reader: threading.Thread | None = None
        try:
            reader, diagnostic_count = _bounded_diagnostics(process)
            try:
                exit_code = process.wait(timeout=timeout_s)
            except subprocess.TimeoutExpired:
                exit_code = _terminate_worker(process)
                if reader is not None:
                    reader.join(timeout=2)
                diagnostics_total = diagnostic_count()
                return WorkerResult(
                    exit_code=exit_code,
                    elapsed_seconds=time.perf_counter() - started,
                    diagnostics_bytes=diagnostics_total,
                    error_code="worker_deadline_exceeded",
                    result_json=None,
                )
            if reader is not None:
                reader.join(timeout=2)
            diagnostics_total = diagnostic_count()
            if exit_code != 0:
                return WorkerResult(
                    exit_code=exit_code,
                    elapsed_seconds=time.perf_counter() - started,
                    diagnostics_bytes=diagnostics_total,
                    error_code="worker_failed",
                    result_json=None,
                )
            result = _read_worker_result(result_path, mode)
            return WorkerResult(
                exit_code=exit_code,
                elapsed_seconds=time.perf_counter() - started,
                diagnostics_bytes=diagnostics_total,
                error_code=None if result is not None else "worker_failed",
                result_json=result,
            )
        finally:
            if reader is not None:
                reader.join(timeout=2)
            if process.stdout is not None:
                process.stdout.close()
