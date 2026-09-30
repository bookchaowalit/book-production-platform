"""Print export for accepted graphics batches (high-resolution PNG + PDF).

``export_batch`` reads a ``graphics-batch.v1`` manifest, re-verifies every
accepted SVG against its recorded hash, re-renders it through the local
browser at ``scale`` times the canvas size, and writes a PNG plus a
single-page PDF that carries the requested DPI (a 1080 px canvas at scale 3
and 300 DPI prints at about 27.4 cm). A ``graphics-export.v1`` manifest
records every output with its hash and the source SVG hash. Nothing is
written for an item unless all of its outputs succeed.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from PIL import Image

from .render import RenderError, _find_browser

EXPORT_RECIPE = "graphics-print-export.v1"
MIN_SCALE, MAX_SCALE = 1, 6
MIN_DPI, MAX_DPI = 72, 1200


class ExportError(ValueError):
    """Raised when a batch manifest cannot be exported."""


@dataclass(frozen=True)
class ExportResult:
    manifest_path: Path
    items: int


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _render_scaled(svg_path: Path, width: int, height: int, scale: int) -> bytes:
    browser = _find_browser()
    if browser is None:
        raise RenderError("browser_not_found")
    profile = Path(tempfile.mkdtemp(prefix="graphics-export-profile-"))
    try:
        with tempfile.TemporaryDirectory(prefix="graphics-export-") as temporary:
            png_path = Path(temporary) / "render.png"
            command = [
                browser, "--headless", "--disable-gpu", "--disable-background-networking", "--disable-extensions",
                "--disable-sync", "--no-first-run", "--hide-scrollbars", f"--force-device-scale-factor={scale}",
                f"--window-size={width},{height}", f"--user-data-dir={profile}", f"--screenshot={png_path}",
                svg_path.resolve().as_uri(),
            ]
            try:
                result = subprocess.run(command, capture_output=True, check=False, timeout=120)
            except (OSError, subprocess.TimeoutExpired) as exc:
                raise RenderError("browser_render_failed") from exc
            if result.returncode != 0:
                raise RenderError("browser_render_failed")
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                if png_path.is_file() and png_path.stat().st_size > 0:
                    return png_path.read_bytes()
                time.sleep(0.1)
            raise RenderError("browser_render_failed")
    finally:
        shutil.rmtree(profile, ignore_errors=True)


def _pdf_bytes(png: bytes, dpi: int) -> bytes:
    with Image.open(io.BytesIO(png)) as image:
        buffer = io.BytesIO()
        image.convert("RGB").save(buffer, format="PDF", resolution=float(dpi))
        return buffer.getvalue()


def _atomic_write(path: Path, data: bytes) -> None:
    staging = path.with_name(f".{path.name}.partial")
    staging.write_bytes(data)
    os.replace(staging, path)


def _artifact_path(batch_root: Path, filename: str) -> Path:
    """Resolve a manifest artifact name (``<batch_id>/<file>`` or a legacy bare name) safely."""

    parts = PurePosixPath(filename).parts
    if not 1 <= len(parts) <= 2 or PurePosixPath(filename).is_absolute() or any(
        part in {"", ".", ".."} or "\\" in part or ":" in part for part in parts
    ):
        raise ExportError(f"{filename}: unsafe artifact path in the batch manifest")
    return batch_root.joinpath(*parts)


def export_batch(manifest_path: str | Path, output_dir: str | Path, *, scale: int = 3, dpi: int = 300) -> ExportResult:
    if not MIN_SCALE <= scale <= MAX_SCALE:
        raise ExportError(f"scale must be between {MIN_SCALE} and {MAX_SCALE}")
    if not MIN_DPI <= dpi <= MAX_DPI:
        raise ExportError(f"dpi must be between {MIN_DPI} and {MAX_DPI}")
    source = Path(manifest_path)
    try:
        manifest_bytes = source.read_bytes()
        manifest: dict[str, Any] = json.loads(manifest_bytes)
    except (OSError, json.JSONDecodeError) as exc:
        raise ExportError(f"cannot read batch manifest: {exc}") from exc
    if manifest.get("schema_version") != "graphics-batch.v1" or manifest.get("status") not in {"accepted", "partial"}:
        raise ExportError("expected an accepted or partial graphics-batch.v1 manifest")

    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    exported: list[dict[str, Any]] = []
    for item in manifest.get("items", []):
        if item.get("status") != "accepted":
            continue
        svg = next((artifact for artifact in item.get("artifacts", []) if artifact.get("media_type") == "image/svg+xml"), None)
        if svg is None:
            raise ExportError(f"{item.get('request_id')}: no SVG artifact to export")
        name = str(svg["filename"])
        svg_path = _artifact_path(source.parent, name)
        if not svg_path.is_file() or _sha256(svg_path.read_bytes()) != svg.get("content_hash"):
            raise ExportError(f"{name}: SVG is missing or does not match the batch manifest")
        width, height = int(svg["width"]), int(svg["height"])
        png = _render_scaled(svg_path, width, height, scale)
        with Image.open(io.BytesIO(png)) as image:
            actual = image.size
        if actual != (width * scale, height * scale):
            raise ExportError(f"{name}: rendered {actual[0]}x{actual[1]}, expected {width * scale}x{height * scale}")
        pdf = _pdf_bytes(png, dpi)
        stem = Path(name).stem
        outputs = [(f"{stem}@{scale}x.png", "image/png", png), (f"{stem}-print.pdf", "application/pdf", pdf)]
        for filename, _, data in outputs:
            _atomic_write(out / filename, data)
        exported.append({
            "request_id": item.get("request_id"),
            "source_svg": {"filename": name, "content_hash": svg["content_hash"]},
            "artifacts": [
                {"filename": filename, "media_type": media_type, "content_hash": _sha256(data), "byte_size": len(data),
                 "width": width * scale, "height": height * scale, "dpi": dpi,
                 "print_size_mm": [round(width * scale / dpi * 25.4, 1), round(height * scale / dpi * 25.4, 1)]}
                for filename, media_type, data in outputs
            ],
        })
    if not exported:
        raise ExportError("the batch has no accepted items to export")
    source_hash = _sha256(manifest_bytes)
    export_manifest = {
        "schema_version": "graphics-export.v1",
        "export_id": f"{manifest.get('batch_id', 'batch')}-x{scale}-{dpi}dpi",
        "status": "accepted",
        "recipe": EXPORT_RECIPE,
        "scale": scale,
        "dpi": dpi,
        "source_manifest": {"filename": source.name, "content_hash": source_hash, "batch_id": manifest.get("batch_id")},
        "items": exported,
        "model_calls": 0,
    }
    manifest_out = out / f"export-{source_hash[:12]}-x{scale}-{dpi}.json"
    _atomic_write(manifest_out, json.dumps(export_manifest, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8"))
    return ExportResult(manifest_out, len(exported))


__all__ = ["ExportError", "ExportResult", "export_batch"]
