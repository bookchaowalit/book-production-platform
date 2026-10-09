"""Replay-safe local Data + Data Viz jobs."""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import time
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from data_ir import ArtifactRef, ChartIR, DatasetIR, DatasetRequest
from data_ir.models import CHART_DISPLAY_DEFAULTS
from production_trace import TraceRecorder
from pydantic import ValidationError

from .aggregation import ChartBuildError, build_chart_ir
from .manual import build_excel_guide
from .qa import qa_outputs
from .recipe import DatasetBuildError, DatasetBuildStats, build_dataset_ir_with_stats
from .render import RenderError, render_outputs
from .telemetry import JobTelemetry

TOOL_ID = "data-viz-v1a"
ENGINE_VERSION = "data-production-engine.v4"
IR_SCHEMA = "dataset-ir.v1+chart-ir.v1"
# v4 adds horizontal_bar/pie, sort/top-N/"Other", number formats and data labels.
# A v3 request (display fields at their defaults) renders byte-identical output.
RECIPE_VERSION = "csv-cleaner-chart-builder-v4"
REPLAYABLE_RECIPE_VERSIONS = frozenset({"csv-cleaner-chart-builder-v1", "csv-cleaner-chart-builder-v3", RECIPE_VERSION})
_MEDIA_TYPES = {"cleaned.csv": "text/csv; charset=utf-8", "chart.svg": "image/svg+xml", "report.json": "application/json"}


class IdempotencyConflict(ValueError):
    """Raised when an idempotency key is reused with another input."""


@dataclass(frozen=True)
class JobResult:
    job_id: str
    status: str
    idempotency_key: str
    input_hash: str
    artifacts: tuple[ArtifactRef, ...] = ()
    manifest_path: Path | None = None
    qa: tuple[str, ...] = ()
    model_calls: int = 0
    replayed: bool = False
    bytes_written: int = 0
    error_code: str | None = None
    metadata: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "status": self.status,
            "idempotency_key": self.idempotency_key,
            "input_hash": self.input_hash,
            "artifacts": [artifact.model_dump() for artifact in self.artifacts],
            "manifest_path": str(self.manifest_path) if self.manifest_path else None,
            "qa": list(self.qa),
            "model_calls": self.model_calls,
            "replayed": self.replayed,
            "bytes_written": self.bytes_written,
            "error_code": self.error_code,
            "metadata": self.metadata or {},
        }


def _request_payload(request: DatasetRequest) -> dict[str, Any]:
    """Request dump without v4 display fields left at their defaults (keeps v1-v3 hashes stable)."""

    payload = request.model_dump(mode="json")
    chart = payload.get("chart")
    if isinstance(chart, dict):
        for key, default in CHART_DISPLAY_DEFAULTS.items():
            if chart.get(key) == default:
                chart.pop(key, None)
    return payload


def _canonical_hash(request: DatasetRequest, recipe_version: str) -> str:
    payload = json.dumps({"recipe": recipe_version, "request": _request_payload(request)}, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _atomic_write(path: Path, data: bytes) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent, delete=False) as handle:
        handle.write(data)
        temporary = Path(handle.name)
    temporary.replace(path)
    return len(data)


def _from_manifest(path: Path, payload: dict[str, Any], artifact_root: Path) -> JobResult:
    artifacts = tuple(ArtifactRef.model_validate(item) for item in payload.get("artifacts", []))
    integrity_errors: list[str] = []
    resolved_root = artifact_root.resolve()
    for artifact in artifacts:
        artifact_path = artifact_root / artifact.filename
        if Path(artifact.filename).name != artifact.filename or artifact_path.resolve().parent != resolved_root:
            integrity_errors.append(f"unsafe_path:{artifact.filename}")
            continue
        if not artifact_path.exists():
            integrity_errors.append(f"missing:{artifact.filename}")
            continue
        data = artifact_path.read_bytes()
        content_hash = hashlib.sha256(data).hexdigest()
        if len(data) != artifact.byte_size or content_hash != artifact.content_hash:
            integrity_errors.append(f"hash_mismatch:{artifact.filename}")
    if integrity_errors:
        return JobResult(job_id=str(payload["job_id"]), status="rejected", idempotency_key=str(payload["idempotency_key"]), input_hash=str(payload["input_hash"]), manifest_path=path, error_code="artifact_integrity_failed", metadata={"artifacts": integrity_errors})
    telemetry = payload.get("telemetry", {})
    return JobResult(
        job_id=str(payload["job_id"]),
        status=str(payload["status"]),
        idempotency_key=str(payload["idempotency_key"]),
        input_hash=str(payload["input_hash"]),
        artifacts=artifacts,
        manifest_path=path,
        qa=tuple(str(value) for value in payload.get("qa", [])),
        model_calls=int(telemetry.get("model_calls", 0)),
        replayed=True,
        bytes_written=0,
        metadata=dict(payload.get("metadata", {})),
    )


def _new_trace(job_id: str, key_hash: str) -> TraceRecorder:
    return TraceRecorder(
        tool_id=TOOL_ID,
        job_id=job_id,
        engine_version=ENGINE_VERSION,
        recipe_version=RECIPE_VERSION,
        schema_version="dataset-request.v1",
        idempotency_key_sha256=key_hash,
    )


def _record_ai(trace: TraceRecorder, ai_proposal: Mapping[str, Any] | None) -> None:
    if ai_proposal is None:
        return
    trace.ai_proposal(
        model_calls=int(ai_proposal.get("model_calls", 0) or 0),
        adapter=str(ai_proposal.get("adapter", "unknown")),
        mode=str(ai_proposal.get("mode", "live")),
        provided=[str(item) for item in ai_proposal.get("provided", []) or []],
        inferred=[str(item) for item in ai_proposal.get("inferred", []) or []],
    )


_SAFE_CODE = re.compile(r"[A-Za-z0-9_.,-]{1,120}")


def _trace_failure(trace: TraceRecorder, job_dir: Path, root: Path, code: str) -> None:
    """Record failed jobs in the history too; tracing must never mask the real failure."""

    if not _SAFE_CODE.fullmatch(code):
        code = "unclassified_error"  # free-form exception text never enters the trace
    try:
        trace.record_step("outcome", "failed", note=code)
        trace.finish(status="failed", job_dir=job_dir, output_root=root)
    except OSError:
        pass


def _dataset_params(dataset: DatasetIR, stats: DatasetBuildStats) -> dict[str, Any]:
    return {
        "schema_mode": stats.schema_mode,
        "source_rows": stats.source_rows,
        "rows": len(dataset.rows),
        "columns": [{"key": column.key, "type": column.data_type, "required": column.required} for column in dataset.columns],
        "transforms": [
            {
                "op": stat.op,
                "column": stat.column,
                "rows_before": stat.rows_before,
                "rows_after": stat.rows_after,
                "cells_changed": stat.cells_changed,
                "dropped_rows": len(stat.dropped_source_rows),
            }
            for stat in stats.transforms
        ],
    }


def _chart_params(chart: ChartIR) -> dict[str, Any]:
    spec = chart.spec
    return {
        "chart_type": spec.chart_type,
        "aggregation": spec.aggregation,
        "x_column": spec.x_column,
        "y_column": spec.y_column,
        "sort": spec.sort,
        "top_n": spec.top_n,
        "grouped_rest": spec.group_rest_as is not None,
        "folded_categories": len(chart.folded_categories),
        "categories": len(chart.categories),
        "value_decimals": spec.value_decimals,
        "thousands_separator": spec.thousands_separator,
        "data_labels": spec.data_labels,
        "width": spec.width,
        "height": spec.height,
    }


def _run_data_job_unlocked(
    request: DatasetRequest | dict[str, Any],
    output_dir: str | Path,
    idempotency_key: str,
    ai_proposal: Mapping[str, Any] | None = None,
) -> JobResult:
    """Clean, aggregate, render and QA one dataset with replay semantics."""

    if not idempotency_key.strip():
        raise IdempotencyConflict("idempotency_key_required")
    root = Path(output_dir)
    key_hash = hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest()[:16]
    job_dir = root / f"job-{key_hash}"
    try:
        parsed = request if isinstance(request, DatasetRequest) else DatasetRequest.model_validate(request)
    except ValidationError:
        trace = _new_trace(key_hash, key_hash)
        trace.input_json("request", request)
        _record_ai(trace, ai_proposal)
        trace.record_step("validate", "parse_request", note="invalid_request").status = "failed"
        _trace_failure(trace, job_dir, root, "invalid_request")
        return JobResult(job_id="", status="rejected", idempotency_key=idempotency_key, input_hash="", error_code="invalid_request")
    input_hash = _canonical_hash(parsed, RECIPE_VERSION)
    manifest_path = job_dir / "manifest.json"
    if manifest_path.exists():
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest_metadata = payload.get("metadata")
        stored_recipe = str(manifest_metadata.get("recipe_version", "")) if isinstance(manifest_metadata, dict) else ""
        stored_request_hash = _canonical_hash(parsed, stored_recipe) if stored_recipe in REPLAYABLE_RECIPE_VERSIONS else ""
        if payload.get("input_hash") not in {input_hash, stored_request_hash}:
            raise IdempotencyConflict("idempotency_key_reused_with_different_input")
        return _from_manifest(manifest_path, payload, job_dir)

    job_id = hashlib.sha256(f"{idempotency_key}:{input_hash}".encode()).hexdigest()[:20]
    trace = _new_trace(job_id, key_hash)
    trace.input_json("request", _request_payload(parsed))
    trace.input_json("csv_text", parsed.csv_text, role="source")
    _record_ai(trace, ai_proposal)
    try:
        with trace.step("validate", "parse_request", schema=parsed.schema_version, delimiter=parsed.delimiter, transforms=len(parsed.transforms), chart=parsed.chart is not None):
            pass
        with trace.step("recipe", "build_dataset_ir", recipe="csv-cleaner-v1") as dataset_step:
            dataset, stats = build_dataset_ir_with_stats(parsed)
            dataset_step.params.update(_dataset_params(dataset, stats))
        chart: ChartIR | None = None
        if parsed.chart is not None:
            with trace.step("aggregate", "build_chart_ir") as chart_step:
                chart = build_chart_ir(dataset, parsed.chart)
                chart_step.params.update(_chart_params(chart))
        ir_payload = {"dataset": dataset.model_dump(mode="json"), "chart": chart.model_dump(mode="json") if chart is not None else None}
        _atomic_write(job_dir / "ir.json", json.dumps(ir_payload, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8"))
        trace.ir_document(ir_payload, schema=IR_SCHEMA, path=job_dir / "ir.json")
        with trace.step("render", "render_outputs") as render_step:
            artifacts = render_outputs(dataset, chart, job_dir)
            render_step.outputs = [artifact.filename for artifact in artifacts]
        with trace.step("qa", "qa_outputs") as qa_step:
            qa = qa_outputs(dataset, chart, job_dir)
            qa_step.status = "failed" if qa else "ok"
            qa_step.params["issues"] = list(qa)
        if qa:
            trace.qa = {"status": "failed", "checks": len(qa)}
            _trace_failure(trace, job_dir, root, "qa_failed")
            return JobResult(job_id=job_id, status="rejected", idempotency_key=idempotency_key, input_hash=input_hash, qa=qa, error_code="qa_failed")
    except (DatasetBuildError, ChartBuildError, RenderError, ValueError) as exc:
        code = "invalid_request" if isinstance(exc, DatasetBuildError) else str(exc).split(":", 1)[0] or exc.__class__.__name__
        _trace_failure(trace, job_dir, root, code)
        return JobResult(job_id=job_id, status="rejected", idempotency_key=idempotency_key, input_hash=input_hash, error_code=code)
    except Exception as exc:
        _trace_failure(trace, job_dir, root, type(exc).__name__)
        raise

    manifest = {
        "schema_version": "data-job.v1",
        "job_id": job_id,
        "status": "accepted",
        "idempotency_key": idempotency_key,
        "input_hash": input_hash,
        "artifacts": [artifact.model_dump() for artifact in artifacts],
        "qa": list(qa),
        "telemetry": JobTelemetry(bytes_written=sum(artifact.byte_size for artifact in artifacts)).to_dict(),
        "metadata": {"recipe_version": RECIPE_VERSION, "model_calls": "0"},
    }
    manifest_bytes = json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8")
    bytes_written = _atomic_write(manifest_path, manifest_bytes) + sum(artifact.byte_size for artifact in artifacts)
    trace.record_step("publish", "write_manifest", outputs=["manifest.json", "ir.json"])
    for artifact in artifacts:
        trace.artifact(job_dir / artifact.filename, media_type=_MEDIA_TYPES.get(artifact.filename, artifact.media_type))
    try:
        trace.finish(status="accepted", job_dir=job_dir, output_root=root, guide=build_excel_guide(parsed, dataset, chart, stats), qa={"status": "passed", "checks": []})
    except OSError:
        pass  # the accepted manifest is already published; a trace write error must not turn it into a failure
    return JobResult(job_id=job_id, status="accepted", idempotency_key=idempotency_key, input_hash=input_hash, artifacts=artifacts, manifest_path=manifest_path, qa=qa, bytes_written=bytes_written, metadata={"recipe_version": RECIPE_VERSION})


def _acquire_lock(lock_path: Path, timeout_seconds: float = 30.0, stale_seconds: float = 300.0) -> None:
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + timeout_seconds
    while True:
        try:
            descriptor = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.close(descriptor)
            return
        except FileExistsError:
            try:
                if time.time() - lock_path.stat().st_mtime > stale_seconds:
                    lock_path.unlink(missing_ok=True)
                    continue
            except FileNotFoundError:
                continue
            if time.monotonic() >= deadline:
                raise IdempotencyConflict("idempotency_lock_timeout")
            time.sleep(0.02)


def run_data_job(
    request: DatasetRequest | dict[str, Any],
    output_dir: str | Path,
    idempotency_key: str,
    *,
    ai_proposal: Mapping[str, Any] | None = None,
) -> JobResult:
    """Serialize duplicate requests for the same key across local processes.

    ``ai_proposal`` is the metadata of a V1B proposal (``model_calls``,
    ``adapter``, ``provided``, ``inferred``) when the request came from
    ``propose``; it is recorded in the trace only.
    """

    if not idempotency_key.strip():
        raise IdempotencyConflict("idempotency_key_required")
    output_root = Path(output_dir)
    key_hash = hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest()[:16]
    lock_path = output_root / f".data-job-{key_hash}.lock"
    _acquire_lock(lock_path)
    try:
        return _run_data_job_unlocked(request, output_root, idempotency_key, ai_proposal)
    finally:
        lock_path.unlink(missing_ok=True)


__all__ = ["ENGINE_VERSION", "RECIPE_VERSION", "TOOL_ID", "IdempotencyConflict", "JobResult", "run_data_job"]
