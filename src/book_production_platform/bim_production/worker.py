"""Data-only worker entry point executed by the pinned FreeCAD Python runtime."""

from __future__ import annotations

import argparse
import ctypes
import hashlib
import importlib
import json
import os
import sys
from pathlib import Path
from typing import Any

from book_production_platform.bim_production.errors import BimProductionError

PROTOCOL = "bim-worker.v1"
MAX_RESULT_BYTES = 16 * 1024
WRAPPER_RELATIVE_PATH = Path(
    "ifcopenshell/_ifcopenshell_wrapper.cp311-win_amd64.pyd"
)
_FAILURE_CODE = "worker_failed"
_FAILURE_STAGE = "startup"
_WORKER_FAILURE_STAGES = {
    "startup",
    "runtime-lock",
    "short-path",
    "wrapper-hash",
    "dll-directory",
    "freecad-import",
    "freecad-version",
    "python-version",
    "ifcopenshell-import",
    "ifcopenshell-api",
    "ifcopenshell-version",
    "schema-factories",
    "ifc-validation",
    "malformed-spf",
    "gui-check",
    "complete",
}


def _is_link(path: Path) -> bool:
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return False
    except OSError:
        return True
    junction = bool(getattr(metadata, "st_file_attributes", 0) & 0x400)
    return path.is_symlink() or junction


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _load_lock() -> dict[str, Any]:
    try:
        value = json.loads(
            Path(__file__).with_name("ifcopenshell-runtime.v1.json").read_text(
                encoding="utf-8"
            )
        )
    except (OSError, TypeError, ValueError):
        raise BimProductionError("unsupported_ifcopenshell_runtime") from None
    if (
        not isinstance(value, dict)
        or value.get("schema_version") != "bim.ifcopenshell-runtime.v1"
        or value.get("worker_protocol") != PROTOCOL
        or value.get("wrapper_relative_path") != WRAPPER_RELATIVE_PATH.as_posix()
    ):
        raise BimProductionError("unsupported_ifcopenshell_runtime") from None
    return value


def _short_path(path: Path) -> Path:
    if os.name != "nt" or _is_link(path) or not path.is_dir():
        raise BimProductionError("bim_runtime_path_unsupported") from None
    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        function = kernel32.GetShortPathNameW
        function.argtypes = (ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_uint32)
        function.restype = ctypes.c_uint32
        buffer = ctypes.create_unicode_buffer(32768)
        size = function(str(path.resolve(strict=True)), buffer, len(buffer))
        if size == 0 or size >= len(buffer):
            raise OSError("short path unavailable")
        result = Path(buffer.value)
        wrapper = result / WRAPPER_RELATIVE_PATH
        if len(str(wrapper)) >= 260 or _is_link(wrapper):
            raise OSError("native module path unavailable")
        return result
    except (AttributeError, OSError, RuntimeError, TypeError, ValueError):
        raise BimProductionError("bim_runtime_path_unsupported") from None


def _atomic_json(path: Path, value: dict[str, object]) -> None:
    payload = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8") + b"\n"
    if len(payload) > MAX_RESULT_BYTES:
        raise ValueError("worker result exceeds its bound")
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("xb") as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def _preflight(site_packages: Path, work_root: Path) -> dict[str, object]:
    global _FAILURE_CODE, _FAILURE_STAGE
    _FAILURE_CODE = "bim_runtime_path_unsupported"
    _FAILURE_STAGE = "runtime-lock"
    lock = _load_lock()
    _FAILURE_STAGE = "short-path"
    short_packages = _short_path(site_packages)
    _FAILURE_CODE = "unsupported_ifcopenshell_runtime"
    _FAILURE_STAGE = "wrapper-hash"
    wrapper = short_packages / WRAPPER_RELATIVE_PATH
    if not wrapper.is_file() or _is_link(wrapper):
        raise BimProductionError("unsupported_ifcopenshell_runtime")
    wrapper_hash = _sha256_file(wrapper)
    if wrapper_hash != lock["wrapper_sha256"]:
        raise BimProductionError("unsupported_ifcopenshell_runtime")
    if len(short_packages.parents) < 2 or short_packages.parents[1].name.casefold() != "bin":
        _FAILURE_STAGE = "dll-directory-missing"
        raise BimProductionError("unsupported_ifcopenshell_runtime")
    dll_directory = short_packages.parents[1]
    if not dll_directory.is_dir():
        _FAILURE_STAGE = "dll-directory-missing"
        raise BimProductionError("unsupported_ifcopenshell_runtime")
    try:
        dll_cookie = os.add_dll_directory(str(dll_directory))
    except (AttributeError, OSError):
        _FAILURE_STAGE = "dll-directory-registration"
        raise BimProductionError("unsupported_ifcopenshell_runtime") from None
    sys.path.insert(0, str(short_packages))
    try:
        _FAILURE_STAGE = "freecad-import"
        freecad = importlib.import_module("FreeCAD")
        version_parts = freecad.Version()
        _FAILURE_STAGE = "freecad-version"
        if (
            not isinstance(version_parts, (list, tuple))
            or len(version_parts) < 3
            or ".".join(str(part) for part in version_parts[:3]) != "1.1.3"
        ):
            raise BimProductionError("unsupported_freecad_build")
        actual_freecad_version = "FreeCAD " + ".".join(
            str(part) for part in version_parts[:3]
        )
        if f"{sys.version_info.major}.{sys.version_info.minor}" != lock["python_version"]:
            _FAILURE_STAGE = "python-version"
            raise BimProductionError("unsupported_freecad_build")
        _FAILURE_STAGE = "ifcopenshell-import"
        ifcopenshell = importlib.import_module("ifcopenshell")
        _FAILURE_STAGE = "ifcopenshell-api"
        validate_api = importlib.import_module("ifcopenshell.validate")
        unit_api = importlib.import_module("ifcopenshell.util.unit")
        element_api = importlib.import_module("ifcopenshell.util.element")
        if getattr(ifcopenshell, "__version__", None) != lock["ifcopenshell_version"]:
            _FAILURE_STAGE = "ifcopenshell-version"
            raise BimProductionError("unsupported_ifcopenshell_runtime")
        _FAILURE_STAGE = "schema-factories"
        # Opening both empty schemas loads the native schema factories in this process.
        ifc2x3 = ifcopenshell.file(schema="IFC2X3")
        ifc4 = ifcopenshell.file(schema="IFC4")
        valid_path = work_root / "preflight-valid-ifc4.ifc"
        malformed_path = work_root / "preflight-malformed.ifc"
        for path in (valid_path, malformed_path):
            if _is_link(path) or path.exists():
                raise BimProductionError("unsafe_output_path")
        ifc4.write(str(valid_path))
        reopened = ifcopenshell.open(str(valid_path))
        logger = validate_api.json_logger()
        _FAILURE_STAGE = "ifc-validation"
        validate_api.validate(reopened, logger, express_rules=False)
        malformed_path.write_text("This is not an IFC-SPF file;\n", encoding="utf-8")
        _FAILURE_STAGE = "malformed-spf"
        try:
            ifcopenshell.open(str(malformed_path))
        # IfcOpenShell raises a native parser exception for invalid SPF text.
        except Exception:  # noqa: BLE001
            malformed_rejected = True
        else:
            malformed_rejected = False
        finally:
            valid_path.unlink(missing_ok=True)
            malformed_path.unlink(missing_ok=True)
        if not malformed_rejected:
            _FAILURE_STAGE = "malformed-spf-accepted"
            raise BimProductionError("unsupported_ifcopenshell_runtime")
        if logger.statements:
            _FAILURE_STAGE = "valid-ifc-validation-findings"
            raise BimProductionError("unsupported_ifcopenshell_runtime")
        _FAILURE_STAGE = "gui-check"
        if "FreeCADGui" in sys.modules:
            raise BimProductionError("unsupported_ifcopenshell_runtime")
        _FAILURE_CODE = "worker_failed"
        _FAILURE_STAGE = "complete"
        return {
            "freecad_version": actual_freecad_version,
            "freecad_build": str(lock["freecad_build"]),
            "python_version": f"{sys.version_info.major}.{sys.version_info.minor}",
            "ifcopenshell_version": str(ifcopenshell.__version__),
            "wrapper_sha256": wrapper_hash,
            "schemas": [ifc2x3.schema, ifc4.schema],
            "open_callable": callable(getattr(ifcopenshell, "open", None)),
            "validation_api": callable(getattr(validate_api, "validate", None)),
            "unit_api": callable(getattr(unit_api, "get_project_unit", None)),
            "qto_api": callable(getattr(element_api, "get_psets", None)),
            "malformed_spf_rejected": malformed_rejected,
            "valid_ifc4_validation_findings": len(logger.statements),
        }
    finally:
        dll_cookie.close()


def _arguments(argv: list[str]) -> argparse.Namespace:
    try:
        marker = argv.index("--")
    except ValueError:
        raise BimProductionError("worker_failed") from None
    parser = argparse.ArgumentParser(prog="bim-worker", add_help=False)
    parser.add_argument("mode", choices=("preflight", "takeoff", "fixture"))
    parser.add_argument("--site-packages", required=True)
    parser.add_argument("--work-root", required=True)
    parser.add_argument("--request")
    return parser.parse_args(argv[marker + 1 :])


def main(argv: list[str]) -> None:
    global _FAILURE_CODE
    mode = "preflight"
    work_root = Path.cwd()
    result_path = work_root / "worker-result-preflight.json"
    try:
        args = _arguments(argv)
        mode = args.mode
        work_root = Path(args.work_root).resolve(strict=True)
        result_path = work_root / f"worker-result-{mode}.json"
        if _is_link(work_root) or not work_root.is_dir() or _is_link(result_path) or result_path.exists():
            raise BimProductionError("unsafe_output_path")
        if mode != "preflight":
            # These fixed modes are implemented by subsequent bounded plan tasks.
            raise BimProductionError("worker_failed")
        values = _preflight(Path(args.site_packages), work_root)
        response: dict[str, object] = {
            "protocol": PROTOCOL,
            "mode": mode,
            "status": "ok",
            "gui_loaded": "FreeCADGui" in sys.modules,
            **values,
        }
        _atomic_json(result_path, response)
    except BimProductionError as error:
        _FAILURE_CODE = error.code
        try:
            if not _is_link(work_root) and work_root.is_dir() and not result_path.exists():
                _atomic_json(
                    result_path,
                    {
                        "protocol": PROTOCOL,
                        "mode": mode,
                        "status": "error",
                        "error_code": error.code,
                        "failure_stage": _FAILURE_STAGE,
                    },
                )
        except (OSError, TypeError, ValueError):
            return
    # Keep unexpected native-runtime diagnostics and private paths inside the worker.
    except Exception:  # noqa: BLE001
        try:
            if not _is_link(work_root) and work_root.is_dir() and not result_path.exists():
                _atomic_json(
                    result_path,
                    {
                        "protocol": PROTOCOL,
                        "mode": mode,
                        "status": "error",
                        "error_code": _FAILURE_CODE,
                        "failure_stage": _FAILURE_STAGE,
                    },
                )
        except (OSError, TypeError, ValueError):
            return
