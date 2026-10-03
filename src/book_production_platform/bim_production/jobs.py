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
from dataclasses import dataclass, field, replace
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
from book_production_platform.bim_production.manual import build_takeoff_guide
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
from book_production_platform.bim_production.tracing import (
    record_artifact,
    record_source_file,
    trace_job_dir,
    trace_root,
    write_ir_file,
)
from book_production_platform.production_trace import TraceRecorder

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
    trace_job_id: str | None = field(default=None, compare=False)


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


_TRACE_STATUS = {
    "complete": "accepted",
    "partial": "accepted",
    "replayed": "replayed",
    "needs_input": "needs_input",
}


class _TraceSession:
    """One takeoff run's trace; persisted under ``<output_root>/.trace/`` once the IR is valid."""

    def __init__(self, *, ifc_path: Path, output_root: Path, timeout_s: int) -> None:
        self.trace = TraceRecorder(
            tool_id="bim-v1a",
            engine_version="bim-worker.v1",
            recipe_version=RECIPE_VERSION,
            schema_version="bim-takeoff.v1",
        )
        self.ifc_path = ifc_path
        self.output_root = output_root
        self.timeout_s = timeout_s
        self.ir: BimTakeoffIR | None = None
        self.source_ok = False
        self.root: Path | None = None
        self.job_dir: Path | None = None

    def _persist(self, status: str, *, report: dict[str, Any] | None, error_code: str | None) -> str | None:
        if self.ir is None:
            return None
        trace = self.trace
        try:
            root = self.root or _safe_root(self.output_root)
            directory = trace_job_dir(root, trace.job_id)
            directory.mkdir(parents=True, exist_ok=True)
            ir_value = self.ir.model_dump(mode="json")
            trace.input_json("bim_takeoff_ir", ir_value)
            if self.source_ok:
                record_source_file(trace, "ifc_source", self.ifc_path)
            ir_file = write_ir_file(directory, ir_value)
            trace.ir_document(ir_value, schema=self.ir.schema_version, path=ir_file)
            record_artifact(trace, ir_file, job_dir=directory, media_type="application/json", role="ir")
            if report is not None and self.job_dir is not None:
                for name in JOB_ARTIFACTS:
                    media = "text/csv" if name.endswith(".csv") else "application/json"
                    record_artifact(trace, self.job_dir / name, job_dir=directory, media_type=media)
            if error_code is not None:
                trace.record_step("outcome", "failed", note=error_code)
            qa = None
            if report is not None:
                qa = {"status": report.get("status"), "checks": report.get("coverage_gaps", [])}
            trace.finish(
                status=status, job_dir=directory, output_root=trace_root(root),
                guide=build_takeoff_guide(self.ir, report), qa=qa,
            )
        except (BimProductionError, OSError, ValueError, TypeError, KeyError):
            return None
        return trace.job_id

    def finalize(self, result: BimJobResult) -> BimJobResult:
        """Trace the returned outcome; a tracing problem never changes it."""

        status = _TRACE_STATUS.get(result.status, "failed")
        report: dict[str, Any] | None = None
        if status in {"accepted", "replayed"} and self.job_dir is not None:
            try:
                report = _load_receipt(self.job_dir / "takeoff.json")
            except BimProductionError:
                report = None
        error = result.error_code if status == "failed" else None
        job_id = self._persist(status, report=report, error_code=error)
        return replace(result, trace_job_id=job_id) if job_id else result

    def fail(self, code: str) -> None:
        self._persist("failed", report=None, error_code=code)


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

    Every run with a valid takeoff IR also records a production trace under
    ``<output_root>/.trace/`` (accepted, replayed, needs_input, and failed runs,
    including raised errors, which are re-raised unchanged).
    """

    session = _TraceSession(ifc_path=ifc_path, output_root=output_root, timeout_s=timeout_s)
    try:
        result = _run_bim_takeoff_job(
            ir_path, ifc_path, output_root, freecadcmd, ifc_site_packages, timeout_s, session
        )
    except BimProductionError as error:
        session.fail(error.code)
        raise
    except (OSError, ValueError, TypeError) as error:
        session.fail(type(error).__name__)
        raise
    return session.finalize(result)


def _worker_summary(ir: BimTakeoffIR, timeout_s: int) -> dict[str, object]:
    return {
        "protocol": "bim-worker.v1",
        "mode": "takeoff",
        "element_classes": list(ir.element_classes),
        "expected_schemas": ["IFC2X3", "IFC4"],
        "max_instances": 250_000,
        "timeout_s": timeout_s,
        "reads": "occurrence-level IfcElementQuantity via IsDefinedBy",
    }


def _run_bim_takeoff_job(
    ir_path: Path,
    ifc_path: Path,
    output_root: Path,
    freecadcmd: Path,
    ifc_site_packages: Path,
    timeout_s: int,
    session: _TraceSession,
) -> BimJobResult:
    trace = session.trace
    try:
        ir = load_bim_takeoff_ir(ir_path)
    except BimTakeoffIRError as error:
        raise BimProductionError("invalid_bim_takeoff_ir") from error
    session.ir = ir
    trace.record_step(
        "validate", "parse_bim_takeoff_ir", schema=ir.schema_version,
        element_classes=list(ir.element_classes), output_units=ir.output_units,
    )
    with trace.step("validate", "ifc_source_and_schema") as source_step:
        source_sha256 = validate_ifc_source(ifc_path)
        session.source_ok = True
        ifc_schema = detect_ifc_schema(ifc_path)
        source_step.params = {"ifc_schema": ifc_schema, "source_sha256": source_sha256}
    with trace.step("fingerprint", "content_address", recipe=RECIPE_VERSION, report_schema=REPORT_SCHEMA_VERSION) as step:
        fingerprint = compute_fingerprint(ir, source_sha256, ifc_schema)
        step.outputs = [fingerprint]

    root = _safe_root(output_root)
    session.root = root
    jobs_dir = _safe_root(root / JOB_DIR)
    receipts_dir = _safe_root(root / RECEIPT_DIR)
    job_dir = jobs_dir / fingerprint
    session.job_dir = job_dir
    receipt_path = receipts_dir / f"{fingerprint}.json"

    with trace.step("replay", "check_published_job"):
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
            with trace.step("runtime", "inspect_bim_runtime") as runtime_step:
                live_identity = inspect_bim_runtime(freecadcmd, ifc_site_packages)
                live_runtime = runtime_identity_payload(live_identity)
                runtime_step.params = {"runtime": live_runtime}
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
            with trace.step("worker", "ifcopenshell_takeoff", **_worker_summary(ir, timeout_s)) as worker_step:
                worker_result = run_bim_worker(
                    freecadcmd,
                    ifc_site_packages,
                    "takeoff",
                    request_path,
                    worker_dir,
                    timeout_s=timeout_s,
                )
                worker_step.params["exit_code"] = worker_result.exit_code
                worker_step.params["elapsed_seconds"] = round(worker_result.elapsed_seconds, 6)
                if worker_result.error_code is not None or worker_result.result_json is None:
                    worker_step.status = "failed"
                    worker_step.note = worker_result.error_code or "worker_failed"
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
            with trace.step("extract", "normalize_quantities_to_si") as extract_step:
                extraction = build_extraction(extraction_records)
                extract_step.params = {"rows": len(extraction.rows), "coverage_gaps": len(extraction.findings)}
            with trace.step("report", "aggregate_exact_keys") as report_step:
                report = build_takeoff_report(ir, source_sha256, validation, extraction)
                report_step.params = {"status": report["status"], "aggregates": len(report["aggregates"])}  # type: ignore[arg-type]
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
            with trace.step("qa", "verify_takeoff_artifacts") as qa_step:
                qa = verify_takeoff_artifacts(build_dir)
                qa_step.params = {"status": qa.status, "rows": qa.row_count, "coverage_gaps": qa.coverage_gap_count}
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
            with trace.step("publish", "recover_receipt" if recovering else "atomic_publish_and_receipt") as publish_step:
                if recovering:
                    if not _published_matches_rebuild(job_dir, build_dir):
                        raise BimProductionError("takeoff_integrity_error")
                else:
                    _rename_directory(build_dir, job_dir)
                # A crash between the rename and this write leaves a published job
                # without a receipt; the next run takes the recovering path above.
                _write_receipt(receipt_path, receipt)
                publish_step.outputs = list(JOB_ARTIFACTS)
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
