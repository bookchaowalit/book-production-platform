"""Replay-safe local Workbook Builder jobs."""

from __future__ import annotations

import hashlib
import json
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import xlsxwriter  # type: ignore[import-untyped]
from pydantic import ValidationError

from book_production_platform.production_trace import TraceRecorder
from book_production_platform.spreadsheet_ir import (
    ArtifactRef,
    WorkbookIR,
    WorkbookRequest,
)

from .formulas import FormulaError, evaluate_workbook
from .manual import build_excel_guide
from .qa import qa_workbook
from .recipe import RECIPE_ID, WorkbookBuildError, build_workbook_ir
from .render import FREEZE_ROWS, RenderError, render_workbook
from .telemetry import JobTelemetry

RECIPE_VERSION = "workbook-builder-v2"
TOOL_ID = "spreadsheet-v1a"
ENGINE_VERSION = f"spreadsheet-v1a+xlsxwriter-{xlsxwriter.__version__}"
# Request fields added after v1 launch; omitted from the idempotency hash while
# they hold their defaults so earlier jobs keep replaying under the same key.
_OPTIONAL_COLUMN_FIELDS = ("number_format", "allowed_values")


class IdempotencyConflict(ValueError):
    """Raised when one idempotency key receives different input."""


@dataclass(frozen=True)
class JobResult:
    job_id: str
    status: str
    idempotency_key: str
    input_hash: str
    artifact: ArtifactRef | None
    manifest_path: Path | None
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
            "artifact": self.artifact.model_dump() if self.artifact else None,
            "manifest_path": str(self.manifest_path) if self.manifest_path else None,
            "qa": list(self.qa),
            "model_calls": self.model_calls,
            "replayed": self.replayed,
            "bytes_written": self.bytes_written,
            "error_code": self.error_code,
            "metadata": self.metadata or {},
        }


def _hash_view(request: WorkbookRequest) -> dict[str, Any]:
    payload = request.model_dump(mode="json")
    if not payload.get("conditional_formats"):
        payload.pop("conditional_formats", None)
    for table in payload.get("tables", []):
        for column in table.get("columns", []):
            for name in _OPTIONAL_COLUMN_FIELDS:
                if column.get(name) is None:
                    column.pop(name, None)
    return payload


def _canonical_hash(request: WorkbookRequest) -> str:
    payload = json.dumps({"recipe": RECIPE_VERSION, "request": _hash_view(request)}, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()


def _atomic_write(path: Path, data: bytes) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent, delete=False) as handle:
        handle.write(data)
        temporary = Path(handle.name)
    temporary.replace(path)
    return len(data)


def _from_manifest(path: Path, payload: dict[str, Any]) -> JobResult:
    artifact = ArtifactRef.model_validate(payload["artifact"]) if payload.get("artifact") else None
    telemetry = payload.get("telemetry", {})
    return JobResult(
        job_id=str(payload["job_id"]),
        status=str(payload["status"]),
        idempotency_key=str(payload["idempotency_key"]),
        input_hash=str(payload["input_hash"]),
        artifact=artifact,
        manifest_path=path,
        qa=tuple(str(value) for value in payload.get("qa", [])),
        model_calls=int(telemetry.get("model_calls", 0)),
        replayed=True,
        bytes_written=0,
        error_code=payload.get("error_code"),
        metadata=dict(payload.get("metadata", {})),
    )


def _sheet_structure(ir: WorkbookIR) -> list[dict[str, Any]]:
    """Structural, value-free description of each sheet for the trace."""

    targets = {formula.target for formula in ir.formulas}
    return [
        {
            "order": order,
            "sheet": sheet.name,
            "columns": len(sheet.column_labels),
            "cells": len(sheet.cells),
            "formulas": sum(1 for target in targets if target.split("!", 1)[0] == sheet.name),
            "column_formats": [{"range": item.range, "number_format": item.number_format} for item in sheet.column_formats],
            "data_validations": [{"range": item.range, "values": len(item.allowed_values)} for item in sheet.data_validations],
            "conditional_formats": [{"range": item.range, "operator": item.operator} for item in sheet.conditional_formats],
            "freeze_rows": FREEZE_ROWS,
        }
        for order, sheet in enumerate(ir.sheets, start=1)
    ]


def _trace_failure(trace: TraceRecorder, job_dir: Path, root: Path, code: str) -> None:
    """Record failed jobs in the history too; tracing must never mask the real failure."""

    try:
        trace.record_step("outcome", "failed", note=code)
        trace.finish(status="failed", job_dir=job_dir, output_root=root)
    except OSError:
        pass


def _record_ai(trace: TraceRecorder, ai_proposal: Mapping[str, Any] | None) -> None:
    if not ai_proposal:
        return
    trace.ai_proposal(
        model_calls=int(ai_proposal.get("model_calls", 0) or 0),
        adapter=str(ai_proposal.get("adapter", "unknown")),
        mode=str(ai_proposal.get("mode", "live")),
        provided=[str(item) for item in ai_proposal.get("provided", ["template_id", "mapping", "intents"])],
        inferred=[str(item) for item in ai_proposal.get("inferred", [])],
    )


def run_workbook_job(
    request: WorkbookRequest | dict[str, Any],
    output_dir: str | Path,
    idempotency_key: str,
    *,
    ai_proposal: Mapping[str, Any] | None = None,
) -> JobResult:
    """Build, export and QA one workbook with idempotent replay semantics.

    Every fresh job (accepted or rejected) records ``trace.json`` in its job
    directory and one line in ``<output_dir>/history.jsonl``; accepted jobs
    also get ``ir.json`` and ``MANUAL-STEPS.md`` (Excel steps). Replays write
    nothing. ``ai_proposal`` is the metadata-only view of a V1B proposal.
    """

    if not idempotency_key.strip():
        raise IdempotencyConflict("idempotency_key_required")
    root = Path(output_dir)
    key_hash = hashlib.sha256(idempotency_key.encode()).hexdigest()[:16]
    job_dir = root / f"job-{key_hash}"
    trace_kwargs = {"tool_id": TOOL_ID, "engine_version": ENGINE_VERSION, "recipe_version": RECIPE_VERSION,
                    "idempotency_key_sha256": hashlib.sha256(idempotency_key.encode()).hexdigest()}
    try:
        parsed = request if isinstance(request, WorkbookRequest) else WorkbookRequest.model_validate(request)
    except ValidationError:
        trace = TraceRecorder(job_id=f"rejected-{key_hash}", schema_version="workbook-request.v1", **trace_kwargs)
        trace.input_json("request", request if isinstance(request, dict) else str(request))
        _record_ai(trace, ai_proposal)
        trace.record_step("validate", "parse_request", note="invalid_request")
        _trace_failure(trace, job_dir, root, "invalid_request")
        return JobResult(job_id="", status="rejected", idempotency_key=idempotency_key, input_hash="", artifact=None, manifest_path=None, error_code="invalid_request")
    input_hash = _canonical_hash(parsed)
    manifest_path = job_dir / "manifest.json"
    if manifest_path.exists():
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        if payload.get("input_hash") != input_hash:
            raise IdempotencyConflict("idempotency_key_reused_with_different_input")
        return _from_manifest(manifest_path, payload)

    job_id = hashlib.sha256(f"{idempotency_key}:{input_hash}".encode()).hexdigest()[:20]
    trace = TraceRecorder(job_id=job_id, schema_version=parsed.schema_version, **trace_kwargs)
    trace.input_json("request", parsed.model_dump(mode="json"))
    trace.inputs[-1].update({
        "tables": len(parsed.tables),
        "rows": [len(table.rows) for table in parsed.tables],
        "columns": [len(table.columns) for table in parsed.tables],
        "formulas": len(parsed.formulas),
    })
    _record_ai(trace, ai_proposal)
    try:
        with trace.step("validate", "parse_request", schema=parsed.schema_version, tables=len(parsed.tables)):
            pass
        with trace.step("recipe", "build_workbook_ir", recipe=RECIPE_ID) as build_step:
            ir = build_workbook_ir(parsed)
            build_step.params.update({
                "sheets": [sheet.name for sheet in ir.sheets], "formulas": len(ir.formulas),
                "named_ranges": sorted(ir.named_ranges), "formula_language": ir.assumptions.get("formula_language", "v1a"),
            })
        ir_payload = ir.model_dump(mode="json")
        _atomic_write(job_dir / "ir.json", (json.dumps(ir_payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8"))
        trace.ir_document(ir_payload, schema=ir.schema_version, path=job_dir / "ir.json")
        with trace.step("evaluate", "evaluate_formulas", formulas=len(ir.formulas)) as evaluate_step:
            evaluation = evaluate_workbook(ir)
            evaluate_step.params["dependencies"] = sum(len(items) for items in evaluation.dependencies.values())
        workbook_path = job_dir / "workbook.xlsx"
        with trace.step("render", "render_xlsx", writer=f"xlsxwriter-{xlsxwriter.__version__}") as render_step:
            artifact = render_workbook(ir, workbook_path)[0]
            render_step.outputs = [artifact.filename]
        for sheet in _sheet_structure(ir):
            trace.record_step("render", "write_sheet", **sheet)
        with trace.step("qa", "reopen_xlsx") as qa_step:
            qa = qa_workbook(ir, workbook_path)
            qa_step.params["issues"] = list(qa)
            qa_step.status = "ok" if not qa else "failed"
        if qa:
            _trace_failure(trace, job_dir, root, "qa_failed")
            return JobResult(job_id=job_id, status="rejected", idempotency_key=idempotency_key, input_hash=input_hash, artifact=None, manifest_path=None, qa=qa, error_code="qa_failed")
    except (WorkbookBuildError, FormulaError, RenderError, ValueError) as exc:
        code = "invalid_request" if isinstance(exc, WorkbookBuildError) else str(exc).split(":", 1)[0] or exc.__class__.__name__
        _trace_failure(trace, job_dir, root, code)
        return JobResult(job_id=job_id, status="rejected", idempotency_key=idempotency_key, input_hash=input_hash, artifact=None, manifest_path=None, error_code=code)

    model_calls = int((ai_proposal or {}).get("model_calls", 0) or 0)
    manifest = {
        "schema_version": "spreadsheet-job.v1",
        "job_id": job_id,
        "status": "accepted",
        "idempotency_key": idempotency_key,
        "input_hash": input_hash,
        "artifact": artifact.model_dump(),
        "qa": list(qa),
        "telemetry": JobTelemetry(bytes_written=artifact.byte_size, model_calls=model_calls).to_dict(),
        "metadata": {"recipe_version": RECIPE_VERSION, "model_calls": str(model_calls)},
    }
    manifest_bytes = json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8")
    bytes_written = _atomic_write(manifest_path, manifest_bytes) + artifact.byte_size
    trace.record_step("publish", "write_manifest", outputs=["manifest.json", "ir.json"])
    trace.artifact(workbook_path, media_type=artifact.media_type)
    trace.finish(status="accepted", job_dir=job_dir, output_root=root, guide=build_excel_guide(ir, filename=workbook_path.name), qa={"status": "passed", "checks": []})
    return JobResult(job_id=job_id, status="accepted", idempotency_key=idempotency_key, input_hash=input_hash, artifact=artifact, manifest_path=manifest_path, qa=qa, model_calls=model_calls, bytes_written=bytes_written, metadata={"recipe_version": RECIPE_VERSION})


__all__ = ["ENGINE_VERSION", "TOOL_ID", "IdempotencyConflict", "JobResult", "run_workbook_job"]
