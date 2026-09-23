"""Content addressing, replay integrity, safe paths, and atomic job receipts."""

from __future__ import annotations

import errno
import hashlib
import json
import math
import os
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Iterator

from book_production_platform.scene3d.errors import SceneInputError
from book_production_platform.scene3d.glb import GlbSummary
from book_production_platform.scene3d.models import SceneIR
from book_production_platform.scene3d.qa import verify_png
from book_production_platform.scene3d.scene_builder import (
    EEVEE_SAMPLES,
    RENDER_ENGINE,
    RENDER_PROFILES,
)

if os.name == "nt":
    import msvcrt
else:
    import fcntl

COMPOSER_VERSION = "scene-composer.v1a.1"
MANIFEST_SCHEMA = "scene3d.qa.v1"
ARTIFACT_NAMES = frozenset({"scene.blend", "render.png", "qa-manifest.json"})
MAX_MANIFEST_BYTES = 1024 * 1024
MANIFEST_FIELDS = frozenset(
    {
        "schema_version",
        "status",
        "fingerprint",
        "scene_ir_sha256",
        "source_asset_sha256",
        "composer_version",
        "scene_ir_version",
        "recipe",
        "blender",
        "render",
        "scene_counts",
        "qa",
        "outputs",
        "process_result",
        "timing_seconds",
        "bytes_written",
        "resource_usage",
        "model_calls",
        "monetary_cost",
    }
)


def _scene_counts(summary: GlbSummary) -> dict[str, Any]:
    return {
        "mesh_object_count": summary.mesh_object_count,
        "triangle_count": summary.triangle_count,
        "texture_bytes": summary.texture_bytes,
        "texture_dimensions": [list(size) for size in summary.texture_dimensions],
        "extensions": list(summary.extensions),
    }


@dataclass(frozen=True)
class JobResult:
    status: str
    fingerprint: str
    artifact_dir: Path
    artifact_hashes: dict[str, str]
    replay: bool
    model_calls: int = 0


def canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def scene_fingerprint(
    scene_ir: SceneIR,
    source_asset_sha256: str,
    blender_version: str,
    blender_build: str,
) -> tuple[str, str]:
    normalized_ir = canonical_json_bytes(scene_ir.model_dump(mode="json"))
    scene_ir_sha256 = hashlib.sha256(normalized_ir).hexdigest()
    fingerprint_payload = {
        "composer_version": COMPOSER_VERSION,
        "scene_ir_sha256": scene_ir_sha256,
        "source_asset_sha256": source_asset_sha256,
        "blender_version": blender_version,
        "blender_build": blender_build,
        "recipe": scene_ir.recipe,
        "render_profile": scene_ir.render_profile,
    }
    fingerprint = hashlib.sha256(canonical_json_bytes(fingerprint_payload)).hexdigest()
    return fingerprint, scene_ir_sha256


def resolve_asset_path(input_root: Path, relative_path: str) -> Path:
    """Resolve a relative asset while rejecting symlinked components and escapes."""
    try:
        root = input_root.resolve(strict=True)
    except OSError as error:
        raise SceneInputError("unsafe_glb_resource", "input root is unavailable") from error
    if not root.is_dir():
        raise SceneInputError("unsafe_glb_resource", "input root is not a directory")
    current = root
    for part in PurePosixPath(relative_path).parts:
        if part in {"", ".", ".."}:
            raise SceneInputError("unsafe_glb_resource", "asset path is unsafe")
        current = current / part
        if current.is_symlink():
            raise SceneInputError("unsafe_glb_resource", "symlinked asset paths are not supported")
    try:
        resolved = current.resolve(strict=True)
    except OSError as error:
        raise SceneInputError("unsafe_glb_resource", "asset is unavailable") from error
    if not resolved.is_relative_to(root) or not resolved.is_file():
        raise SceneInputError("unsafe_glb_resource", "asset path is outside the input root")
    return resolved


@contextmanager
def fingerprint_lock(output_root: Path, fingerprint: str, timeout_s: int) -> Iterator[None]:
    lock_dir = output_root / ".locks"
    if lock_dir.is_symlink():
        raise SceneInputError("unsafe_output_path", "job lock path is unsafe")
    lock_dir.mkdir(exist_ok=True)
    lock_path = lock_dir / f"{fingerprint}.lock"
    if lock_path.is_symlink():
        raise SceneInputError("unsafe_output_path", "job lock path is unsafe")
    try:
        with lock_path.open("a+b") as lock_file:
            lock_file.seek(0, os.SEEK_END)
            if lock_file.tell() == 0:
                lock_file.write(b"\0")
                lock_file.flush()
            deadline = time.monotonic() + timeout_s
            while True:
                lock_file.seek(0)
                try:
                    if os.name == "nt":
                        msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)
                    else:
                        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except OSError as error:
                    if error.errno not in {errno.EACCES, errno.EAGAIN, errno.EDEADLK, None}:
                        raise
                    if time.monotonic() >= deadline:
                        raise SceneInputError("job_lock_timeout", "job lock deadline exceeded") from error
                    time.sleep(0.05)
            try:
                yield
            finally:
                lock_file.seek(0)
                if os.name == "nt":
                    msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
    except SceneInputError:
        raise
    except OSError as error:
        raise SceneInputError("unsafe_output_path", "job lock cannot be acquired") from error


def create_manifest(
    *,
    fingerprint: str,
    scene_ir_sha256: str,
    source_asset_sha256: str,
    scene_ir: SceneIR,
    blender_version: str,
    blender_build: str,
    glb_summary: GlbSummary,
    blend_path: Path,
    render_path: Path,
    build_seconds: float,
    render_seconds: float,
    wall_seconds: float,
) -> dict[str, Any]:
    width, height = RENDER_PROFILES[scene_ir.render_profile]
    blend_bytes = blend_path.stat().st_size
    render_bytes = render_path.stat().st_size
    return {
        "schema_version": MANIFEST_SCHEMA,
        "status": "accepted",
        "fingerprint": fingerprint,
        "scene_ir_sha256": scene_ir_sha256,
        "source_asset_sha256": source_asset_sha256,
        "composer_version": COMPOSER_VERSION,
        "scene_ir_version": scene_ir.schema_version,
        "recipe": scene_ir.recipe,
        "blender": {"version": blender_version, "build": blender_build},
        "render": {
            "engine": RENDER_ENGINE,
            "profile": scene_ir.render_profile,
            "size": [width, height],
            "eevee_samples": EEVEE_SAMPLES,
        },
        "scene_counts": _scene_counts(glb_summary),
        "qa": {
            "scene_ir": "passed",
            "glb_preflight": "passed",
            "blend_file": "passed",
            "fresh_process_reopen": "passed",
            "render_png": "passed",
            "replay_integrity": "verified",
        },
        "outputs": {
            "scene.blend": {"sha256": sha256_file(blend_path), "bytes": blend_bytes},
            "render.png": {
                "sha256": sha256_file(render_path),
                "bytes": render_bytes,
                "width": width,
                "height": height,
            },
        },
        "process_result": {"build": "success", "fresh_reopen_render": "success"},
        "timing_seconds": {
            "build": round(build_seconds, 3),
            "fresh_reopen_render": round(render_seconds, 3),
            "wall": round(wall_seconds, 3),
        },
        "bytes_written": blend_bytes + render_bytes,
        "resource_usage": {"peak_memory_bytes": "unknown"},
        "model_calls": 0,
        "monetary_cost": "unknown",
    }


def verify_replay(
    artifact_dir: Path,
    *,
    fingerprint: str,
    scene_ir_sha256: str,
    source_asset_sha256: str,
    scene_ir: SceneIR,
    blender_version: str,
    blender_build: str,
    glb_summary: GlbSummary,
) -> dict[str, str]:
    def fail() -> None:
        raise SceneInputError("replay_integrity_error", "existing job artifacts failed integrity checks")

    try:
        if artifact_dir.is_symlink() or not artifact_dir.is_dir():
            fail()
        if {item.name for item in artifact_dir.iterdir()} != ARTIFACT_NAMES:
            fail()
        manifest_path = artifact_dir / "qa-manifest.json"
        if any((artifact_dir / name).is_symlink() for name in ARTIFACT_NAMES):
            fail()
        if manifest_path.stat().st_size > MAX_MANIFEST_BYTES:
            fail()
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if not isinstance(manifest, dict) or set(manifest) != MANIFEST_FIELDS:
            fail()
        width, height = RENDER_PROFILES[scene_ir.render_profile]
        expected_render = {
            "engine": RENDER_ENGINE,
            "profile": scene_ir.render_profile,
            "size": [width, height],
            "eevee_samples": EEVEE_SAMPLES,
        }
        if (
            manifest.get("schema_version") != MANIFEST_SCHEMA
            or manifest.get("status") != "accepted"
            or manifest.get("fingerprint") != fingerprint
            or manifest.get("scene_ir_sha256") != scene_ir_sha256
            or manifest.get("source_asset_sha256") != source_asset_sha256
            or manifest.get("composer_version") != COMPOSER_VERSION
            or manifest.get("scene_ir_version") != scene_ir.schema_version
            or manifest.get("recipe") != scene_ir.recipe
            or manifest.get("blender") != {"version": blender_version, "build": blender_build}
            or manifest.get("render") != expected_render
            or manifest.get("scene_counts") != _scene_counts(glb_summary)
            or manifest.get("model_calls") != 0
            or manifest.get("monetary_cost") != "unknown"
            or manifest.get("process_result") != {"build": "success", "fresh_reopen_render": "success"}
            or not isinstance(manifest.get("timing_seconds"), dict)
            or set(manifest["timing_seconds"]) != {"build", "fresh_reopen_render", "wall"}
            or any(
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
                or value < 0
                for value in manifest["timing_seconds"].values()
            )
            or isinstance(manifest.get("bytes_written"), bool)
            or not isinstance(manifest.get("bytes_written"), int)
            or not isinstance(manifest.get("resource_usage"), dict)
            or manifest["resource_usage"] != {"peak_memory_bytes": "unknown"}
            or manifest.get("qa")
            != {
                "scene_ir": "passed",
                "glb_preflight": "passed",
                "blend_file": "passed",
                "fresh_process_reopen": "passed",
                "render_png": "passed",
                "replay_integrity": "verified",
            }
            or not isinstance(manifest.get("outputs"), dict)
            or set(manifest["outputs"]) != {"scene.blend", "render.png"}
        ):
            fail()
        artifact_hashes: dict[str, str] = {}
        for name in ("scene.blend", "render.png"):
            path = artifact_dir / name
            details = manifest["outputs"][name]
            expected_fields = {"sha256", "bytes"} if name == "scene.blend" else {
                "sha256",
                "bytes",
                "width",
                "height",
            }
            if not isinstance(details, dict) or set(details) != expected_fields:
                fail()
            if details.get("sha256") != sha256_file(path):
                fail()
            if details.get("bytes") != path.stat().st_size:
                fail()
            artifact_hashes[name] = details["sha256"]
        if manifest["bytes_written"] != sum(
            manifest["outputs"][name]["bytes"] for name in ("scene.blend", "render.png")
        ):
            fail()
        render_details = manifest["outputs"]["render.png"]
        if render_details.get("width") != width or render_details.get("height") != height:
            fail()
        verify_png(artifact_dir / "render.png", (width, height))
        artifact_hashes["qa-manifest.json"] = sha256_file(manifest_path)
        return artifact_hashes
    except SceneInputError as error:
        if error.code == "replay_integrity_error":
            raise
        raise SceneInputError("replay_integrity_error", "existing job artifacts failed integrity checks") from error
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError) as error:
        raise SceneInputError("replay_integrity_error", "existing job artifacts failed integrity checks") from error
