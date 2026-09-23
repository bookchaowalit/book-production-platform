"""Local Blender process runner and isolated SceneIR job lifecycle."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

from book_production_platform.scene3d.errors import SceneInputError
from book_production_platform.scene3d.glb import inspect_glb
from book_production_platform.scene3d.jobs import (
    _publish_receipt,
    ARTIFACT_NAMES,
    JobResult,
    create_manifest,
    fingerprint_lock,
    resolve_asset_path,
    scene_fingerprint,
    sha256_file,
    verify_replay,
)
from book_production_platform.scene3d.models import parse_scene_ir
from book_production_platform.scene3d.qa import verify_png
from book_production_platform.scene3d.scene_builder import RENDER_PROFILES

MAX_TIMEOUT_SECONDS = 300
MAX_SCENE_IR_BYTES = 64 * 1024
MAX_DIAGNOSTIC_BYTES = 64 * 1024
WORKER_PATH = Path(__file__).with_name("blender_worker.py").resolve()
_SAFE_ENVIRONMENT_KEYS = (
    "PATH",
    "SYSTEMROOT",
    "WINDIR",
    "TEMP",
    "TMP",
)


def _safe_environment(worker_dir: Path) -> dict[str, str]:
    environment = {key: os.environ[key] for key in _SAFE_ENVIRONMENT_KEYS if key in os.environ}
    environment["TEMP"] = str(worker_dir)
    environment["TMP"] = str(worker_dir)
    environment["BLENDER_USER_CONFIG"] = str(worker_dir / "config")
    environment["BLENDER_USER_DATAFILES"] = str(worker_dir / "datafiles")
    environment["BLENDER_USER_SCRIPTS"] = str(worker_dir / "scripts")
    return environment


def _blender_identity(blender_bin: Path, timeout_s: int) -> tuple[str, str]:
    try:
        result = subprocess.run(
            [str(blender_bin), "--version"],
            check=False,
            capture_output=True,
            text=True,
            timeout=min(timeout_s, 15),
            shell=False,
            env=_safe_environment(Path(tempfile.gettempdir())),
        )
    except subprocess.TimeoutExpired as error:
        raise SceneInputError("blender_version_timeout", "Blender version check timed out") from error
    except OSError as error:
        raise SceneInputError("blender_unavailable", "Blender executable is unavailable") from error
    if result.returncode != 0:
        raise SceneInputError("blender_unavailable", "Blender version check failed")
    lines = result.stdout.splitlines()
    version = lines[0].strip() if lines else ""
    if version != "Blender 5.2.2 LTS":
        raise SceneInputError("unsupported_blender_version", "Blender 5.2.2 LTS is required")
    match = re.search(r"(?im)^\s*build hash:\s*([0-9a-f]{8,40})\s*$", result.stdout)
    if match is None:
        raise SceneInputError("unsupported_blender_version", "Blender build identity is unavailable")
    return version, match.group(1).lower()


def _run_worker(command: list[str], *, cwd: Path, timeout_s: int) -> float:
    started = time.perf_counter()
    diagnostics = bytearray()
    try:
        process = subprocess.Popen(
            command,
            cwd=cwd,
            env=_safe_environment(cwd),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            shell=False,
        )
    except OSError as error:
        raise SceneInputError("blender_unavailable", "Blender worker could not be started") from error

    def drain_output() -> None:
        if process.stdout is None:
            return
        while True:
            chunk = process.stdout.read(8192)
            if not chunk:
                return
            diagnostics.extend(chunk)
            if len(diagnostics) > MAX_DIAGNOSTIC_BYTES:
                del diagnostics[: len(diagnostics) - MAX_DIAGNOSTIC_BYTES]

    reader = threading.Thread(target=drain_output, name="blender-output-reader", daemon=True)
    reader.start()
    try:
        return_code = process.wait(timeout=timeout_s)
    except subprocess.TimeoutExpired as error:
        if process.poll() is None:
            try:
                process.kill()
            except OSError:
                pass
        process.wait()
        reader.join(timeout=2)
        raise SceneInputError("deadline_exceeded", "Blender worker exceeded its deadline") from error
    finally:
        reader.join(timeout=2)
        if process.stdout is not None:
            process.stdout.close()
    if return_code != 0:
        raise SceneInputError("blender_failed", "Blender worker failed")
    return time.perf_counter() - started


def _worker_command(blender_bin: Path, mode: str, values: list[tuple[str, Path]]) -> list[str]:
    command = [
        str(blender_bin),
        "--background",
        "--factory-startup",
        "--python",
        str(WORKER_PATH),
        "--",
        mode,
    ]
    for flag, path in values:
        command.extend((flag, str(path)))
    return command


def _execute_worker(command: list[str], *, cwd: Path, timeout_s: int) -> float:
    try:
        return _run_worker(command, cwd=cwd, timeout_s=timeout_s)
    except subprocess.TimeoutExpired as error:
        raise SceneInputError("deadline_exceeded", "Blender worker exceeded its deadline") from error


def _read_scene_ir(scene_path: Path) -> Any:
    if scene_path.is_symlink() or not scene_path.is_file():
        raise SceneInputError("invalid_scene_ir", "scene description is unavailable")
    try:
        if scene_path.stat().st_size > MAX_SCENE_IR_BYTES:
            raise SceneInputError("invalid_scene_ir", "scene description exceeds its size limit")
        return json.loads(scene_path.read_text(encoding="utf-8"))
    except SceneInputError:
        raise
    except (OSError, UnicodeDecodeError, ValueError, RecursionError) as error:
        raise SceneInputError("invalid_scene_ir", "scene description is invalid") from error


def _prepare_output_root(output_root: Path) -> Path:
    if output_root.is_symlink():
        raise SceneInputError("unsafe_output_path", "output root cannot be a symlink")
    try:
        output_root.mkdir(parents=True, exist_ok=True)
        resolved = output_root.resolve(strict=True)
    except OSError as error:
        raise SceneInputError("unsafe_output_path", "output root is unavailable") from error
    if not resolved.is_dir():
        raise SceneInputError("unsafe_output_path", "output root is not a directory")
    return resolved


def _cleanup_staging(staging: Path, output_root: Path) -> None:
    try:
        if staging.parent.resolve() == output_root and staging.name.startswith(".staging-"):
            shutil.rmtree(staging, ignore_errors=True)
    except OSError:
        return


def compose_scene(
    scene_path: Path,
    input_root: Path,
    output_root: Path,
    blender_bin: Path,
    timeout_s: int = MAX_TIMEOUT_SECONDS,
) -> JobResult:
    """Validate, compose, reopen, render, and atomically publish one local job."""
    job_started = time.perf_counter()
    if isinstance(timeout_s, bool) or not isinstance(timeout_s, int) or not 0 < timeout_s <= MAX_TIMEOUT_SECONDS:
        raise SceneInputError("invalid_timeout", "timeout must be between 1 and 300 seconds")
    blender_bin = Path(blender_bin).expanduser().resolve()
    scene_ir = parse_scene_ir(_read_scene_ir(scene_path))
    asset_path = resolve_asset_path(input_root, scene_ir.asset.path)
    glb_summary = inspect_glb(asset_path)
    source_asset_sha256 = sha256_file(asset_path)
    blender_version, blender_build = _blender_identity(blender_bin, timeout_s)
    fingerprint, scene_ir_sha256 = scene_fingerprint(
        scene_ir,
        source_asset_sha256,
        blender_version,
        blender_build,
    )
    expected_size = RENDER_PROFILES[scene_ir.render_profile]
    root = _prepare_output_root(output_root)
    final_dir = root / fingerprint

    with fingerprint_lock(root, fingerprint, timeout_s):
        if final_dir.exists() or final_dir.is_symlink():
            hashes = verify_replay(
                final_dir,
                receipt_path=root / ".receipts" / f"{fingerprint}.json",
                fingerprint=fingerprint,
                scene_ir_sha256=scene_ir_sha256,
                source_asset_sha256=source_asset_sha256,
                scene_ir=scene_ir,
                blender_version=blender_version,
                blender_build=blender_build,
                glb_summary=glb_summary,
            )
            return JobResult("replayed", fingerprint, final_dir.resolve(), hashes, True, 0)

        try:
            staging = Path(tempfile.mkdtemp(prefix=f".staging-{fingerprint[:12]}-", dir=root))
        except OSError as error:
            raise SceneInputError("publication_failed", "job staging directory cannot be created") from error
        try:
            staged_asset = staging / "asset.glb"
            scene_ir_file = staging / "scene.json"
            blend_file = staging / "scene.blend"
            render_file = staging / "render.png"
            worker_dir = staging / "worker"
            worker_dir.mkdir()
            try:
                shutil.copyfile(asset_path, staged_asset)
            except OSError as error:
                raise SceneInputError("unsafe_glb_resource", "asset cannot be staged") from error
            if sha256_file(staged_asset) != source_asset_sha256:
                raise SceneInputError("source_changed", "asset changed while the job was being prepared")
            staged_summary = inspect_glb(staged_asset)
            if staged_summary != glb_summary:
                raise SceneInputError("source_changed", "asset preflight changed while the job was being prepared")
            scene_ir_file.write_bytes(
                json.dumps(
                    scene_ir.model_dump(mode="json"),
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            )

            build_seconds = _execute_worker(
                _worker_command(
                    blender_bin,
                    "build",
                    [("--scene", scene_ir_file), ("--asset", staged_asset), ("--blend", blend_file)],
                ),
                cwd=worker_dir,
                timeout_s=timeout_s,
            )
            if blend_file.is_symlink() or not blend_file.is_file() or blend_file.stat().st_size == 0:
                raise SceneInputError("blender_failed", "Blender did not produce an editable scene")

            render_seconds = _execute_worker(
                _worker_command(
                    blender_bin,
                    "render",
                    [("--blend", blend_file), ("--png", render_file)],
                ),
                cwd=worker_dir,
                timeout_s=timeout_s,
            )
            verify_png(render_file, expected_size)
            if render_file.is_symlink():
                raise SceneInputError("invalid_render", "render output cannot be a symlink")

            shutil.rmtree(worker_dir)
            staged_asset.unlink()
            scene_ir_file.unlink()
            wall_seconds = time.perf_counter() - job_started
            manifest = create_manifest(
                fingerprint=fingerprint,
                scene_ir_sha256=scene_ir_sha256,
                source_asset_sha256=source_asset_sha256,
                scene_ir=scene_ir,
                blender_version=blender_version,
                blender_build=blender_build,
                glb_summary=glb_summary,
                blend_path=blend_file,
                render_path=render_file,
                build_seconds=build_seconds,
                render_seconds=render_seconds,
                wall_seconds=wall_seconds,
            )
            manifest_path = staging / "qa-manifest.json"
            manifest_path.write_text(
                json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
                encoding="utf-8",
            )
            if {item.name for item in staging.iterdir()} != ARTIFACT_NAMES:
                raise SceneInputError("publication_failed", "staged artifact set is invalid")
            hashes = {
                "scene.blend": sha256_file(blend_file),
                "render.png": sha256_file(render_file),
                "qa-manifest.json": sha256_file(manifest_path),
            }
            if final_dir.exists() or final_dir.is_symlink():
                raise SceneInputError("publication_conflict", "content-addressed output already exists")
            _publish_receipt(root, fingerprint, hashes)
            try:
                os.rename(staging, final_dir)
            except OSError as error:
                raise SceneInputError("publication_failed", "verified job cannot be published") from error
            return JobResult("accepted", fingerprint, final_dir.resolve(), hashes, False, 0)
        except SceneInputError:
            raise
        except OSError as error:
            raise SceneInputError("publication_failed", "job artifacts could not be staged or published") from error
        finally:
            _cleanup_staging(staging, root)
