"""Private, content-addressed local jobs for Data Pipeline plan artifacts."""

from __future__ import annotations

import errno
import hashlib
import importlib
import json
import os
import re
import shutil
import sqlite3
import tempfile
import time
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from book_production_platform.data_pipeline_ir.models import (
    PipelineIR,
    PipelineNode,
    PipelineRequest,
)
from book_production_platform.data_pipeline_ir.validation import (
    PipelineValidationError,
    _parse_request,
    build_pipeline_ir,
)
from book_production_platform.production_trace import (
    MANUAL_FILENAME,
    TRACE_FILENAME,
    TraceRecorder,
    sha256_json,
)

from .manual import build_power_query_guide
from .pipeline import (
    CompiledPipeline,
    ExplainResult,
    PipelineExplainError,
    compile_pipeline_ir,
    explain_pipeline_sql,
    parameter_document,
)
from .qa import (
    MAX_ARTIFACT_BYTES,
    REPORT_SCHEMA,
    build_pipeline_report,
    qa_pipeline_artifacts,
)

if os.name == "nt":
    import msvcrt

RECIPE_VERSION = "data-sql-pipeline-builder-v1a"
MANIFEST_SCHEMA = "pipeline-qa-manifest.v1"
RECEIPT_SCHEMA = "data-pipeline.receipt.v1"
ARTIFACT_NAMES = frozenset({"pipeline.sql", "parameters.json", "report.json", "qa-manifest.json"})
PRE_MANIFEST_NAMES = frozenset({"pipeline.sql", "parameters.json", "report.json"})
TOOL_ID = "data-pipeline-v1a"
ENGINE_VERSION = f"data-pipeline-v1a+sqlite-{sqlite3.sqlite_version}"
IR_FILENAME = "ir.json"
# Trace sidecars written after publication; they are not plan artifacts, are
# not covered by the receipt, and are ignored by the artifact-set checks.
SIDECAR_NAMES = frozenset({TRACE_FILENAME, MANUAL_FILENAME, IR_FILENAME})
_SIDECAR_TEMP = re.compile(r"^\.(?:trace\.json|MANUAL-STEPS\.md|ir\.json)\.\d+\.tmp$")
MAX_MANIFEST_BYTES = MAX_ARTIFACT_BYTES
MAX_RECEIPT_BYTES = 8192
LOCK_TIMEOUT_SECONDS = 15.0
PUBLISH_RETRIES = 2
_MANIFEST_FIELDS = frozenset(
    {
        "schema_version",
        "recipe_version",
        "fingerprint",
        "artifact_hashes",
        "qa",
        "execution_mode",
        "model_calls",
        "query_plan_verified",
        "query_plan",
        "sqlite_version",
    }
)
_REPORT_FIELDS = frozenset(
    {
        "schema_version",
        "pipeline_id",
        "source_ref",
        "source_table",
        "operations",
        "output_node",
        "output_schema",
        "output_lineage",
        "sql_sha256",
        "query_plan",
        "sqlite_version",
        "execution_mode",
        "query_plan_verified",
        "model_calls",
        "parameter_count",
    }
)


class PipelineJobError(ValueError):
    """A stable output-safety, replay, or publication failure."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _lock_file_descriptor(descriptor: int, *, unlock: bool = False) -> None:
    if os.name == "nt":
        mode = msvcrt.LK_UNLCK if unlock else msvcrt.LK_NBLCK
        msvcrt.locking(descriptor, mode, 1)
        return
    fcntl = importlib.import_module("fcntl")
    operation_names = ["LOCK_UN"] if unlock else ["LOCK_EX", "LOCK_NB"]
    operation = 0
    for name in operation_names:
        operation |= getattr(fcntl, name)
    lock_function_name = "flock"
    getattr(fcntl, lock_function_name)(descriptor, operation)


@dataclass(frozen=True)
class PipelineJobResult:
    fingerprint: str | None
    status: str
    artifact_dir: Path | None
    artifact_hashes: dict[str, str]
    qa: tuple[str, ...]
    model_calls: int
    replay: bool
    bytes_written: int
    error_code: str | None
    metadata: dict[str, str | bool]

    def to_dict(self) -> dict[str, Any]:
        """Expose response-safe metadata only; never return source or parameter values."""
        return {
            "status": self.status,
            "fingerprint": self.fingerprint,
            "artifact_dir": str(self.artifact_dir) if self.artifact_dir is not None else None,
            "artifact_names": sorted(self.artifact_hashes),
            "artifact_hashes": dict(self.artifact_hashes),
            "qa": list(self.qa),
            "model_calls": self.model_calls,
            "replay": self.replay,
            "bytes_written": self.bytes_written,
            "error_code": self.error_code,
            "metadata": dict(self.metadata),
        }


def _json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8") + b"\n"


def _parse_pipeline_request(request: PipelineRequest | dict[str, Any]) -> PipelineRequest:
    return _parse_request(request)


def _fingerprint_view(request: PipelineRequest) -> dict[str, Any]:
    """Request JSON without fields added after launch while they are unset (keeps old fingerprints)."""

    payload = request.model_dump(mode="json")
    for node in payload.get("nodes", []):
        if node.get("operator") is None:
            node.pop("operator", None)
    return payload


def _canonical_fingerprint(request: PipelineRequest, recipe_version: str) -> str:
    payload = {
        "recipe_version": recipe_version,
        "request": _fingerprint_view(request),
    }
    return hashlib.sha256(_json_bytes(payload)).hexdigest()


def _artifact_names(directory: Path) -> set[str]:
    return {path.name for path in directory.iterdir() if path.name not in SIDECAR_NAMES and not _SIDECAR_TEMP.match(path.name)}


def _private_write(path: Path, data: bytes) -> None:
    """Write a private (0600) file atomically next to its final name."""

    _reject_symlink_components(path.parent)
    descriptor, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temp_path = Path(temp_name)
    try:
        with os.fdopen(descriptor, "wb") as output:
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
        if os.name != "nt":
            temp_path.chmod(0o600)
        os.replace(temp_path, path)
    finally:
        temp_path.unlink(missing_ok=True)


def _restrict_trace_files(directory: Path) -> None:
    """Trace sidecars may hold filter values (manual guide, IR): keep them owner-only."""

    if os.name == "nt":
        return
    for name in SIDECAR_NAMES:
        path = directory / name
        if path.is_file() and not path.is_symlink():
            path.chmod(0o600)


def _node_params(node: PipelineNode) -> dict[str, Any]:
    """Structural node parameters for the trace; filter values are never recorded."""

    params: dict[str, Any] = {"node_id": node.node_id, "op": node.op, "parents": list(node.parent_ids)}
    for name in ("columns", "column", "operator", "direction", "limit", "group_by"):
        value = getattr(node, name)
        if value is not None:
            params[name] = value
    if node.metrics:
        params["metrics"] = [{"alias": metric.alias, "op": metric.op, "column": metric.column} for metric in node.metrics]
    if node.op in {"filter_equals", "filter_compare"}:
        params["value_type"] = type(node.value).__name__
    return params


def _new_trace(job_id: str, request_payload: Any, ai_proposal: Mapping[str, Any] | None = None) -> TraceRecorder:
    trace = TraceRecorder(tool_id=TOOL_ID, job_id=job_id, engine_version=ENGINE_VERSION, recipe_version=RECIPE_VERSION, schema_version="pipeline-request.v1")
    trace.input_json("request", request_payload)
    if ai_proposal:
        trace.ai_proposal(
            model_calls=int(ai_proposal.get("model_calls", 0) or 0),
            adapter=str(ai_proposal.get("adapter", "unknown")),
            mode=str(ai_proposal.get("mode", "live")),
            provided=[str(item) for item in ai_proposal.get("provided", ["table", "steps"])],
            inferred=[str(item) for item in ai_proposal.get("inferred", [])],
        )
    return trace


def _trace_plan(trace: TraceRecorder, parsed: PipelineRequest, pipeline: PipelineIR) -> None:
    trace.inputs[-1].update({"source_columns": len(parsed.source.columns), "nodes": len(parsed.nodes)})
    trace.record_step("validate", "parse_request", schema=parsed.schema_version, source_table=parsed.source.table_name)
    trace.record_step(
        "recipe", "build_pipeline_ir", nodes=len(pipeline.ordered_nodes), output_node=pipeline.output_node,
        output_columns=[column.key for column in pipeline.output_columns],
    )
    for node in pipeline.ordered_nodes:
        trace.record_step("recipe", "node", **_node_params(node))


def _trace_rejection(trace: TraceRecorder, root: Path, code: str, issues: tuple[str, ...] = ()) -> None:
    """Record a rejected job under ``<root>/traces``; tracing never masks the rejection."""

    try:
        _ensure_directory(root)
        _ensure_directory(root / "traces")
        trace_dir = root / "traces" / trace.job_id
        _ensure_directory(trace_dir)
        trace.record_step("outcome", "failed", note=code, issues=list(issues))
        trace.finish(status="failed", job_dir=trace_dir, output_root=root)
    except (OSError, PipelineJobError):
        pass


def _trace_accepted(trace: TraceRecorder, root: Path, artifact_dir: Path, pipeline: PipelineIR, compiled: CompiledPipeline, *, note: str = "") -> None:
    ir_payload = pipeline.model_dump(mode="json")
    _private_write(artifact_dir / IR_FILENAME, _json_bytes(ir_payload))
    trace.ir_document(ir_payload, schema=pipeline.schema_version, path=artifact_dir / IR_FILENAME)
    trace.record_step("publish", "publish_artifacts", outputs=sorted(ARTIFACT_NAMES), note=note)
    media = {"pipeline.sql": "application/sql", "parameters.json": "application/json", "report.json": "application/json", "qa-manifest.json": "application/json"}
    for name in sorted(ARTIFACT_NAMES):
        trace.artifact(artifact_dir / name, media_type=media[name])
    trace.finish(status="accepted", job_dir=artifact_dir, output_root=root, guide=build_power_query_guide(pipeline, compiled), qa={"status": "passed", "checks": []})
    _restrict_trace_files(artifact_dir)


def _reject_symlink_components(path: Path) -> None:
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current = current / part
        if current.is_symlink():
            raise PipelineJobError("unsafe_output_path")


def _ensure_directory(path: Path) -> None:
    _reject_symlink_components(path)
    was_missing = not path.exists()
    try:
        if path.exists() and not path.is_dir():
            raise PipelineJobError("unsafe_output_path")
        path.mkdir(parents=True, exist_ok=True, mode=0o700)
        if os.name != "nt" and was_missing:
            path.chmod(0o700)
    except PipelineJobError:
        raise
    except OSError:
        raise PipelineJobError("unsafe_output_path") from None
    _reject_symlink_components(path)
    if not path.is_dir():
        raise PipelineJobError("unsafe_output_path")


@contextmanager
def _idempotency_lock(path: Path) -> Iterator[None]:
    _ensure_directory(path.parent)
    _reject_symlink_components(path)
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
                _lock_file_descriptor(lock_file.fileno())
                break
            except OSError as error:
                if error.errno not in {errno.EACCES, errno.EAGAIN, errno.EDEADLK, None}:
                    raise PipelineJobError("job_lock_unavailable") from None
                if time.monotonic() >= deadline:
                    raise PipelineJobError("job_lock_timeout") from None
                time.sleep(0.025)
        try:
            yield
        finally:
            lock_file.seek(0)
            _lock_file_descriptor(lock_file.fileno(), unlock=True)
    except PipelineJobError:
        raise
    except OSError:
        raise PipelineJobError("job_lock_unavailable") from None
    finally:
        if lock_file is not None:
            lock_file.close()


def _create_staging_dir(root: Path, fingerprint: str) -> Path:
    jobs_dir = root / "jobs"
    _ensure_directory(jobs_dir)
    try:
        name = tempfile.mkdtemp(prefix=f".{fingerprint}.staging-", dir=jobs_dir)
    except OSError:
        raise PipelineJobError("artifact_write_failed") from None
    staging_dir = Path(name)
    _reject_symlink_components(staging_dir)
    return staging_dir


def _write_staged_artifacts(staging_dir: Path, files: dict[str, bytes]) -> None:
    if staging_dir.is_symlink() or not staging_dir.is_dir() or not set(files) <= ARTIFACT_NAMES:
        raise PipelineJobError("unsafe_output_path")
    for name, data in files.items():
        if not isinstance(data, bytes) or len(data) > MAX_ARTIFACT_BYTES:
            raise PipelineJobError("artifact_write_failed")
        path = staging_dir / name
        if path.is_symlink() or path.exists():
            raise PipelineJobError("unsafe_output_path")
        descriptor: int | None = None
        try:
            flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
            if hasattr(os, "O_BINARY"):
                flags |= os.O_BINARY
            descriptor = os.open(path, flags, 0o600)
            with os.fdopen(descriptor, "wb") as output:
                descriptor = None
                output.write(data)
                output.flush()
                os.fsync(output.fileno())
        except OSError:
            raise PipelineJobError("artifact_write_failed") from None
        finally:
            if descriptor is not None:
                os.close(descriptor)


def _remove_staging_dir(staging_dir: Path) -> None:
    if staging_dir.is_symlink():
        raise PipelineJobError("unsafe_output_path")
    if staging_dir.exists():
        if not staging_dir.is_dir() or staging_dir.parent.name != "jobs" or not staging_dir.name.startswith("."):
            raise PipelineJobError("unsafe_output_path")
        try:
            shutil.rmtree(staging_dir)
        except OSError:
            raise PipelineJobError("artifact_cleanup_failed") from None


def _hash_artifacts(staging_dir: Path, files: dict[str, bytes]) -> dict[str, str]:
    hashes: dict[str, str] = {}
    for name in sorted(files):
        path = staging_dir / name
        if path.is_symlink() or not path.is_file():
            raise PipelineJobError("artifact_read_error")
        data = path.read_bytes()
        hashes[name] = hashlib.sha256(data).hexdigest()
    return hashes


def _hash_all_artifacts(staging_dir: Path) -> dict[str, str]:
    if staging_dir.is_symlink() or _artifact_names(staging_dir) != ARTIFACT_NAMES:
        raise PipelineJobError("artifact_set_mismatch")
    hashes: dict[str, str] = {}
    for name in sorted(ARTIFACT_NAMES):
        path = staging_dir / name
        if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_ARTIFACT_BYTES:
            raise PipelineJobError("artifact_read_error")
        hashes[name] = hashlib.sha256(path.read_bytes()).hexdigest()
    return hashes


def _build_qa_manifest(
    request: PipelineRequest,
    fingerprint: str,
    hashes: dict[str, str],
    qa: tuple[str, ...],
    query_plan: ExplainResult,
) -> dict[str, Any]:
    if set(hashes) != PRE_MANIFEST_NAMES:
        raise PipelineJobError("artifact_hash_set_mismatch")
    return {
        "schema_version": MANIFEST_SCHEMA,
        "recipe_version": RECIPE_VERSION,
        "fingerprint": fingerprint,
        "artifact_hashes": dict(hashes),
        "qa": list(qa),
        "execution_mode": "plan_only",
        "model_calls": 0,
        "query_plan_verified": True,
        "query_plan": [
            {"node_id": step.node_id, "parent_id": step.parent_id, "detail": step.detail}
            for step in query_plan.steps
        ],
        "sqlite_version": query_plan.sqlite_version,
    }


def _build_receipt(fingerprint: str, hashes: dict[str, str]) -> dict[str, Any]:
    if set(hashes) != ARTIFACT_NAMES:
        raise PipelineJobError("artifact_hash_set_mismatch")
    return {"schema_version": RECEIPT_SCHEMA, "fingerprint": fingerprint, "hashes": dict(hashes)}


def _write_receipt_exclusive(receipt_path: Path, receipt: dict[str, Any]) -> int:
    """Publish a complete receipt atomically; never leave a truncated one.

    The bytes are written and fsynced to a private temporary file first and
    then hard-linked into place, so a crash leaves either no receipt or a
    complete one. Linking also keeps the create-exclusive guarantee.
    """
    _ensure_directory(receipt_path.parent)
    _reject_symlink_components(receipt_path)
    data = _json_bytes(receipt)
    try:
        descriptor, temp_name = tempfile.mkstemp(
            prefix=f".{receipt_path.stem}.", suffix=".tmp", dir=receipt_path.parent
        )
    except OSError:
        raise PipelineJobError("artifact_write_failed") from None
    temp_path = Path(temp_name)
    try:
        with os.fdopen(descriptor, "wb") as output:
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
        try:
            os.link(temp_path, receipt_path)
        except FileExistsError:
            raise PipelineJobError("replay_integrity_error") from None
        except OSError:
            # Filesystems without hard links: the per-fingerprint lock is held,
            # so an existence check followed by an atomic replace is exclusive.
            if receipt_path.exists() or receipt_path.is_symlink():
                raise PipelineJobError("replay_integrity_error") from None
            os.replace(temp_path, receipt_path)
    except PipelineJobError:
        raise
    except OSError:
        raise PipelineJobError("artifact_write_failed") from None
    finally:
        try:
            temp_path.unlink(missing_ok=True)
        except OSError:
            pass
    return len(data)


def _publish_job(staging_dir: Path, artifact_dir: Path, receipt_path: Path, receipt: dict[str, Any]) -> None:
    _ensure_directory(artifact_dir.parent)
    _ensure_directory(receipt_path.parent)
    _reject_symlink_components(staging_dir)
    _reject_symlink_components(artifact_dir)
    _reject_symlink_components(receipt_path)
    if artifact_dir.exists() or receipt_path.exists() or artifact_dir.is_symlink() or receipt_path.is_symlink():
        raise PipelineJobError("replay_integrity_error")
    if _artifact_names(staging_dir) != ARTIFACT_NAMES:
        raise PipelineJobError("artifact_set_mismatch")
    for attempt in range(PUBLISH_RETRIES + 1):
        try:
            staging_dir.rename(artifact_dir)
            break
        except PermissionError:
            if os.name != "nt" or attempt == PUBLISH_RETRIES:
                raise PipelineJobError("artifact_publish_failed") from None
            time.sleep(0.025 * (attempt + 1))
        except OSError:
            raise PipelineJobError("artifact_publish_failed") from None
    # A crash after the rename and before this write leaves published
    # artifacts without a receipt; run_pipeline_job recovers that state.
    _write_receipt_exclusive(receipt_path, receipt)


def _read_json_object(path: Path, max_bytes: int) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file() or path.stat().st_size > max_bytes:
        raise ValueError("invalid_json_artifact")
    raw = path.read_bytes()
    value = json.loads(raw)
    if not isinstance(value, dict) or _json_bytes(value) != raw:
        raise ValueError("invalid_json_artifact")
    return value


def _verify_replay(fingerprint: str, artifact_dir: Path, receipt_path: Path) -> PipelineJobResult:
    def fail() -> None:
        raise PipelineJobError("replay_integrity_error")

    try:
        _reject_symlink_components(artifact_dir)
        _reject_symlink_components(receipt_path)
        if artifact_dir.is_symlink() or not artifact_dir.is_dir():
            fail()
        if _artifact_names(artifact_dir) != ARTIFACT_NAMES:
            fail()
        if receipt_path.parent.is_symlink() or receipt_path.is_symlink():
            fail()
        receipt = _read_json_object(receipt_path, MAX_RECEIPT_BYTES)
        if (
            set(receipt) != {"schema_version", "fingerprint", "hashes"}
            or receipt.get("schema_version") != RECEIPT_SCHEMA
            or receipt.get("fingerprint") != fingerprint
            or not isinstance(receipt.get("hashes"), dict)
            or set(receipt["hashes"]) != ARTIFACT_NAMES
        ):
            fail()
        hashes: dict[str, str] = {}
        for name in sorted(ARTIFACT_NAMES):
            path = artifact_dir / name
            if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_ARTIFACT_BYTES:
                fail()
            actual = hashlib.sha256(path.read_bytes()).hexdigest()
            expected = receipt["hashes"].get(name)
            if not isinstance(expected, str) or expected != actual:
                fail()
            hashes[name] = actual

        manifest = _read_json_object(artifact_dir / "qa-manifest.json", MAX_MANIFEST_BYTES)
        if (
            set(manifest) != _MANIFEST_FIELDS
            or manifest.get("schema_version") != MANIFEST_SCHEMA
            or manifest.get("recipe_version") != RECIPE_VERSION
            or manifest.get("fingerprint") != fingerprint
            or manifest.get("artifact_hashes") != {name: hashes[name] for name in sorted(PRE_MANIFEST_NAMES)}
            or manifest.get("qa") != []
            or manifest.get("execution_mode") != "plan_only"
            or type(manifest.get("model_calls")) is not int
            or manifest.get("model_calls") != 0
            or manifest.get("query_plan_verified") is not True
            or not isinstance(manifest.get("query_plan"), list)
            or not manifest.get("query_plan")
            or not isinstance(manifest.get("sqlite_version"), str)
        ):
            fail()

        report = _read_json_object(artifact_dir / "report.json", MAX_ARTIFACT_BYTES)
        if (
            set(report) != _REPORT_FIELDS
            or report.get("schema_version") != REPORT_SCHEMA
            or report.get("execution_mode") != "plan_only"
            or type(report.get("model_calls")) is not int
            or report.get("model_calls") != 0
            or report.get("query_plan_verified") is not True
            or report.get("sql_sha256") != hashes["pipeline.sql"]
            or report.get("query_plan") != manifest.get("query_plan")
            or report.get("sqlite_version") != manifest.get("sqlite_version")
        ):
            fail()
        if type(report.get("parameter_count")) is not int or report["parameter_count"] < 0:
            fail()

        return _accepted_result(
            fingerprint,
            artifact_dir,
            hashes,
            (),
            0,
            replay=True,
            metadata={"execution_mode": "plan_only", "query_plan_verified": True},
        )
    except PipelineJobError as error:
        if error.code == "replay_integrity_error":
            raise
        raise PipelineJobError("replay_integrity_error") from None
    except (OSError, ValueError, TypeError, KeyError, RecursionError):
        raise PipelineJobError("replay_integrity_error") from None


def _job_bytes(artifact_dir: Path, receipt_path: Path) -> int:
    total = receipt_path.stat().st_size
    for name in ARTIFACT_NAMES:
        total += (artifact_dir / name).stat().st_size
    return total


def _accepted_result(
    fingerprint: str,
    artifact_dir: Path,
    hashes: dict[str, str],
    qa: tuple[str, ...],
    bytes_written: int,
    *,
    replay: bool = False,
    metadata: dict[str, str | bool] | None = None,
) -> PipelineJobResult:
    return PipelineJobResult(
        fingerprint=fingerprint,
        status="accepted",
        artifact_dir=artifact_dir,
        artifact_hashes=dict(hashes),
        qa=qa,
        model_calls=0,
        replay=replay,
        bytes_written=bytes_written,
        error_code=None,
        metadata=metadata or {"execution_mode": "plan_only", "query_plan_verified": True},
    )


def _rejected_result(error_code: str, qa: tuple[str, ...] = ()) -> PipelineJobResult:
    return PipelineJobResult(
        fingerprint=None,
        status="rejected",
        artifact_dir=None,
        artifact_hashes={},
        qa=qa,
        model_calls=0,
        replay=False,
        bytes_written=0,
        error_code=error_code,
        metadata={"execution_mode": "plan_only", "query_plan_verified": False},
    )


def _remove_stale_staging_dirs(root: Path, fingerprint: str) -> None:
    """Remove staging dirs a crashed run left for this fingerprint.

    Staging names embed the fingerprint and are only created while holding
    this fingerprint's lock, so any that exist now are abandoned.
    """
    jobs_dir = root / "jobs"
    if jobs_dir.is_symlink() or not jobs_dir.is_dir():
        return
    for candidate in jobs_dir.glob(f".{fingerprint}.staging-*"):
        _remove_staging_dir(candidate)


def _recover_missing_receipt(
    fingerprint: str,
    staging_dir: Path,
    artifact_dir: Path,
    receipt_path: Path,
    rebuilt_hashes: dict[str, str],
) -> PipelineJobResult:
    """Write the receipt a crashed publish never wrote, if the bytes match.

    The job is deterministic, so a fresh rebuild in ``staging_dir`` must hash
    identically to the published directory. Any difference (tampering, or a
    different SQLite/recipe) fails closed and leaves the directory untouched.
    """
    try:
        published_hashes = _hash_all_artifacts(artifact_dir)
    except PipelineJobError:
        raise PipelineJobError("replay_integrity_error") from None
    if published_hashes != rebuilt_hashes:
        raise PipelineJobError("replay_integrity_error")
    _remove_staging_dir(staging_dir)
    receipt_bytes = _write_receipt_exclusive(receipt_path, _build_receipt(fingerprint, rebuilt_hashes))
    verified = _verify_replay(fingerprint, artifact_dir, receipt_path)
    return replace(
        verified,
        bytes_written=receipt_bytes,
        metadata={**verified.metadata, "receipt_recovered": True},
    )


def run_pipeline_job(
    request: PipelineRequest | dict[str, Any],
    output_root: str | Path,
    *,
    ai_proposal: Mapping[str, Any] | None = None,
) -> PipelineJobResult:
    """Validate, compile, plan-check, and publish private replay-safe artifacts.

    Crash recovery: abandoned staging directories for the fingerprint are
    removed, and published artifacts without a receipt (a crash between the
    publish rename and the receipt write) get their receipt only after a
    deterministic rebuild reproduces them byte for byte.

    Every fresh job records ``trace.json`` (+ ``MANUAL-STEPS.md`` and
    ``ir.json`` when accepted) and one ``history.jsonl`` line; replays write
    nothing. ``ai_proposal`` is the metadata-only view of a V1B proposal.
    """
    root = Path(output_root).expanduser()
    if not root.is_absolute():
        root = Path.cwd() / root
    root = Path(os.path.abspath(root))
    try:
        parsed = _parse_pipeline_request(request)
        pipeline = build_pipeline_ir(parsed)
    except PipelineValidationError as error:
        codes = tuple(issue.code for issue in error.issues)
        code = codes[0] if codes else "invalid_pipeline_request"
        raw = request.model_dump(mode="json") if isinstance(request, PipelineRequest) else request
        rejected = _new_trace(f"rejected-{sha256_json(raw)[:24]}", raw, ai_proposal)
        rejected.record_step("validate", "build_pipeline_ir", note=code)
        _trace_rejection(rejected, root, code, codes)
        return _rejected_result(code, codes)

    fingerprint = _canonical_fingerprint(parsed, RECIPE_VERSION)
    _ensure_directory(root)
    artifact_dir = root / "jobs" / fingerprint
    receipt_path = root / ".receipts" / f"{fingerprint}.json"
    lock_path = root / ".locks" / f"{fingerprint}.lock"

    with _idempotency_lock(lock_path):
        _remove_stale_staging_dirs(root, fingerprint)
        artifacts_present = artifact_dir.exists() or artifact_dir.is_symlink()
        receipt_present = receipt_path.exists() or receipt_path.is_symlink()
        if receipt_present or (artifacts_present and (artifact_dir.is_symlink() or not artifact_dir.is_dir())):
            return _verify_replay(fingerprint, artifact_dir, receipt_path)
        recovering = artifacts_present

        trace = _new_trace(fingerprint, _fingerprint_view(parsed), ai_proposal)
        _trace_plan(trace, parsed, pipeline)
        with trace.step("compile", "compile_sql", dialect="sqlite") as compile_step:
            compiled = compile_pipeline_ir(pipeline)
            compile_step.params.update({
                "ctes": len(pipeline.ordered_nodes), "parameters": len(compiled.parameters),
                "parameter_types": list(compiled.parameter_types), "sql_sha256": hashlib.sha256(compiled.sql.encode("utf-8")).hexdigest(),
            })
        try:
            with trace.step("plan", "explain_query_plan") as plan_step:
                query_plan = explain_pipeline_sql(pipeline, compiled)
                plan_step.params.update({"plan_steps": len(query_plan.steps), "sqlite_version": query_plan.sqlite_version})
        except PipelineExplainError:
            if recovering:
                raise PipelineJobError("replay_integrity_error") from None
            _trace_rejection(trace, root, "pipeline_sql_invalid")
            return _rejected_result("pipeline_sql_invalid")

        report = build_pipeline_report(pipeline, compiled, query_plan.steps, query_plan.sqlite_version)
        pre_manifest_files = {
            "pipeline.sql": compiled.sql.encode("utf-8"),
            "parameters.json": _json_bytes(parameter_document(compiled)),
            "report.json": _json_bytes(report),
        }
        staging_dir = _create_staging_dir(root, fingerprint)
        recovered: PipelineJobResult | None = None
        try:
            _write_staged_artifacts(staging_dir, pre_manifest_files)
            trace.record_step("render", "write_plan_artifacts", outputs=sorted(pre_manifest_files))
            qa = qa_pipeline_artifacts(pipeline, compiled, query_plan, staging_dir)
            trace.record_step("qa", "reopen_artifacts", issues=list(qa))
            if qa:
                if recovering:
                    raise PipelineJobError("replay_integrity_error")
                _trace_rejection(trace, root, "pipeline_artifact_qa_failed", qa)
                return _rejected_result("pipeline_artifact_qa_failed", qa)
            hashes = _hash_artifacts(staging_dir, pre_manifest_files)
            manifest = _build_qa_manifest(parsed, fingerprint, hashes, qa, query_plan)
            _write_staged_artifacts(staging_dir, {"qa-manifest.json": _json_bytes(manifest)})
            all_hashes = _hash_all_artifacts(staging_dir)
            if recovering:
                recovered = _recover_missing_receipt(fingerprint, staging_dir, artifact_dir, receipt_path, all_hashes)
            else:
                receipt = _build_receipt(fingerprint, all_hashes)
                _publish_job(staging_dir, artifact_dir, receipt_path, receipt)
        except PipelineJobError:
            raise
        except (OSError, ValueError, TypeError):
            raise PipelineJobError("artifact_write_failed") from None
        finally:
            if staging_dir.exists():
                _remove_staging_dir(staging_dir)

        try:
            _trace_accepted(trace, root, artifact_dir, pipeline, compiled, note="receipt_recovered" if recovered is not None else "")
        except OSError:
            raise PipelineJobError("trace_write_failed") from None
        if recovered is not None:
            return recovered
        return _accepted_result(
            fingerprint,
            artifact_dir,
            all_hashes,
            qa,
            _job_bytes(artifact_dir, receipt_path),
        )


__all__ = ["ENGINE_VERSION", "SIDECAR_NAMES", "TOOL_ID", "PipelineJobError", "PipelineJobResult", "run_pipeline_job"]
