"""Replay-safe local batch execution for Graphics V1A."""

from __future__ import annotations

import hashlib
import json
import re
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from book_production_platform.graphics_ir import ArtifactRef, GraphicIR, TextNode
from book_production_platform.production_trace import TraceRecorder

from .assets import AssetReferenceError, validate_local_reference
from .layout import LayoutError
from .manual import build_figma_guide, text_lines
from .qa import contrast_report, run_graphic_qa
from .render import RenderError, render_png, render_svg
from .telemetry import BatchTelemetry
from .templates import TEMPLATE_VERSION, VARIANT_SIZES, TemplateError, compile_template

CONTRACT_VERSION = "graphics.v1a"
RECIPE_VERSION = "template-batch-v3"
TOOL_ID = "graphics-v1a"
ENGINE_VERSION = f"{CONTRACT_VERSION}/templates@{TEMPLATE_VERSION}/graphics-svg-browser-v2"
BATCH_IR_SCHEMA = "graphics-batch-ir.v1"
_TRACE_STATUS = {"accepted": "accepted", "partial": "partial", "rejected": "failed"}
_PROPOSAL_KEYS = ("adapter", "adapter_version", "mode", "model_calls", "templates", "variants", "shorten_requested")


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
        if not isinstance(item, dict):
            fingerprints.append(item_fingerprints)
            continue
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


def expand_variants(items: list[Any]) -> list[Any]:
    """Expand ``"variants": [...]`` into one item per size (like Canva's Magic Resize).

    An item that names ``variants`` instead of ``variant``/``canvas`` becomes one
    item per listed variant with the registered canvas size, the same brand,
    content, and assets, and ``request_id`` suffixed with ``-<variant>``. Any
    other shape is left untouched, so the request validator still rejects it.
    """

    expanded: list[Any] = []
    for item in items:
        variants = item.get("variants") if isinstance(item, dict) else None
        if (
            not isinstance(item, dict)
            or not isinstance(variants, list)
            or not variants
            or "variant" in item
            or not all(isinstance(variant, str) for variant in variants)
            or len(set(variants)) != len(variants)
        ):
            expanded.append(item)
            continue
        base = {key: value for key, value in item.items() if key != "variants"}
        canvas = dict(base.get("canvas", {})) if isinstance(base.get("canvas"), dict) else {}
        for variant in variants:
            width, height = VARIANT_SIZES.get(variant, (0, 0))
            expanded.append({
                **base,
                "request_id": f"{base.get('request_id', 'item')}-{variant}",
                "variant": variant,
                "canvas": {**canvas, "width": width, "height": height},
            })
    return expanded


def _canonical_hash(items: list[dict[str, Any]]) -> str:
    canonical_items = [
        {key: value for key, value in item.items() if key != "asset_root"} if isinstance(item, dict) else item
        for item in items
    ]
    payload = json.dumps({"recipe": RECIPE_VERSION, "items": canonical_items, "asset_fingerprints": _asset_fingerprints(items)}, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


# Windows refuses these device names as a file stem, with or without an
# extension ("CON.svg", "com1.png"), so an artifact named after one can never
# be written or copied there.
_WINDOWS_RESERVED_STEMS = frozenset(
    {"CON", "PRN", "AUX", "NUL", *(f"COM{n}" for n in range(10)), *(f"LPT{n}" for n in range(10))}
)


def _safe_name(request_id: str, fallback: str) -> str:
    value = re.sub(r"[^A-Za-z0-9._-]+", "_", request_id).strip("._")[:80]
    if value.split(".", 1)[0].upper() in _WINDOWS_RESERVED_STEMS:
        value = f"_{value}"[:80]
    return value or fallback


def _write_atomic(path: Path, data: bytes) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent, delete=False) as handle:
        handle.write(data)
        temporary = Path(handle.name)
    temporary.replace(path)
    return len(data)


def _conflicting_existing_file(path: Path, data: bytes) -> bool:
    """Return True when ``path`` already holds different bytes (or is not a regular file).

    Each batch writes into its own ``<batch_id>/`` folder, so batches never share
    artifact files; this still guards a folder left behind by a batch whose
    manifest was removed, so bytes a surviving copy of that manifest hashes are
    never replaced. Identical bytes are fine.
    """

    if path.is_symlink():
        return True
    if not path.exists():
        return False
    if not path.is_file():
        return True
    try:
        return path.read_bytes() != data
    except OSError:
        return True


def _artifact(path: Path, media_type: str, width: int, height: int) -> ArtifactRef:
    """Describe an artifact by its POSIX path relative to the output directory (``<batch_id>/<name>``)."""

    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    filename = f"{path.parent.name}/{path.name}"
    return ArtifactRef(filename=filename, media_type=media_type, content_hash=digest, byte_size=path.stat().st_size, width=width, height=height)


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


def _item_structure(ir: GraphicIR) -> list[dict[str, Any]]:
    """Structural, text-free description of the IR nodes for the trace."""

    nodes: list[dict[str, Any]] = []
    for node in ir.nodes:
        entry: dict[str, Any] = {"id": node.node_id, "type": node.node_type, "box": [node.x, node.y, node.width, node.height]}
        if isinstance(node, TextNode):
            entry.update({"font_size": node.font_size, "lines": len(text_lines(node)), "chars": len(node.text), "align": node.align})
        nodes.append(entry)
    return nodes


def _proposal(value: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """Keep only metadata keys of a V1B brief proposal (never brief or slot text)."""

    if not isinstance(value, Mapping):
        return None
    calls = value.get("model_calls")
    if isinstance(calls, bool) or not isinstance(calls, int) or calls < 0:
        return None
    return {key: value[key] for key in _PROPOSAL_KEYS if key in value}


def _trace_failure(trace: TraceRecorder, job_dir: Path, root: Path, code: str) -> None:
    """Record failed batches in the history too; tracing must never mask the real failure."""

    try:
        trace.record_step("outcome", "failed", note=code)
        trace.finish(status="failed", job_dir=job_dir, output_root=root)
    except OSError:
        pass


def run_batch(
    items: list[dict[str, Any]],
    output_dir: str | Path,
    idempotency_key: str,
    *,
    ai_proposal: Mapping[str, Any] | None = None,
) -> BatchResult:
    """Render a batch, persist a manifest keyed by idempotency key, and trace the job.

    Fresh batches write ``trace.json``, ``ir.json``, and ``MANUAL-STEPS.md`` in
    ``<output_dir>/<batch_id>/`` and append ``<output_dir>/history.jsonl``
    (accepted, partial, and rejected batches alike). ``ai_proposal`` carries the
    metadata of a V1B brief proposal (adapter, model calls) into the trace.
    Exact replays return the stored manifest and write nothing.
    """

    if not idempotency_key.strip():
        raise IdempotencyConflict("idempotency_key_required")
    items = expand_variants(items)
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
    # Every batch owns a private artifact folder, so a later batch that reuses a
    # request_id (and whose PNG bytes may differ run to run) never collides with
    # files an earlier manifest hashes.
    batch_dir = output_path / batch_id
    trace = TraceRecorder(
        tool_id=TOOL_ID,
        job_id=batch_id,
        engine_version=ENGINE_VERSION,
        recipe_version=RECIPE_VERSION,
        schema_version="graphic-request.v1",
        idempotency_key_sha256=hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest(),
    )
    trace.input_json("batch", {"recipe": RECIPE_VERSION, "items": items})
    for index, fingerprints in enumerate(_asset_fingerprints(items)):
        for key, digest in sorted(fingerprints.items()):
            trace.inputs.append({"name": f"item-{index + 1}:{key}", "role": "asset", "sha256": digest})
    proposal = _proposal(ai_proposal)
    if proposal is not None:
        trace.ai_proposal(
            model_calls=int(proposal["model_calls"]),
            adapter=f"{proposal.get('adapter', 'unknown')}@{proposal.get('adapter_version', 'unknown')}",
            mode=str(proposal.get("mode", "live")),
            provided=[str(value) for value in proposal.get("templates", []) if isinstance(value, str)],
        )
    try:
        return _render_batch(items, output_path, batch_dir, batch_id, idempotency_key, input_hash, manifest_path, trace)
    except BaseException as exc:
        _trace_failure(trace, batch_dir, output_path, type(exc).__name__)
        raise


def _render_batch(
    items: list[Any],
    output_path: Path,
    batch_dir: Path,
    batch_id: str,
    idempotency_key: str,
    input_hash: str,
    manifest_path: Path,
    trace: TraceRecorder,
) -> BatchResult:
    accepted_irs: list[tuple[str, GraphicIR]] = []
    contrast: dict[str, list[dict[str, object]]] = {}
    output_items: list[ItemResult] = []
    bytes_written = 0
    accepted = 0
    # Artifact names already written by this batch, case-folded because Windows
    # and macOS file systems treat "Card" and "card" as the same file.
    claimed_names: set[str] = set()
    for index, raw in enumerate(items):
        item_no = index + 1
        if not isinstance(raw, dict):
            output_items.append(ItemResult(request_id=f"item-{item_no}", status="rejected", error_code="invalid_request"))
            trace.record_step("item", "rejected", item=item_no, note="invalid_request")
            continue
        request_id = str(raw.get("request_id", f"item-{item_no}"))
        prefix = _safe_name(request_id, f"item-{item_no}")
        if prefix.casefold() in claimed_names:
            # A duplicate or sanitize-equivalent request_id would overwrite an
            # accepted item's files and invalidate the hashes in this manifest.
            output_items.append(ItemResult(request_id=request_id, status="rejected", error_code="artifact_name_collision"))
            trace.record_step("item", "rejected", item=item_no, artifact=prefix, note="artifact_name_collision")
            continue
        try:
            with trace.step("recipe", "compile_template", item=item_no, template=raw.get("template_id"), variant=raw.get("variant")) as step:
                ir = compile_template(raw)
                step.params.update({"template_ref": ir.template_ref, "canvas": [ir.canvas.width, ir.canvas.height], "nodes": _item_structure(ir)})
            asset_root = raw.get("asset_root")
            with trace.step("render", "render_svg", item=item_no, renderer=ir.assumptions.get("renderer", "")):
                svg = render_svg(ir, asset_root=asset_root)
            with trace.step("render", "render_png", item=item_no, size=[ir.canvas.width, ir.canvas.height], rasterizer="local-chromium-headless"):
                png = render_png(ir, asset_root=asset_root)
            with trace.step("qa", "graphic_qa", item=item_no) as qa_step:
                issues = run_graphic_qa(svg, png, ir)
                report = contrast_report(ir)
                qa_step.params.update({
                    "issues": list(issues),
                    "contrast": [{key: row[key] for key in ("node_id", "ratio", "required", "passes_aa")} for row in report],
                })
                if issues:
                    qa_step.status = "failed"
                    raise RenderError("qa_failed:" + ",".join(issues))
            svg_path = batch_dir / f"{prefix}.svg"
            png_path = batch_dir / f"{prefix}.png"
            svg_bytes = svg.encode("utf-8")
            if _conflicting_existing_file(svg_path, svg_bytes) or _conflicting_existing_file(png_path, png):
                # An earlier run left different bytes in this batch folder;
                # never replace files a manifest may still hash.
                output_items.append(ItemResult(request_id=request_id, status="rejected", error_code="artifact_exists_with_different_content"))
                trace.record_step("item", "rejected", item=item_no, artifact=prefix, note="artifact_exists_with_different_content")
                continue
            with trace.step("publish", "write_artifacts", item=item_no) as write_step:
                bytes_written += _write_atomic(svg_path, svg_bytes)
                bytes_written += _write_atomic(png_path, png)
                write_step.outputs = [svg_path.name, png_path.name]
            output_items.append(ItemResult(request_id=request_id, status="accepted", artifacts=(
                _artifact(svg_path, "image/svg+xml", ir.canvas.width, ir.canvas.height),
                _artifact(png_path, "image/png", ir.canvas.width, ir.canvas.height),
            )))
            trace.artifact(svg_path, media_type="image/svg+xml")
            trace.artifact(png_path, media_type="image/png")
            accepted_irs.append((prefix, ir))
            contrast[prefix] = report
            claimed_names.add(prefix.casefold())
            accepted += 1
        except (TemplateError, LayoutError, RenderError, ValidationError, ValueError) as exc:
            code = "invalid_request" if isinstance(exc, ValidationError) else str(exc).split(":", 1)[0] or exc.__class__.__name__
            output_items.append(ItemResult(request_id=request_id, status="rejected", error_code=code))
            trace.record_step("item", "rejected", item=item_no, note=code)

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
    manifest_bytes = json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8")
    bytes_written += _write_atomic(manifest_path, manifest_bytes)
    trace.record_step("publish", "write_manifest", outputs=[manifest_path.name], status=result.status, manifest_sha256=hashlib.sha256(manifest_bytes).hexdigest())
    batch_ir = {"schema_version": BATCH_IR_SCHEMA, "batch_id": batch_id, "items": [ir.model_dump(mode="json") for _, ir in accepted_irs]}
    qa_checks = [{"item": name, **row} for name, rows in contrast.items() for row in rows]
    try:
        batch_dir.mkdir(parents=True, exist_ok=True)
        _write_atomic(batch_dir / "ir.json", json.dumps(batch_ir, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8"))
        trace.ir_document(batch_ir, schema=BATCH_IR_SCHEMA, path=batch_dir / "ir.json")
        trace.finish(
            status=_TRACE_STATUS[result.status],
            job_dir=batch_dir,
            output_root=output_path,
            guide=build_figma_guide(accepted_irs, batch_id=batch_id) if accepted_irs else None,
            qa={"status": "passed" if accepted else "failed", "checks": qa_checks},
        )
    except OSError:
        # The manifest is already published; a trace write failure must not
        # turn a finished batch into an error.
        pass
    return BatchResult(**{**result.__dict__, "bytes_written": bytes_written})


__all__ = ["BatchResult", "IdempotencyConflict", "ItemResult", "expand_variants", "run_batch"]
