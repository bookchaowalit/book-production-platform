"""Replay-safe local execution for Web V1A landing pages."""

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
from web_ir import ArtifactRef, PageIR, PageRequest, compile_page, validate_request

from .manual import build_webflow_guide
from .qa import QA_CHECKS, run_page_qa
from .render import BREAKPOINTS, render_css, render_html
from .telemetry import JobTelemetry

CONTRACT_VERSION = "web.v1a"
# v3: OpenGraph/Twitter meta, optional pricing + FAQ sections, Webflow-aligned breakpoints (991/767px).
RECIPE_VERSION = "landing-template-v3"
TOOL_ID = "web-v1a"
ENGINE_VERSION = "web-render.v3"
JOBS_DIRNAME = "jobs"
_CODE = re.compile(r"[a-z][a-z0-9_,]{0,119}")


class IdempotencyConflict(ValueError):
    """Raised when an idempotency key is reused with another input."""


class ReplayIntegrityError(ValueError):
    """Raised when a previously accepted artifact no longer matches."""


@dataclass(frozen=True)
class PageJobResult:
    page_id: str
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
            "page_id": self.page_id,
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
    return re.sub(r"[^A-Za-z0-9._-]+", "_", value).strip("._")[:80] or "page"


def _artifact(path: Path, media_type: str) -> ArtifactRef:
    data = path.read_bytes()
    return ArtifactRef(filename=path.name, media_type=media_type, content_hash=hashlib.sha256(data).hexdigest(), byte_size=len(data))


def _input_hash(ir: PageIR) -> str:
    payload = {"recipe_version": RECIPE_VERSION, "page": ir.model_dump(mode="json")}
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


_IMAGE_MEDIA_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}


def _verify_artifacts(output_path: Path, manifest: dict[str, Any]) -> tuple[ArtifactRef, ...]:
    raw = manifest.get("artifacts")
    if not isinstance(raw, list) or len(raw) not in (2, 3):
        raise ReplayIntegrityError("manifest_artifacts_invalid")
    artifacts: list[ArtifactRef] = []
    for item in raw:
        try:
            artifact = ArtifactRef.model_validate(item)
        except ValidationError as exc:
            raise ReplayIntegrityError("manifest_artifact_invalid") from exc
        if Path(artifact.filename).name != artifact.filename:
            raise ReplayIntegrityError("artifact_path_invalid")
        path = output_path / artifact.filename
        if not path.is_file():
            raise ReplayIntegrityError("artifact_missing")
        actual = _artifact(path, artifact.media_type)
        if actual.byte_size != artifact.byte_size or actual.content_hash != artifact.content_hash:
            raise ReplayIntegrityError("artifact_hash_mismatch")
        artifacts.append(artifact)
    html_artifact = next((item for item in artifacts if item.media_type == "text/html"), None)
    css_artifact = next((item for item in artifacts if item.media_type == "text/css"), None)
    if html_artifact is None or css_artifact is None:
        raise ReplayIntegrityError("artifact_types_invalid")
    images = frozenset(item.filename for item in artifacts if item.media_type.startswith("image/"))
    if len(artifacts) != 2 + len(images):
        raise ReplayIntegrityError("artifact_types_invalid")
    issues = run_page_qa(
        (output_path / html_artifact.filename).read_text(encoding="utf-8"),
        (output_path / css_artifact.filename).read_text(encoding="utf-8"),
        css_artifact.filename,
        images,
    )
    if issues:
        raise ReplayIntegrityError("page_qa_failed")
    return tuple(artifacts)


def _replay(output_path: Path, manifest_path: Path, input_hash: str, key: str) -> PageJobResult:
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ReplayIntegrityError("manifest_unreadable") from exc
    if not isinstance(manifest, dict) or manifest.get("schema_version") != "web-manifest.v1" or manifest.get("status") != "accepted":
        raise ReplayIntegrityError("manifest_schema_invalid")
    if manifest.get("idempotency_key") != key:
        raise ReplayIntegrityError("manifest_key_mismatch")
    if manifest.get("input_hash") != input_hash:
        raise IdempotencyConflict("idempotency_key_reused_with_different_input")
    artifacts = _verify_artifacts(output_path, manifest)
    telemetry = manifest.get("telemetry", {})
    return PageJobResult(
        page_id=str(manifest.get("page_id", "")),
        status="accepted",
        idempotency_key=key,
        input_hash=input_hash,
        artifacts=artifacts,
        manifest_filename=manifest_path.name,
        model_calls=int(telemetry.get("model_calls", 0)),
        replayed=True,
        metadata=dict(manifest.get("metadata", {})),
    )


def _rejected(key: str, page_id: str, code: str) -> PageJobResult:
    return PageJobResult(page_id=page_id, status="rejected", idempotency_key=key, input_hash="", error_code=code, metadata={"contract_version": CONTRACT_VERSION, "recipe_version": RECIPE_VERSION})


def _hero_image_bytes(ir: PageIR, asset_root: str | Path | None) -> bytes | None:
    """Read and verify the hero image named in the IR; the request hash already covers its content hash."""

    if ir.hero_image is None:
        return None
    if asset_root is None:
        raise ValueError("hero_image_asset_root_required")
    path = Path(asset_root) / ir.hero_image.filename
    if not path.is_file():
        raise ValueError("hero_image_missing")
    data = path.read_bytes()
    if hashlib.sha256(data).hexdigest() != ir.hero_image.content_hash:
        raise ValueError("hero_image_hash_mismatch")
    return data


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


def _section_names(ir: PageIR) -> list[str]:
    return ["hero", "features", *(["pricing"] if ir.pricing else []), *(["faq"] if ir.faq else []), "cta", "footer"]


def run_page_job(
    payload: dict[str, Any] | PageRequest,
    output_dir: str | Path,
    idempotency_key: str,
    *,
    asset_root: str | Path | None = None,
    proposal: Mapping[str, Any] | None = None,
) -> PageJobResult:
    """Validate, render, QA, and persist a landing page with replay-safe idempotency.

    A hero image is read from ``asset_root``, checked against the request's
    content hash, and shipped inside the bundle next to the HTML and CSS.
    Every new attempt (accepted, rejected, or failed) writes
    ``jobs/<job_id>/trace.json`` and one ``history.jsonl`` line; accepted jobs
    also get ``ir.json`` and ``MANUAL-STEPS.md`` (Webflow). ``proposal`` is
    metadata from the V1B brief adapter (no brief text).
    """

    if not idempotency_key.strip():
        raise IdempotencyConflict("idempotency_key_required")
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    page_id = str(payload.get("page_id", "")) if isinstance(payload, dict) else payload.page_id
    trace = TraceRecorder(
        tool_id=TOOL_ID,
        engine_version=ENGINE_VERSION,
        recipe_version=RECIPE_VERSION,
        schema_version="page-request.v1",
        idempotency_key_sha256=hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest(),
    )
    job_dir = output_path / JOBS_DIRNAME / trace.job_id
    trace.input_json("request", payload.model_dump(mode="json") if isinstance(payload, PageRequest) else payload)
    _proposal_trace(trace, proposal)
    try:
        return _run_traced(payload, output_path, idempotency_key, asset_root, page_id, trace, job_dir)
    except Exception as exc:
        _trace_end(trace, job_dir, output_path, "failed", _error_code(exc))
        raise


def _run_traced(
    payload: dict[str, Any] | PageRequest,
    output_path: Path,
    idempotency_key: str,
    asset_root: str | Path | None,
    page_id: str,
    trace: TraceRecorder,
    job_dir: Path,
) -> PageJobResult:
    try:
        with trace.step("validate", "validate_request", schema="page-request.v1") as validate_step:
            request = payload if isinstance(payload, PageRequest) else PageRequest.model_validate(payload)
            issues = validate_request(request)
            validate_step.outputs = [issue.code for issue in issues[:20]]
        if issues:
            _trace_end(trace, job_dir, output_path, "rejected", issues[0].code)
            return _rejected(idempotency_key, request.page_id, issues[0].code)
        with trace.step("recipe", "compile_page", template="landing-v1", recipe=RECIPE_VERSION):
            ir = compile_page(request)
        input_hash = _input_hash(ir)
        if ir.hero_image is not None:
            with trace.step("assets", "verify_hero_image", sha256=ir.hero_image.content_hash):
                hero_bytes = _hero_image_bytes(ir, asset_root)
            trace.input_file("hero_image", Path(str(asset_root)) / ir.hero_image.filename, role="asset")
        else:
            hero_bytes = None
    except (ValidationError, ValueError) as exc:
        _trace_end(trace, job_dir, output_path, "rejected", _error_code(exc))
        code = "invalid_request" if isinstance(exc, ValidationError) else str(exc).split(":", 1)[-1] or "invalid_request"
        return _rejected(idempotency_key, page_id, code)

    manifest_path = output_path / f"manifest-{hashlib.sha256(idempotency_key.encode('utf-8')).hexdigest()[:16]}.json"
    with _key_lock(output_path, idempotency_key):
        if manifest_path.exists():
            # A replay produces nothing new; the original job's trace stays the record.
            return _replay(output_path, manifest_path, input_hash, idempotency_key)
        ir_json = ir.model_dump(mode="json")
        job_dir.mkdir(parents=True, exist_ok=True)
        _write_atomic(job_dir / "ir.json", json.dumps(ir_json, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8"))
        trace.ir_document(ir_json, schema=ir.schema_version, path=job_dir / "ir.json")
        prefix = f"{_safe_name(ir.slug)}-{input_hash[:12]}"
        html_path = output_path / f"{prefix}.html"
        css_path = output_path / f"{prefix}.css"
        image_path: Path | None = None
        if ir.hero_image is not None and hero_bytes is not None:
            suffix = Path(ir.hero_image.filename).suffix.lower()
            image_path = output_path / f"{prefix}-hero{suffix}"
        with trace.step(
            "render", "render_html", template=ir.template_ref, language=ir.language, sections=_section_names(ir),
            features=len(ir.features), pricing_plans=len(ir.pricing), faq_items=len(ir.faq),
            secondary_cta=ir.secondary_cta is not None, hero_image=image_path is not None,
        ) as html_step:
            html_bytes = render_html(ir, css_path.name, image_path.name if image_path else None).encode("utf-8")
            html_step.outputs = [html_path.name]
        with trace.step(
            "render", "render_css", breakpoints=[f"{name}:{width}px" for name, width in BREAKPOINTS],
            theme=ir.theme.model_dump(mode="json"),
        ) as css_step:
            css_bytes = render_css(ir).encode("utf-8")
            css_step.outputs = [css_path.name]
        with trace.step("write", "write_bundle") as write_step:
            if image_path is not None and hero_bytes is not None:
                _write_atomic(image_path, hero_bytes)
            _write_atomic(html_path, html_bytes)
            _write_atomic(css_path, css_bytes)
            write_step.outputs = [path.name for path in (html_path, css_path, image_path) if path is not None]
        bundle_assets = frozenset({image_path.name}) if image_path else frozenset()
        with trace.step("qa", "run_page_qa", checks=list(QA_CHECKS)) as qa_step:
            qa_issues = run_page_qa(html_bytes.decode("utf-8"), css_bytes.decode("utf-8"), css_path.name, bundle_assets)
            qa_step.status = "failed" if qa_issues else "ok"
            qa_step.outputs = qa_issues[:20]
        if qa_issues:
            for path in (html_path, css_path, image_path):
                if path is not None:
                    path.unlink(missing_ok=True)
            _trace_end(trace, job_dir, output_path, "rejected", qa_issues[0])
            return _rejected(idempotency_key, ir.page_id, qa_issues[0])
        artifacts: tuple[ArtifactRef, ...] = (_artifact(html_path, "text/html"), _artifact(css_path, "text/css"))
        if image_path is not None:
            artifacts += (_artifact(image_path, _IMAGE_MEDIA_TYPES[image_path.suffix]),)
        metadata = {"contract_version": CONTRACT_VERSION, "recipe_version": RECIPE_VERSION, "template_ref": ir.template_ref, "trace_job_id": trace.job_id}
        manifest = {
            "schema_version": "web-manifest.v1",
            "page_id": ir.page_id,
            "status": "accepted",
            "idempotency_key": idempotency_key,
            "input_hash": input_hash,
            "artifacts": [artifact.model_dump() for artifact in artifacts],
            "telemetry": JobTelemetry(model_calls=0, bytes_written=sum(item.byte_size for item in artifacts)).to_dict(),
            "metadata": metadata,
        }
        guide = build_webflow_guide(ir, html_name=html_path.name, css_name=css_path.name, image_name=image_path.name if image_path else None)
        for artifact in artifacts:
            trace.artifact(_snapshot(output_path / artifact.filename, job_dir), media_type=artifact.media_type)
        trace.record_step("publish", "write_manifest", outputs=[manifest_path.name])
        # The trace is written before the manifest, so every accepted manifest has a trace.
        trace.finish(status="accepted", job_dir=job_dir, output_root=output_path, guide=guide, qa={"status": "passed", "checks": list(QA_CHECKS)})
        manifest_bytes = json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8")
        bytes_written = sum(item.byte_size for item in artifacts) + _write_atomic(manifest_path, manifest_bytes)
        return PageJobResult(page_id=ir.page_id, status="accepted", idempotency_key=idempotency_key, input_hash=input_hash, artifacts=artifacts, manifest_filename=manifest_path.name, bytes_written=bytes_written, metadata=dict(metadata))


__all__ = ["IdempotencyConflict", "PageJobResult", "ReplayIntegrityError", "run_page_job"]
