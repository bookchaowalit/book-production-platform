"""Data-only worker entry point executed by the pinned FreeCAD Python runtime."""

from __future__ import annotations

import argparse
import ctypes
import hashlib
import importlib
import json
import math
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, NoReturn

from book_production_platform.bim_production.errors import BimProductionError

PROTOCOL = "bim-worker.v1"
MAX_RESULT_BYTES = 16 * 1024
MAX_REQUEST_BYTES = 64 * 1024
MAX_IFC_INSTANCES = 250_000
WRAPPER_RELATIVE_PATH = Path(
    "ifcopenshell/_ifcopenshell_wrapper.cp311-win_amd64.pyd"
)
SUPPORTED_SCHEMAS = frozenset({"IFC2X3", "IFC4"})
SUPPORTED_ELEMENT_CLASSES = (
    "IfcWall",
    "IfcSlab",
    "IfcColumn",
    "IfcBeam",
    "IfcDoor",
    "IfcWindow",
)
GLOBAL_ID_PATTERN = re.compile(r"[0-9A-Za-z_$]{22}")
TAKEOFF_ID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}")
_QUANTITY_KINDS = frozenset(
    {
        "IfcQuantityLength",
        "IfcQuantityArea",
        "IfcQuantityVolume",
        "IfcQuantityCount",
    }
)
_TAKEOFF_REQUEST_KEYS = {
    "protocol",
    "mode",
    "takeoff_id",
    "ifc_path",
    "element_classes",
    "expected_schemas",
    "max_instances",
}
_FAILURE_CODE = "worker_failed"
_FAILURE_STAGE = "startup"
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
    "request-invalid",
    "ifc-parse",
    "schema-gate",
    "instance-bound",
    "identity",
    "ifc-validation",
    "unit-assignment",
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


def _bootstrap(site_packages: Path, load_schema_factories: bool) -> dict[str, Any]:
    """Verify the pinned runtime through the short path and import its fixed APIs."""

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
        factories: dict[str, Any] = {}
        if load_schema_factories:
            _FAILURE_STAGE = "schema-factories"
            # Opening both empty schemas loads the native schema factories in this process.
            factories = {
                "IFC2X3": ifcopenshell.file(schema="IFC2X3"),
                "IFC4": ifcopenshell.file(schema="IFC4"),
            }
        return {
            "freecad_version": actual_freecad_version,
            "ifcopenshell": ifcopenshell,
            "validate": validate_api,
            "unit": unit_api,
            "element": element_api,
            "wrapper_hash": wrapper_hash,
            "factories": factories,
        }
    finally:
        dll_cookie.close()


def _reject_duplicate_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    value: dict[str, object] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate key")
        value[key] = item
    return value


def _reject_non_finite(value: str) -> NoReturn:
    raise ValueError(f"non-finite JSON constant: {value}")


def _read_bounded_json(path: Path) -> dict[str, object]:
    payload = path.read_bytes()
    if len(payload) > MAX_REQUEST_BYTES:
        raise ValueError("request exceeds its bound")
    value = json.loads(
        payload.decode("utf-8"),
        object_pairs_hook=_reject_duplicate_pairs,
        parse_constant=_reject_non_finite,
    )
    if not isinstance(value, dict):
        raise TypeError("request is not an object")
    return value


@dataclass(frozen=True, slots=True)
class _TakeoffRequest:
    takeoff_id: str
    ifc_path: str
    element_classes: tuple[str, ...]
    expected_schemas: frozenset[str]
    max_instances: int


def _load_takeoff_request(path: Path) -> _TakeoffRequest:
    global _FAILURE_STAGE
    _FAILURE_STAGE = "request-invalid"
    try:
        request = _read_bounded_json(path)
    except (OSError, UnicodeDecodeError, ValueError, TypeError, RecursionError):
        raise BimProductionError("invalid_bim_request") from None
    if set(request) != _TAKEOFF_REQUEST_KEYS or request.get("protocol") != PROTOCOL:
        raise BimProductionError("invalid_bim_request")
    takeoff_id = request.get("takeoff_id")
    if not isinstance(takeoff_id, str) or not TAKEOFF_ID_PATTERN.fullmatch(takeoff_id):
        raise BimProductionError("invalid_bim_request")
    classes = request.get("element_classes")
    schemas = request.get("expected_schemas")
    if not isinstance(classes, list) or not classes:
        raise BimProductionError("invalid_bim_request")
    if not all(isinstance(item, str) for item in classes):
        raise BimProductionError("invalid_bim_request")
    if len(set(classes)) != len(classes) or not set(classes) <= set(SUPPORTED_ELEMENT_CLASSES):
        raise BimProductionError("invalid_bim_request")
    if not isinstance(schemas, list) or not schemas:
        raise BimProductionError("invalid_bim_request")
    if not all(isinstance(item, str) for item in schemas):
        raise BimProductionError("invalid_bim_request")
    if not set(schemas) <= SUPPORTED_SCHEMAS:
        raise BimProductionError("invalid_bim_request")
    limit = request.get("max_instances")
    if (
        isinstance(limit, bool)
        or not isinstance(limit, int)
        or not 1 <= limit <= MAX_IFC_INSTANCES
    ):
        raise BimProductionError("invalid_bim_request")
    if request.get("mode") != "takeoff" or not isinstance(request.get("ifc_path"), str):
        raise BimProductionError("invalid_bim_request")
    _FAILURE_STAGE = "ifc-parse"
    return _TakeoffRequest(
        takeoff_id=takeoff_id,
        ifc_path=str(request["ifc_path"]),
        element_classes=tuple(str(item) for item in classes),
        expected_schemas=frozenset(str(item) for item in schemas),
        max_instances=limit,
    )


def _validate_unit_assignments(model: Any) -> None:
    """Reject malformed project unit assignments that would corrupt conversion."""

    global _FAILURE_STAGE
    _FAILURE_STAGE = "unit-assignment"
    for project in model.by_type("IfcProject"):
        assignment = getattr(project, "UnitsInContext", None)
        if assignment is None:
            continue
        for unit in getattr(assignment, "Units", None) or []:
            if unit.is_a("IfcSIUnit"):
                continue
            if not unit.is_a("IfcConversionBasedUnit"):
                raise BimProductionError("ifc_schema_invalid")
            current = unit
            while current.is_a("IfcConversionBasedUnit"):
                factor = getattr(current, "ConversionFactor", None)
                value = getattr(factor, "ValueComponent", None)
                raw = getattr(value, "wrappedValue", value)
                if (
                    isinstance(raw, bool)
                    or not isinstance(raw, (int, float))
                    or not math.isfinite(raw)
                    or raw <= 0
                ):
                    raise BimProductionError("ifc_schema_invalid")
                current = getattr(factor, "UnitComponent", None)
                if current is None:
                    raise BimProductionError("ifc_schema_invalid")
            if not current.is_a("IfcSIUnit"):
                raise BimProductionError("ifc_schema_invalid")


def _takeoff(site_packages: Path, request_path: Path, work_root: Path) -> dict[str, object]:
    """Validate IFC syntax, schema, identity, and units for one staged request."""

    global _FAILURE_STAGE
    request = _load_takeoff_request(request_path)
    runtime = _bootstrap(site_packages, load_schema_factories=False)
    ifcopenshell = runtime["ifcopenshell"]
    validate_api = runtime["validate"]
    ifc_path = Path(request.ifc_path)
    if _is_link(ifc_path) or ifc_path.parent != work_root or ifc_path.name != "source.ifc":
        raise BimProductionError("ifc_path_invalid")
    try:
        model = ifcopenshell.open(str(ifc_path))
    except BimProductionError:
        raise
    # IfcOpenShell raises a native parser exception for invalid SPF input.
    except Exception:  # noqa: BLE001
        raise BimProductionError("ifc_parse_invalid") from None
    _FAILURE_STAGE = "schema-gate"
    schema = str(model.schema)
    if schema not in request.expected_schemas:
        raise BimProductionError("unsupported_ifc_schema")
    _FAILURE_STAGE = "instance-bound"
    total_instances = sum(1 for _ in iter(model))
    if not isinstance(total_instances, int) or total_instances > request.max_instances:
        raise BimProductionError("ifc_input_too_large")
    _FAILURE_STAGE = "identity"
    selected_elements = 0
    seen_global_ids: set[str] = set()
    for class_name in request.element_classes:
        for element in model.by_type(str(class_name)):
            global_id = getattr(element, "GlobalId", None)
            if not isinstance(global_id, str) or not GLOBAL_ID_PATTERN.fullmatch(global_id):
                raise BimProductionError("ifc_identity_invalid")
            if global_id in seen_global_ids:
                raise BimProductionError("ifc_identity_invalid")
            seen_global_ids.add(global_id)
            selected_elements += 1
    _FAILURE_STAGE = "ifc-validation"
    logger = validate_api.json_logger()
    validate_api.validate(model, logger, express_rules=False)
    if logger.statements:
        raise BimProductionError("ifc_schema_invalid")
    _validate_unit_assignments(model)
    projects = model.by_type("IfcProject")
    project_identity_hash = ""
    if projects:
        project = projects[0]
        project_identity_hash = hashlib.sha256(
            f"{getattr(project, 'GlobalId', '')}|{getattr(project, 'Name', '')}".encode()
        ).hexdigest()
    _FAILURE_STAGE = "gui-check"
    if "FreeCADGui" in sys.modules:
        raise BimProductionError("unsupported_ifcopenshell_runtime")
    _FAILURE_STAGE = "complete"
    return {
        "validation": {
            "schema": str(schema),
            "project_identity_hash": project_identity_hash,
            "total_instances": total_instances,
            "selected_elements": selected_elements,
            "validation_findings": len(logger.statements),
        }
    }


def _preflight(site_packages: Path, work_root: Path) -> dict[str, object]:
    global _FAILURE_CODE, _FAILURE_STAGE
    _FAILURE_CODE = "unsupported_ifcopenshell_runtime"
    runtime = _bootstrap(site_packages, load_schema_factories=True)
    ifcopenshell = runtime["ifcopenshell"]
    validate_api = runtime["validate"]
    unit_api = runtime["unit"]
    element_api = runtime["element"]
    wrapper_hash = str(runtime["wrapper_hash"])
    ifc2x3 = runtime["factories"]["IFC2X3"]
    ifc4 = runtime["factories"]["IFC4"]
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
        "freecad_version": str(runtime["freecad_version"]),
        "freecad_build": str(_load_lock()["freecad_build"]),
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
        if mode == "preflight":
            values = _preflight(Path(args.site_packages), work_root)
        elif mode == "takeoff":
            if not args.request:
                raise BimProductionError("invalid_bim_request")
            values = _takeoff(
                Path(args.site_packages), Path(args.request), work_root
            )
        else:
            # The fixture mode is implemented only by the test fixture macro.
            raise BimProductionError("worker_failed")
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


def bootstrap_bim_runtime(site_packages: Path) -> dict[str, Any]:
    """Verify the pinned runtime and expose its fixed APIs for test tooling only."""

    return _bootstrap(site_packages, load_schema_factories=False)


def write_worker_result(path: Path, value: dict[str, object]) -> None:
    """Write one bounded worker result atomically for the fixed protocol."""

    _atomic_json(path, value)
