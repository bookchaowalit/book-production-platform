"""Thin scene3d-to-graphics orchestration over the existing product jobs.

The pipeline owns no rendering or layout logic: stage one delegates to the
scene3d composer, stage two delegates to the graphics batch, and the only new
surface is the ``scene-render-batch.v1`` handoff artifact plus a pipeline
manifest that links both stages' job identities. Nothing here calls a model or
the network.
"""

from __future__ import annotations

import hashlib
import json
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from book_production_platform.graphics_ir import ArtifactRef
from book_production_platform.graphics_production.jobs import (
    BatchResult,
    IdempotencyConflict,
    run_batch,
)
from book_production_platform.graphics_production.templates import VARIANT_SIZES
from book_production_platform.scene3d.jobs import (
    ARTIFACT_NAMES,
    JobResult,
    canonical_json_bytes,
    sha256_file,
)
from book_production_platform.scene3d.runtime import (
    MAX_TIMEOUT_SECONDS,
    compose_scene,
)
from book_production_platform.scene3d.scene_builder import RENDER_PROFILES

from .errors import PipelineError
from .models import (
    CONTRACT_SCHEMA_VERSION,
    GraphicsBatchInputs,
    GraphicsSpecInput,
    SceneArtifactRef,
    SceneRenderBatch,
    SceneRenderOutput,
    SceneRenderOutputs,
    SceneRenderProfile,
    read_graphics_spec,
    relative_path_or_raise,
)

PIPELINE_RECIPE = "scene-to-graphics.v1"
PIPELINE_MANIFEST_SCHEMA = "scene-to-graphics.pipeline.v1"
CONTRACT_FILENAME = "scene-render-batch.json"
MANIFEST_FILENAME = "pipeline-manifest.json"
PIPELINE_DIRECTORY = ".pipeline"
MANIFEST_LIMIT_BYTES = 1024 * 1024
SCENE_QA_SCHEMA = "scene3d.qa.v1"


@dataclass(frozen=True)
class PipelineResult:
    """Outcome of one pipeline run, safe to print: identities and statuses only."""

    status: str
    pipeline_id: str
    pipeline_dir: Path
    scene_status: str
    scene_fingerprint: str
    batch_id: str
    graphics_status: str
    model_calls: int = 0


def _read_bounded_json(path: Path, *, code: str) -> dict[str, Any]:
    try:
        if path.is_symlink() or not path.is_file() or path.stat().st_size > MANIFEST_LIMIT_BYTES:
            raise PipelineError(code, "file is unavailable or exceeds its size limit")
        payload = json.loads(path.read_text(encoding="utf-8"))
    except PipelineError:
        raise
    except (OSError, UnicodeDecodeError, ValueError, RecursionError) as error:
        raise PipelineError(code, "file cannot be read as JSON") from error
    if not isinstance(payload, dict):
        raise PipelineError(code, "file must contain a JSON object")
    return payload


def _entry(payload: dict[str, Any], key: str, *, code: str) -> dict[str, Any]:
    value = payload.get(key)
    if not isinstance(value, dict):
        raise PipelineError(code, f"entry {key!r} is missing or invalid")
    return value


def _text(payload: dict[str, Any], key: str, *, code: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise PipelineError(code, f"entry {key!r} must be a non-empty string")
    return value


def _hash_artifact(path: Path, code: str) -> str:
    try:
        if path.is_symlink() or not path.is_file():
            raise PipelineError(code, "artifact is missing")
        return sha256_file(path)
    except PipelineError:
        raise
    except OSError as error:
        raise PipelineError(code, "artifact cannot be hashed") from error


def _relative_to_root(root: Path, path: Path, *, code: str) -> str:
    try:
        return path.resolve().relative_to(root).as_posix()
    except ValueError as error:
        raise PipelineError(code, "artifact escaped the pipeline output root") from error


def _scene_contract_outputs(root: Path, scene_result: JobResult) -> SceneRenderOutputs:
    """Validate the finished scene job and convert it into the contract's scene half."""

    code = "contract_validation_failed"
    artifact_dir = scene_result.artifact_dir
    try:
        artifact_relative = artifact_dir.relative_to(root).as_posix()
    except ValueError as error:
        raise PipelineError(code, "scene artifacts are outside the pipeline output root") from error
    try:
        names = {item.name for item in artifact_dir.iterdir()}
    except OSError as error:
        raise PipelineError(code, "scene artifacts cannot be listed") from error
    if names != set(ARTIFACT_NAMES):
        raise PipelineError(code, "scene job artifact set is invalid")
    manifest = _read_bounded_json(artifact_dir / "qa-manifest.json", code=code)
    outputs = _entry(manifest, "outputs", code=code)
    render = _entry(manifest, "render", code=code)
    render_entry = _entry(outputs, "render.png", code=code)
    blend_entry = _entry(outputs, "scene.blend", code=code)
    if (
        manifest.get("schema_version") != SCENE_QA_SCHEMA
        or manifest.get("status") != "accepted"
        or manifest.get("fingerprint") != scene_result.fingerprint
    ):
        raise PipelineError(code, "scene QA manifest does not match the job identity")
    if render.get("profile") == "square-1024.v1":
        profile: SceneRenderProfile = "square-1024.v1"
    elif render.get("profile") == "landscape-1920x1080.v1":
        profile = "landscape-1920x1080.v1"
    else:
        raise PipelineError(code, "scene render profile is invalid")
    render_sha256 = _hash_artifact(artifact_dir / "render.png", code)
    blend_sha256 = _hash_artifact(artifact_dir / "scene.blend", code)
    qa_sha256 = _hash_artifact(artifact_dir / "qa-manifest.json", code)
    if render_entry.get("sha256") != render_sha256 or blend_entry.get("sha256") != blend_sha256:
        raise PipelineError(code, "scene artifact hashes do not match the QA manifest")
    width = render_entry.get("width")
    height = render_entry.get("height")
    if (
        isinstance(width, bool)
        or isinstance(height, bool)
        or not isinstance(width, int)
        or not isinstance(height, int)
        or (width, height) != RENDER_PROFILES[profile]
    ):
        raise PipelineError(code, "scene render dimensions do not match the render profile")
    return SceneRenderOutputs(
        fingerprint=scene_result.fingerprint,
        render_profile=profile,
        artifact_dir=artifact_relative,
        render_png=SceneRenderOutput(path="render.png", sha256=render_sha256, width=width, height=height),
        scene_blend=SceneArtifactRef(path="scene.blend", sha256=blend_sha256),
        qa_manifest=SceneArtifactRef(path="qa-manifest.json", sha256=qa_sha256),
    )


def _pipeline_id(contract: SceneRenderBatch) -> str:
    payload = {"recipe": PIPELINE_RECIPE, "contract": contract.model_dump(mode="json")}
    return hashlib.sha256(canonical_json_bytes(payload)).hexdigest()


def _idempotency_key(pipeline_id: str) -> str:
    return f"scene-to-graphics:{pipeline_id}"


def _graphics_items(contract: SceneRenderBatch, scene_dir: Path) -> list[dict[str, Any]]:
    """Build one graphics request per size, using the scene render as the product image."""

    graphics = contract.graphics
    items: list[dict[str, Any]] = []
    for variant in graphics.variants:
        width, height = VARIANT_SIZES[variant]
        items.append(
            {
                "request_id": f"sc{contract.scene.fingerprint[:12]}-{variant}",
                "template_id": graphics.template_id,
                "variant": variant,
                "canvas": {"width": width, "height": height},
                "brand": graphics.brand.model_dump(mode="json"),
                "content": dict(graphics.content),
                "assets": {"product_image": contract.scene.render_png.path},
                "asset_root": str(scene_dir),
            }
        )
    return items


def _publish_new(path: Path, data: bytes) -> None:
    """Publish one deterministic pipeline artifact atomically without overwriting."""

    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists() or path.is_symlink():
            if path.is_file() and path.read_bytes() == data:
                return
            raise PipelineError("pipeline_conflict", "pipeline artifact already exists with different content")
        with tempfile.NamedTemporaryFile(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent, delete=False) as handle:
            handle.write(data)
            temporary = Path(handle.name)
        temporary.replace(path)
    except PipelineError:
        raise
    except OSError as error:
        raise PipelineError("publication_failed", "pipeline artifact cannot be published") from error


def _run_graphics_stage(
    contract: SceneRenderBatch, root: Path, pipeline_dir: Path, pipeline_id: str
) -> BatchResult:
    items = _graphics_items(contract, root / contract.scene.artifact_dir)
    try:
        return run_batch(items, pipeline_dir / "graphics", _idempotency_key(pipeline_id))
    except IdempotencyConflict as error:
        raise PipelineError("pipeline_conflict", "graphics stage idempotency conflict") from error


def _artifact_entries(
    root: Path, graphics_dir: Path, artifacts: tuple[ArtifactRef, ...], *, code: str
) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for artifact in artifacts:
        path = graphics_dir / artifact.filename
        entries.append(
            {
                "path": _relative_to_root(root, path, code=code),
                "media_type": artifact.media_type,
                "sha256": _hash_artifact(path, code),
                "width": artifact.width,
                "height": artifact.height,
            }
        )
    return entries


def _pipeline_manifest(
    root: Path,
    pipeline_dir: Path,
    pipeline_id: str,
    contract: SceneRenderBatch,
    contract_bytes: bytes,
    scene_result: JobResult,
    batch: BatchResult,
) -> dict[str, Any]:
    code = "publication_failed"
    graphics_manifest = batch.manifest_path
    if graphics_manifest is None:
        raise PipelineError(code, "graphics batch manifest is missing")
    graphics_dir = pipeline_dir / "graphics"
    items: list[dict[str, Any]] = []
    for entry in batch.items:
        items.append(
            {
                "request_id": entry.request_id,
                "status": entry.status,
                "artifacts": _artifact_entries(root, graphics_dir, entry.artifacts, code=code),
            }
        )
    return {
        "schema_version": PIPELINE_MANIFEST_SCHEMA,
        "pipeline_id": pipeline_id,
        "status": "accepted",
        "recipe": PIPELINE_RECIPE,
        "contract": {
            "schema_version": CONTRACT_SCHEMA_VERSION,
            "path": _relative_to_root(root, pipeline_dir / CONTRACT_FILENAME, code=code),
            "sha256": hashlib.sha256(contract_bytes).hexdigest(),
        },
        "scene_stage": {
            "product": "scene3d",
            "status": scene_result.status,
            "fingerprint": scene_result.fingerprint,
            "artifact_dir": contract.scene.artifact_dir,
            "render_png": contract.scene.render_png.model_dump(mode="json"),
        },
        "graphics_stage": {
            "product": "graphics",
            "status": batch.status,
            "batch_id": batch.batch_id,
            "idempotency_key": batch.idempotency_key,
            "manifest": {
                "path": _relative_to_root(root, graphics_manifest, code=code),
                "sha256": _hash_artifact(graphics_manifest, code),
            },
            "items": items,
        },
        "model_calls": 0,
    }


def _replayed_result(
    root: Path,
    pipeline_dir: Path,
    pipeline_id: str,
    contract: SceneRenderBatch,
    contract_bytes: bytes,
) -> PipelineResult:
    """Return the recorded pipeline outcome after verifying the published artifacts."""

    code = "pipeline_conflict"
    manifest = _read_bounded_json(pipeline_dir / MANIFEST_FILENAME, code=code)
    if manifest.get("schema_version") != PIPELINE_MANIFEST_SCHEMA or manifest.get("pipeline_id") != pipeline_id:
        raise PipelineError(code, "existing pipeline manifest does not match this pipeline")
    contract_entry = _entry(manifest, "contract", code=code)
    if contract_entry.get("sha256") != hashlib.sha256(contract_bytes).hexdigest():
        raise PipelineError(code, "existing pipeline manifest pins a different contract")
    contract_path = pipeline_dir / CONTRACT_FILENAME
    if contract_path.is_symlink() or not contract_path.is_file() or sha256_file(contract_path) != hashlib.sha256(contract_bytes).hexdigest():
        raise PipelineError(code, "published contract is missing or modified")
    scene = _entry(manifest, "scene_stage", code=code)
    graphics = _entry(manifest, "graphics_stage", code=code)
    if _text(scene, "fingerprint", code=code) != contract.scene.fingerprint:
        raise PipelineError(code, "existing pipeline manifest pins a different scene job")
    graphics_manifest_entry = _entry(graphics, "manifest", code=code)
    try:
        recorded_path = relative_path_or_raise(_text(graphics_manifest_entry, "path", code=code))
    except ValueError as error:
        raise PipelineError(code, "existing pipeline manifest has an unsafe path") from error
    recorded_hash = _text(graphics_manifest_entry, "sha256", code=code)
    graphics_manifest = root / recorded_path
    if graphics_manifest.is_symlink() or not graphics_manifest.is_file() or sha256_file(graphics_manifest) != recorded_hash:
        raise PipelineError(code, "graphics batch manifest is missing or modified")
    return PipelineResult(
        status="replayed",
        pipeline_id=pipeline_id,
        pipeline_dir=pipeline_dir,
        scene_status=_text(scene, "status", code=code),
        scene_fingerprint=contract.scene.fingerprint,
        batch_id=_text(graphics, "batch_id", code=code),
        graphics_status=_text(graphics, "status", code=code),
    )


def run_scene_to_graphics(
    scene_path: Path,
    input_root: Path,
    output_root: Path,
    blender_bin: Path,
    spec_path: Path,
    *,
    timeout_s: int = MAX_TIMEOUT_SECONDS,
) -> PipelineResult:
    """Render one scene with Blender, then batch multi-size graphics from its PNG."""

    if isinstance(timeout_s, bool) or not isinstance(timeout_s, int) or not 0 < timeout_s <= MAX_TIMEOUT_SECONDS:
        raise PipelineError("invalid_timeout", "timeout must be between 1 and 300 seconds")
    spec: GraphicsSpecInput = read_graphics_spec(spec_path)
    scene_result = compose_scene(scene_path, input_root, output_root, blender_bin, timeout_s=timeout_s)
    root = scene_result.artifact_dir.parent
    scene = _scene_contract_outputs(root, scene_result)
    graphics = GraphicsBatchInputs(
        template_id=spec.template_id,
        variants=spec.variants,
        brand=spec.brand,
        content=spec.content,
    )
    contract = SceneRenderBatch(schema_version=CONTRACT_SCHEMA_VERSION, scene=scene, graphics=graphics)
    pipeline_id = _pipeline_id(contract)
    pipeline_dir = root / PIPELINE_DIRECTORY / pipeline_id
    contract_bytes = canonical_json_bytes(contract.model_dump(mode="json")) + b"\n"
    if (pipeline_dir / MANIFEST_FILENAME).exists() or (pipeline_dir / MANIFEST_FILENAME).is_symlink():
        return _replayed_result(root, pipeline_dir, pipeline_id, contract, contract_bytes)
    _publish_new(pipeline_dir / CONTRACT_FILENAME, contract_bytes)
    batch = _run_graphics_stage(contract, root, pipeline_dir, pipeline_id)
    if batch.status != "accepted" or batch.manifest_path is None:
        raise PipelineError("graphics_batch_failed", "graphics batch did not accept every size")
    manifest = _pipeline_manifest(
        root, pipeline_dir, pipeline_id, contract, contract_bytes, scene_result, batch
    )
    _publish_new(pipeline_dir / MANIFEST_FILENAME, canonical_json_bytes(manifest) + b"\n")
    return PipelineResult(
        status="accepted",
        pipeline_id=pipeline_id,
        pipeline_dir=pipeline_dir,
        scene_status=scene_result.status,
        scene_fingerprint=scene_result.fingerprint,
        batch_id=batch.batch_id,
        graphics_status=batch.status,
    )


__all__ = [
    "CONTRACT_FILENAME",
    "MANIFEST_FILENAME",
    "PIPELINE_DIRECTORY",
    "PIPELINE_MANIFEST_SCHEMA",
    "PIPELINE_RECIPE",
    "PipelineResult",
    "run_scene_to_graphics",
]
