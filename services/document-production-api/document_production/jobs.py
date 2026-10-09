"""Idempotent local job execution and immutable manifest publication."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from document_ir import DocumentRequest
from production_trace import TraceRecorder
from pydantic import ValidationError

from .budget import BudgetLedger
from .extraction import BriefRequest
from .job_store import IdempotencyConflict, JobClaim, JobStore
from .manual import build_word_guide
from .proposal import PROPOSAL_RECIPE_ID, ProposalRequest, build_proposal_ir
from .qa import qa_document
from .recipe import RECIPE_ID, RequestValidationError, build_document_ir
from .renderers import render_document
from .report import REPORT_RECIPE_ID, ReportDocumentRequest, build_report_ir
from .routing import route_brief
from .spec_sheet import SPEC_SHEET_RECIPE_ID, SpecSheetRequest, build_spec_sheet_ir
from .telemetry import build_cost_event
from .transcript import (
    TRANSCRIPT_RECIPE_ID,
    TranscriptDocumentRequest,
    build_transcript_ir,
)

ENGINE_VERSION = "document-engine.v1"

__all__ = [
    "IdempotencyConflict",
    "JobResult",
    "run_document_brief_job",
    "run_document_job",
    "run_proposal_job",
    "run_report_job",
    "run_spec_sheet_job",
    "run_transcript_job",
]

@dataclass(frozen=True)
class JobResult:
    job_id: str
    status: str
    model_calls: int
    artifacts: list[dict[str, Any]]
    qa: dict[str, Any]
    cost_event: dict[str, Any]
    manifest_path: Path | None
    error_code: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "status": self.status,
            "model_calls": self.model_calls,
            "artifacts": self.artifacts,
            "qa": self.qa,
            "cost_event": self.cost_event,
            "manifest_path": str(self.manifest_path) if self.manifest_path else None,
            "error_code": self.error_code,
            "metadata": self.metadata,
        }


def _hash_json(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent, delete=False, mode="w", encoding="utf-8") as handle:
            temporary = Path(handle.name)
            json.dump(value, handle, ensure_ascii=False, sort_keys=True, indent=2)
            handle.write("\n")
        os.replace(temporary, path)
    except (OSError, TypeError, ValueError):
        if temporary is not None:
            temporary.unlink(missing_ok=True)
        raise


def _manifest_cost_event(manifest: dict[str, Any]) -> dict[str, Any]:
    event = dict(manifest.get("cost_event", {}))
    event.setdefault("schema_version", "cost-event-v2")
    event.setdefault("attempt_id", manifest["job_id"])
    event.setdefault("wall_seconds", event.get("cpu_seconds", 0.0))
    event.setdefault("replayed", False)
    return event


def _from_manifest(manifest: dict[str, Any], path: Path, cost_event: dict[str, Any] | None = None) -> JobResult:
    return JobResult(
        job_id=manifest["job_id"],
        status=manifest["status"],
        model_calls=manifest["model_calls"],
        artifacts=manifest["artifacts"],
        qa=manifest["qa"],
        cost_event=cost_event or _manifest_cost_event(manifest),
        manifest_path=path,
        metadata=dict(manifest.get("metadata", {})),
    )


def run_document_job(
    request: Any,
    output_root: str | Path,
    idempotency_key: str,
    *,
    request_hash_payload: Any | None = None,
    manifest_metadata: dict[str, Any] | None = None,
    model_calls: int = 0,
    ledger_schema_version: str | None = None,
    preclaimed_claim: JobClaim | None = None,
    ir_builder: Any = build_document_ir,
    recipe_version: str = RECIPE_ID,
    request_model: Any = DocumentRequest,
) -> JobResult:
    """Run the pinned recipe once and publish a manifest only after QA passes.

    ``request`` must satisfy ``request_model`` (the invoice
    :class:`DocumentRequest` by default, or the transcript request for
    :func:`run_transcript_job`, which also pins ``ir_builder`` and
    ``recipe_version``).
    """

    root = Path(output_root)
    root.mkdir(parents=True, exist_ok=True)
    job_id = str(uuid.uuid4())
    started_wall = time.perf_counter()
    started_cpu = time.process_time()
    try:
        parsed = request if isinstance(request, request_model) else request_model.model_validate(request)
    except ValidationError as exc:
        event = build_cost_event(
            job_id=job_id,
            attempt_id=job_id,
            model_calls=model_calls,
            wall_seconds=time.perf_counter() - started_wall,
            cpu_seconds=time.process_time() - started_cpu,
            bytes_written=0,
            status="failed",
        )
        issues = [{"code": "invalid_request", "message": error["msg"]} for error in exc.errors()]
        return JobResult(job_id, "failed", model_calls, [], {"status": "failed", "issues": issues, "page_count": None}, event.to_dict(), None, "ValidationError")
    request_hash = _hash_json(parsed.model_dump(mode="json") if request_hash_payload is None else request_hash_payload)
    key_hash = hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest()
    store = JobStore(root)
    claim = preclaimed_claim or store.claim(
        key_hash=key_hash,
        request_hash=request_hash,
        job_id=job_id,
        recipe_version=recipe_version,
        schema_version=ledger_schema_version or parsed.schema_version,
        engine_version=ENGINE_VERSION,
    )
    if claim.action == "running":
        event = build_cost_event(
            job_id=claim.job_id,
            attempt_id=job_id,
            model_calls=0,
            wall_seconds=time.perf_counter() - started_wall,
            cpu_seconds=time.process_time() - started_cpu,
            bytes_written=0,
            status="pending",
        )
        return JobResult(
            claim.job_id,
            "pending",
            0,
            [],
            {"status": "pending", "issues": [{"code": "job_in_progress", "message": "an equivalent job is already running"}], "page_count": None},
            event.to_dict(),
            None,
            "job_in_progress",
        )
    if claim.action == "replay":
        manifest_path = root / str(claim.manifest_path)
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        replay_event = build_cost_event(
            job_id=manifest["job_id"],
            attempt_id=str(uuid.uuid4()),
            model_calls=0,
            wall_seconds=time.perf_counter() - started_wall,
            cpu_seconds=time.process_time() - started_cpu,
            bytes_written=0,
            status=manifest["status"],
            replayed=True,
        ).to_dict()
        return _from_manifest(manifest, manifest_path, replay_event)

    job_id = claim.job_id
    job_dir = root / "jobs" / job_id
    trace = TraceRecorder(
        tool_id="documents-v1a",
        job_id=job_id,
        engine_version=ENGINE_VERSION,
        recipe_version=recipe_version,
        schema_version=ledger_schema_version or parsed.schema_version,
        idempotency_key_sha256=key_hash,
    )
    trace.input_json("request", parsed.model_dump(mode="json") if request_hash_payload is None else request_hash_payload)
    if model_calls:
        extraction = (manifest_metadata or {}).get("extraction", {})
        trace.ai_proposal(
            model_calls=model_calls,
            adapter=str(extraction.get("adapter", "unknown")),
            mode="live",
            provided=list(extraction.get("provided", [])),
            inferred=list(extraction.get("inferred", [])),
        )
    try:
        with trace.step("validate", "parse_request", schema=parsed.schema_version):
            pass
        with trace.step("recipe", "build_ir", recipe=recipe_version):
            ir = ir_builder(parsed)
        trace.ir_document(ir.model_dump(mode="json"), schema=ir.schema_version, path=job_dir / "ir.json")
        with trace.step("render", "render_docx_pdf", sections=len(ir.sections), totals=sorted(ir.derived_totals)) as render_step:
            artifacts = render_document(ir, job_dir)
            render_step.outputs = [artifact.filename for artifact in artifacts]
        with trace.step("qa", "qa_document") as qa_step:
            qa = qa_document(ir, artifacts, job_dir)
            qa_step.status = "ok" if qa.status == "passed" else "failed"
        bytes_written = sum(artifact.byte_size for artifact in artifacts)
        status = "accepted" if qa.status == "passed" else "failed"
        event = build_cost_event(
            job_id=job_id,
            attempt_id=job_id,
            model_calls=model_calls,
            wall_seconds=time.perf_counter() - started_wall,
            cpu_seconds=time.process_time() - started_cpu,
            bytes_written=bytes_written,
            status=status,
        )
        if status != "accepted":
            _trace_failure(trace, job_dir, root, "qa_failed")
            store.finish(job_id, state="failed", manifest_path=None)
            return JobResult(job_id, status, model_calls, [artifact.model_dump() for artifact in artifacts], qa.to_dict(), event.to_dict(), None, "qa_failed")

        manifest = {
            "job_id": job_id,
            "status": status,
            "model_calls": model_calls,
            "request_hash": request_hash,
            "recipe": ir.assumptions["recipe"],
            "artifacts": [artifact.model_dump() for artifact in artifacts],
            "qa": qa.to_dict(),
            "cost_event": event.to_dict(),
        }
        if manifest_metadata is not None:
            manifest["metadata"] = manifest_metadata
        manifest_path = job_dir / "manifest.json"
        _atomic_json(manifest_path, manifest)
        _atomic_json(job_dir / "ir.json", ir.model_dump(mode="json"))
        trace.record_step("publish", "write_manifest", outputs=["manifest.json", "ir.json"])
        for artifact in artifacts:
            trace.artifact(job_dir / artifact.filename, media_type=artifact.media_type)
        trace.finish(status=status, job_dir=job_dir, output_root=root, guide=build_word_guide(ir), qa=qa.to_dict())
        store.finish(job_id, state="accepted", manifest_path=str(manifest_path.relative_to(root)))
        return _from_manifest(manifest, manifest_path)
    except RequestValidationError as exc:
        event = build_cost_event(
            job_id=job_id,
            attempt_id=job_id,
            model_calls=model_calls,
            wall_seconds=time.perf_counter() - started_wall,
            cpu_seconds=time.process_time() - started_cpu,
            bytes_written=0,
            status="failed",
        )
        _trace_failure(trace, job_dir, root, type(exc).__name__)
        store.finish(job_id, state="failed", manifest_path=None)
        issues = [{"code": issue.code, "message": issue.message} for issue in exc.issues]
        return JobResult(job_id, "failed", model_calls, [], {"status": "failed", "issues": issues, "page_count": None}, event.to_dict(), None, type(exc).__name__)
    except (OSError, TypeError, ValueError, RuntimeError, KeyError) as exc:
        event = build_cost_event(
            job_id=job_id,
            attempt_id=job_id,
            model_calls=model_calls,
            wall_seconds=time.perf_counter() - started_wall,
            cpu_seconds=time.process_time() - started_cpu,
            bytes_written=0,
            status="failed",
        )
        _trace_failure(trace, job_dir, root, type(exc).__name__)
        store.finish(job_id, state="failed", manifest_path=None)
        return JobResult(job_id, "failed", model_calls, [], {"status": "failed", "issues": [{"code": type(exc).__name__, "message": "job failed before acceptance"}], "page_count": None}, event.to_dict(), None, type(exc).__name__)


def _trace_failure(trace: TraceRecorder, job_dir: Path, root: Path, code: str) -> None:
    """Record failed jobs in the history too; tracing must never mask the real failure."""

    try:
        trace.record_step("outcome", "failed", note=code)
        trace.finish(status="failed", job_dir=job_dir, output_root=root)
    except OSError:
        pass


def run_document_brief_job(
    request: dict[str, Any] | BriefRequest,
    output_root: str | Path,
    idempotency_key: str,
    *,
    adapter: Any | None = None,
) -> JobResult:
    """Extract a brief, then enter the existing deterministic V1A job path."""

    root = Path(output_root)
    root.mkdir(parents=True, exist_ok=True)
    started_wall = time.perf_counter()
    started_cpu = time.process_time()
    job_id = str(uuid.uuid4())
    try:
        parsed = request if isinstance(request, BriefRequest) else BriefRequest.model_validate(request)
    except ValidationError as exc:
        event = build_cost_event(
            job_id=job_id,
            attempt_id=job_id,
            model_calls=0,
            wall_seconds=time.perf_counter() - started_wall,
            cpu_seconds=time.process_time() - started_cpu,
            bytes_written=0,
            status="needs_input",
        )
        issues = [{"code": "invalid_brief_request", "path": ".".join(str(part) for part in error["loc"]), "message": error["msg"]} for error in exc.errors()]
        return JobResult(job_id, "needs_input", 0, [], {"status": "needs_input", "issues": issues, "page_count": None}, event.to_dict(), None, "needs_input", {"input_mode": "brief", "extraction": {"missing": []}})

    brief_hash = hashlib.sha256(parsed.brief.encode("utf-8")).hexdigest()
    request_hash_payload = {
        "schema_version": parsed.schema_version,
        "document_id": parsed.document_id,
        "locale": parsed.locale,
        "template_id": parsed.template_id,
        "template_version": parsed.template_version,
        "brief_hash": brief_hash,
    }
    store = JobStore(root)
    claim = store.claim(
        key_hash=hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest(),
        request_hash=_hash_json(request_hash_payload),
        job_id=job_id,
        recipe_version=RECIPE_ID,
        schema_version=parsed.schema_version,
        engine_version=ENGINE_VERSION,
    )
    if claim.action == "running":
        event = build_cost_event(
            job_id=claim.job_id,
            attempt_id=job_id,
            model_calls=0,
            wall_seconds=time.perf_counter() - started_wall,
            cpu_seconds=time.process_time() - started_cpu,
            bytes_written=0,
            status="pending",
        )
        return JobResult(
            claim.job_id,
            "pending",
            0,
            [],
            {"status": "pending", "issues": [{"code": "job_in_progress", "message": "an equivalent job is already running"}], "page_count": None},
            event.to_dict(),
            None,
            "job_in_progress",
            {"input_mode": "brief"},
        )
    if claim.action == "replay":
        manifest_path = root / str(claim.manifest_path)
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        replay_event = build_cost_event(
            job_id=manifest["job_id"],
            attempt_id=str(uuid.uuid4()),
            model_calls=0,
            wall_seconds=time.perf_counter() - started_wall,
            cpu_seconds=time.process_time() - started_cpu,
            bytes_written=0,
            status=manifest["status"],
            replayed=True,
        ).to_dict()
        return _from_manifest(manifest, manifest_path, replay_event)

    budget = BudgetLedger(root) if adapter is not None else None
    route = route_brief(parsed, adapter=adapter, budget=budget, budget_job_id=claim.job_id)
    extraction_metadata = route.extraction.model_dump(mode="json")
    metadata = {"input_mode": "brief", "extraction": extraction_metadata}
    if route.status != "accepted" or route.request is None:
        store.finish(claim.job_id, state="failed", manifest_path=None)
        event = build_cost_event(
            job_id=claim.job_id,
            attempt_id=job_id,
            model_calls=route.extraction.model_calls,
            wall_seconds=time.perf_counter() - started_wall,
            cpu_seconds=time.process_time() - started_cpu,
            bytes_written=0,
            status=route.status,
        )
        return JobResult(
            claim.job_id,
            route.status,
            route.extraction.model_calls,
            [],
            {"status": route.status, "issues": route.issues, "page_count": None},
            event.to_dict(),
            None,
            route.status,
            metadata,
        )

    return run_document_job(
        route.request,
        root,
        idempotency_key,
        request_hash_payload=request_hash_payload,
        manifest_metadata=metadata,
        model_calls=route.extraction.model_calls,
        ledger_schema_version=parsed.schema_version,
        preclaimed_claim=claim,
    )


def run_transcript_job(
    request: dict[str, Any] | TranscriptDocumentRequest,
    output_root: str | Path,
    idempotency_key: str,
) -> JobResult:
    """Run the pinned transcript recipe through the shared deterministic job path."""

    return run_document_job(
        request,
        output_root,
        idempotency_key,
        ir_builder=build_transcript_ir,
        recipe_version=TRANSCRIPT_RECIPE_ID,
        request_model=TranscriptDocumentRequest,
    )


def run_report_job(
    request: dict[str, Any] | ReportDocumentRequest,
    output_root: str | Path,
    idempotency_key: str,
    *,
    chart_root: str | Path | None = None,
) -> JobResult:
    """Run the pinned tabular report recipe through the shared deterministic job path.

    ``chart_root`` embeds the request's PNG/JPEG charts (verified by sha256 and
    size) into the DOCX and PDF. Embedding is part of the request hash, so the
    same request with and without embedded charts never replays the other.
    """

    if chart_root is None:
        return run_document_job(
            request,
            output_root,
            idempotency_key,
            ir_builder=build_report_ir,
            recipe_version=REPORT_RECIPE_ID,
            request_model=ReportDocumentRequest,
        )
    parsed = request if isinstance(request, ReportDocumentRequest) else ReportDocumentRequest.model_validate(request)
    return run_document_job(
        parsed,
        output_root,
        idempotency_key,
        request_hash_payload={"request": parsed.model_dump(mode="json"), "embed_charts": True},
        ir_builder=lambda item: build_report_ir(item, chart_root=chart_root),
        recipe_version=REPORT_RECIPE_ID,
        request_model=ReportDocumentRequest,
    )


def run_spec_sheet_job(
    request: dict[str, Any] | SpecSheetRequest,
    output_root: str | Path,
    idempotency_key: str,
    *,
    image_root: str | Path | None = None,
) -> JobResult:
    """Run the pinned spec-sheet recipe; ``image_root`` embeds verified PNG/JPEG previews."""

    if image_root is None:
        return run_document_job(
            request, output_root, idempotency_key,
            ir_builder=build_spec_sheet_ir, recipe_version=SPEC_SHEET_RECIPE_ID, request_model=SpecSheetRequest,
        )
    parsed = request if isinstance(request, SpecSheetRequest) else SpecSheetRequest.model_validate(request)
    return run_document_job(
        parsed, output_root, idempotency_key,
        request_hash_payload={"request": parsed.model_dump(mode="json"), "embed_images": True},
        ir_builder=lambda item: build_spec_sheet_ir(item, image_root=image_root),
        recipe_version=SPEC_SHEET_RECIPE_ID, request_model=SpecSheetRequest,
    )


def run_proposal_job(
    request: dict[str, Any] | ProposalRequest,
    output_root: str | Path,
    idempotency_key: str,
) -> JobResult:
    """Run the pinned proposal recipe through the shared deterministic job path."""

    return run_document_job(
        request, output_root, idempotency_key,
        ir_builder=build_proposal_ir, recipe_version=PROPOSAL_RECIPE_ID, request_model=ProposalRequest,
    )
