"""Content-addressed takeoff jobs with atomic publication and verified replay."""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
import time
from collections.abc import Iterator
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from typing import Any

from book_production_platform.bim_ir import (
    BimTakeoffIR,
    BimTakeoffIRError,
    canonical_bim_takeoff_ir,
    load_bim_takeoff_ir,
)
from book_production_platform.bim_production.errors import BimProductionError
from book_production_platform.bim_production.extract import build_extraction
from book_production_platform.bim_production.qa import (
    build_qa_manifest,
    render_qa_manifest_json,
    verify_takeoff_artifacts,
)
from book_production_platform.bim_production.reports import (
    REPORT_SCHEMA_VERSION,
    build_takeoff_report,
    csv_bytes,
    render_takeoff_json,
    write_takeoff_csv,
)
from book_production_platform.bim_production.runtime import (
    BimRuntimeIdentity,
    inspect_bim_runtime,
    run_bim_worker,
    stage_ifc_input,
    validate_ifc_source,
)

RECIPE_VERSION = "bim-takeoff-recipe.v1"
RECEIPT_SCHEMA_VERSION = "bim-takeoff-receipt.v1"
JOB_DIR = "jobs"
RECEIPT_DIR = ".receipts"
STAGING_DIR = "staging"
STAGED_IFC_NAME = "source.ifc"
WORKER_REQUEST_NAME = "takeoff-request.bimreq"
WORKER_RESULT_NAME = "worker-result-takeoff.json"
JOB_ARTIFACTS = ("takeoff.csv", "takeoff.json", "qa-manifest.json")
FINGERPRINT_PATTERN = re.compile(r"[0-9a-f]{64}")
LOCK_TIMEOUT_SECONDS = 15
MAX_RECEIPT_BYTES = 64 * 1024
IFC_HEADER_SCAN_BYTES = 64 * 1024
_HEADER_SCHEMA_PATTERN = re.compile(
    r"FILE_SCHEMA\s*\(\s*\(\s*'(IFC[0-9A-Za-z_]+)'\s*\)\s*\)"
)


@dataclass(frozen=True, slots=True)
class BimJobResult:
    """A bounded, privacy-safe job outcome carrying no local paths."""

    status: str
    fingerprint: str | None
    error_code: str | None
    row_count: int
    coverage_gap_count: int
    model_calls: int
    receipt_recovered: bool = False


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON object key")
        result[key] = value
    return result


def _reject_non_finite(value: str) -> None:
    raise ValueError(f"non-finite JSON constant: {value}")


def detect_ifc_schema(source: Path) -> str:
    """Read the IFC-SPF header schema without launching the parser."""

    with source.open("rb") as stream:
        head = stream.read(IFC_HEADER_SCAN_BYTES).decode("ascii", errors="replace")
    match = _HEADER_SCHEMA_PATTERN.search(head)
    if match is None:
        raise BimProductionError("ifc_parse_invalid")
    return match.group(1)


def runtime_identity_payload(identity: BimRuntimeIdentity) -> dict[str, str]:
    return {
        "freecad_version": identity.freecad_version,
        "freecad_build": identity.freecad_build,
        "ifcopenshell_version": identity.ifcopenshell_version,
        "python_version": identity.python_version,
        "platform": identity.platform,
        "freecadcmd_sha256": identity.freecadcmd_sha256,
        "wrapper_sha256": identity.wrapper_sha256,
    }


def _load_runtime_lock() -> dict[str, Any]:
    try:
        resource = resources.files("book_production_platform.bim_production").joinpath(
            "ifcopenshell-runtime.v1.json"
        )
        value = json.loads(resource.read_text(encoding="utf-8"))
    except (OSError, TypeError, ValueError, ModuleNotFoundError):
        raise BimProductionError("unsupported_ifcopenshell_runtime") from None
    if not isinstance(value, dict):
        raise BimProductionError("unsupported_ifcopenshell_runtime")
    return value


def compute_fingerprint(
    ir: BimTakeoffIR, source_sha256: str, ifc_schema: str
) -> str:
    """Hash every input that can change the report; never any local path.

    The runtime identity comes from the packaged lock: the worker preflight
    proves the live runtime matches it before any takeoff is extracted.
    """

    lock = _load_runtime_lock()
    digest = hashlib.sha256()
    digest.update(canonical_bim_takeoff_ir(ir))
    digest.update(b"\0" + source_sha256.encode("ascii"))
    digest.update(b"\0" + ifc_schema.encode("ascii"))
    digest.update(b"\0" + RECIPE_VERSION.encode("ascii"))
    digest.update(b"\0" + REPORT_SCHEMA_VERSION.encode("ascii"))
    for key in (
        "freecad_version",
        "freecad_build",
        "python_version",
        "ifcopenshell_version",
        "freecadcmd_sha256",
        "wrapper_sha256",
    ):
        digest.update(b"\0" + str(lock.get(key, "")).encode("ascii"))
    return digest.hexdigest()


def _safe_root(root: Path) -> Path:
    try:
        if root.exists() and not root.is_dir():
            raise BimProductionError("unsafe_output_path")
        root.mkdir(parents=True, exist_ok=True)
        resolved = root.resolve(strict=True)
    except BimProductionError:
        raise
    except OSError:
        raise BimProductionError("unsafe_output_path") from None
    return resolved


def _lock_descriptor(descriptor: int, *, unlock: bool = False) -> None:
    """Take or release a non-blocking exclusive lock (msvcrt or flock)."""

    if sys.platform == "win32":
        import msvcrt

        msvcrt.locking(descriptor, msvcrt.LK_UNLCK if unlock else msvcrt.LK_NBLCK, 1)
        return
    import fcntl

    fcntl.flock(descriptor, fcntl.LOCK_UN if unlock else fcntl.LOCK_EX | fcntl.LOCK_NB)


@contextlib.contextmanager
def _fingerprint_lock(receipts: Path, fingerprint: str) -> Iterator[None]:
    lock_path = receipts / f"{fingerprint}.lock"
    handle = lock_path.open("a+b")
    deadline = time.monotonic() + LOCK_TIMEOUT_SECONDS
    while True:
        try:
            handle.seek(0)
            _lock_descriptor(handle.fileno())
            break
        except OSError:
            if time.monotonic() >= deadline:
                handle.close()
                raise BimProductionError("publication_failed") from None
            time.sleep(0.05)
    try:
        yield
    finally:
        try:
            handle.seek(0)
            _lock_descriptor(handle.fileno(), unlock=True)
        except OSError:
            pass
        handle.close()


def _write_receipt(path: Path, receipt: dict[str, object]) -> None:
    """Write the receipt atomically; the caller holds the fingerprint lock.

    A private temporary name (not a fixed ``.tmp``) means a crash mid-write
    can never block later runs, and ``os.replace`` means a crash leaves either
    no receipt or a complete one.
    """

    payload = (
        json.dumps(
            receipt,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        + b"\n"
    )
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _load_receipt(path: Path) -> dict[str, Any]:
    try:
        payload = path.read_bytes()
    except OSError:
        raise BimProductionError("takeoff_integrity_error") from None
    if len(payload) > MAX_RECEIPT_BYTES:
        raise BimProductionError("takeoff_integrity_error")
    try:
        receipt = json.loads(
            payload.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_non_finite,
        )
    except (UnicodeDecodeError, ValueError) as error:
        raise BimProductionError("takeoff_integrity_error") from error
    if not isinstance(receipt, dict):
        raise BimProductionError("takeoff_integrity_error")
    return receipt


def _artifact_digest(path: Path) -> tuple[int, str]:
    try:
        payload = path.read_bytes()
    except OSError:
        raise BimProductionError("takeoff_integrity_error") from None
    return len(payload), hashlib.sha256(payload).hexdigest()


def _verify_published_job(
    job_dir: Path, receipt: dict[str, Any], fingerprint: str
) -> BimJobResult:
    if receipt.get("schema_version") != RECEIPT_SCHEMA_VERSION:
        raise BimProductionError("takeoff_integrity_error")
    if receipt.get("fingerprint") != fingerprint:
        raise BimProductionError("takeoff_integrity_error")
    artifacts = receipt.get("artifacts")
    if not isinstance(artifacts, dict) or set(artifacts) != set(JOB_ARTIFACTS):
        raise BimProductionError("takeoff_integrity_error")
    for name in JOB_ARTIFACTS:
        entry = artifacts[name]
        if not isinstance(entry, dict):
            raise BimProductionError("takeoff_integrity_error")
        size, digest = _artifact_digest(job_dir / name)
        if size != entry.get("bytes") or digest != entry.get("sha256"):
            raise BimProductionError("takeoff_integrity_error")
    report = _load_receipt(job_dir / "takeoff.json")
    if receipt.get("source_sha256") != report.get("source_sha256"):
        raise BimProductionError("takeoff_integrity_error")
    if receipt.get("status") != report.get("status"):
        raise BimProductionError("takeoff_integrity_error")
    if receipt.get("row_count") != len(report.get("rows", [])):
        raise BimProductionError("takeoff_integrity_error")
    if receipt.get("coverage_gap_count") != len(report.get("coverage_gaps", [])):
        raise BimProductionError("takeoff_integrity_error")
    manifest = _load_receipt(job_dir / "qa-manifest.json")
    if manifest.get("source_sha256") != report.get("source_sha256"):
        raise BimProductionError("takeoff_integrity_error")
    if manifest.get("model_calls") != 0 or manifest.get("monetary_cost") != "unknown":
        raise BimProductionError("takeoff_integrity_error")
    return BimJobResult(
        status="replayed",
        fingerprint=fingerprint,
        error_code=None,
        row_count=int(receipt.get("row_count", -1)),
        coverage_gap_count=int(receipt.get("coverage_gap_count", -1)),
        model_calls=0,
    )


def _check_existing_job(
    job_dir: Path, receipt_path: Path, fingerprint: str
) -> BimJobResult | None:
    if not job_dir.exists():
        return None
    if job_dir.is_symlink() or not job_dir.is_dir():
        raise BimProductionError("takeoff_integrity_error")
    if not receipt_path.exists() and not receipt_path.is_symlink():
        # Published without a receipt (a crash between the publish rename and
        # the receipt write): the caller rebuilds and recovers under the lock.
        return None
    if not receipt_path.is_file():
        raise BimProductionError("takeoff_integrity_error")
    receipt = _load_receipt(receipt_path)
    return _verify_published_job(job_dir, receipt, fingerprint)


def _published_matches_rebuild(job_dir: Path, build_dir: Path) -> bool:
    """True when the published job holds exactly the rebuilt artifact bytes."""

    try:
        published = sorted(entry.name for entry in job_dir.iterdir())
    except OSError:
        return False
    if published != sorted(JOB_ARTIFACTS):
        return False
    for name in JOB_ARTIFACTS:
        candidate = job_dir / name
        if candidate.is_symlink() or not candidate.is_file():
            return False
        if _artifact_digest(candidate) != _artifact_digest(build_dir / name):
            return False
    return True


def shutil_rmtree(directory: Path) -> None:
    shutil.rmtree(directory, ignore_errors=True)


def _rename_directory(source: Path, target: Path) -> None:
    """Atomically publish a directory, tolerating brief Windows share locks."""

    deadline = time.monotonic() + 5
    while True:
        try:
            os.replace(source, target)
            return
        except PermissionError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(0.1)


def build_bim_takeoff_job(
    ir_path: Path,
    ifc_path: Path,
    output_root: Path,
    freecadcmd: Path,
    ifc_site_packages: Path,
    timeout_s: int = 120,
) -> BimJobResult:
    """Validate, extract, QA, and atomically publish (or replay) one takeoff job.

    Crash recovery: a published job directory without a receipt (a crash
    between the publish rename and the receipt write) is rebuilt in staging;
    the receipt is written only when the rebuild reproduces every published
    artifact byte for byte. Anything else fails closed, leaving it untouched.
    """

    try:
        ir = load_bim_takeoff_ir(ir_path)
    except BimTakeoffIRError as error:
        raise BimProductionError("invalid_bim_takeoff_ir") from error
    source_sha256 = validate_ifc_source(ifc_path)
    ifc_schema = detect_ifc_schema(ifc_path)
    fingerprint = compute_fingerprint(ir, source_sha256, ifc_schema)

    root = _safe_root(output_root)
    jobs_dir = _safe_root(root / JOB_DIR)
    receipts_dir = _safe_root(root / RECEIPT_DIR)
    job_dir = jobs_dir / fingerprint
    receipt_path = receipts_dir / f"{fingerprint}.json"

    existing = _check_existing_job(job_dir, receipt_path, fingerprint)
    if existing is not None:
        return existing

    staging_root = _safe_root(root / STAGING_DIR)
    with _fingerprint_lock(receipts_dir, fingerprint):
        existing = _check_existing_job(job_dir, receipt_path, fingerprint)
        if existing is not None:
            return existing
        recovering = job_dir.exists()
        build_dir = staging_root / f"build-{fingerprint[:16]}"
        worker_dir = staging_root / f"worker-{fingerprint[:16]}"
        for directory in (build_dir, worker_dir):
            if directory.exists():
                shutil_rmtree(directory)
            directory.mkdir(parents=True)
        try:
            live_identity = inspect_bim_runtime(freecadcmd, ifc_site_packages)
            live_runtime = runtime_identity_payload(live_identity)
            staged, staged_hash = stage_ifc_input(ifc_path, worker_dir)
            if staged_hash != source_sha256:
                raise BimProductionError("takeoff_integrity_error")
            request: dict[str, object] = {
                "protocol": "bim-worker.v1",
                "mode": "takeoff",
                "takeoff_id": ir.takeoff_id,
                "ifc_path": str(staged.resolve()),
                "element_classes": list(ir.element_classes),
                "expected_schemas": ["IFC2X3", "IFC4"],
                "max_instances": 250_000,
            }
            request_path = worker_dir / WORKER_REQUEST_NAME
            request_path.write_text(
                json.dumps(request, sort_keys=True), encoding="utf-8"
            )
            worker_result = run_bim_worker(
                freecadcmd,
                ifc_site_packages,
                "takeoff",
                request_path,
                worker_dir,
                timeout_s=timeout_s,
            )
            if worker_result.error_code is not None or worker_result.result_json is None:
                if recovering:
                    raise BimProductionError("takeoff_integrity_error")
                return BimJobResult(
                    status="rejected",
                    fingerprint=fingerprint,
                    error_code=worker_result.error_code or "worker_failed",
                    row_count=0,
                    coverage_gap_count=0,
                    model_calls=0,
                )
            result_json = worker_result.result_json
            validation = result_json.get("validation")
            extraction = result_json.get("extraction")
            extraction_records = (
                extraction.get("records") if isinstance(extraction, dict) else None
            )
            if not isinstance(validation, dict) or not isinstance(
                extraction_records, list
            ):
                raise BimProductionError("worker_failed")
            extraction = build_extraction(extraction_records)
            report = build_takeoff_report(ir, source_sha256, validation, extraction)
            if report["status"] == "needs_input":
                if recovering:
                    raise BimProductionError("takeoff_integrity_error")
                return BimJobResult(
                    status="needs_input",
                    fingerprint=fingerprint,
                    error_code=None,
                    row_count=0,
                    coverage_gap_count=len(extraction.findings),
                    model_calls=0,
                )
            report_payload = render_takeoff_json(report)
            csv_payload = csv_bytes(extraction.rows)
            manifest = build_qa_manifest(
                ir,
                live_runtime,
                source_sha256,
                validation,
                extraction,
                report_payload,
                csv_payload,
                report,
            )
            manifest_payload = render_qa_manifest_json(manifest)
            (build_dir / "takeoff.json").write_bytes(report_payload)
            write_takeoff_csv(extraction.rows, build_dir / "takeoff.csv")
            (build_dir / "qa-manifest.json").write_bytes(manifest_payload)
            qa = verify_takeoff_artifacts(build_dir)
            if qa.status == "needs_input":
                if recovering:
                    raise BimProductionError("takeoff_integrity_error")
                return BimJobResult(
                    status="needs_input",
                    fingerprint=fingerprint,
                    error_code=None,
                    row_count=qa.row_count,
                    coverage_gap_count=qa.coverage_gap_count,
                    model_calls=0,
                )
            receipt: dict[str, object] = {
                "schema_version": RECEIPT_SCHEMA_VERSION,
                "fingerprint": fingerprint,
                "source_sha256": source_sha256,
                "runtime": live_runtime,
                "ifc_schema": str(validation["schema"]),
                "status": qa.status,
                "row_count": qa.row_count,
                "coverage_gap_count": qa.coverage_gap_count,
                "artifacts": {
                    name: {
                        "bytes": qa.artifact_bytes[name],
                        "sha256": qa.artifact_hashes[name],
                    }
                    for name in JOB_ARTIFACTS
                },
            }
            if recovering:
                if not _published_matches_rebuild(job_dir, build_dir):
                    raise BimProductionError("takeoff_integrity_error")
            else:
                _rename_directory(build_dir, job_dir)
            # A crash between the rename and this write leaves a published job
            # without a receipt; the next run takes the recovering path above.
            _write_receipt(receipt_path, receipt)
            return BimJobResult(
                status=qa.status,
                fingerprint=fingerprint,
                error_code=None,
                row_count=qa.row_count,
                coverage_gap_count=qa.coverage_gap_count,
                model_calls=0,
                receipt_recovered=recovering,
            )
        finally:
            shutil_rmtree(build_dir)
            shutil_rmtree(worker_dir)
