"""Replay-safe local execution for Workflow V1A form-to-document jobs."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
import threading
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from production_trace import TraceRecorder
from pydantic import ValidationError
from workflow_ir import (
    ArtifactRef,
    WorkflowIR,
    WorkflowRequest,
    compile_workflow,
    route_workflow,
    validate_request,
)

from .bpmn import build_bpmn_model, render_bpmn
from .manual import build_workflow_guide
from .qa import QA_CHECKS, run_bpmn_qa, run_workflow_qa
from .render import render_document, render_execution_plan
from .telemetry import JobTelemetry

CONTRACT_VERSION = "workflow.v1a"
# v2: decision gateways (conditional branches), SLA timeouts/escalation, BPMN 2.0 XML export.
RECIPE_VERSION = "form-to-document-v2"
TOOL_ID = "workflow-v1a"
ENGINE_VERSION = "workflow-render.v2"
JOBS_DIRNAME = "jobs"
BPMN_MEDIA_TYPE = "application/xml"
ARTIFACT_MEDIA_TYPES = frozenset(("application/json", "text/markdown", BPMN_MEDIA_TYPE))
_CODE = re.compile(r"[a-z][a-z0-9_,]{0,119}")


class IdempotencyConflict(ValueError):
    """Raised when an idempotency key is reused with another input."""


class ReplayIntegrityError(ValueError):
    """Raised when a previously accepted artifact no longer matches."""


@dataclass(frozen=True)
class WorkflowJobResult:
    workflow_id: str
    status: str
    idempotency_key: str
    input_hash: str
    artifacts: tuple[ArtifactRef, ...] = ()
    manifest_filename: str | None = None
    model_calls: int = 0
    replayed: bool = False
    bytes_written: int = 0
    error_code: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "workflow_id": self.workflow_id,
            "status": self.status,
            "idempotency_key": self.idempotency_key,
            "input_hash": self.input_hash,
            "artifacts": [artifact.model_dump() for artifact in self.artifacts],
            "manifest_filename": self.manifest_filename,
            "model_calls": self.model_calls,
            "replayed": self.replayed,
            "bytes_written": self.bytes_written,
            "error_code": self.error_code,
            "metadata": self.metadata,
        }


_LOCKS: dict[str, threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()


def _key_lock(output_dir: Path, key: str) -> threading.Lock:
    name = f"{output_dir.resolve()}::{key}"
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(name, threading.Lock())


def _write_atomic(path: Path, data: bytes) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent, delete=False) as handle:
        handle.write(data)
        temporary = Path(handle.name)
    temporary.replace(path)
    return len(data)


def _safe_name(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", value).strip("._")[:80] or "workflow"


def _artifact(path: Path, media_type: str) -> ArtifactRef:
    data = path.read_bytes()
    return ArtifactRef(filename=path.name, media_type=media_type, content_hash=hashlib.sha256(data).hexdigest(), byte_size=len(data))


def _input_hash(ir: WorkflowIR) -> str:
    payload = {"recipe_version": RECIPE_VERSION, "workflow": ir.model_dump(mode="json")}
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _verify_artifacts(output_path: Path, manifest: dict[str, Any]) -> tuple[ArtifactRef, ...]:
    raw = manifest.get("artifacts")
    if not isinstance(raw, list) or len(raw) != len(ARTIFACT_MEDIA_TYPES):
        raise ReplayIntegrityError("manifest_artifacts_invalid")
    artifacts: list[ArtifactRef] = []
    for item in raw:
        try:
            artifact = ArtifactRef.model_validate(item)
        except ValidationError as exc:
            raise ReplayIntegrityError("manifest_artifact_invalid") from exc
        if Path(artifact.filename).name != artifact.filename or Path(artifact.filename).is_absolute():
            raise ReplayIntegrityError("artifact_path_invalid")
        path = output_path / artifact.filename
        if not path.is_file():
            raise ReplayIntegrityError("artifact_missing")
        actual = _artifact(path, artifact.media_type)
        if actual.byte_size != artifact.byte_size or actual.content_hash != artifact.content_hash:
            raise ReplayIntegrityError("artifact_hash_mismatch")
        artifacts.append(artifact)
    media_types = {artifact.media_type for artifact in artifacts}
    if media_types != ARTIFACT_MEDIA_TYPES:
        raise ReplayIntegrityError("artifact_types_invalid")
    plan = next(artifact for artifact in artifacts if artifact.media_type == "application/json")
    document = next(artifact for artifact in artifacts if artifact.media_type == "text/markdown")
    bpmn = next(artifact for artifact in artifacts if artifact.media_type == BPMN_MEDIA_TYPE)
    try:
        plan_payload = json.loads((output_path / plan.filename).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ReplayIntegrityError("execution_plan_unreadable") from exc
    if not isinstance(plan_payload, dict) or manifest.get("workflow_id") != plan_payload.get("workflow_id"):
        raise ReplayIntegrityError("manifest_workflow_mismatch")
    issues = run_workflow_qa(
        (output_path / plan.filename).read_text(encoding="utf-8"),
        (output_path / document.filename).read_text(encoding="utf-8"),
    )
    issues += run_bpmn_qa((output_path / bpmn.filename).read_text(encoding="utf-8"), (output_path / plan.filename).read_text(encoding="utf-8"))
    if issues:
        raise ReplayIntegrityError("workflow_qa_failed")
    return tuple(artifacts)


def _replay(output_path: Path, manifest_path: Path, input_hash: str, key: str) -> WorkflowJobResult:
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ReplayIntegrityError("manifest_unreadable") from exc
    if not isinstance(manifest, dict) or set(manifest) != {
        "schema_version",
        "workflow_id",
        "status",
        "idempotency_key",
        "input_hash",
        "artifacts",
        "telemetry",
        "metadata",
    } or manifest.get("schema_version") != "workflow-manifest.v1" or manifest.get("status") != "accepted":
        raise ReplayIntegrityError("manifest_schema_invalid")
    if manifest.get("idempotency_key") != key:
        raise ReplayIntegrityError("manifest_key_mismatch")
    if manifest.get("input_hash") != input_hash:
        raise IdempotencyConflict("idempotency_key_reused_with_different_input")
    artifacts = _verify_artifacts(output_path, manifest)
    telemetry = manifest.get("telemetry", {})
    if not isinstance(telemetry, dict) or set(telemetry) != {"model_calls", "replayed", "bytes_written"} or telemetry.get("model_calls") != 0 or telemetry.get("replayed") is not False or telemetry.get("bytes_written") != sum(item.byte_size for item in artifacts):
        raise ReplayIntegrityError("telemetry_invalid")
    metadata = manifest.get("metadata", {})
    if not isinstance(metadata, dict) or set(metadata) - {"trace_job_id"} != {"contract_version", "recipe_version", "execution_mode"} or metadata.get("contract_version") != CONTRACT_VERSION or metadata.get("recipe_version") != RECIPE_VERSION or metadata.get("execution_mode") != "plan_only":
        raise ReplayIntegrityError("metadata_invalid")
    return WorkflowJobResult(
        workflow_id=str(manifest.get("workflow_id", "")),
        status="accepted",
        idempotency_key=key,
        input_hash=input_hash,
        artifacts=artifacts,
        manifest_filename=manifest_path.name,
        model_calls=0,
        replayed=True,
        metadata=dict(metadata),
    )


def _rejected(key: str, workflow_id: str, code: str) -> WorkflowJobResult:
    return WorkflowJobResult(
        workflow_id=workflow_id,
        status="rejected",
        idempotency_key=key,
        input_hash="",
        error_code=code,
        metadata={"contract_version": CONTRACT_VERSION, "recipe_version": RECIPE_VERSION},
    )


def _error_code(exc: Exception) -> str:
    """A stable, text-free failure code for traces (pydantic messages can echo input values)."""

    if isinstance(exc, ValueError) and not isinstance(exc, ValidationError):
        code = str(exc).split(":", 1)[-1]
        if _CODE.fullmatch(code):
            return code[:120]
    return type(exc).__name__


def _snapshot(source: Path, job_dir: Path) -> Path:
    """Hard-link (or copy) a delivered file into the job folder so ``verify`` re-hashes the same bytes."""

    job_dir.mkdir(parents=True, exist_ok=True)
    target = job_dir / source.name
    target.unlink(missing_ok=True)
    try:
        os.link(source, target)
    except OSError:
        shutil.copy2(source, target)
    return target


def _proposal_trace(trace: TraceRecorder, proposal: Mapping[str, Any] | None) -> None:
    if not proposal:
        return
    trace.ai_proposal(
        model_calls=int(proposal.get("model_calls", 0)),
        adapter=str(proposal.get("adapter", "unknown"))[:80],
        mode=str(proposal.get("mode", "unknown"))[:20],
        provided=[name for name in proposal.get("provided", []) if isinstance(name, str) and _CODE.fullmatch(name)],
        inferred=[name for name in proposal.get("inferred", []) if isinstance(name, str) and _CODE.fullmatch(name)],
    )


def _trace_end(trace: TraceRecorder, job_dir: Path, root: Path, status: str, code: str) -> None:
    """Record rejected/failed attempts in the history too; tracing must never mask the real outcome."""

    try:
        trace.record_step("outcome", status, note=code)
        trace.finish(status=status, job_dir=job_dir, output_root=root)
    except OSError:
        pass


def run_workflow_job(
    payload: dict[str, Any] | WorkflowRequest,
    output_dir: str | Path,
    idempotency_key: str,
    *,
    proposal: Mapping[str, Any] | None = None,
) -> WorkflowJobResult:
    """Validate, plan, render, QA, and persist a replay-safe workflow package.

    The package is the execution plan (JSON), the document (Markdown), and a
    BPMN 2.0 diagram. Every new attempt (accepted, rejected, or failed) writes
    ``jobs/<job_id>/trace.json`` and one ``history.jsonl`` line; accepted jobs
    also get ``ir.json`` and ``MANUAL-STEPS.md``. ``proposal`` is metadata from
    the V1B brief adapter (no description text).
    """

    if not idempotency_key.strip():
        raise IdempotencyConflict("idempotency_key_required")
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    workflow_id = str(payload.get("workflow_id", "")) if isinstance(payload, dict) else payload.workflow_id
    trace = TraceRecorder(
        tool_id=TOOL_ID,
        engine_version=ENGINE_VERSION,
        recipe_version=RECIPE_VERSION,
        schema_version="workflow-request.v1",
        idempotency_key_sha256=hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest(),
    )
    job_dir = output_path / JOBS_DIRNAME / trace.job_id
    trace.input_json("request", payload.model_dump(mode="json") if isinstance(payload, WorkflowRequest) else payload)
    _proposal_trace(trace, proposal)
    try:
        return _run_traced(payload, output_path, idempotency_key, workflow_id, trace, job_dir)
    except Exception as exc:
        _trace_end(trace, job_dir, output_path, "failed", _error_code(exc))
        raise


def _run_traced(
    payload: dict[str, Any] | WorkflowRequest,
    output_path: Path,
    idempotency_key: str,
    workflow_id: str,
    trace: TraceRecorder,
    job_dir: Path,
) -> WorkflowJobResult:
    try:
        with trace.step("validate", "validate_request", schema="workflow-request.v1") as validate_step:
            request = payload if isinstance(payload, WorkflowRequest) else WorkflowRequest.model_validate(payload)
            issues = validate_request(request)
            validate_step.outputs = [issue.code for issue in issues[:20]]
        if issues:
            _trace_end(trace, job_dir, output_path, "rejected", issues[0].code)
            return _rejected(idempotency_key, request.workflow_id, issues[0].code)
        with trace.step("recipe", "compile_workflow", recipe=RECIPE_VERSION) as compile_step:
            ir = compile_workflow(request)
            compile_step.outputs = [step.step_id for step in ir.steps]
        input_hash = _input_hash(ir)
    except (ValidationError, ValueError) as exc:
        _trace_end(trace, job_dir, output_path, "rejected", _error_code(exc))
        return _rejected(idempotency_key, workflow_id, "invalid_request")

    manifest_path = output_path / f"manifest-{hashlib.sha256(idempotency_key.encode('utf-8')).hexdigest()[:16]}.json"
    with _key_lock(output_path, idempotency_key):
        if manifest_path.exists():
            # A replay produces nothing new; the original job's trace stays the record.
            return _replay(output_path, manifest_path, input_hash, idempotency_key)
        ir_json = ir.model_dump(mode="json")
        job_dir.mkdir(parents=True, exist_ok=True)
        _write_atomic(job_dir / "ir.json", json.dumps(ir_json, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8"))
        trace.ir_document(ir_json, schema=ir.schema_version, path=job_dir / "ir.json")
        prefix = f"{_safe_name(ir.workflow_id)}-{input_hash[:12]}"
        plan_path = output_path / f"{prefix}.execution.json"
        document_path = output_path / f"{prefix}.document.md"
        bpmn_path = output_path / f"{prefix}.bpmn"
        with trace.step("plan", "route_decisions") as route_step:
            route = route_workflow(ir)
            route_step.outputs = [f"{decision}->{target}" for decision, target in sorted(route.selected.items())]
            route_step.note = f"skipped={len(route.skipped)}"
        with trace.step(
            "render", "render_execution_plan", steps=len(ir.steps), kinds=sorted({step.kind for step in ir.steps}),
            edges=sum(len(step.depends_on) for step in ir.steps), roles=sorted({step.role for step in ir.steps if step.role}),
            max_attempts=[step.retry.max_attempts for step in ir.steps],
            timeouts=[step.timeout_minutes for step in ir.steps if step.timeout_minutes],
        ) as plan_step:
            plan_bytes = render_execution_plan(ir).encode("utf-8")
            plan_step.outputs = [plan_path.name]
        with trace.step("render", "render_document", sections=[section.kind for section in ir.document.sections]) as document_step:
            document_bytes = render_document(ir).encode("utf-8")
            document_step.outputs = [document_path.name]
        with trace.step("render", "render_bpmn", standard="BPMN 2.0", extensions="zeebe") as bpmn_step:
            model = build_bpmn_model(ir)
            bpmn_bytes = render_bpmn(ir, model).encode("utf-8")
            bpmn_step.params.update({"lanes": len(model.lanes), "nodes": len(model.nodes), "flows": len(model.flows)})
            bpmn_step.outputs = [bpmn_path.name]
        with trace.step("write", "write_package") as write_step:
            _write_atomic(plan_path, plan_bytes)
            _write_atomic(document_path, document_bytes)
            _write_atomic(bpmn_path, bpmn_bytes)
            write_step.outputs = [plan_path.name, document_path.name, bpmn_path.name]
        with trace.step("qa", "run_workflow_qa", checks=list(QA_CHECKS)) as qa_step:
            qa_issues = run_workflow_qa(plan_bytes.decode("utf-8"), document_bytes.decode("utf-8"))
            qa_issues = sorted(set(qa_issues + run_bpmn_qa(bpmn_bytes.decode("utf-8"), plan_bytes.decode("utf-8"))))
            qa_step.status = "failed" if qa_issues else "ok"
            qa_step.outputs = qa_issues[:20]
        if qa_issues:
            for path in (plan_path, document_path, bpmn_path):
                path.unlink(missing_ok=True)
            _trace_end(trace, job_dir, output_path, "rejected", qa_issues[0])
            return _rejected(idempotency_key, ir.workflow_id, qa_issues[0])
        artifacts = (_artifact(plan_path, "application/json"), _artifact(document_path, "text/markdown"), _artifact(bpmn_path, BPMN_MEDIA_TYPE))
        metadata = {"contract_version": CONTRACT_VERSION, "recipe_version": RECIPE_VERSION, "execution_mode": ir.execution_mode, "trace_job_id": trace.job_id}
        manifest = {
            "schema_version": "workflow-manifest.v1",
            "workflow_id": ir.workflow_id,
            "status": "accepted",
            "idempotency_key": idempotency_key,
            "input_hash": input_hash,
            "artifacts": [artifact.model_dump() for artifact in artifacts],
            "telemetry": JobTelemetry(model_calls=0, bytes_written=sum(item.byte_size for item in artifacts)).to_dict(),
            "metadata": metadata,
        }
        guide = build_workflow_guide(ir, plan_name=plan_path.name, document_name=document_path.name, bpmn_name=bpmn_path.name, model=model)
        for artifact in artifacts:
            trace.artifact(_snapshot(output_path / artifact.filename, job_dir), media_type=artifact.media_type)
        trace.record_step("publish", "write_manifest", outputs=[manifest_path.name])
        # The trace is written before the manifest, so every accepted manifest has a trace.
        trace.finish(status="accepted", job_dir=job_dir, output_root=output_path, guide=guide, qa={"status": "passed", "checks": list(QA_CHECKS)})
        manifest_bytes = json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8")
        bytes_written = sum(item.byte_size for item in artifacts) + _write_atomic(manifest_path, manifest_bytes)
        return WorkflowJobResult(
            workflow_id=ir.workflow_id,
            status="accepted",
            idempotency_key=idempotency_key,
            input_hash=input_hash,
            artifacts=artifacts,
            manifest_filename=manifest_path.name,
            bytes_written=bytes_written,
            metadata=dict(metadata),
        )


__all__ = ["IdempotencyConflict", "ReplayIntegrityError", "WorkflowJobResult", "run_workflow_job"]
