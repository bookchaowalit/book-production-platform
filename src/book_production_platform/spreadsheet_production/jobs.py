"""Replay-safe local Workbook Builder jobs."""

from __future__ import annotations

import hashlib
import json
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from book_production_platform.spreadsheet_ir import ArtifactRef, WorkbookRequest

from .formulas import FormulaError
from .qa import qa_workbook
from .recipe import WorkbookBuildError, build_workbook_ir
from .render import RenderError, render_workbook
from .telemetry import JobTelemetry

RECIPE_VERSION = "workbook-builder-v1"


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


def _canonical_hash(request: WorkbookRequest) -> str:
    payload = json.dumps({"recipe": RECIPE_VERSION, "request": request.model_dump(mode="json")}, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
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


def run_workbook_job(request: WorkbookRequest | dict[str, Any], output_dir: str | Path, idempotency_key: str) -> JobResult:
    """Build, export and QA one workbook with idempotent replay semantics."""

    if not idempotency_key.strip():
        raise IdempotencyConflict("idempotency_key_required")
    try:
        parsed = request if isinstance(request, WorkbookRequest) else WorkbookRequest.model_validate(request)
    except ValidationError:
        return JobResult(job_id="", status="rejected", idempotency_key=idempotency_key, input_hash="", artifact=None, manifest_path=None, error_code="invalid_request")
    input_hash = _canonical_hash(parsed)
    key_hash = hashlib.sha256(idempotency_key.encode()).hexdigest()[:16]
    job_dir = Path(output_dir) / f"job-{key_hash}"
    manifest_path = job_dir / "manifest.json"
    if manifest_path.exists():
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        if payload.get("input_hash") != input_hash:
            raise IdempotencyConflict("idempotency_key_reused_with_different_input")
        return _from_manifest(manifest_path, payload)

    job_id = hashlib.sha256(f"{idempotency_key}:{input_hash}".encode()).hexdigest()[:20]
    try:
        ir = build_workbook_ir(parsed)
        workbook_path = job_dir / "workbook.xlsx"
        artifact = render_workbook(ir, workbook_path)[0]
        qa = qa_workbook(ir, workbook_path)
        if qa:
            return JobResult(job_id=job_id, status="rejected", idempotency_key=idempotency_key, input_hash=input_hash, artifact=None, manifest_path=None, qa=qa, error_code="qa_failed")
    except (WorkbookBuildError, FormulaError, RenderError, ValueError) as exc:
        code = "invalid_request" if isinstance(exc, WorkbookBuildError) else str(exc).split(":", 1)[0] or exc.__class__.__name__
        return JobResult(job_id=job_id, status="rejected", idempotency_key=idempotency_key, input_hash=input_hash, artifact=None, manifest_path=None, error_code=code)

    manifest = {
        "schema_version": "spreadsheet-job.v1",
        "job_id": job_id,
        "status": "accepted",
        "idempotency_key": idempotency_key,
        "input_hash": input_hash,
        "artifact": artifact.model_dump(),
        "qa": list(qa),
        "telemetry": JobTelemetry(bytes_written=artifact.byte_size).to_dict(),
        "metadata": {"recipe_version": RECIPE_VERSION, "model_calls": "0"},
    }
    manifest_bytes = json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8")
    bytes_written = _atomic_write(manifest_path, manifest_bytes) + artifact.byte_size
    return JobResult(job_id=job_id, status="accepted", idempotency_key=idempotency_key, input_hash=input_hash, artifact=artifact, manifest_path=manifest_path, qa=qa, bytes_written=bytes_written, metadata={"recipe_version": RECIPE_VERSION})


__all__ = ["IdempotencyConflict", "JobResult", "run_workbook_job"]
