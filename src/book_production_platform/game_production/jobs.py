"""Content-addressed Unity GameIR jobs with verified replay and atomic publish."""

from __future__ import annotations

import errno
import hashlib
import json
import os
import re
import shutil
import tempfile
import time
from collections import Counter
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from pathlib import Path, PurePosixPath
from typing import Any

from book_production_platform.game_ir import (
    MAX_GAME_IR_BYTES,
    GameInputError,
    GameIR,
    canonical_game_ir_bytes,
    game_ir_sha256,
    parse_game_ir,
    to_unity_worker_json,
    validate_game_ir,
)
from book_production_platform.game_production import runtime
from book_production_platform.game_production.assets import (
    ResolvedAsset,
    resolve_assets,
    stage_assets,
    verify_published_assets,
)
from book_production_platform.game_production.errors import GameJobError
from book_production_platform.game_production.level_map import render_level_svg
from book_production_platform.game_production.manual import build_unity_guide
from book_production_platform.game_production.metrics import level_metrics
from book_production_platform.game_production.qa import (
    UnityQaEvidence,
    summarize_test_xml,
    verify_gameplay_trace,
    verify_worker_report,
)
from book_production_platform.game_production.tracing import (
    record_artifact,
    record_source_file,
    trace_job_dir,
    trace_root,
    write_ir_file,
)
from book_production_platform.production_trace import TraceRecorder

if os.name == "nt":
    import msvcrt
else:
    from importlib import import_module

    fcntl: Any = import_module("fcntl")

COMPOSER_VERSION = "game-scene-composer.v1a.2"
QA_MANIFEST_SCHEMA = "game-production.qa.v1"
RECEIPT_SCHEMA = "game-production.receipt.v1"
MAX_UNITY_TIMEOUT_SECONDS = 120
MAX_STAGING_BYTES = 1024 * 1024 * 1024
MAX_PROJECT_BYTES = 64 * 1024 * 1024
MAX_QA_MANIFEST_BYTES = 1024 * 1024
MAX_RECEIPT_BYTES = 4096
STAGING_ROOT_NAME = ".staging"
LOCK_ROOT_NAME = ".locks"
TEMPLATE_PROJECT = Path(__file__).with_name("templates") / "unity_project"
PROJECT_ROOT_ENTRIES = frozenset({"Assets", "Packages", "ProjectSettings", ".gitattributes"})
UNITY_WORK_DIRS = (".book-production", "Library", "Temp", "Logs", "UserSettings")
ARTIFACT_ROOT_ENTRIES = frozenset({"project", "gameplay-trace.json", "qa-manifest.json", ".receipt.json"})
FINGERPRINT_PATTERN = re.compile(r"^[0-9a-f]{64}$")
STAGING_NAME_PATTERN = re.compile(r"^g[0-9a-f]{4}-[A-Za-z0-9_-]{8,16}$")


@dataclass(frozen=True)
class GameJobResult:
    status: str
    fingerprint: str
    artifact_dir: Path
    artifact_hashes: dict[str, str]
    replay: bool
    model_calls: int = 0
    trace_job_id: str | None = field(default=None, compare=False)


def canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def pretty_json_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n").encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _durable_write(path: Path, content: bytes) -> None:
    try:
        with path.open("wb") as destination:
            destination.write(content)
            destination.flush()
            os.fsync(destination.fileno())
    except OSError as error:
        raise GameJobError("publication_failed", "Job artifact could not be flushed") from error


def _read_game_ir(game_path: Path) -> tuple[GameIR, bytes]:
    source = Path(game_path).expanduser()
    if source.is_symlink():
        raise GameJobError("unsafe_input_path", "GameIR input cannot be a symlink")
    try:
        resolved = source.resolve(strict=True)
        if not resolved.is_file():
            raise GameJobError("invalid_game_ir", "GameIR input must be a regular file")
        if resolved.stat().st_size > MAX_GAME_IR_BYTES:
            raise GameJobError("resource_cap_exceeded", "GameIR input exceeds its size limit")
        with resolved.open("rb") as source_file:
            payload = source_file.read(MAX_GAME_IR_BYTES + 1)
        if len(payload) > MAX_GAME_IR_BYTES:
            raise GameJobError("resource_cap_exceeded", "GameIR input exceeds its size limit")
    except GameJobError:
        raise
    except (OSError, RuntimeError) as error:
        raise GameJobError("invalid_game_ir", "GameIR input is unavailable") from error
    return validate_game_ir(parse_game_ir(payload)), payload


def _resolve_unity_executable(unity_bin: Path) -> Path:
    executable = Path(unity_bin).expanduser()
    if executable.is_symlink():
        raise GameJobError("unity_unavailable", "Unity executable cannot be a symlink")
    try:
        resolved = executable.resolve(strict=True)
    except (OSError, RuntimeError) as error:
        raise GameJobError("unity_unavailable", "Unity executable is unavailable") from error
    if not resolved.is_file():
        raise GameJobError("unity_unavailable", "Unity executable is unavailable")
    return resolved


def _prepare_output_root(output_root: Path) -> Path:
    requested = Path(output_root).expanduser()
    if requested.is_symlink():
        raise GameJobError("unsafe_output_path", "Output root cannot be a symlink")
    try:
        requested.mkdir(parents=True, exist_ok=True)
        resolved = requested.resolve(strict=True)
    except (OSError, RuntimeError) as error:
        raise GameJobError("unsafe_output_path", "Output root is unavailable") from error
    if not resolved.is_dir():
        raise GameJobError("unsafe_output_path", "Output root must be a directory")
    return resolved


def _fingerprint_payload(
    game_ir: GameIR,
    unity_executable_sha256: str,
    template_sha256: str,
) -> dict[str, str]:
    return {
        "schema_version": game_ir.schema_version,
        "composer_version": COMPOSER_VERSION,
        "normalized_game_ir_sha256": game_ir_sha256(game_ir),
        "template_sha256": template_sha256,
        "unity_version": runtime.EXPECTED_UNITY_VERSION,
        "unity_executable_sha256": unity_executable_sha256,
    }


def game_fingerprint(
    game_ir: GameIR,
    unity_executable_sha256: str,
    template_sha256: str | None = None,
) -> str:
    """Compute the content address for one normalized GameIR and pinned Editor."""
    selected_template_hash = template_sha256 or project_tree_sha256(TEMPLATE_PROJECT)
    return sha256_bytes(
        canonical_json_bytes(
            _fingerprint_payload(game_ir, unity_executable_sha256, selected_template_hash)
        )
    )


def _retryable_lock_error(error: OSError) -> bool:
    return error.errno in {errno.EACCES, errno.EAGAIN, errno.EDEADLK}


@contextmanager
def fingerprint_lock(root: Path, fingerprint: str, deadline: float) -> Iterator[None]:
    """Serialize build and replay decisions across processes for one fingerprint."""
    if not FINGERPRINT_PATTERN.fullmatch(fingerprint):
        raise GameJobError("publication_failed", "Fingerprint is invalid")
    lock_root = Path(root) / LOCK_ROOT_NAME
    if lock_root.is_symlink():
        raise GameJobError("unsafe_output_path", "Fingerprint lock path is unsafe")
    try:
        lock_root.mkdir(exist_ok=True)
        if not lock_root.is_dir():
            raise GameJobError("unsafe_output_path", "Fingerprint lock path is invalid")
        lock_path = lock_root / f"{fingerprint}.lock"
        if lock_path.is_symlink():
            raise GameJobError("unsafe_output_path", "Fingerprint lock file is unsafe")
        handle = lock_path.open("a+b")
    except GameJobError:
        raise
    except OSError as error:
        raise GameJobError("publication_failed", "Fingerprint lock cannot be created") from error

    acquired = False
    try:
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"\0")
            handle.flush()
        while not acquired:
            if deadline <= time.monotonic():
                raise GameJobError("deadline_exceeded", "Job deadline expired while waiting for its lock")
            handle.seek(0)
            try:
                if os.name == "nt":
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                acquired = True
            except OSError as error:
                if not _retryable_lock_error(error):
                    raise GameJobError("publication_failed", "Fingerprint lock could not be acquired") from error
                time.sleep(min(0.05, max(0.0, deadline - time.monotonic())))
        yield
    finally:
        if acquired:
            try:
                handle.seek(0)
                if os.name == "nt":
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            except OSError:
                pass
        handle.close()


def create_job_staging(root: Path, fingerprint: str) -> Path:
    """Create a uniquely named, same-filesystem staging directory for one job."""
    if not FINGERPRINT_PATTERN.fullmatch(fingerprint):
        raise GameJobError("publication_failed", "Fingerprint is invalid")
    staging_root = Path(root) / STAGING_ROOT_NAME
    if staging_root.is_symlink():
        raise GameJobError("unsafe_output_path", "Job staging root is unsafe")
    try:
        staging_root.mkdir(exist_ok=True)
        if not staging_root.is_dir():
            raise GameJobError("unsafe_output_path", "Job staging root is invalid")
        return Path(tempfile.mkdtemp(prefix=f"g{fingerprint[:4]}-", dir=staging_root))
    except GameJobError:
        raise
    except OSError as error:
        raise GameJobError("publication_failed", "Job staging directory cannot be created") from error


def remove_only_job_staging(staging: Path) -> None:
    """Remove one direct child of the dedicated staging root and nothing else."""
    candidate = Path(staging)
    if candidate.is_symlink() or not STAGING_NAME_PATTERN.fullmatch(candidate.name):
        return
    try:
        parent = candidate.parent.resolve(strict=True)
        resolved = candidate.resolve(strict=True)
        if parent.name != STAGING_ROOT_NAME or resolved.parent != parent or not resolved.is_dir():
            return
        shutil.rmtree(resolved)
    except (OSError, RuntimeError):
        return


def _regular_files(root: Path) -> list[Path]:
    if root.is_symlink() or not root.is_dir():
        raise GameJobError("unsafe_output_path", "Artifact directory is unsafe")
    files: list[Path] = []
    try:
        entries = sorted(root.rglob("*"), key=lambda item: item.relative_to(root).as_posix())
        for item in entries:
            if item.is_symlink():
                raise GameJobError("unsafe_output_path", "Symlinks are not allowed in job artifacts")
            if item.is_dir():
                continue
            if not item.is_file():
                raise GameJobError("unsafe_output_path", "Job artifact is not a regular file")
            files.append(item)
    except GameJobError:
        raise
    except OSError as error:
        raise GameJobError("unsafe_output_path", "Job artifact tree cannot be inspected") from error
    return files


def _tree_size(root: Path) -> int:
    total = 0
    for item in _regular_files(root):
        try:
            total += item.stat().st_size
        except OSError as error:
            raise GameJobError("unsafe_output_path", "Job artifact size cannot be inspected") from error
    return total


def project_tree_sha256(project_path: Path) -> str:
    """Hash sorted relative project paths and file bytes, excluding metadata."""
    files = _regular_files(project_path)
    digest = hashlib.sha256()
    for item in files:
        relative = item.relative_to(project_path).as_posix()
        file_hash = sha256_file(item)
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(bytes.fromhex(file_hash))
        digest.update(b"\0")
    return digest.hexdigest()


def _project_tree_stats(project_path: Path) -> tuple[str, int]:
    files = _regular_files(project_path)
    digest = hashlib.sha256()
    total = 0
    for item in files:
        relative = item.relative_to(project_path).as_posix()
        total += item.stat().st_size
        if total > MAX_PROJECT_BYTES:
            raise GameJobError("resource_cap_exceeded", "Unity project exceeds its size limit")
        file_hash = sha256_file(item)
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(bytes.fromhex(file_hash))
        digest.update(b"\0")
    return digest.hexdigest(), total


def receipt_exists(artifact_dir: Path) -> bool:
    candidate = Path(artifact_dir)
    receipt = candidate / ".receipt.json"
    return not candidate.is_symlink() and receipt.is_file() and not receipt.is_symlink()


def _safe_relative_path(value: object, expected: str) -> bool:
    if not isinstance(value, str) or value != expected:
        return False
    path = PurePosixPath(value)
    return not path.is_absolute() and ".." not in path.parts and "\\" not in value


def _load_limited_json(path: Path, limit: int, error_code: str) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise GameJobError(error_code, "Job integrity file is missing or unsafe")
    try:
        if path.stat().st_size > limit:
            raise GameJobError(error_code, "Job integrity file exceeds its size limit")
        value = json.loads(path.read_text(encoding="utf-8"))
    except GameJobError:
        raise
    except (OSError, UnicodeDecodeError, ValueError, RecursionError) as error:
        raise GameJobError(error_code, "Job integrity file is invalid") from error
    if not isinstance(value, dict):
        raise GameJobError(error_code, "Job integrity file has an invalid shape")
    return value


def verify_replay_and_load_result(artifact_dir: Path) -> GameJobResult:
    """Verify every published project, trace, manifest, and receipt before replay."""
    candidate = Path(artifact_dir)
    try:
        if candidate.is_symlink() or not candidate.is_dir():
            raise GameJobError("replay_integrity_error", "Published artifact directory is unavailable")
        resolved = candidate.resolve(strict=True)
        if not FINGERPRINT_PATTERN.fullmatch(resolved.name):
            raise GameJobError("replay_integrity_error", "Published artifact fingerprint is invalid")
        if {item.name for item in resolved.iterdir()} != ARTIFACT_ROOT_ENTRIES:
            raise GameJobError("replay_integrity_error", "Published artifact set is incomplete or unexpected")

        project = resolved / "project"
        manifest_path = resolved / "qa-manifest.json"
        trace_path = resolved / "gameplay-trace.json"
        receipt_path = resolved / ".receipt.json"
        if {item.name for item in project.iterdir()} != PROJECT_ROOT_ENTRIES:
            raise GameJobError("replay_integrity_error", "Published Unity project has an unexpected layout")

        receipt = _load_limited_json(receipt_path, MAX_RECEIPT_BYTES, "replay_integrity_error")
        manifest = _load_limited_json(manifest_path, MAX_QA_MANIFEST_BYTES, "replay_integrity_error")
        source_path = project / "Assets" / "BookProduction" / "Source" / "game-ir.json"
        if source_path.is_symlink() or not source_path.is_file():
            raise GameJobError("replay_integrity_error", "Published canonical GameIR is missing")
        if source_path.stat().st_size > MAX_GAME_IR_BYTES:
            raise GameJobError("replay_integrity_error", "Published canonical GameIR exceeds its size limit")
        with source_path.open("rb") as source_file:
            source_bytes = source_file.read(MAX_GAME_IR_BYTES + 1)
        if len(source_bytes) > MAX_GAME_IR_BYTES:
            raise GameJobError("replay_integrity_error", "Published canonical GameIR exceeds its size limit")
        game_ir = validate_game_ir(parse_game_ir(source_bytes))
        normalized_hash = game_ir_sha256(game_ir)
        expected_scene_relative = f"project/Assets/Scenes/{game_ir.scene_id}.unity"
        current_template_hash = project_tree_sha256(TEMPLATE_PROJECT)
        expected_fingerprint = game_fingerprint(
            game_ir,
            str(manifest.get("unity_executable_sha256", "")),
            current_template_hash,
        )
        project_hash, project_bytes = _project_tree_stats(project)
        scene_path = project / "Assets" / "Scenes" / f"{game_ir.scene_id}.unity"
        trace_events, trace_bytes = verify_gameplay_trace(trace_path, game_ir)
        trace_hash = sha256_bytes(trace_bytes)
        scene_hash = sha256_file(scene_path) if scene_path.is_file() and not scene_path.is_symlink() else ""
        manifest_hash = sha256_file(manifest_path)

        hashes = {
            "project_tree_sha256": project_hash,
            "scene_sha256": scene_hash,
            "gameplay_trace_sha256": trace_hash,
            "qa_manifest_sha256": manifest_hash,
        }
        if (
            set(receipt) != {"schema_version", "fingerprint", "hashes"}
            or receipt.get("schema_version") != RECEIPT_SCHEMA
            or receipt.get("fingerprint") != resolved.name
            or receipt.get("hashes") != hashes
            or manifest.get("schema_version") != QA_MANIFEST_SCHEMA
            or manifest.get("status") != "accepted"
            or manifest.get("fingerprint") != resolved.name
            or manifest.get("composer_version") != COMPOSER_VERSION
            or manifest.get("template_sha256") != current_template_hash
            or manifest.get("schema_version_game_ir") != game_ir.schema_version
            or manifest.get("normalized_game_ir_sha256") != normalized_hash
            or manifest.get("unity_version") != runtime.EXPECTED_UNITY_VERSION
            or expected_fingerprint != resolved.name
        ):
            raise GameJobError("replay_integrity_error", "Published receipt does not match its artifacts")

        outputs = manifest.get("outputs")
        if not isinstance(outputs, dict):
            raise GameJobError("replay_integrity_error", "Published QA manifest has no output index")
        expected_output_values = {
            "project": ("project", project_hash, project_bytes),
            "scene": (expected_scene_relative, scene_hash, scene_path.stat().st_size),
            "gameplay_trace": ("gameplay-trace.json", trace_hash, len(trace_bytes)),
        }
        if set(outputs) != set(expected_output_values):
            raise GameJobError("replay_integrity_error", "Published QA manifest output set is invalid")
        for key, (relative, digest, size) in expected_output_values.items():
            item = outputs.get(key)
            if (
                not isinstance(item, dict)
                or not _safe_relative_path(item.get("path"), relative)
                or item.get("sha256") != digest
                or item.get("bytes") != size
            ):
                raise GameJobError("replay_integrity_error", "Published QA manifest output hash is invalid")
        verify_published_assets(project, game_ir, manifest.get("assets"))
        qa = manifest.get("qa")
        if not isinstance(qa, dict) or qa.get("gameplay_events") != list(trace_events):
            raise GameJobError("replay_integrity_error", "Published gameplay evidence does not match its trace")
        return GameJobResult("replayed", resolved.name, resolved, hashes, True, 0)
    except GameJobError as error:
        if error.code == "replay_integrity_error":
            raise
        raise GameJobError("replay_integrity_error", "Published artifact failed integrity checks") from error
    except (OSError, RuntimeError, TypeError, ValueError, KeyError) as error:
        raise GameJobError("replay_integrity_error", "Published artifact failed integrity checks") from error


def run_compile_tests_and_reopen(
    project: Path,
    game_ir: GameIR,
    unity_bin: Path,
    deadline: float,
) -> UnityQaEvidence:
    """Compile, test, replay gameplay, and reopen the generated scene in fresh Editor processes."""
    private_root = project / ".book-production"
    if private_root.is_symlink():
        raise GameJobError("unsafe_output_path", "Unity worker input path is unsafe")
    try:
        private_root.mkdir(exist_ok=True)
        if not private_root.is_dir():
            raise GameJobError("unsafe_output_path", "Unity worker input path is invalid")
        source_ir = project / "Assets" / "BookProduction" / "Source" / "game-ir.json"
        if source_ir.is_symlink() or not source_ir.parent.is_dir():
            raise GameJobError("unsafe_output_path", "Canonical GameIR destination is unsafe")
        _durable_write(source_ir, canonical_game_ir_bytes(game_ir))
        _durable_write(private_root / "worker-input.json", to_unity_worker_json(game_ir))
    except GameJobError:
        raise
    except OSError as error:
        raise GameJobError("unity_worker_failed", "Unity worker input could not be staged") from error

    executable = _resolve_unity_executable(unity_bin)
    expected_executable_hash = sha256_file(executable)
    results: list[runtime.UnityProcessResult] = []
    for stage in ("compile", "editmode", "playmode", "reopen"):
        result = runtime.run_unity_stage(executable, project, stage, deadline)
        if (
            result.version != runtime.EXPECTED_UNITY_VERSION
            or result.executable_sha256 != expected_executable_hash
            or result.returncode != 0
        ):
            raise GameJobError("unsupported_unity_version", "Unity runtime identity changed during the job")
        results.append(result)
        if _tree_size(project.parent) > MAX_STAGING_BYTES:
            raise GameJobError("resource_cap_exceeded", "Unity job staging exceeds its size limit")

    worker_report = verify_worker_report(project, game_ir, runtime.EXPECTED_UNITY_VERSION)
    editmode_result = results[1]
    playmode_result = results[2]
    if editmode_result.test_xml is None or playmode_result.test_xml is None:
        raise GameJobError("unity_test_failed", "Unity test result is missing")
    editmode_summary = summarize_test_xml(editmode_result.test_xml)
    playmode_summary = summarize_test_xml(playmode_result.test_xml)
    trace_path = private_root / "gameplay-trace.json"
    events, trace_bytes = verify_gameplay_trace(trace_path, game_ir)

    scene_path = project / "Assets" / "Scenes" / f"{game_ir.scene_id}.unity"
    scene_meta = Path(str(scene_path) + ".meta")
    if (
        scene_path.is_symlink()
        or not scene_path.is_file()
        or scene_path.stat().st_size == 0
        or scene_meta.is_symlink()
        or not scene_meta.is_file()
    ):
        raise GameJobError("scene_qa_failed", "Unity did not produce a reopenable scene")
    total_elapsed = sum(item.elapsed_seconds for item in results)
    total_log_bytes = sum(item.log_bytes for item in results)
    return UnityQaEvidence(
        unity_version=runtime.EXPECTED_UNITY_VERSION,
        unity_executable_sha256=expected_executable_hash,
        worker_report=worker_report,
        editmode=editmode_summary,
        playmode=playmode_summary,
        gameplay_events=events,
        gameplay_trace_sha256=sha256_bytes(trace_bytes),
        elapsed_seconds=total_elapsed,
        total_log_bytes=total_log_bytes,
    )


def _sanitize_project(project: Path) -> None:
    for name in UNITY_WORK_DIRS:
        path = project / name
        if path.is_symlink():
            raise GameJobError("unsafe_output_path", "Unity generated an unsafe project directory")
        if not path.exists():
            continue
        try:
            if path.is_dir():
                shutil.rmtree(path)
            else:
                path.unlink()
        except OSError as error:
            raise GameJobError("publication_failed", "Temporary Unity data could not be removed") from error
    if {item.name for item in project.iterdir()} != PROJECT_ROOT_ENTRIES:
        raise GameJobError("publication_failed", "Unity project contains unexpected top-level files")


def write_manifest_and_receipt(
    staging: Path,
    game_ir: GameIR,
    fingerprint: str,
    evidence: UnityQaEvidence,
    *,
    template_sha256: str,
    source_game_ir_sha256: str,
    normalized_game_ir_sha256: str,
    source_bytes: int,
    job_elapsed_seconds: float,
    timeout_s: int,
    assets: list[ResolvedAsset] | None = None,
) -> GameJobResult:
    """Write the complete QA manifest and pinned receipt inside one staging directory."""
    if not FINGERPRINT_PATTERN.fullmatch(fingerprint):
        raise GameJobError("publication_failed", "Fingerprint is invalid")
    project = staging / "project"
    project_hash, project_bytes = _project_tree_stats(project)
    if project_bytes > MAX_PROJECT_BYTES:
        raise GameJobError("resource_cap_exceeded", "Published Unity project exceeds its size limit")
    scene_relative = f"project/Assets/Scenes/{game_ir.scene_id}.unity"
    scene_path = project / "Assets" / "Scenes" / f"{game_ir.scene_id}.unity"
    trace_path = staging / "gameplay-trace.json"
    if trace_path.is_symlink() or not trace_path.is_file():
        raise GameJobError("gameplay_qa_failed", "Gameplay trace was not staged for publication")
    trace_bytes = trace_path.read_bytes()
    events, verified_trace_bytes = verify_gameplay_trace(trace_path, game_ir)
    if verified_trace_bytes != trace_bytes or events != evidence.gameplay_events:
        raise GameJobError("gameplay_qa_failed", "Staged gameplay trace changed before publication")
    scene_hash = sha256_file(scene_path)
    trace_hash = sha256_bytes(trace_bytes)
    manifest_path = staging / "qa-manifest.json"
    receipt_path = staging / ".receipt.json"
    outputs: dict[str, dict[str, Any]] = {
        "project": {"path": "project", "sha256": project_hash, "bytes": project_bytes},
        "scene": {
            "path": scene_relative,
            "sha256": scene_hash,
            "bytes": scene_path.stat().st_size,
        },
        "gameplay_trace": {
            "path": "gameplay-trace.json",
            "sha256": trace_hash,
            "bytes": len(trace_bytes),
        },
    }
    manifest: dict[str, Any] = {
        "schema_version": QA_MANIFEST_SCHEMA,
        "schema_version_game_ir": game_ir.schema_version,
        "status": "accepted",
        "fingerprint": fingerprint,
        "composer_version": COMPOSER_VERSION,
        "template_sha256": template_sha256,
        "source_game_ir_sha256": source_game_ir_sha256,
        "normalized_game_ir_sha256": normalized_game_ir_sha256,
        "source_game_ir_bytes": source_bytes,
        "unity_version": evidence.unity_version,
        "unity_executable_sha256": evidence.unity_executable_sha256,
        "checks": {
            "schema_and_geometry": True,
            "scene_hierarchy": True,
            "editmode_tests": True,
            "playmode_tests": True,
            "gameplay_trace": True,
            "fresh_process_reopen": True,
            "no_external_resources": True,
            "no_disallowed_components": True,
            "trusted_template_no_network_references": True,
        },
        "qa": {
            "worker_report": evidence.worker_report,
            "editmode_tests": {"total": evidence.editmode.total, "passed": evidence.editmode.passed},
            "playmode_tests": {"total": evidence.playmode.total, "passed": evidence.playmode.passed},
            "gameplay_events": list(events),
            "gameplay_event_counts": dict(sorted(Counter(events).items())),
        },
        "outputs": outputs,
        **({"assets": [item.manifest_entry() for item in assets]} if assets else {}),
        "resource_usage": {
            "project_bytes": project_bytes,
            "staging_bytes": 0,
            "unity_log_bytes": evidence.total_log_bytes,
            "job_elapsed_seconds": round(job_elapsed_seconds, 6),
            "unity_elapsed_seconds": round(evidence.elapsed_seconds, 6),
            "timeout_seconds": timeout_s,
        },
        "replay": False,
        "model_calls": 0,
        "monetary_cost": "unknown",
    }

    base_hashes = {
        "project_tree_sha256": project_hash,
        "scene_sha256": scene_hash,
        "gameplay_trace_sha256": trace_hash,
    }
    staging_bytes = 0
    manifest_bytes = b""
    receipt_bytes = b""
    artifact_hashes: dict[str, str] = {}
    for _ in range(8):
        manifest["resource_usage"]["staging_bytes"] = staging_bytes
        manifest_bytes = pretty_json_bytes(manifest)
        artifact_hashes = {**base_hashes, "qa_manifest_sha256": sha256_bytes(manifest_bytes)}
        receipt_bytes = pretty_json_bytes(
            {
                "schema_version": RECEIPT_SCHEMA,
                "fingerprint": fingerprint,
                "hashes": artifact_hashes,
            }
        )
        expected_staging_bytes = project_bytes + len(trace_bytes) + len(manifest_bytes) + len(receipt_bytes)
        if expected_staging_bytes == staging_bytes:
            break
        staging_bytes = expected_staging_bytes
    else:
        raise GameJobError("publication_failed", "Artifact byte accounting did not stabilize")

    if staging_bytes > MAX_STAGING_BYTES:
        raise GameJobError("resource_cap_exceeded", "Job staging exceeds its size limit")
    _durable_write(manifest_path, manifest_bytes)
    _durable_write(receipt_path, receipt_bytes)
    if _tree_size(staging) != staging_bytes:
        raise GameJobError("publication_failed", "Staging byte accounting changed before publish")
    return GameJobResult("accepted", fingerprint, staging, artifact_hashes, False, 0)


def atomic_publish(staging: Path, destination: Path) -> None:
    """Atomically move one verified staging directory without replacing output."""
    source = Path(staging)
    target = Path(destination)
    if (
        source.is_symlink()
        or not STAGING_NAME_PATTERN.fullmatch(source.name)
        or source.parent.name != STAGING_ROOT_NAME
        or target.name not in {""} and not FINGERPRINT_PATTERN.fullmatch(target.name)
        or target.parent.resolve() != source.parent.parent.resolve()
    ):
        raise GameJobError("publication_failed", "Staging and destination paths are invalid")
    if target.exists() or target.is_symlink():
        raise GameJobError("publication_conflict", "Content-addressed destination already exists")
    try:
        os.rename(source, target)
    except OSError as error:
        if target.exists() or target.is_symlink():
            raise GameJobError("publication_conflict", "Content-addressed destination already exists") from error
        raise GameJobError("publication_failed", "Verified job cannot be published") from error


class _ComposeTrace:
    """One compose run's trace, persisted under ``<output_root>/.trace/`` once GameIR is valid."""

    def __init__(self, *, game_path: Path, output_root: Path) -> None:
        self.trace = TraceRecorder(
            tool_id="game-v1a",
            engine_version=COMPOSER_VERSION,
            recipe_version=COMPOSER_VERSION,
            schema_version="game-ir",
        )
        self.game_path = game_path
        self.output_root = output_root
        self.ir: GameIR | None = None
        self.root: Path | None = None

    def _persist(self, status: str, result: GameJobResult | None, error_code: str | None) -> str | None:
        if self.ir is None:
            return None
        trace = self.trace
        try:
            root = self.root or _prepare_output_root(self.output_root)
            directory = trace_job_dir(root, trace.job_id)
            if directory.exists() or directory.is_symlink():
                return None
            directory.mkdir(parents=True)
            ir_value = json.loads(canonical_game_ir_bytes(self.ir).decode("utf-8"))
            trace.schema_version = self.ir.schema_version
            trace.input_json("game_ir", ir_value)
            source = Path(self.game_path).expanduser()
            if source.is_file() and not source.is_symlink():
                record_source_file(trace, "game_ir_file", source)
            _attach_game_proposal(trace, source, ir_value)
            ir_file = write_ir_file(directory, ir_value)
            trace.ir_document(ir_value, schema=self.ir.schema_version, path=ir_file)
            record_artifact(trace, ir_file, job_dir=directory, media_type="application/json", role="ir")
            if result is not None:
                artifact_dir = result.artifact_dir
                for relative, media in (
                    (f"project/Assets/Scenes/{self.ir.scene_id}.unity", "application/x-unity-scene"),
                    ("gameplay-trace.json", "application/json"),
                    ("qa-manifest.json", "application/json"),
                    (".receipt.json", "application/json"),
                ):
                    record_artifact(trace, artifact_dir / relative, job_dir=directory, media_type=media)
            if error_code is not None:
                trace.record_step("outcome", "failed", note=error_code)
            with trace.step("analyze", "level_metrics_and_map") as step:
                metrics = level_metrics(self.ir)
                metrics_path = directory / "level-metrics.json"
                metrics_path.write_text(json.dumps(metrics.to_dict(), ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
                map_path = directory / "level-map.svg"
                map_path.write_bytes(render_level_svg(self.ir, metrics))
                step.params = {
                    "route_steps": metrics.route_steps,
                    "route_method": metrics.route_method,
                    "difficulty_band": metrics.difficulty_band,
                }
                step.outputs = [metrics_path.name, map_path.name]
            record_artifact(trace, metrics_path, job_dir=directory, media_type="application/json", role="analysis")
            record_artifact(trace, map_path, job_dir=directory, media_type="image/svg+xml", role="analysis")
            qa = {"status": "passed", "checks": ["scene_hierarchy", "editmode", "playmode", "gameplay_trace", "reopen"]} if result else None
            trace.finish(
                status=status, job_dir=directory, output_root=trace_root(root),
                guide=build_unity_guide(self.ir, metrics), qa=qa,
            )
        except (GameJobError, OSError, ValueError, TypeError, KeyError):
            return None
        return trace.job_id

    def finalize(self, result: GameJobResult) -> GameJobResult:
        job_id = self._persist("replayed" if result.replay else "accepted", result, None)
        return replace(result, trace_job_id=job_id) if job_id else result

    def fail(self, code: str) -> None:
        self._persist("failed", None, code)


PROPOSAL_SIDECAR_SUFFIX = ".proposal.json"
PROPOSAL_SCHEMA = "production-proposal.v1"
_MAX_PROPOSAL_BYTES = 16 * 1024


def _attach_game_proposal(trace: TraceRecorder, source: Path, ir_value: object) -> None:
    """Record AI proposal metadata from a matching ``<game-ir>.proposal.json`` sidecar."""

    from book_production_platform.production_trace import sha256_json

    sidecar = source.with_name(source.name + PROPOSAL_SIDECAR_SUFFIX)
    try:
        if sidecar.is_symlink() or not sidecar.is_file() or sidecar.stat().st_size > _MAX_PROPOSAL_BYTES:
            return
        value = json.loads(sidecar.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError):
        return
    if not isinstance(value, dict) or value.get("schema_version") != PROPOSAL_SCHEMA:
        return
    calls = value.get("model_calls")
    if isinstance(calls, bool) or not isinstance(calls, int) or calls < 0:
        return
    if value.get("ir_sha256") != sha256_json(ir_value):
        trace.record_step("provenance", "proposal_edited_by_human", note="proposal_hash_mismatch")
        return
    provided = value.get("provided")
    trace.ai_proposal(
        model_calls=calls,
        adapter=str(value.get("adapter", "unknown"))[:80],
        mode=str(value.get("mode", "unknown"))[:20],
        provided=[str(item)[:64] for item in provided] if isinstance(provided, list) else [],
        inferred=[],
    )


def compose_game_scene(
    game_path: Path,
    output_root: Path,
    unity_bin: Path,
    timeout_s: int = MAX_UNITY_TIMEOUT_SECONDS,
    asset_root: Path | None = None,
) -> GameJobResult:
    """Validate, build, QA, and atomically publish one editable Unity project.

    Every run with a valid GameIR also records a production trace under
    ``<output_root>/.trace/`` (accepted, replayed, and failed runs; errors are
    re-raised unchanged). The trace holds the Unity 6 manual guide, the level
    metrics, and a top-down level map.
    """
    session = _ComposeTrace(game_path=game_path, output_root=output_root)
    try:
        result = _compose_game_scene(game_path, output_root, unity_bin, timeout_s, asset_root, session)
    except (GameJobError, GameInputError) as error:
        session.fail(error.code)
        raise
    except Exception as error:
        session.fail(type(error).__name__)
        raise
    return session.finalize(result)


def _compose_game_scene(
    game_path: Path,
    output_root: Path,
    unity_bin: Path,
    timeout_s: int,
    asset_root: Path | None,
    session: _ComposeTrace,
) -> GameJobResult:
    if isinstance(timeout_s, bool) or not isinstance(timeout_s, int) or not 0 < timeout_s <= MAX_UNITY_TIMEOUT_SECONDS:
        raise GameJobError("invalid_timeout", "Unity job timeout must be between 1 and 120 seconds")
    trace = session.trace
    job_started = time.monotonic()
    deadline = job_started + timeout_s
    game_ir, source_bytes = _read_game_ir(game_path)
    session.ir = game_ir
    trace.record_step(
        "validate", "parse_and_validate_game_ir", schema=game_ir.schema_version, template=game_ir.template,
        world={"width": game_ir.world.width, "height": game_ir.world.height, "tile_size": game_ir.world.tile_size},
        player_speed=game_ir.player.speed, wall_cells=sum(len(wall.cells) for wall in game_ir.walls),
        objectives=len(game_ir.objectives), assets=len(game_ir.assets),
    )
    with trace.step("assets", "resolve_local_assets", count=len(game_ir.assets)):
        resolved_assets = resolve_assets(game_ir, asset_root)
    normalized_bytes = canonical_game_ir_bytes(game_ir)
    source_hash = sha256_bytes(source_bytes)
    normalized_hash = sha256_bytes(normalized_bytes)
    with trace.step("runtime", "resolve_unity_and_template", unity_version=runtime.EXPECTED_UNITY_VERSION) as step:
        executable = _resolve_unity_executable(unity_bin)
        executable_hash = sha256_file(executable)
        if not TEMPLATE_PROJECT.is_dir() or TEMPLATE_PROJECT.is_symlink():
            raise GameJobError("template_unavailable", "Pinned Unity project template is unavailable")
        template_hash = project_tree_sha256(TEMPLATE_PROJECT)
        step.params.update({"unity_executable_sha256": executable_hash, "template_sha256": template_hash})
    fingerprint = game_fingerprint(game_ir, executable_hash, template_hash)
    trace.record_step("fingerprint", "content_address", outputs=[fingerprint], composer=COMPOSER_VERSION)
    root = _prepare_output_root(output_root)
    session.root = root
    destination = root / fingerprint

    with fingerprint_lock(root, fingerprint, deadline):
        if destination.exists() or destination.is_symlink():
            with trace.step("replay", "verify_receipt_and_tree"):
                return verify_replay_and_load_result(destination)

        staging = create_job_staging(root, fingerprint)
        try:
            project = staging / "project"
            with trace.step("stage", "copy_template_and_assets", assets=len(resolved_assets)):
                shutil.copytree(TEMPLATE_PROJECT, project)
                if project_tree_sha256(project) != template_hash:
                    raise GameJobError("template_unavailable", "Unity project template changed during staging")
                stage_assets(project, resolved_assets)
                if _tree_size(staging) > MAX_STAGING_BYTES:
                    raise GameJobError("resource_cap_exceeded", "Job staging exceeds its size limit")

            with trace.step(
                "unity", "compile_editmode_playmode_reopen",
                stages=["compile", "editmode", "playmode", "reopen"], method="BookProduction.Editor.SceneCompiler.BuildFromGameIR",
                worker_input=".book-production/worker-input.json",
            ) as unity_step:
                evidence = run_compile_tests_and_reopen(project, game_ir, executable, deadline)
                unity_step.params.update(
                    {
                        "unity_version": evidence.unity_version,
                        "editmode": {"total": evidence.editmode.total, "passed": evidence.editmode.passed},
                        "playmode": {"total": evidence.playmode.total, "passed": evidence.playmode.passed},
                        "elapsed_seconds": round(evidence.elapsed_seconds, 6),
                    }
                )
            if evidence.unity_executable_sha256 != executable_hash:
                raise GameJobError("unsupported_unity_version", "Unity executable changed during the job")
            if project_tree_sha256(TEMPLATE_PROJECT) != template_hash:
                raise GameJobError("template_unavailable", "Unity project template changed during the job")
            with trace.step("qa", "verify_gameplay_trace") as qa_step:
                trace_path = project / ".book-production" / "gameplay-trace.json"
                events, trace_bytes = verify_gameplay_trace(trace_path, game_ir)
                qa_step.params = {"events": len(events), "event_counts": dict(sorted(Counter(event.split(":")[0] for event in events).items()))}
            _durable_write(staging / "gameplay-trace.json", trace_bytes)
            _sanitize_project(project)
            if _tree_size(project) > MAX_PROJECT_BYTES:
                raise GameJobError("resource_cap_exceeded", "Published Unity project exceeds its size limit")

            with trace.step("publish", "manifest_receipt_atomic_publish") as publish_step:
                result = write_manifest_and_receipt(
                    staging,
                    game_ir,
                    fingerprint,
                    evidence,
                    template_sha256=template_hash,
                    source_game_ir_sha256=source_hash,
                    normalized_game_ir_sha256=normalized_hash,
                    source_bytes=len(source_bytes),
                    job_elapsed_seconds=time.monotonic() - job_started,
                    timeout_s=timeout_s,
                    assets=resolved_assets,
                )
                if _tree_size(staging) > MAX_STAGING_BYTES:
                    raise GameJobError("resource_cap_exceeded", "Job staging exceeds its size limit")
                atomic_publish(staging, destination)
                publish_step.outputs = sorted(ARTIFACT_ROOT_ENTRIES)
            return replace(result, artifact_dir=destination.resolve(strict=True))
        except Exception:
            remove_only_job_staging(staging)
            raise


__all__ = [
    "COMPOSER_VERSION",
    "MAX_PROJECT_BYTES",
    "MAX_STAGING_BYTES",
    "GameJobResult",
    "atomic_publish",
    "canonical_json_bytes",
    "compose_game_scene",
    "create_job_staging",
    "fingerprint_lock",
    "game_fingerprint",
    "project_tree_sha256",
    "receipt_exists",
    "remove_only_job_staging",
    "run_compile_tests_and_reopen",
    "sha256_file",
    "verify_replay_and_load_result",
    "write_manifest_and_receipt",
]
