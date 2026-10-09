"""Replay-safe local execution for Marketing V1A Campaign Packages."""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import tempfile
import threading
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from marketing_ir import (
    ArtifactRef,
    CampaignIR,
    CampaignRequest,
    compile_campaign,
    validate_request,
)
from production_trace import TraceRecorder
from pydantic import ValidationError

from .channel_tools import asset_checks, experiments, tracked_links
from .copy_rules import CHANNEL_LIMITS, copy_length
from .manual import build_marketing_guide
from .qa import run_package_qa
from .telemetry import JobTelemetry

CONTRACT_VERSION = "marketing.v1a"
# v2 adds A/B variants, the UTM link builder, and hashtag/mention rules. A v1
# request (no variant, no utm, tags within limits) keeps its exact hash and bytes.
RECIPE_VERSION = "campaign-package-v2"
REPLAYABLE_RECIPE_VERSIONS = frozenset({"campaign-package-v1", RECIPE_VERSION})
TOOL_ID = "marketing-v1a"
ENGINE_VERSION = "marketing-production-engine.v2"


class IdempotencyConflict(ValueError):
    """Raised when one idempotency key is reused with another input."""


class ReplayIntegrityError(ValueError):
    """Raised when a previously accepted package no longer matches its manifest."""


class MissingAssetError(ValueError):
    """Raised when a local asset reference cannot be read."""


@dataclass(frozen=True)
class CampaignJobResult:
    campaign_id: str
    status: str
    idempotency_key: str
    input_hash: str
    package: ArtifactRef | None = None
    manifest_filename: str | None = None
    model_calls: int = 0
    replayed: bool = False
    bytes_written: int = 0
    error_code: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "campaign_id": self.campaign_id,
            "status": self.status,
            "idempotency_key": self.idempotency_key,
            "input_hash": self.input_hash,
            "package": self.package.model_dump() if self.package else None,
            "manifest_filename": self.manifest_filename,
            "model_calls": self.model_calls,
            "replayed": self.replayed,
            "bytes_written": self.bytes_written,
            "error_code": self.error_code,
            "metadata": self.metadata,
        }


_LOCKS: dict[str, threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()


def _key_lock(output_dir: Path, idempotency_key: str) -> threading.Lock:
    lock_name = f"{output_dir.resolve()}::{idempotency_key}"
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(lock_name, threading.Lock())


def _write_atomic(path: Path, data: bytes) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent, delete=False) as handle:
        handle.write(data)
        temporary = Path(handle.name)
    temporary.replace(path)
    return len(data)


def _safe_name(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", value).strip("._")[:80] or "campaign"


def _artifact(path: Path) -> ArtifactRef:
    data = path.read_bytes()
    return ArtifactRef(
        filename=path.name,
        media_type="application/json",
        content_hash=hashlib.sha256(data).hexdigest(),
        byte_size=len(data),
    )


def _resolve_asset(request: CampaignRequest, uri: str) -> Path:
    if request.asset_root is None:
        raise MissingAssetError("asset_root_required")
    root = Path(request.asset_root).resolve()
    candidate = Path(uri)
    if not candidate.is_absolute():
        candidate = root / candidate
    resolved = candidate.resolve(strict=False)
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise MissingAssetError("asset_path_outside_root") from exc
    if not resolved.is_file():
        raise MissingAssetError("asset_missing")
    return resolved


def _source_hashes(request: CampaignRequest) -> dict[str, str]:
    hashes: dict[str, str] = {}
    for asset in request.assets:
        if asset.uri.lower().startswith("inline:"):
            source = (asset.text or asset.uri).encode("utf-8")
        else:
            source = _resolve_asset(request, asset.uri).read_bytes()
        hashes[asset.asset_id] = hashlib.sha256(source).hexdigest()
    return dict(sorted(hashes.items()))


def campaign_dump(ir: CampaignIR) -> dict[str, Any]:
    """IR dump without the v2 fields left unset (keeps v1 hashes and package bytes)."""

    payload = ir.model_dump(mode="json")
    if payload.get("utm") is None:
        payload.pop("utm", None)
    for asset in payload.get("assets", []):
        if asset.get("variant") is None:
            asset.pop("variant", None)
    return payload


def _input_hash(ir: CampaignIR, source_hashes: dict[str, str], recipe_version: str | None = None) -> str:
    payload = {
        "recipe_version": recipe_version or RECIPE_VERSION,
        "campaign": campaign_dump(ir),
        "source_hashes": source_hashes,
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _verify_replay(output_path: Path, manifest: dict[str, Any]) -> ArtifactRef:
    raw = manifest.get("package")
    if not isinstance(raw, dict):
        raise ReplayIntegrityError("manifest_package_missing")
    try:
        artifact = ArtifactRef.model_validate(raw)
    except ValidationError as exc:
        raise ReplayIntegrityError("manifest_package_invalid") from exc
    if Path(artifact.filename).name != artifact.filename:
        raise ReplayIntegrityError("manifest_package_path_invalid")
    path = output_path / artifact.filename
    if not path.is_file():
        raise ReplayIntegrityError("package_missing")
    actual = _artifact(path)
    if actual.byte_size != artifact.byte_size or actual.content_hash != artifact.content_hash:
        raise ReplayIntegrityError("package_hash_mismatch")
    if run_package_qa(path):
        raise ReplayIntegrityError("package_qa_failed")
    return artifact


def _replay(output_path: Path, manifest_path: Path, input_hashes: set[str], idempotency_key: str) -> CampaignJobResult:
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ReplayIntegrityError("manifest_unreadable") from exc
    if not isinstance(manifest, dict):
        raise ReplayIntegrityError("manifest_schema_invalid")
    if manifest.get("schema_version") != "campaign-manifest.v1" or manifest.get("status") != "accepted":
        raise ReplayIntegrityError("manifest_schema_invalid")
    if manifest.get("idempotency_key") != idempotency_key:
        raise ReplayIntegrityError("manifest_key_mismatch")
    if manifest.get("input_hash") not in input_hashes:
        raise IdempotencyConflict("idempotency_key_reused_with_different_input")
    input_hash = str(manifest["input_hash"])
    artifact = _verify_replay(output_path, manifest)
    telemetry = manifest.get("telemetry", {})
    return CampaignJobResult(
        campaign_id=str(manifest.get("campaign_id", "")),
        status="accepted",
        idempotency_key=str(manifest.get("idempotency_key", "")),
        input_hash=input_hash,
        package=artifact,
        manifest_filename=manifest_path.name,
        model_calls=int(telemetry.get("model_calls", 0)),
        replayed=True,
        bytes_written=0,
        metadata=dict(manifest.get("metadata", {})),
    )


def _rejected(key: str, campaign_id: str, code: str) -> CampaignJobResult:
    return CampaignJobResult(
        campaign_id=campaign_id,
        status="rejected",
        idempotency_key=key,
        input_hash="",
        error_code=code,
        metadata={"contract_version": CONTRACT_VERSION, "recipe_version": RECIPE_VERSION},
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


def _new_trace(job_id: str, key_hash: str, ai_proposal: Mapping[str, Any] | None) -> TraceRecorder:
    trace = TraceRecorder(
        tool_id=TOOL_ID,
        job_id=job_id,
        engine_version=ENGINE_VERSION,
        recipe_version=RECIPE_VERSION,
        schema_version="campaign-request.v1",
        idempotency_key_sha256=key_hash,
    )
    if ai_proposal is not None:
        trace.ai_proposal(
            model_calls=int(ai_proposal.get("model_calls", 0) or 0),
            adapter=str(ai_proposal.get("adapter", "unknown")),
            mode=str(ai_proposal.get("mode", "live")),
            provided=[str(item) for item in ai_proposal.get("provided", []) or []],
            inferred=[str(item) for item in ai_proposal.get("inferred", []) or []],
        )
    return trace


def _rule_params(ir: CampaignIR, checks: list[dict[str, str]]) -> dict[str, Any]:
    return {
        "assets": [
            {
                "asset_id": asset.asset_id,
                "channel": asset.channel,
                "kind": asset.kind,
                "variant": asset.variant,
                "chars": copy_length(asset.text) if asset.text else None,
                "limit": CHANNEL_LIMITS.get(asset.channel),
            }
            for asset in ir.assets
        ],
        "findings": [f"{item['asset_id']}:{item['code']}" for item in checks],
    }


def run_campaign_job(
    payload: dict[str, Any] | CampaignRequest,
    output_dir: str | Path,
    idempotency_key: str,
    *,
    ai_proposal: Mapping[str, Any] | None = None,
) -> CampaignJobResult:
    """Validate, package, and persist a campaign with replay-safe idempotency.

    Every new job (accepted or rejected) also writes ``jobs/<key>/trace.json``,
    ``ir.json``, ``MANUAL-STEPS.md``, and a frozen copy of the package, and
    appends ``history.jsonl`` in ``output_dir``. ``ai_proposal`` is the V1B
    proposal metadata when the request came from ``propose``.
    """

    if not idempotency_key.strip():
        raise IdempotencyConflict("idempotency_key_required")
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    key_hash = hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest()[:16]
    job_dir = output_path / "jobs" / key_hash
    campaign_id = str(payload.get("campaign_id", "")) if isinstance(payload, dict) else payload.campaign_id
    trace = _new_trace(key_hash, key_hash, ai_proposal)
    trace.input_json("request", payload.model_dump(mode="json") if isinstance(payload, CampaignRequest) else payload)
    try:
        with trace.step("validate", "parse_request") as parse_step:
            request = payload if isinstance(payload, CampaignRequest) else CampaignRequest.model_validate(payload)
            parse_step.params.update({"channels": list(request.channels), "assets": len(request.assets), "objective": request.objective, "utm": request.utm is not None})
        with trace.step("validate", "validate_request") as validate_step:
            issues = validate_request(request)
            validate_step.params["issues"] = [issue.code for issue in issues]
            if issues:
                validate_step.status = "failed"
        if issues:
            _trace_failure(trace, job_dir, output_path, issues[0].code)
            return _rejected(idempotency_key, request.campaign_id, issues[0].code)
        with trace.step("recipe", "compile_campaign"):
            ir = compile_campaign(request)
        with trace.step("rules", "channel_rules") as rules_step:
            checks = asset_checks(ir)
            rules_step.params.update(_rule_params(ir, checks))
            errors = [item for item in checks if item["severity"] == "error"]
            if errors:
                rules_step.status = "failed"
        if errors:
            _trace_failure(trace, job_dir, output_path, errors[0]["code"])
            return _rejected(idempotency_key, request.campaign_id, errors[0]["code"])
        with trace.step("links", "build_tracked_links") as links_step:
            links = tracked_links(ir)
            groups = experiments(ir)
            links_step.params.update({"links": len(links), "experiments": [f"{group['channel']}:{group['kind']}:{len(group['variants'])}" for group in groups]})
        with trace.step("lineage", "hash_sources") as hash_step:
            source_hashes = _source_hashes(request)
            hash_step.params["assets"] = len(source_hashes)
        for asset in request.assets:
            if not asset.uri.lower().startswith("inline:"):
                trace.input_file(asset.asset_id, _resolve_asset(request, asset.uri), role="asset")
        input_hash = _input_hash(ir, source_hashes)
    except (ValidationError, ValueError, MissingAssetError) as exc:
        code = str(exc).split(":", 1)[-1] or "invalid_request"
        _trace_failure(trace, job_dir, output_path, code)
        return _rejected(idempotency_key, campaign_id, code)

    manifest_path = output_path / f"manifest-{key_hash}.json"
    with _key_lock(output_path, idempotency_key):
        if manifest_path.exists():
            legacy = _input_hash(ir, source_hashes, "campaign-package-v1")
            return _replay(output_path, manifest_path, {input_hash, legacy}, idempotency_key)

        trace.job_id = hashlib.sha256(f"{idempotency_key}:{input_hash}".encode()).hexdigest()[:20]
        try:
            campaign = campaign_dump(ir)
            ir_bytes = json.dumps(campaign, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8")
            _write_atomic(job_dir / "ir.json", ir_bytes)
            trace.ir_document(campaign, schema=ir.schema_version, path=job_dir / "ir.json")
            package_name = f"{_safe_name(request.campaign_id)}-{input_hash[:12]}.campaign.json"
            package_path = output_path / package_name
            package_payload: dict[str, Any] = {
                "schema_version": "campaign-package.v1",
                "campaign": campaign,
                "lineage": {"source_refs": ir.source_refs, "asset_hashes": source_hashes},
                "telemetry": JobTelemetry(model_calls=0).to_dict(),
            }
            if links:
                package_payload["links"] = links
            if groups:
                package_payload["experiments"] = groups
            if checks:
                package_payload["checks"] = checks
            with trace.step("render", "write_package") as render_step:
                package_bytes = json.dumps(package_payload, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8")
                _write_atomic(package_path, package_bytes)
                render_step.outputs = [package_name]
            with trace.step("qa", "run_package_qa") as qa_step:
                qa_issues = run_package_qa(package_path)
                qa_step.params["issues"] = list(qa_issues)
                if qa_issues:
                    qa_step.status = "failed"
            if qa_issues:
                package_path.unlink(missing_ok=True)
                trace.qa = {"status": "failed", "checks": len(qa_issues)}
                _trace_failure(trace, job_dir, output_path, qa_issues[0])
                return _rejected(idempotency_key, request.campaign_id, qa_issues[0])
        except Exception as exc:
            _trace_failure(trace, job_dir, output_path, type(exc).__name__)
            raise
        artifact = _artifact(package_path)
        manifest = {
            "schema_version": "campaign-manifest.v1",
            "campaign_id": request.campaign_id,
            "status": "accepted",
            "idempotency_key": idempotency_key,
            "input_hash": input_hash,
            "package": artifact.model_dump(),
            "lineage": {"source_refs": ir.source_refs, "asset_hashes": source_hashes},
            "telemetry": JobTelemetry(model_calls=0, bytes_written=artifact.byte_size).to_dict(),
            "metadata": {"contract_version": CONTRACT_VERSION, "recipe_version": RECIPE_VERSION},
        }
        manifest_bytes = json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8")
        bytes_written = artifact.byte_size + _write_atomic(manifest_path, manifest_bytes)
        trace.record_step("publish", "write_manifest", outputs=[manifest_path.name, "ir.json"])
        try:
            # The job directory keeps a frozen copy so `production_trace verify` can re-hash it;
            # the top-level package stays where the manifest (and the Production Hub) expect it.
            shutil.copyfile(package_path, job_dir / package_name)
            trace.artifact(job_dir / package_name, media_type="application/json")
            guide = build_marketing_guide(ir, links=links, experiments=groups, checks=checks)
            trace.finish(status="accepted", job_dir=job_dir, output_root=output_path, guide=guide, qa={"status": "passed", "checks": []})
        except OSError:
            pass  # the accepted manifest is already published; a trace write error must not turn it into a failure
        return CampaignJobResult(
            campaign_id=request.campaign_id,
            status="accepted",
            idempotency_key=idempotency_key,
            input_hash=input_hash,
            package=artifact,
            manifest_filename=manifest_path.name,
            model_calls=0,
            replayed=False,
            bytes_written=bytes_written,
            metadata={"contract_version": CONTRACT_VERSION, "recipe_version": RECIPE_VERSION},
        )


__all__ = [
    "ENGINE_VERSION",
    "RECIPE_VERSION",
    "TOOL_ID",
    "CampaignJobResult",
    "IdempotencyConflict",
    "MissingAssetError",
    "ReplayIntegrityError",
    "campaign_dump",
    "run_campaign_job",
]
