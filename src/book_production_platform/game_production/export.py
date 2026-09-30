"""Export an accepted Unity game project to a playable build for one platform."""

from __future__ import annotations

import json
import shutil
import subprocess
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from book_production_platform.game_ir import parse_game_ir, validate_game_ir
from book_production_platform.game_production import runtime
from book_production_platform.game_production.errors import GameJobError
from book_production_platform.game_production.jobs import (
    FINGERPRINT_PATTERN,
    _durable_write,
    _load_limited_json,
    _prepare_output_root,
    _regular_files,
    _resolve_unity_executable,
    atomic_publish,
    canonical_json_bytes,
    create_job_staging,
    fingerprint_lock,
    pretty_json_bytes,
    remove_only_job_staging,
    sha256_bytes,
    sha256_file,
    verify_replay_and_load_result,
)

EXPORTER_VERSION = "game-player-exporter.v1"
EXPORT_MANIFEST_SCHEMA = "game-production.export.v1"
EXPORT_RECEIPT_SCHEMA = "game-production.export-receipt.v1"
DEFAULT_EXPORT_TIMEOUT_SECONDS = 900
MAX_EXPORT_TIMEOUT_SECONDS = 1800
MAX_EXPORT_LOG_BYTES = 64 * 1024 * 1024
MAX_PLAYER_BYTES = 512 * 1024 * 1024
MAX_EXPORT_MANIFEST_BYTES = 4 * 1024 * 1024
MAX_EXPORT_RECEIPT_BYTES = 4096
MAX_EXPORT_REPORT_BYTES = 64 * 1024
EXPORTER_SOURCE = Path(__file__).with_name("exporter") / "PlayerExporter.cs"
EXPORTER_METHOD = "BookProduction.Editor.PlayerExporter.Build"
EXPORT_ROOT_ENTRIES = frozenset({"player", "export-manifest.json", ".receipt.json"})
# Unity command-line -buildTarget names for each supported export target.
EXPORT_TARGETS = {"web": "WebGL", "windows": "Win64"}
# Unity writes these next to Windows players; they must never be shipped.
_DO_NOT_SHIP_SUFFIXES = ("_BurstDebugInformation_DoNotShip", "_BackUpThisFolder_ButDontShipItWithYourGame")


@dataclass(frozen=True)
class GameExportResult:
    status: str
    fingerprint: str
    target: str
    artifact_dir: Path
    entrypoint: str
    source_fingerprint: str
    artifact_hashes: dict[str, str]
    replay: bool
    model_calls: int = 0


def export_fingerprint(
    source_fingerprint: str,
    source_project_sha256: str,
    target: str,
    unity_executable_sha256: str,
    exporter_sha256: str,
) -> str:
    """Content address for one accepted project exported to one target."""
    return sha256_bytes(
        canonical_json_bytes(
            {
                "exporter_version": EXPORTER_VERSION,
                "source_fingerprint": source_fingerprint,
                "source_project_sha256": source_project_sha256,
                "target": target,
                "unity_version": runtime.EXPECTED_UNITY_VERSION,
                "unity_executable_sha256": unity_executable_sha256,
                "exporter_sha256": exporter_sha256,
            }
        )
    )


def _validate_request(target: str, timeout_s: int) -> None:
    if target not in EXPORT_TARGETS:
        raise GameJobError("unsupported_export_target", "Export target must be web or windows")
    if isinstance(timeout_s, bool) or not isinstance(timeout_s, int) or not 0 < timeout_s <= MAX_EXPORT_TIMEOUT_SECONDS:
        raise GameJobError("invalid_timeout", "Export timeout must be between 1 and 1800 seconds")


def _scene_id(project: Path) -> str:
    source = project / "Assets" / "BookProduction" / "Source" / "game-ir.json"
    return validate_game_ir(parse_game_ir(source.read_bytes())).scene_id


def _player_index(player: Path) -> tuple[str, int, list[dict[str, object]]]:
    """Hash every player file; returns tree hash, total bytes, and the file index."""
    files: list[dict[str, object]] = []
    total = 0
    tree = bytearray()
    for item in _regular_files(player):
        size = item.stat().st_size
        total += size
        if total > MAX_PLAYER_BYTES:
            raise GameJobError("resource_cap_exceeded", "Exported player exceeds its size limit")
        relative = item.relative_to(player).as_posix()
        file_hash = sha256_file(item)
        files.append({"path": relative, "sha256": file_hash, "bytes": size})
        tree += relative.encode("utf-8") + b"\0" + bytes.fromhex(file_hash) + b"\0"
    return sha256_bytes(bytes(tree)), total, files


def _entrypoint(player: Path, target: str, product_name: str) -> str:
    """Check the platform's required files and return the file a player opens."""
    if target == "web":
        # Unity names WebGL files after the output folder, not the product.
        build = player / "Build"
        names = [item.name for item in build.iterdir() if item.is_file()] if build.is_dir() else []
        loaders = [name for name in names if name.endswith(".loader.js")]
        parts = (".data", ".framework.js", ".wasm")
        if (
            not (player / "index.html").is_file()
            or len(loaders) != 1
            or not all(any(part in name for name in names) for part in parts)
        ):
            raise GameJobError("player_incomplete", "Web player is missing index.html, loader, data, framework, or wasm")
        return "index.html"
    exe = player / f"{product_name}.exe"
    if not exe.is_file() or not (player / "UnityPlayer.dll").is_file() or not (player / f"{product_name}_Data").is_dir():
        raise GameJobError("player_incomplete", "Windows player is missing its executable, runtime, or data")
    return exe.name


def _remove_do_not_ship(player: Path) -> None:
    for item in player.iterdir():
        if item.is_dir() and not item.is_symlink() and item.name.endswith(_DO_NOT_SHIP_SUFFIXES):
            shutil.rmtree(item)


def _run_unity_export(executable: Path, work: Path, target: str, deadline: float) -> tuple[str, float]:
    started = time.perf_counter()
    version = runtime._probe_unity_version(executable, work, deadline)
    log_path = work / ".book-production" / "logs" / "export.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    argv = [
        str(executable),
        "-batchmode",
        "-nographics",
        "-quit",
        "-projectPath",
        str(work),
        "-buildTarget",
        EXPORT_TARGETS[target],
        "-executeMethod",
        EXPORTER_METHOD,
        "-logFile",
        str(log_path),
    ]
    try:
        process = subprocess.Popen(
            argv,
            cwd=work,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            shell=False,
        )
    except OSError as error:
        raise GameJobError("unity_unavailable", "Unity export worker could not start") from error
    while process.poll() is None:
        if deadline <= time.monotonic():
            runtime._stop_process(process)
            raise GameJobError("deadline_exceeded", "Unity export exceeded its deadline")
        if runtime._read_log_size(log_path) > MAX_EXPORT_LOG_BYTES:
            runtime._stop_process(process)
            raise GameJobError("unity_log_cap_exceeded", "Unity export log exceeded its size limit")
        time.sleep(min(runtime.POLL_INTERVAL_SECONDS, max(0.0, deadline - time.monotonic())))

    report = _load_limited_json(
        work / ".book-production" / "export-report.json", MAX_EXPORT_REPORT_BYTES, "unity_export_failed"
    )
    if report.get("status") == "target_unsupported":
        raise GameJobError("export_target_not_installed", "Unity build support for this target is not installed")
    if process.returncode != 0 or report.get("status") != "built" or report.get("result") != "Succeeded":
        raise GameJobError("unity_export_failed", "Unity player build failed")
    if report.get("unity_version") != version or report.get("target") != target:
        raise GameJobError("unity_export_failed", "Unity export report does not match the request")
    return version, time.perf_counter() - started


def verify_export_replay(artifact_dir: Path) -> GameExportResult:
    """Verify a published export's player files, manifest, and receipt."""
    try:
        resolved = Path(artifact_dir).resolve(strict=True)
        if Path(artifact_dir).is_symlink() or not FINGERPRINT_PATTERN.fullmatch(resolved.name):
            raise GameJobError("replay_integrity_error", "Published export fingerprint is invalid")
        if {item.name for item in resolved.iterdir()} != EXPORT_ROOT_ENTRIES:
            raise GameJobError("replay_integrity_error", "Published export set is incomplete or unexpected")
        manifest_path = resolved / "export-manifest.json"
        receipt = _load_limited_json(resolved / ".receipt.json", MAX_EXPORT_RECEIPT_BYTES, "replay_integrity_error")
        manifest = _load_limited_json(manifest_path, MAX_EXPORT_MANIFEST_BYTES, "replay_integrity_error")
        tree_hash, total, files = _player_index(resolved / "player")
        hashes = {"player_tree_sha256": tree_hash, "export_manifest_sha256": sha256_file(manifest_path)}
        player = manifest.get("player")
        if (
            set(receipt) != {"schema_version", "fingerprint", "hashes"}
            or receipt.get("schema_version") != EXPORT_RECEIPT_SCHEMA
            or receipt.get("fingerprint") != resolved.name
            or receipt.get("hashes") != hashes
            or manifest.get("schema_version") != EXPORT_MANIFEST_SCHEMA
            or manifest.get("status") != "accepted"
            or manifest.get("fingerprint") != resolved.name
            or manifest.get("exporter_version") != EXPORTER_VERSION
            or manifest.get("target") not in EXPORT_TARGETS
            or not isinstance(player, dict)
            or player.get("tree_sha256") != tree_hash
            or player.get("bytes") != total
            or player.get("files") != files
        ):
            raise GameJobError("replay_integrity_error", "Published export receipt does not match its files")
        return GameExportResult(
            "replayed",
            resolved.name,
            str(manifest["target"]),
            resolved,
            str(player.get("entrypoint", "")),
            str(manifest.get("source_fingerprint", "")),
            hashes,
            True,
            0,
        )
    except GameJobError as error:
        if error.code == "replay_integrity_error":
            raise
        raise GameJobError("replay_integrity_error", "Published export failed integrity checks") from error
    except (OSError, RuntimeError, TypeError, ValueError, KeyError) as error:
        raise GameJobError("replay_integrity_error", "Published export failed integrity checks") from error


def export_game_player(
    artifact_dir: Path,
    target: str,
    output_root: Path,
    unity_bin: Path,
    timeout_s: int = DEFAULT_EXPORT_TIMEOUT_SECONDS,
) -> GameExportResult:
    """Build, check, and atomically publish one playable build of an accepted project."""
    _validate_request(target, timeout_s)
    job_started = time.monotonic()
    deadline = job_started + timeout_s
    source = verify_replay_and_load_result(artifact_dir)
    source_project = source.artifact_dir / "project"
    product_name = _scene_id(source_project)
    executable = _resolve_unity_executable(unity_bin)
    executable_hash = sha256_file(executable)
    if not EXPORTER_SOURCE.is_file() or EXPORTER_SOURCE.is_symlink():
        raise GameJobError("template_unavailable", "Pinned player exporter is unavailable")
    exporter_hash = sha256_file(EXPORTER_SOURCE)
    fingerprint = export_fingerprint(
        source.fingerprint,
        source.artifact_hashes["project_tree_sha256"],
        target,
        executable_hash,
        exporter_hash,
    )
    root = _prepare_output_root(output_root)
    destination = root / fingerprint

    with fingerprint_lock(root, fingerprint, deadline):
        if destination.exists() or destination.is_symlink():
            return verify_export_replay(destination)

        staging = create_job_staging(root, fingerprint)
        try:
            work = staging / "work"
            shutil.copytree(source_project, work)
            exporter_dir = work / "Assets" / "BookProduction" / "Editor" / "Export"
            exporter_dir.mkdir(parents=True)
            shutil.copyfile(EXPORTER_SOURCE, exporter_dir / EXPORTER_SOURCE.name)
            player = staging / "player"
            player.mkdir()
            private_root = work / ".book-production"
            private_root.mkdir(exist_ok=True)
            _durable_write(
                private_root / "export-input.json",
                json.dumps(
                    {"target": target, "output_dir": str(player.resolve()), "product_name": product_name}
                ).encode("utf-8"),
            )

            version, unity_seconds = _run_unity_export(executable, work, target, deadline)
            report: dict[str, Any] = _load_limited_json(
                private_root / "export-report.json", MAX_EXPORT_REPORT_BYTES, "unity_export_failed"
            )
            if sha256_file(executable) != executable_hash:
                raise GameJobError("unsupported_unity_version", "Unity executable changed during the export")
            shutil.rmtree(work)
            _remove_do_not_ship(player)
            entrypoint = _entrypoint(player, target, product_name)
            tree_hash, total, files = _player_index(player)

            manifest = {
                "schema_version": EXPORT_MANIFEST_SCHEMA,
                "status": "accepted",
                "fingerprint": fingerprint,
                "exporter_version": EXPORTER_VERSION,
                "exporter_sha256": exporter_hash,
                "target": target,
                "product_name": product_name,
                "source_fingerprint": source.fingerprint,
                "source_hashes": source.artifact_hashes,
                "unity_version": version,
                "unity_executable_sha256": executable_hash,
                "build_report": {
                    "result": report.get("result"),
                    "total_errors": report.get("total_errors"),
                    "total_warnings": report.get("total_warnings"),
                    "scenes": report.get("scenes"),
                },
                "player": {
                    "entrypoint": entrypoint,
                    "tree_sha256": tree_hash,
                    "bytes": total,
                    "files": files,
                },
                "timing": {
                    "unity_seconds": round(unity_seconds, 3),
                    "job_seconds": round(time.monotonic() - job_started, 3),
                    "timeout_s": timeout_s,
                },
                "model_calls": 0,
            }
            manifest_path = staging / "export-manifest.json"
            _durable_write(manifest_path, pretty_json_bytes(manifest))
            hashes = {"player_tree_sha256": tree_hash, "export_manifest_sha256": sha256_file(manifest_path)}
            _durable_write(
                staging / ".receipt.json",
                pretty_json_bytes(
                    {"schema_version": EXPORT_RECEIPT_SCHEMA, "fingerprint": fingerprint, "hashes": hashes}
                ),
            )
            atomic_publish(staging, destination)
            result = GameExportResult(
                "accepted",
                fingerprint,
                target,
                destination,
                entrypoint,
                source.fingerprint,
                hashes,
                False,
                0,
            )
            return replace(result, artifact_dir=destination.resolve(strict=True))
        except Exception:
            remove_only_job_staging(staging)
            raise


__all__ = [
    "DEFAULT_EXPORT_TIMEOUT_SECONDS",
    "EXPORTER_VERSION",
    "EXPORT_TARGETS",
    "GameExportResult",
    "export_fingerprint",
    "export_game_player",
    "verify_export_replay",
]
