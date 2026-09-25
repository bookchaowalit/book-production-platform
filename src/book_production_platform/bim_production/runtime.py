"""Pinned IfcOpenShell process inspection and bounded FreeCAD worker execution."""

from __future__ import annotations

import ctypes
import hashlib
import json
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
from typing import Any, Literal

from book_production_platform.bim_production.errors import BimProductionError

MAX_WORKER_TIMEOUT_SECONDS = 120
MAX_DIAGNOSTIC_BYTES = 64 * 1024
MAX_WORKER_RESULT_BYTES = 16 * 1024
WORKER_PROTOCOL = "bim-worker.v1"
WORKER_MODES = frozenset({"preflight", "takeoff", "fixture"})
WORKER_PATH = Path(__file__).with_name("freecad_worker.FCMacro")
WRAPPER_RELATIVE_PATH = Path(
    "ifcopenshell/_ifcopenshell_wrapper.cp311-win_amd64.pyd"
)
_VERSION_PATTERN = re.compile(
    r"(?m)^FreeCAD (?P<version>[0-9]+\.[0-9]+\.[0-9]+) Revision: (?P<build>[^\r\n]+)$"
)
_SAFE_ENVIRONMENT_KEYS = ("SYSTEMROOT", "WINDIR")
_WORKER_RESULT_KEYS = {
    "protocol",
    "mode",
    "status",
    "error_code",
    "failure_stage",
    "gui_loaded",
    "freecad_version",
    "freecad_build",
    "python_version",
    "ifcopenshell_version",
    "wrapper_sha256",
    "schemas",
    "open_callable",
    "validation_api",
    "unit_api",
    "qto_api",
    "malformed_spf_rejected",
    "valid_ifc4_validation_findings",
}
_SAFE_WORKER_ERRORS = {
    "bim_runtime_path_unsupported",
    "unsupported_freecad_build",
    "unsupported_ifcopenshell_runtime",
        "unsafe_output_path",
        "worker_failed",
}
_WORKER_FAILURE_STAGES = {
    "startup",
    "runtime-lock",
    "short-path",
    "wrapper-hash",
    "dll-directory",
    "dll-directory-missing",
    "dll-directory-registration",
    "freecad-import",
    "freecad-version",
    "python-version",
    "ifcopenshell-import",
    "ifcopenshell-api",
    "ifcopenshell-version",
    "schema-factories",
    "ifc-validation",
    "malformed-spf",
    "malformed-spf-accepted",
    "valid-ifc-validation-findings",
    "gui-check",
    "complete",
}


@dataclass(frozen=True, slots=True)
class BimRuntimeIdentity:
    freecad_version: str
    freecad_build: str
    ifcopenshell_version: str
    python_version: str
    platform: str
    freecadcmd_sha256: str
    wrapper_sha256: str


@dataclass(frozen=True, slots=True)
class BimWorkerResult:
    exit_code: int | None
    elapsed_seconds: float
    diagnostics_bytes: int
    error_code: str | None
    result_json: dict[str, object] | None


def _load_runtime_lock() -> dict[str, Any]:
    try:
        resource = resources.files("book_production_platform.bim_production").joinpath(
            "ifcopenshell-runtime.v1.json"
        )
        value = json.loads(resource.read_text(encoding="utf-8"))
    except (OSError, TypeError, ValueError, ModuleNotFoundError):
        raise BimProductionError("unsupported_ifcopenshell_runtime") from None
    required = {
        "schema_version",
        "freecad_version",
        "freecad_build",
        "freecad_platform",
        "freecadcmd_sha256",
        "python_version",
        "ifcopenshell_version",
        "wrapper_relative_path",
        "wrapper_sha256",
        "worker_protocol",
    }
    if (
        not isinstance(value, dict)
        or set(value) != required
        or value.get("schema_version") != "bim.ifcopenshell-runtime.v1"
        or value.get("worker_protocol") != WORKER_PROTOCOL
        or not all(isinstance(value.get(key), str) for key in required)
        or value.get("wrapper_relative_path") != WRAPPER_RELATIVE_PATH.as_posix()
    ):
        raise BimProductionError("unsupported_ifcopenshell_runtime") from None
    return value


def _host_platform() -> str:
    if platform.system() == "Windows" and platform.machine().upper() in {
        "AMD64",
        "X86_64",
    }:
        return "windows-x86_64"
    return f"{platform.system().lower()}-{platform.machine().lower()}"


def _is_link(path: Path) -> bool:
    try:
        return path.is_symlink() or path.is_junction()
    except OSError:
        return True


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _get_short_path_name(path: Path) -> str:
    """Return the Win32 short path, or an empty string if the API cannot."""

    if os.name != "nt":
        return ""
    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        function = kernel32.GetShortPathNameW
        function.argtypes = (ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_uint32)
        function.restype = ctypes.c_uint32
        buffer = ctypes.create_unicode_buffer(32768)
        length = function(str(path), buffer, len(buffer))
    except (AttributeError, OSError, TypeError, ValueError):
        return ""
    if length == 0 or length >= len(buffer):
        return ""
    return buffer.value


def windows_short_path(site_packages: Path) -> Path:
    """Resolve the existing site-packages directory and enforce native path limits."""

    if not isinstance(site_packages, Path) or _is_link(site_packages):
        raise BimProductionError("bim_runtime_path_unsupported") from None
    try:
        resolved = site_packages.resolve(strict=True)
        if not resolved.is_dir():
            raise OSError("site-packages unavailable")
        short_text = _get_short_path_name(resolved)
        if not short_text:
            raise OSError("short path unavailable")
        short_path = Path(short_text)
        wrapper_path = short_path / WRAPPER_RELATIVE_PATH
        if len(str(wrapper_path)) >= 260:
            raise OSError("native wrapper path too long")
        if _is_link(short_path) or _is_link(wrapper_path):
            raise OSError("linked runtime path")
    except (OSError, RuntimeError, ValueError):
        raise BimProductionError("bim_runtime_path_unsupported") from None
    return short_path


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
    environment.update(
        {
            "PATH": os.pathsep.join(str(path) for path in path_entries),
            "TEMP": str(workspace),
            "TMP": str(workspace),
            "APPDATA": str(workspace / "appdata"),
            "LOCALAPPDATA": str(workspace / "localappdata"),
            "USERPROFILE": str(workspace / "profile"),
            "HOME": str(workspace / "profile"),
        }
    )
    return environment


def inspect_bim_runtime(
    freecadcmd: Path, ifc_site_packages: Path
) -> BimRuntimeIdentity:
    """Verify FreeCADCmd and the native IfcOpenShell wrapper against the lock."""

    if not isinstance(freecadcmd, Path) or _is_link(freecadcmd):
        raise BimProductionError("unsupported_freecad_build") from None
    if freecadcmd.name.casefold() != "freecadcmd.exe" or not freecadcmd.is_file():
        raise BimProductionError("freecad_unavailable") from None
    if _host_platform() != "windows-x86_64":
        raise BimProductionError("unsupported_freecad_build") from None
    try:
        executable = freecadcmd.resolve(strict=True)
        executable_hash = _sha256_file(executable)
    except OSError:
        raise BimProductionError("freecad_unavailable") from None
    lock = _load_runtime_lock()
    if (
        lock["freecad_platform"] != _host_platform()
        or lock["freecadcmd_sha256"] != executable_hash
    ):
        raise BimProductionError("unsupported_freecad_build") from None

    short_site_packages = windows_short_path(ifc_site_packages)
    wrapper = short_site_packages / WRAPPER_RELATIVE_PATH
    if not wrapper.is_file() or _is_link(wrapper):
        raise BimProductionError("unsupported_ifcopenshell_runtime") from None
    try:
        wrapper_hash = _sha256_file(wrapper)
    except OSError:
        raise BimProductionError("unsupported_ifcopenshell_runtime") from None
    if wrapper_hash != lock["wrapper_sha256"]:
        raise BimProductionError("unsupported_ifcopenshell_runtime") from None

    try:
        with tempfile.TemporaryDirectory(prefix="bim-runtime-") as temporary:
            workspace = Path(temporary)
            result = subprocess.run(
                [str(executable), "--version"],
                cwd=workspace,
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
        raise BimProductionError("freecad_unavailable") from None
    match = _VERSION_PATTERN.search(f"{result.stdout}\n{result.stderr}")
    if result.returncode != 0 or match is None:
        raise BimProductionError("unsupported_freecad_build") from None
    freecad_version = f"FreeCAD {match.group('version')}"
    freecad_build = f"Revision: {match.group('build')}"
    if freecad_version != lock["freecad_version"] or freecad_build != lock["freecad_build"]:
        raise BimProductionError("unsupported_freecad_build") from None
    return BimRuntimeIdentity(
        freecad_version=freecad_version,
        freecad_build=freecad_build,
        ifcopenshell_version=lock["ifcopenshell_version"],
        python_version=lock["python_version"],
        platform=lock["freecad_platform"],
        freecadcmd_sha256=executable_hash,
        wrapper_sha256=wrapper_hash,
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
    if (
        not isinstance(value, dict)
        or set(value) - _WORKER_RESULT_KEYS
        or value.get("protocol") != WORKER_PROTOCOL
        or value.get("mode") != mode
        or not isinstance(value.get("status"), str)
        or value.get("status") not in {"ok", "error"}
    ):
        return None
    if value["status"] == "ok" and value.get("gui_loaded") is not False:
        return None
    if value["status"] == "error" and value.get("error_code") not in _SAFE_WORKER_ERRORS:
        return None
    if value["status"] == "error" and value.get("failure_stage") not in _WORKER_FAILURE_STAGES:
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

    reader = threading.Thread(target=drain, name="bim-worker-output-reader", daemon=True)
    reader.start()
    return reader, lambda: min(state["total"], MAX_DIAGNOSTIC_BYTES)


def _terminate_worker(process: subprocess.Popen[bytes]) -> int | None:
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


def _preflight_failure(
    result: dict[str, object], identity: BimRuntimeIdentity
) -> str | None:
    required = {
        "protocol",
        "mode",
        "status",
        "gui_loaded",
        "freecad_version",
        "freecad_build",
        "python_version",
        "ifcopenshell_version",
        "wrapper_sha256",
        "schemas",
        "open_callable",
        "validation_api",
        "unit_api",
        "qto_api",
        "malformed_spf_rejected",
        "valid_ifc4_validation_findings",
    }
    if set(result) != required:
        return "unsupported_ifcopenshell_runtime"
    if (
        result.get("freecad_version") != identity.freecad_version
        or result.get("freecad_build") != identity.freecad_build
    ):
        return "unsupported_freecad_build"
    if (
        result.get("python_version") != identity.python_version
        or result.get("ifcopenshell_version") != identity.ifcopenshell_version
        or result.get("wrapper_sha256") != identity.wrapper_sha256
    ):
        return "unsupported_ifcopenshell_runtime"
    if (
        result.get("schemas") != ["IFC2X3", "IFC4"]
        or result.get("open_callable") is not True
        or result.get("validation_api") is not True
        or result.get("unit_api") is not True
        or result.get("qto_api") is not True
        or result.get("malformed_spf_rejected") is not True
        or type(result.get("valid_ifc4_validation_findings")) is not int
        or result.get("valid_ifc4_validation_findings") != 0
    ):
        return "unsupported_ifcopenshell_runtime"
    return None


def run_bim_worker(
    freecadcmd: Path,
    ifc_site_packages: Path,
    mode: Literal["preflight", "takeoff", "fixture"] | str,
    request_path: Path,
    work_root: Path,
    timeout_s: int,
) -> BimWorkerResult:
    """Run one fixed BIM worker mode with bounded output and a strict deadline."""

    if mode not in WORKER_MODES:
        raise BimProductionError("invalid_worker_mode")
    if (
        isinstance(timeout_s, bool)
        or not isinstance(timeout_s, int)
        or not 1 <= timeout_s <= MAX_WORKER_TIMEOUT_SECONDS
    ):
        raise BimProductionError("invalid_timeout")
    if not isinstance(work_root, Path) or _is_link(work_root):
        raise BimProductionError("unsafe_output_path")
    try:
        root = work_root.resolve(strict=True)
        if not root.is_dir():
            raise OSError("worker root unavailable")
        if mode != "preflight":
            if not isinstance(request_path, Path) or _is_link(request_path):
                raise BimProductionError("invalid_bim_request")
            request = request_path.resolve(strict=True)
            if not request.is_file() or request.parent != root:
                raise BimProductionError("invalid_bim_request")
        else:
            request = None
    except BimProductionError:
        raise
    except (OSError, RuntimeError):
        raise BimProductionError("unsafe_output_path") from None
    if not WORKER_PATH.is_file() or _is_link(WORKER_PATH):
        raise BimProductionError("freecad_unavailable")
    try:
        result_path = root / f"worker-result-{mode}.json"
        if _is_link(result_path) or result_path.exists():
            raise BimProductionError("publication_failed")
    except OSError:
        raise BimProductionError("unsafe_output_path") from None

    identity = inspect_bim_runtime(freecadcmd, ifc_site_packages)
    started = time.perf_counter()
    diagnostics_total = 0
    exit_code: int | None = None
    with tempfile.TemporaryDirectory(prefix=".bim-worker-") as temporary:
        workspace = Path(temporary)
        command = [
            str(freecadcmd.resolve(strict=True)),
            "--console",
            str(WORKER_PATH.resolve(strict=True)),
            "--",
            mode,
            "--site-packages",
            str(ifc_site_packages.resolve(strict=True)),
            "--work-root",
            str(root),
        ]
        if request is not None:
            command.extend(("--request", str(request)))
        try:
            process = subprocess.Popen(
                command,
                cwd=workspace,
                env=_safe_environment(workspace, freecadcmd.resolve(strict=True)),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                shell=False,
            )
        except OSError:
            return BimWorkerResult(None, time.perf_counter() - started, 0, "freecad_unavailable", None)
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
                return BimWorkerResult(
                    exit_code,
                    time.perf_counter() - started,
                    diagnostics_total,
                    "worker_deadline_exceeded",
                    None,
                )
            if reader is not None:
                reader.join(timeout=2)
            diagnostics_total = diagnostic_count()
            if exit_code != 0:
                return BimWorkerResult(
                    exit_code,
                    time.perf_counter() - started,
                    diagnostics_total,
                    "worker_failed",
                    None,
                )
            result = _read_worker_result(result_path, mode)
            if result is None:
                return BimWorkerResult(
                    exit_code,
                    time.perf_counter() - started,
                    diagnostics_total,
                    "worker_failed",
                    None,
                )
            if mode == "preflight" and result.get("status") == "ok":
                preflight_error = _preflight_failure(result, identity)
                if preflight_error is not None:
                    return BimWorkerResult(
                        exit_code,
                        time.perf_counter() - started,
                        diagnostics_total,
                        preflight_error,
                        None,
                    )
            error_code = result.get("error_code") if result.get("status") == "error" else None
            if error_code is not None and not isinstance(error_code, str):
                error_code = "worker_failed"
            return BimWorkerResult(
                exit_code,
                time.perf_counter() - started,
                diagnostics_total,
                error_code,
                result,
            )
        finally:
            if reader is not None:
                reader.join(timeout=2)
            if process.stdout is not None:
                process.stdout.close()
