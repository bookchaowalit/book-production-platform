"""Content-addressed CAD jobs with verified replay and fail-closed publication."""

from __future__ import annotations

import errno
import hashlib
import importlib
import json
import math
import os
import shutil
import tempfile
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import NoReturn

from book_production_platform.cad_ir import (
    PartIR,
    PartIRError,
    canonical_part_ir,
    load_part_ir,
)
from book_production_platform.cad_production.drawing import (
    DRAWING_PROFILE,
    write_drawings,
)
from book_production_platform.cad_production.errors import CadProductionError
from book_production_platform.cad_production.manual import build_freecad_guide
from book_production_platform.cad_production.preview import PREVIEW_PROFILE
from book_production_platform.cad_production.qa import CadQAResult, verify_cad_outputs
from book_production_platform.cad_production.runtime import (
    MAX_WORKER_TIMEOUT_SECONDS,
    WORKER_PROTOCOL,
    FreeCADIdentity,
    WorkerResult,
    _load_runtime_lock,
    inspect_freecad_runtime,
    run_freecad_worker,
)
from book_production_platform.cad_production.tracing import (
    record_artifact,
    record_source_file,
    trace_job_dir,
    trace_root,
    write_ir_file,
)
from book_production_platform.production_trace import TraceRecorder

RECIPE_VERSION = "cad-parametric-mounting-plate-v1a"
MANIFEST_SCHEMA = "cad-qa-manifest.v1"
RECEIPT_SCHEMA = "cad-job.receipt.v1"
FINGERPRINT_SCHEMA = "cad-job-fingerprint.v1"
LOCK_TIMEOUT_SECONDS = 15.0
MAX_MANIFEST_BYTES = 64 * 1024
MAX_RECEIPT_BYTES = 16 * 1024
MAX_ARTIFACT_BYTES = 64 * 1024 * 1024

ARTIFACT_NAMES = frozenset(
    {"part.FCStd", "part.step", "part.stl", "preview.png", "qa-manifest.json"}
)
PRE_MANIFEST_NAMES = frozenset({"part.FCStd", "part.step", "part.stl", "preview.png"})
_TRANSIENT_NAMES = frozenset(
    {
        ".part-ir.json",
        "tessellation.json",
        "worker-result-build.json",
        "worker-result-verify-native.json",
        "worker-result-verify-step.json",
    }
)
_HASH_PATTERN = frozenset("0123456789abcdef")

if os.name == "nt":
    import msvcrt


@dataclass(frozen=True, slots=True)
class CadJobResult:
    fingerprint: str | None
    status: str
    artifact_dir: Path | None
    artifact_hashes: dict[str, str]
    qa: CadQAResult | None
    replay: bool
    bytes_written: int
    model_calls: int
    error_code: str | None
    receipt_recovered: bool = False
    trace_job_id: str | None = field(default=None, compare=False)

    def to_dict(self) -> dict[str, object]:
        return {
            "trace_job_id": self.trace_job_id,
            "fingerprint": self.fingerprint,
            "status": self.status,
            "artifact_dir": str(self.artifact_dir) if self.artifact_dir is not None else None,
            "artifact_names": sorted(self.artifact_hashes),
            "artifact_hashes": dict(self.artifact_hashes),
            "qa": asdict(self.qa) if self.qa is not None else None,
            "replay": self.replay,
            "bytes_written": self.bytes_written,
            "model_calls": self.model_calls,
            "error_code": self.error_code,
            "receipt_recovered": self.receipt_recovered,
        }


def _json_bytes(value: object) -> bytes:
    try:
        return (
            json.dumps(
                value,
                ensure_ascii=False,
                allow_nan=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
            + b"\n"
        )
    except (TypeError, ValueError):
        _fail("publication_failed")


def _reject_constant(value: str) -> NoReturn:
    raise ValueError(value)


def _object_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate key")
        result[key] = value
    return result


def _fail(code: str) -> NoReturn:
    raise CadProductionError(code) from None


def _is_link(path: Path) -> bool:
    try:
        return path.is_symlink() or path.is_junction()
    except (OSError, AttributeError):
        return True


def _reject_link_components(path: Path) -> None:
    absolute = Path(os.path.abspath(path))
    current = Path(absolute.anchor)
    for part in absolute.parts[1:]:
        current = current / part
        if _is_link(current):
            _fail("unsafe_output_path")


def _ensure_directory(path: Path) -> None:
    _reject_link_components(path)
    try:
        if path.exists() and not path.is_dir():
            _fail("unsafe_output_path")
        path.mkdir(parents=True, exist_ok=True)
    except CadProductionError:
        raise
    except OSError:
        _fail("unsafe_output_path")
    _reject_link_components(path)
    if not path.is_dir():
        _fail("unsafe_output_path")


def _lock_descriptor(descriptor: int, *, unlock: bool = False) -> None:
    if os.name == "nt":
        mode = msvcrt.LK_UNLCK if unlock else msvcrt.LK_NBLCK
        msvcrt.locking(descriptor, mode, 1)
        return
    fcntl = importlib.import_module("fcntl")
    operation = fcntl.LOCK_UN if unlock else fcntl.LOCK_EX | fcntl.LOCK_NB
    fcntl.flock(descriptor, operation)


@contextmanager
def _job_lock(path: Path) -> Iterator[None]:
    _ensure_directory(path.parent)
    _reject_link_components(path)
    lock_file = None
    try:
        lock_file = path.open("a+b")
        lock_file.seek(0, os.SEEK_END)
        if lock_file.tell() == 0:
            lock_file.write(b"\0")
            lock_file.flush()
        deadline = time.monotonic() + LOCK_TIMEOUT_SECONDS
        while True:
            lock_file.seek(0)
            try:
                _lock_descriptor(lock_file.fileno())
                break
            except OSError as error:
                if error.errno not in {errno.EACCES, errno.EAGAIN, errno.EDEADLK, None}:
                    _fail("publication_failed")
                if time.monotonic() >= deadline:
                    _fail("publication_failed")
                time.sleep(0.025)
        try:
            yield
        finally:
            lock_file.seek(0)
            _lock_descriptor(lock_file.fileno(), unlock=True)
    except CadProductionError:
        raise
    except OSError:
        _fail("publication_failed")
    finally:
        if lock_file is not None:
            lock_file.close()


def _identity_document(identity: FreeCADIdentity) -> dict[str, str]:
    return {
        "version": identity.version,
        "build": identity.build,
        "platform": identity.platform,
        "executable_sha256": identity.executable_sha256,
    }


def _canonical_fingerprint(canonical_ir: bytes, identity: FreeCADIdentity) -> tuple[str, str]:
    lock = _load_runtime_lock()
    if lock.get("preview_profile") != PREVIEW_PROFILE:
        _fail("unsupported_freecad_build")
    ir_hash = hashlib.sha256(canonical_ir).hexdigest()
    payload = {
        "schema_version": FINGERPRINT_SCHEMA,
        "part_ir_sha256": ir_hash,
        "part_ir_schema_version": "part-ir.v1",
        "template": "mounting-plate.v1",
        "recipe_version": RECIPE_VERSION,
        "worker_protocol": WORKER_PROTOCOL,
        "runtime": _identity_document(identity),
        "runtime_lock_schema": lock["schema_version"],
        "preview_profile": PREVIEW_PROFILE,
    }
    return hashlib.sha256(_json_bytes(payload)).hexdigest(), ir_hash


def _normalized_root(path: Path) -> Path:
    if not isinstance(path, Path):
        _fail("unsafe_output_path")
    expanded = path.expanduser()
    absolute = expanded if expanded.is_absolute() else Path.cwd() / expanded
    return Path(os.path.abspath(absolute))


def _file_metadata(path: Path) -> dict[str, object]:
    if _is_link(path) or not path.is_file():
        _fail("artifact_read_error")
    try:
        size = path.stat().st_size
        if not 0 < size <= MAX_ARTIFACT_BYTES:
            _fail("artifact_read_error")
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
    except CadProductionError:
        raise
    except OSError:
        _fail("artifact_read_error")
    return {"sha256": digest.hexdigest(), "bytes": size}


def _artifact_metadata(root: Path, names: frozenset[str]) -> dict[str, dict[str, object]]:
    if _is_link(root) or not root.is_dir():
        _fail("artifact_set_mismatch")
    try:
        if {entry.name for entry in root.iterdir()} != names:
            _fail("artifact_set_mismatch")
    except OSError:
        _fail("artifact_read_error")
    return {name: _file_metadata(root / name) for name in sorted(names)}


def _metadata_byte_sum(metadata: dict[str, dict[str, object]]) -> int:
    total = 0
    for item in metadata.values():
        size = item.get("bytes")
        if type(size) is not int or size <= 0:
            _fail("artifact_read_error")
        total += size
    return total


def _canonical_json_object(path: Path, max_bytes: int) -> dict[str, object]:
    if _is_link(path):
        raise ValueError("invalid json")
    try:
        if not path.is_file() or path.stat().st_size > max_bytes:
            raise ValueError("invalid json")
        payload = path.read_bytes()
        value = json.loads(
            payload.decode("utf-8"),
            object_pairs_hook=_object_pairs,
            parse_constant=_reject_constant,
        )
    except (OSError, UnicodeDecodeError, ValueError, TypeError, RecursionError):
        raise ValueError("invalid json") from None
    if not isinstance(value, dict) or _json_bytes(value) != payload:
        raise ValueError("invalid json")
    return value


def _hash_is_valid(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in _HASH_PATTERN for character in value)
    )


def _parse_qa(value: object) -> CadQAResult:
    if not isinstance(value, dict) or set(value) != {
        "bounds_mm",
        "volume_mm3",
        "solid_count",
        "bore_count",
        "step_relative_volume_error",
        "stl_triangle_count",
        "stl_volume_mm3",
        "tessellation_vertices",
        "tessellation_faces",
        "preview_profile",
    }:
        _fail("replay_integrity_error")
    bounds = value["bounds_mm"]
    numeric_fields = ("volume_mm3", "step_relative_volume_error", "stl_volume_mm3")
    if (
        not isinstance(bounds, (list, tuple))
        or len(bounds) != 6
        or any(
            isinstance(item, bool) or not isinstance(item, (int, float)) or not math.isfinite(item)
            for item in bounds
        )
        or any(
            isinstance(value[field], bool)
            or not isinstance(value[field], (int, float))
            or not math.isfinite(value[field])
            for field in numeric_fields
        )
        or type(value["solid_count"]) is not int
        or value["solid_count"] != 1
        or type(value["bore_count"]) is not int
        or value["bore_count"] != 4
        or type(value["stl_triangle_count"]) is not int
        or not 1 <= value["stl_triangle_count"] <= 100_000
        or type(value["tessellation_vertices"]) is not int
        or not 1 <= value["tessellation_vertices"] <= 100_000
        or type(value["tessellation_faces"]) is not int
        or not 1 <= value["tessellation_faces"] <= 100_000
        or value["volume_mm3"] <= 0
        or value["stl_volume_mm3"] <= 0
        or value["step_relative_volume_error"] < 0
        or value["step_relative_volume_error"] > 0.001
        or value["preview_profile"] != PREVIEW_PROFILE
    ):
        _fail("replay_integrity_error")
    return CadQAResult(
        bounds_mm=tuple(float(item) for item in bounds),  # type: ignore[arg-type]
        volume_mm3=float(value["volume_mm3"]),
        solid_count=value["solid_count"],
        bore_count=value["bore_count"],
        step_relative_volume_error=float(value["step_relative_volume_error"]),
        stl_triangle_count=value["stl_triangle_count"],
        stl_volume_mm3=float(value["stl_volume_mm3"]),
        tessellation_vertices=value["tessellation_vertices"],
        tessellation_faces=value["tessellation_faces"],
        preview_profile=PREVIEW_PROFILE,
    )


def _receipt_bytes(
    fingerprint: str,
    identity: dict[str, str],
    artifacts: dict[str, dict[str, object]],
) -> bytes:
    return _json_bytes(
        {
            "schema_version": RECEIPT_SCHEMA,
            "fingerprint": fingerprint,
            "runtime": identity,
            "artifacts": artifacts,
        }
    )


def _write_receipt_exclusive(path: Path, payload: bytes) -> int:
    _ensure_directory(path.parent)
    _reject_link_components(path)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb", prefix=f".{path.name}.", suffix=".tmp", dir=path.parent, delete=False
        ) as stream:
            temporary = Path(stream.name)
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        if _is_link(path) or path.exists():
            _fail("replay_integrity_error")
        os.link(temporary, path)
        temporary.unlink()
        return len(payload)
    except CadProductionError:
        raise
    except FileExistsError:
        _fail("replay_integrity_error")
    except OSError:
        _fail("publication_failed")
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass


def _verify_replay(
    fingerprint: str,
    artifact_dir: Path,
    receipt_path: Path,
    identity: dict[str, str],
) -> CadJobResult:
    try:
        _reject_link_components(receipt_path)
        receipt = _canonical_json_object(receipt_path, MAX_RECEIPT_BYTES)
    except CadProductionError:
        _fail("replay_integrity_error")
    except (OSError, ValueError, TypeError, RecursionError):
        _fail("replay_integrity_error")
    return _verify_against_receipt(fingerprint, artifact_dir, receipt, identity)


def _verify_against_receipt(
    fingerprint: str,
    artifact_dir: Path,
    receipt: dict[str, object],
    identity: dict[str, str],
) -> CadJobResult:
    try:
        _reject_link_components(artifact_dir)
        if (
            artifact_dir.is_symlink()
            or not artifact_dir.is_dir()
            or set(receipt)
            != {"schema_version", "fingerprint", "runtime", "artifacts"}
            or receipt.get("schema_version") != RECEIPT_SCHEMA
            or receipt.get("fingerprint") != fingerprint
            or receipt.get("runtime") != identity
        ):
            _fail("replay_integrity_error")
        receipt_artifacts = receipt.get("artifacts")
        if not isinstance(receipt_artifacts, dict) or set(receipt_artifacts) != ARTIFACT_NAMES:
            _fail("replay_integrity_error")
        metadata = _artifact_metadata(artifact_dir, ARTIFACT_NAMES)
        hashes: dict[str, str] = {}
        expected: dict[str, object] = receipt_artifacts
        for name in sorted(ARTIFACT_NAMES):
            recorded = expected.get(name)
            if not isinstance(recorded, dict):
                _fail("replay_integrity_error")
            if (
                set(recorded) != {"sha256", "bytes"}
                or not _hash_is_valid(recorded.get("sha256"))
                or type(recorded.get("bytes")) is not int
                or metadata[name] != recorded
            ):
                _fail("replay_integrity_error")
            hashes[name] = str(recorded["sha256"])

        manifest = _canonical_json_object(artifact_dir / "qa-manifest.json", MAX_MANIFEST_BYTES)
        manifest_bytes = manifest.get("bytes_written")
        timing_seconds = manifest.get("timing_seconds")
        if (
            type(manifest_bytes) is not int
            or manifest_bytes <= 0
            or not isinstance(timing_seconds, dict)
        ):
            _fail("replay_integrity_error")
        total_seconds = timing_seconds.get("total")
        if (
            isinstance(total_seconds, bool)
            or not isinstance(total_seconds, (int, float))
            or not math.isfinite(total_seconds)
            or total_seconds < 0
        ):
            _fail("replay_integrity_error")
        if (
            set(manifest)
            != {
                "schema_version",
                "recipe_version",
                "fingerprint",
                "part_ir_sha256",
                "part_ir_schema_version",
                "template",
                "runtime",
                "preview_profile",
                "qa",
                "artifacts",
                "timing_seconds",
                "build_status",
                "bytes_written",
                "model_calls",
                "monetary_cost",
            }
            or manifest.get("schema_version") != MANIFEST_SCHEMA
            or manifest.get("recipe_version") != RECIPE_VERSION
            or manifest.get("fingerprint") != fingerprint
            or not _hash_is_valid(manifest.get("part_ir_sha256"))
            or manifest.get("part_ir_schema_version") != "part-ir.v1"
            or manifest.get("template") != "mounting-plate.v1"
            or manifest.get("runtime") != identity
            or manifest.get("preview_profile") != PREVIEW_PROFILE
            or manifest.get("build_status") != "accepted"
            or type(manifest.get("model_calls")) is not int
            or manifest.get("model_calls") != 0
            or manifest.get("monetary_cost") != "unknown"
        ):
            _fail("replay_integrity_error")
        manifest_artifacts = manifest.get("artifacts")
        if not isinstance(manifest_artifacts, dict) or set(manifest_artifacts) != PRE_MANIFEST_NAMES:
            _fail("replay_integrity_error")
        for name in sorted(PRE_MANIFEST_NAMES):
            if manifest_artifacts[name] != metadata[name]:
                _fail("replay_integrity_error")
        qa_result = _parse_qa(manifest.get("qa"))
        return CadJobResult(
            fingerprint=fingerprint,
            status="replayed",
            artifact_dir=artifact_dir,
            artifact_hashes=hashes,
            qa=qa_result,
            replay=True,
            bytes_written=0,
            model_calls=0,
            error_code=None,
        )
    except CadProductionError as error:
        if error.code == "replay_integrity_error":
            raise
        _fail("replay_integrity_error")
    except (OSError, ValueError, TypeError, KeyError, RecursionError):
        _fail("replay_integrity_error")


def _rejected(
    error_code: str,
    *,
    fingerprint: str | None = None,
) -> CadJobResult:
    return CadJobResult(
        fingerprint=fingerprint,
        status="rejected",
        artifact_dir=None,
        artifact_hashes={},
        qa=None,
        replay=False,
        bytes_written=0,
        model_calls=0,
        error_code=error_code,
    )


def _cleanup_staging(path: Path, staging_root: Path, fingerprint: str) -> None:
    if not path.exists():
        return
    if (
        _is_link(path)
        or path.parent != staging_root
        or not path.name.startswith(f".{fingerprint}.staging-")
        or not path.is_dir()
    ):
        _fail("unsafe_output_path")
    try:
        shutil.rmtree(path)
    except OSError:
        _fail("publication_failed")


def _write_request(path: Path, data: bytes) -> None:
    descriptor: int | None = None
    try:
        flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
        if hasattr(os, "O_BINARY"):
            flags |= os.O_BINARY
        descriptor = os.open(path, flags, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            descriptor = None
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
    except OSError:
        _fail("publication_failed")
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _remove_transient_outputs(staging: Path) -> None:
    for name in _TRANSIENT_NAMES:
        path = staging / name
        if _is_link(path):
            _fail("unsafe_output_path")
        if path.exists():
            if not path.is_file():
                _fail("artifact_set_mismatch")
            try:
                path.unlink()
            except OSError:
                _fail("publication_failed")


def _validate_qa_result(value: object) -> CadQAResult:
    if not isinstance(value, CadQAResult):
        _fail("geometry_invalid")
    try:
        _parse_qa(asdict(value))
    except CadProductionError:
        _fail("geometry_invalid")
    return value


def _publish(staging: Path, destination: Path) -> None:
    if _is_link(staging) or not staging.is_dir() or destination.exists() or _is_link(destination):
        _fail("replay_integrity_error")
    try:
        os.rename(staging, destination)
    except OSError:
        _fail("publication_failed")


def _remove_stale_staging(staging_root: Path, fingerprint: str) -> None:
    """Remove staging dirs a crashed run left for this fingerprint.

    Staging names embed the fingerprint and are only created while holding
    this fingerprint's lock, so any that exist while it is held are abandoned.
    """
    if _is_link(staging_root) or not staging_root.is_dir():
        return
    for candidate in staging_root.glob(f".{fingerprint}.staging-*"):
        _cleanup_staging(candidate, staging_root, fingerprint)


def _recover_missing_receipt(
    fingerprint: str,
    artifact_dir: Path,
    receipt_path: Path,
    identity: dict[str, str],
) -> CadJobResult:
    """Write the receipt a crash between publish and receipt never wrote.

    A CAD rebuild is not byte-reproducible (the QA manifest records timings),
    so recovery cannot compare against a fresh build. Instead the published
    directory must hold exactly the five artifacts and pass every replay check
    against a receipt derived from its bytes: the QA manifest must name this
    fingerprint and runtime and hash the four other artifacts. Anything else
    fails closed and leaves the directory untouched, with no receipt written.
    """
    try:
        _reject_link_components(artifact_dir)
        metadata = _artifact_metadata(artifact_dir, ARTIFACT_NAMES)
    except CadProductionError:
        _fail("replay_integrity_error")
    receipt_data = _receipt_bytes(fingerprint, identity, metadata)
    if len(receipt_data) > MAX_RECEIPT_BYTES:
        _fail("replay_integrity_error")
    receipt = json.loads(receipt_data.decode("utf-8"))
    verified = _verify_against_receipt(fingerprint, artifact_dir, receipt, identity)
    receipt_bytes = _write_receipt_exclusive(receipt_path, receipt_data)
    return CadJobResult(
        fingerprint=fingerprint,
        status="replayed",
        artifact_dir=artifact_dir,
        artifact_hashes=verified.artifact_hashes,
        qa=verified.qa,
        replay=True,
        bytes_written=receipt_bytes,
        model_calls=0,
        error_code=None,
        receipt_recovered=True,
    )


_MEDIA_TYPES = {
    "part.FCStd": "application/vnd.freecad",
    "part.step": "model/step",
    "part.stl": "model/stl",
    "preview.png": "image/png",
    "qa-manifest.json": "application/json",
    "drawing.svg": "image/svg+xml",
    "drawing.dxf": "image/vnd.dxf",
}
_TRACE_STATUS = {"accepted": "accepted", "replayed": "replayed"}


def _worker_summary(part: PartIR, timeout_s: int) -> dict[str, object]:
    """Structural description of the FreeCAD build invocation (no local paths)."""

    parameters = part.parameters
    return {
        "worker_protocol": WORKER_PROTOCOL,
        "mode": "build",
        "macro": "freecad_worker.FCMacro",
        "timeout_s": timeout_s,
        "features": ["Body", "PlateSketch", "Pad", "HoleSketch", "Pocket"],
        "plate_sketch": {
            "plane": "XY_Plane",
            "geometry": "rectangle (4 LineSegment)",
            "constraints": 12,
            "width_mm": parameters.width_mm,
            "height_mm": parameters.height_mm,
        },
        "pad": {"type": "Dimension", "length_mm": parameters.thickness_mm},
        "hole_sketch": {
            "placement_z_mm": parameters.thickness_mm,
            "geometry": "4 Circle",
            "constraints": 12,
            "diameter_mm": parameters.hole_diameter_mm,
            "offset_x_mm": parameters.hole_offset_x_mm,
            "offset_y_mm": parameters.hole_offset_y_mm,
        },
        "pocket": {"type": "ThroughAll"},
        "exports": ["part.FCStd", "part.step", "part.stl", "tessellation.json"],
    }


def _qa_summary(qa: CadQAResult) -> dict[str, object]:
    return {
        "volume_mm3": round(qa.volume_mm3, 6),
        "bounds_mm": [round(value, 6) for value in qa.bounds_mm],
        "solid_count": qa.solid_count,
        "bore_count": qa.bore_count,
        "step_relative_volume_error": qa.step_relative_volume_error,
        "stl_triangle_count": qa.stl_triangle_count,
        "preview_profile": qa.preview_profile,
    }


class _TraceSession:
    """Collect one run's trace in memory; persist it only under a safe output root."""

    def __init__(self, *, input_path: Path, timeout_s: int) -> None:
        self.trace = TraceRecorder(
            tool_id="cad-v1a",
            engine_version="freecad-worker.v1",
            recipe_version=RECIPE_VERSION,
            schema_version="part-ir.v1",
        )
        self.input_path = input_path
        self.timeout_s = timeout_s
        self.part: PartIR | None = None
        self.canonical_ir: bytes | None = None
        self.output_root: Path | None = None
        self.root: Path | None = None

    def _safe_root(self) -> Path | None:
        if self.root is not None:
            return self.root
        if self.output_root is None or not isinstance(self.output_root, Path):
            return None
        try:
            root = _normalized_root(self.output_root)
            _ensure_directory(root)
        except (CadProductionError, OSError):
            return None
        return root

    def finalize(self, result: CadJobResult) -> CadJobResult:
        """Write trace.json, MANUAL-STEPS.md, ir.json, drawings; never alter the outcome."""

        if self.part is None or self.canonical_ir is None:
            return result
        root = self._safe_root()
        if root is None:
            return result
        trace = self.trace
        try:
            job_dir = trace_job_dir(root, trace.job_id)
            _ensure_directory(job_dir)
            ir_value = json.loads(self.canonical_ir.decode("utf-8"))
            trace.input_json("part_ir", ir_value, role="request")
            if self.input_path.is_file():
                record_source_file(trace, "part_ir_file", self.input_path)
            _attach_proposal(trace, self.input_path, ir_value)
            ir_path = write_ir_file(job_dir, ir_value)
            trace.ir_document(ir_value, schema=self.part.schema_version, path=ir_path)
            record_artifact(trace, ir_path, job_dir=job_dir, media_type="application/json", role="ir")
            status = _TRACE_STATUS.get(result.status, "failed")
            if status == "failed":
                trace.record_step("outcome", "rejected", note=result.error_code or "cad_job_failed")
            else:
                artifact_dir = result.artifact_dir
                if artifact_dir is not None:
                    for name in sorted(ARTIFACT_NAMES):
                        record_artifact(
                            trace, artifact_dir / name, job_dir=job_dir,
                            media_type=_MEDIA_TYPES.get(name, ""),
                        )
            with trace.step("drawing", "render_2d_drawing", profile=DRAWING_PROFILE, formats=["svg", "dxf-r12"]) as step:
                drawings = write_drawings(self.part, job_dir)
                step.outputs = [path.name for path in drawings]
            for path in drawings:
                record_artifact(trace, path, job_dir=job_dir, media_type=_MEDIA_TYPES[path.name], role="drawing")
            qa_doc = None
            if result.qa is not None:
                qa_doc = {"status": "passed", "checks": list(_qa_summary(result.qa))}
            elif status == "failed":
                qa_doc = {"status": "failed", "checks": [result.error_code or "cad_job_failed"]}
            trace.finish(
                status=status,
                job_dir=job_dir,
                output_root=trace_root(root),
                guide=build_freecad_guide(self.part, qa=result.qa),
                qa=qa_doc,
            )
        except (CadProductionError, OSError, ValueError, TypeError, KeyError):
            return result
        return replace(result, trace_job_id=trace.job_id)


PROPOSAL_SIDECAR_SUFFIX = ".proposal.json"
PROPOSAL_SCHEMA = "production-proposal.v1"
MAX_PROPOSAL_BYTES = 16 * 1024


def _attach_proposal(trace: TraceRecorder, input_path: Path, ir_value: object) -> None:
    """Record AI proposal metadata when a matching ``<input>.proposal.json`` sits beside the input.

    ``cad brief`` writes that sidecar. It is honoured only when its recorded
    PartIR hash equals the canonical PartIR being built; an edited PartIR is
    recorded as a human edit of an AI proposal instead.
    """

    from book_production_platform.production_trace import sha256_json

    sidecar = input_path.with_name(input_path.name + PROPOSAL_SIDECAR_SUFFIX)
    try:
        if _is_link(sidecar) or not sidecar.is_file() or sidecar.stat().st_size > MAX_PROPOSAL_BYTES:
            return
        value = json.loads(sidecar.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError):
        return
    if not isinstance(value, dict) or value.get("schema_version") != PROPOSAL_SCHEMA:
        return
    calls = value.get("model_calls")
    if isinstance(calls, bool) or not isinstance(calls, int) or calls < 0:
        return
    if value.get("ir_sha256") != sha256_json(ir_value):
        trace.record_step("provenance", "proposal_edited_by_human", note="proposal_hash_mismatch")
        return
    provided = value.get("provided")
    inferred = value.get("inferred")
    trace.ai_proposal(
        model_calls=calls,
        adapter=str(value.get("adapter", "unknown"))[:80],
        mode=str(value.get("mode", "unknown"))[:20],
        provided=[str(item)[:64] for item in provided] if isinstance(provided, list) else [],
        inferred=[str(item)[:64] for item in inferred] if isinstance(inferred, list) else [],
    )


def build_cad_job(
    input_path: Path,
    output_root: Path,
    freecadcmd: Path,
    timeout_s: int = 120,
) -> CadJobResult:
    """Validate one PartIR, build and QA it, then atomically publish a replayable job.

    Every run whose PartIR is valid and whose output root is safe also records a
    production trace under ``<output_root>/.trace/`` (see :mod:`.tracing`):
    accepted, replayed, and rejected runs alike. Tracing never changes the
    returned status or masks the engine's error code.
    """

    session = _TraceSession(input_path=input_path, timeout_s=timeout_s)
    result = _run_cad_job(input_path, output_root, freecadcmd, timeout_s, session)
    return session.finalize(result)


def _run_cad_job(
    input_path: Path,
    output_root: Path,
    freecadcmd: Path,
    timeout_s: int,
    session: _TraceSession,
) -> CadJobResult:
    if (
        not isinstance(input_path, Path)
        or not isinstance(output_root, Path)
        or not isinstance(freecadcmd, Path)
    ):
        return _rejected("invalid_part_ir")
    if (
        isinstance(timeout_s, bool)
        or not isinstance(timeout_s, int)
        or not 1 <= timeout_s <= MAX_WORKER_TIMEOUT_SECONDS
    ):
        return _rejected("invalid_timeout")
    try:
        _reject_link_components(input_path)
        part = load_part_ir(input_path)
        canonical_ir = canonical_part_ir(part)
    except PartIRError as error:
        return _rejected(error.code)
    except CadProductionError as error:
        return _rejected(error.code)
    except OSError:
        return _rejected("invalid_part_ir")
    session.part = part
    session.canonical_ir = canonical_ir
    session.output_root = output_root
    trace = session.trace
    trace.record_step(
        "validate", "parse_part_ir", schema=part.schema_version, template=part.template,
        hole_pattern=part.hole_pattern, units=part.units, parameters=part.parameters.model_dump(mode="json"),
    )

    try:
        with trace.step("runtime", "inspect_freecad_runtime") as runtime_step:
            identity_model = inspect_freecad_runtime(freecadcmd)
            identity = _identity_document(identity_model)
            runtime_step.params = {"freecad": identity}
        with trace.step("fingerprint", "content_address", recipe=RECIPE_VERSION, worker_protocol=WORKER_PROTOCOL) as step:
            fingerprint, part_ir_hash = _canonical_fingerprint(canonical_ir, identity_model)
            step.outputs = [fingerprint]
        root = _normalized_root(output_root)
        _ensure_directory(root)
        session.root = root
        artifact_dir = root / fingerprint
        receipt_path = root / ".receipts" / f"{fingerprint}.json"
        lock_path = root / ".locks" / f"{fingerprint}.lock"
        staging_root = root / ".staging"
        with _job_lock(lock_path):
            _remove_stale_staging(staging_root, fingerprint)
            receipt_present = receipt_path.exists() or _is_link(receipt_path)
            if receipt_present:
                with trace.step("replay", "verify_receipt_and_artifacts", artifacts=sorted(ARTIFACT_NAMES)):
                    return _verify_replay(fingerprint, artifact_dir, receipt_path, identity)
            if artifact_dir.exists() or _is_link(artifact_dir):
                # Published without a receipt: a crash hit between the publish
                # rename and the receipt write.
                with trace.step("replay", "recover_missing_receipt", artifacts=sorted(ARTIFACT_NAMES)):
                    return _recover_missing_receipt(fingerprint, artifact_dir, receipt_path, identity)

            _ensure_directory(staging_root)
            try:
                staging_name = tempfile.mkdtemp(
                    prefix=f".{fingerprint}.staging-", dir=staging_root
                )
            except OSError:
                _fail("publication_failed")
            staging = Path(staging_name)
            build_started = time.monotonic()
            try:
                # FreeCAD and its embedded Python rely on regular Win32 paths.
                # Keep every request/output/QA path inside a short system-temp
                # workspace, then copy verified artifacts into the user's
                # possibly deep output root before atomic publication.
                with tempfile.TemporaryDirectory(prefix="cad-job-") as work_name:
                    work = Path(work_name)
                    request_path = work / ".part-ir.json"
                    _write_request(request_path, canonical_ir)
                    with trace.step("worker", "freecad_build", **_worker_summary(part, timeout_s)) as worker_step:
                        worker: WorkerResult = run_freecad_worker(
                            freecadcmd,
                            "build",
                            request_path,
                            work,
                            timeout_s,
                        )
                        worker_step.params["exit_code"] = worker.exit_code
                        worker_step.params["elapsed_seconds"] = round(worker.elapsed_seconds, 6)
                        if worker.error_code is not None:
                            worker_step.note = worker.error_code
                            _fail(worker.error_code)
                        if worker.result_json is None:
                            _fail("worker_failed")
                        worker_step.outputs = sorted(PRE_MANIFEST_NAMES - {"preview.png"})
                    build_seconds = max(0.0, worker.elapsed_seconds)
                    qa_started = time.monotonic()
                    with trace.step("qa", "verify_cad_outputs", modes=["verify-native", "verify-step", "stl", "preview"]) as qa_step:
                        qa = _validate_qa_result(
                            verify_cad_outputs(part, work, freecadcmd, timeout_s)
                        )
                        qa_step.params.update(_qa_summary(qa))
                        qa_step.outputs = ["preview.png"]
                    qa_seconds = max(0.0, time.monotonic() - qa_started)
                    _remove_transient_outputs(work)
                    artifact_metadata = _artifact_metadata(work, PRE_MANIFEST_NAMES)
                    total_output_bytes = _metadata_byte_sum(artifact_metadata)
                    total_seconds = max(0.0, time.monotonic() - build_started)
                    manifest = {
                        "schema_version": MANIFEST_SCHEMA,
                        "recipe_version": RECIPE_VERSION,
                        "fingerprint": fingerprint,
                        "part_ir_sha256": part_ir_hash,
                        "part_ir_schema_version": part.schema_version,
                        "template": part.template,
                        "runtime": identity,
                        "preview_profile": PREVIEW_PROFILE,
                        "qa": asdict(qa),
                        "artifacts": artifact_metadata,
                        "timing_seconds": {
                            "build": round(build_seconds, 6),
                            "qa": round(qa_seconds, 6),
                            "total": round(total_seconds, 6),
                        },
                        "build_status": "accepted",
                        "bytes_written": total_output_bytes,
                        "model_calls": 0,
                        "monetary_cost": "unknown",
                    }
                    manifest_path = work / "qa-manifest.json"
                    _write_request(manifest_path, _json_bytes(manifest))
                    work_metadata = _artifact_metadata(work, ARTIFACT_NAMES)
                    for name in sorted(ARTIFACT_NAMES):
                        try:
                            shutil.copyfile(work / name, staging / name)
                        except OSError:
                            _fail("publication_failed")
                    all_metadata = _artifact_metadata(staging, ARTIFACT_NAMES)
                    if all_metadata != work_metadata:
                        _fail("publication_failed")
                receipt_data = _receipt_bytes(fingerprint, identity, all_metadata)
                if len(receipt_data) > MAX_RECEIPT_BYTES:
                    _fail("publication_failed")
                with trace.step("publish", "atomic_publish_and_receipt") as publish_step:
                    _publish(staging, artifact_dir)
                    # A crash here leaves a published job without a receipt; the
                    # next run recovers it through _recover_missing_receipt.
                    receipt_bytes = _write_receipt_exclusive(receipt_path, receipt_data)
                    publish_step.outputs = sorted(ARTIFACT_NAMES)
                hashes = {
                    name: str(metadata["sha256"])
                    for name, metadata in all_metadata.items()
                }
                return CadJobResult(
                    fingerprint=fingerprint,
                    status="accepted",
                    artifact_dir=artifact_dir,
                    artifact_hashes=hashes,
                    qa=qa,
                    replay=False,
                    bytes_written=_metadata_byte_sum(all_metadata) + receipt_bytes,
                    model_calls=0,
                    error_code=None,
                )
            finally:
                _cleanup_staging(staging, staging_root, fingerprint)
    except CadProductionError as error:
        return _rejected(
            error.code,
            fingerprint=locals().get("fingerprint"),
        )
    except OSError:
        return _rejected(
            "publication_failed",
            fingerprint=locals().get("fingerprint"),
        )


__all__ = ["RECIPE_VERSION", "CadJobResult", "build_cad_job"]
