"""Local audio assets for GameIR v2: verify, probe, and stage into the Unity project.

Every asset must be a regular file directly inside the caller's asset root,
match its pinned sha256, stay within the size and duration caps, and probe
as WAV or Ogg with ffprobe before Unity sees it. Staged files are named by
asset id and get a deterministic ``.meta`` GUID so the generated scene
references the same clip on every rebuild.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from book_production_platform.game_ir import (
    UNITY_AUDIO_ROOT,
    AssetIR,
    GameIR,
    unity_asset_path,
)

from .errors import GameJobError

MAX_AUDIO_BYTES = 20 * 1024 * 1024
MAX_AUDIO_SECONDS = 600.0
PROBE_TIMEOUT_SECONDS = 30
AUDIO_FORMATS = {".wav": "wav", ".ogg": "ogg"}
AUDIO_FOLDER_GUID = "b0a4d10f5e2c4a7e9d3b6c8f1a2e4d60"


@dataclass(frozen=True)
class ResolvedAsset:
    asset: AssetIR
    source: Path
    bytes: int
    format: str
    duration_seconds: float

    def manifest_entry(self) -> dict[str, Any]:
        return {
            "asset_id": self.asset.asset_id,
            "kind": self.asset.kind,
            "filename": self.asset.filename,
            "sha256": self.asset.sha256,
            "bytes": self.bytes,
            "format": self.format,
            "duration_seconds": round(self.duration_seconds, 3),
            "path": "project/" + unity_asset_path(self.asset),
        }


Prober = Callable[[Path], tuple[str, float]]


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _ffprobe_bin() -> str:
    configured = os.environ.get("FFPROBE_BIN")
    found = configured if configured else shutil.which("ffprobe")
    if not found:
        raise GameJobError("asset_probe_unavailable", "ffprobe is required to check game audio")
    return found


def ffprobe_audio(path: Path) -> tuple[str, float]:
    """Return (container format, duration seconds) as reported by ffprobe."""
    try:
        completed = subprocess.run(
            [_ffprobe_bin(), "-v", "error", "-show_entries", "format=format_name,duration", "-of", "json", str(path)],
            capture_output=True, text=True, timeout=PROBE_TIMEOUT_SECONDS, check=False,
        )
    except subprocess.TimeoutExpired as error:
        raise GameJobError("asset_format_invalid", "Audio probe timed out") from error
    except OSError as error:
        raise GameJobError("asset_probe_unavailable", "ffprobe could not start") from error
    if completed.returncode != 0:
        raise GameJobError("asset_format_invalid", "Audio file could not be decoded")
    try:
        info = json.loads(completed.stdout)["format"]
        return str(info["format_name"]), float(info["duration"])
    except (ValueError, KeyError, TypeError) as error:
        raise GameJobError("asset_format_invalid", "Audio probe returned no format") from error


def resolve_assets(game_ir: GameIR, asset_root: Path | None, probe: Prober | None = None) -> list[ResolvedAsset]:
    """Verify every declared asset before any Unity work starts (REQ-GAM-10/11)."""
    probe = probe or ffprobe_audio
    if not game_ir.assets:
        return []
    if asset_root is None:
        raise GameJobError("asset_root_required", "GameIR v2 assets need --asset-root")
    requested = Path(asset_root).expanduser()
    if requested.is_symlink():
        raise GameJobError("unsafe_asset_path", "Asset root cannot be a symlink")
    try:
        root = requested.resolve(strict=True)
    except (OSError, RuntimeError) as error:
        raise GameJobError("asset_unavailable", "Asset root is unavailable") from error
    if not root.is_dir():
        raise GameJobError("asset_unavailable", "Asset root must be a directory")

    resolved: list[ResolvedAsset] = []
    for asset in game_ir.assets:
        candidate = root / asset.filename
        if candidate.is_symlink():
            raise GameJobError("unsafe_asset_path", f"Asset {asset.asset_id} cannot be a symlink")
        try:
            source = candidate.resolve(strict=True)
        except (OSError, RuntimeError) as error:
            raise GameJobError("asset_unavailable", f"Asset {asset.asset_id} is missing") from error
        if source.parent != root or not source.is_file():
            raise GameJobError("unsafe_asset_path", f"Asset {asset.asset_id} must be a file in the asset root")
        size = source.stat().st_size
        if size > MAX_AUDIO_BYTES:
            raise GameJobError("asset_too_large", f"Asset {asset.asset_id} exceeds {MAX_AUDIO_BYTES} bytes")
        if _sha256_file(source) != asset.sha256:
            raise GameJobError("asset_hash_mismatch", f"Asset {asset.asset_id} does not match its sha256")
        expected = AUDIO_FORMATS[source.suffix.lower()]
        format_name, duration = probe(source)
        if expected not in format_name.split(","):
            raise GameJobError("asset_format_invalid", f"Asset {asset.asset_id} is not {expected}")
        if not 0 < duration <= MAX_AUDIO_SECONDS:
            raise GameJobError("asset_too_large", f"Asset {asset.asset_id} must be 0-{MAX_AUDIO_SECONDS:g} seconds")
        resolved.append(ResolvedAsset(asset, source, size, expected, duration))
    return resolved


def asset_guid(asset: AssetIR) -> str:
    return hashlib.sha256(f"book-production-audio:{asset.asset_id}:{asset.sha256}".encode()).hexdigest()[:32]


def _meta(guid: str, importer: str, folder: bool = False) -> str:
    lines = ["fileFormatVersion: 2", f"guid: {guid}"]
    if folder:
        lines.append("folderAsset: yes")
    lines += [f"{importer}:", "  externalObjects: {}", "  userData: ", "  assetBundleName: ", "  assetBundleVariant: ", ""]
    return "\n".join(lines)


def stage_assets(project: Path, resolved: list[ResolvedAsset]) -> None:
    """Copy verified assets into the staged project with stable metas."""
    if not resolved:
        return
    folder = project / UNITY_AUDIO_ROOT
    try:
        folder.mkdir(parents=True, exist_ok=False)
        Path(str(folder) + ".meta").write_text(_meta(AUDIO_FOLDER_GUID, "DefaultImporter", folder=True), encoding="utf-8")
        for item in resolved:
            target = project / unity_asset_path(item.asset)
            shutil.copyfile(item.source, target)
            if _sha256_file(target) != item.asset.sha256:
                raise GameJobError("asset_hash_mismatch", f"Asset {item.asset.asset_id} changed while staging")
            Path(str(target) + ".meta").write_text(_meta(asset_guid(item.asset), "AudioImporter"), encoding="utf-8")
    except FileExistsError as error:
        raise GameJobError("template_unavailable", "Unity template already contains an audio folder") from error
    except OSError as error:
        raise GameJobError("asset_unavailable", "Audio assets could not be staged") from error


def verify_published_assets(project: Path, game_ir: GameIR, manifest_assets: object) -> None:
    """Replay check: every published asset is present, unchanged, and indexed in the manifest."""
    if not game_ir.assets:
        if manifest_assets is not None:
            raise GameJobError("replay_integrity_error", "Published manifest lists unexpected assets")
        return
    if not isinstance(manifest_assets, list) or len(manifest_assets) != len(game_ir.assets):
        raise GameJobError("replay_integrity_error", "Published manifest asset index is invalid")
    indexed = {item.get("asset_id"): item for item in manifest_assets if isinstance(item, dict)}
    for asset in game_ir.assets:
        path = project / unity_asset_path(asset)
        entry = indexed.get(asset.asset_id)
        if (
            entry is None
            or path.is_symlink()
            or not path.is_file()
            or entry.get("sha256") != asset.sha256
            or entry.get("bytes") != path.stat().st_size
            or _sha256_file(path) != asset.sha256
        ):
            raise GameJobError("replay_integrity_error", f"Published asset {asset.asset_id} failed integrity checks")


__all__ = [
    "MAX_AUDIO_BYTES",
    "MAX_AUDIO_SECONDS",
    "ResolvedAsset",
    "asset_guid",
    "ffprobe_audio",
    "resolve_assets",
    "stage_assets",
    "verify_published_assets",
]
