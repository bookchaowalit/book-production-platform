"""Replay-safe local batch execution for Graphics V1A."""

from __future__ import annotations

import hashlib
import json
import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from book_production_platform.graphics_ir import ArtifactRef

from .assets import AssetReferenceError, validate_local_reference
from .layout import LayoutError
from .qa import run_graphic_qa
from .render import RenderError, render_png, render_svg
from .telemetry import BatchTelemetry
from .templates import TemplateError, compile_template

CONTRACT_VERSION = "graphics.v1a"
RECIPE_VERSION = "template-batch-v3"


class IdempotencyConflict(ValueError):
    """Raised when one idempotency key is reused with another input."""


@dataclass(frozen=True)
class ItemResult:
    request_id: str
    status: str
    artifacts: tuple[ArtifactRef, ...] = ()
    error_code: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "status": self.status,
            "error_code": self.error_code,
            "artifacts": [artifact.model_dump() for artifact in self.artifacts],
        }


@dataclass(frozen=True)
class BatchResult:
    batch_id: str
    status: str
    idempotency_key: str
    input_hash: str
    items: tuple[ItemResult, ...]
    manifest_path: Path | None
    model_calls: int = 0
    fresh_count: int = 0
    replay_count: int = 0
    bytes_written: int = 0
    error_code: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "batch_id": self.batch_id,
            "status": self.status,
            "idempotency_key": self.idempotency_key,
            "input_hash": self.input_hash,
            "items": [item.to_dict() for item in self.items],
            "manifest_path": str(self.manifest_path) if self.manifest_path else None,
            "model_calls": self.model_calls,
            "fresh_count": self.fresh_count,
            "replay_count": self.replay_count,
            "bytes_written": self.bytes_written,
            "error_code": self.error_code,
            "metadata": self.metadata,
        }


def _asset_fingerprints(items: list[dict[str, Any]]) -> list[dict[str, str]]:
    fingerprints: list[dict[str, str]] = []
    for item in items:
        item_fingerprints: dict[str, str] = {}
        root = item.get("asset_root")
        references = dict(item.get("assets", {})) if isinstance(item.get("assets"), dict) else {}
        font_ref = item.get("font_ref", "builtin:sans")
        if isinstance(font_ref, str) and font_ref != "builtin:sans":
            references["font_ref"] = font_ref
        for key, reference in sorted(references.items()):
            if not isinstance(reference, str) or not isinstance(root, str):
                item_fingerprints[str(key)] = "invalid_reference"
                continue
            try:
                path = validate_local_reference(reference, root, field=str(key))
                item_fingerprints[str(key)] = hashlib.sha256(path.read_bytes()).hexdigest()
            except (AssetReferenceError, OSError):
                item_fingerprints[str(key)] = "invalid_reference"
        fingerprints.append(item_fingerprints)
    return fingerprints


def _canonical_hash(items: list[dict[str, Any]]) -> str:
    canonical_items = [{key: value for key, value in item.items() if key != "asset_root"} for item in items]
    payload = json.dumps({"recipe": RECIPE_VERSION, "items": canonical_items, "asset_fingerprints": _asset_fingerprints(items)}, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _safe_name(request_id: str, fallback: str) -> str:
    value = re.sub(r"[^A-Za-z0-9._-]+", "_", request_id).strip("._")
    return value[:80] or fallback


def _write_atomic(path: Path, data: bytes) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent, delete=False) as handle:
        handle.write(data)
        temporary = Path(handle.name)
    temporary.replace(path)
    return len(data)


def _artifact(path: Path, media_type: str, width: int, height: int) -> ArtifactRef:
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    return ArtifactRef(filename=path.name, media_type=media_type, content_hash=digest, byte_size=path.stat().st_size, width=width, height=height)


def _from_manifest(path: Path, payload: dict[str, Any], *, replay: bool) -> BatchResult:
    items = tuple(
        ItemResult(
            request_id=str(item["request_id"]),
            status=str(item["status"]),
            error_code=item.get("error_code"),
            artifacts=tuple(ArtifactRef.model_validate(artifact) for artifact in item.get("artifacts", [])),
        )
        for item in payload.get("items", [])
    )
    telemetry = payload.get("telemetry", {})
    return BatchResult(
        batch_id=str(payload["batch_id"]),
        status=str(payload["status"]),
        idempotency_key=str(payload["idempotency_key"]),
        input_hash=str(payload["input_hash"]),
        items=items,
        manifest_path=path,
        model_calls=int(telemetry.get("model_calls", 0)),
        fresh_count=0,
        replay_count=1 if replay else 0,
        bytes_written=0,
        error_code=payload.get("error_code"),
        metadata=dict(payload.get("metadata", {})),
    )


def run_batch(items: list[dict[str, Any]], output_dir: str | Path, idempotency_key: str) -> BatchResult:
    """Render a batch and persist a manifest keyed by idempotency key."""

    if not idempotency_key.strip():
        raise IdempotencyConflict("idempotency_key_required")
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    input_hash = _canonical_hash(items)
    manifest_path = output_path / f"manifest-{hashlib.sha256(idempotency_key.encode('utf-8')).hexdigest()[:16]}.json"
    if manifest_path.exists():
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        if payload.get("input_hash") != input_hash:
            raise IdempotencyConflict("idempotency_key_reused_with_different_input")
        return _from_manifest(manifest_path, payload, replay=True)

    batch_id = hashlib.sha256(f"{idempotency_key}:{input_hash}".encode()).hexdigest()[:20]
    output_items: list[ItemResult] = []
    bytes_written = 0
    accepted = 0
    # Artifact names already written by this batch, case-folded because Windows
    # and macOS file systems treat "Card" and "card" as the same file.
    claimed_names: set[str] = set()
    for index, raw in enumerate(items):
        request_id = str(raw.get("request_id", f"item-{index + 1}"))
        prefix = _safe_name(request_id, f"item-{index + 1}")
        if prefix.casefold() in claimed_names:
            # A duplicate or sanitize-equivalent request_id would overwrite an
            # accepted item's files and invalidate the hashes in this manifest.
            output_items.append(ItemResult(request_id=request_id, status="rejected", error_code="artifact_name_collision"))
            continue
        try:
            ir = compile_template(raw)
            asset_root = raw.get("asset_root")
            svg = render_svg(ir, asset_root=asset_root)
            png = render_png(ir, asset_root=asset_root)
            issues = run_graphic_qa(svg, png, ir)
            if issues:
                raise RenderError("qa_failed:" + ",".join(issues))
            svg_path = output_path / f"{prefix}.svg"
            png_path = output_path / f"{prefix}.png"
            bytes_written += _write_atomic(svg_path, svg.encode("utf-8"))
            bytes_written += _write_atomic(png_path, png)
            output_items.append(ItemResult(request_id=request_id, status="accepted", artifacts=(
                _artifact(svg_path, "image/svg+xml", ir.canvas.width, ir.canvas.height),
                _artifact(png_path, "image/png", ir.canvas.width, ir.canvas.height),
            )))
            claimed_names.add(prefix.casefold())
            accepted += 1
        except (TemplateError, LayoutError, RenderError, ValidationError, ValueError) as exc:
            code = "invalid_request" if isinstance(exc, ValidationError) else str(exc).split(":", 1)[0] or exc.__class__.__name__
            output_items.append(ItemResult(request_id=request_id, status="rejected", error_code=code))

    status = "accepted" if accepted == len(output_items) and accepted > 0 else "partial" if accepted else "rejected"
    result = BatchResult(
        batch_id=batch_id,
        status=status,
        idempotency_key=idempotency_key,
        input_hash=input_hash,
        items=tuple(output_items),
        manifest_path=manifest_path,
        model_calls=0,
        fresh_count=accepted,
        replay_count=0,
        bytes_written=bytes_written,
        error_code=None if status != "rejected" else "no_accepted_items",
        metadata={"contract_version": CONTRACT_VERSION, "recipe_version": RECIPE_VERSION},
    )
    manifest = {
        "schema_version": "graphics-batch.v1",
        "batch_id": result.batch_id,
        "status": result.status,
        "idempotency_key": result.idempotency_key,
        "input_hash": result.input_hash,
        "items": [item.to_dict() for item in result.items],
        "telemetry": BatchTelemetry(fresh_count=accepted, bytes_written=bytes_written).to_dict(),
        "error_code": result.error_code,
        "metadata": result.metadata,
    }
    bytes_written += _write_atomic(manifest_path, json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8"))
    return BatchResult(**{**result.__dict__, "bytes_written": bytes_written})


__all__ = ["BatchResult", "IdempotencyConflict", "ItemResult", "run_batch"]
