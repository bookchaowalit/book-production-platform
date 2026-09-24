"""Editable SVG and local PNG preview renderers for GraphicIR."""

from __future__ import annotations

import base64
import hashlib
import io
import mimetypes
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from xml.sax.saxutils import escape

from PIL import Image, ImageFont

from book_production_platform.graphics_ir import (
    GraphicIR,
    ImageNode,
    ShapeNode,
    TextNode,
)

from .assets import AssetReferenceError, validate_local_reference
from .layout import wrap_text


class RenderError(RuntimeError):
    """Raised when a local artifact cannot be produced."""


_BROWSER_RENDER_LOCK = threading.Lock()


def _attr(value: object) -> str:
    return escape(str(value), {"\"": "&quot;"})


def _lines(node: TextNode) -> tuple[str, ...]:
    return wrap_text(node.text, node.width, node.font_size)


def _image_data(node: ImageNode) -> tuple[str, str]:
    path = Path(node.source_ref)
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise RenderError(f"image_read_failed:{node.asset_key}") from exc
    mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    if mime not in {"image/png", "image/jpeg", "image/webp", "image/gif", "image/svg+xml"}:
        raise RenderError(f"unsupported_image_type:{node.asset_key}")
    if mime == "image/svg+xml":
        text = data.decode("utf-8", errors="replace").lower()
        if "<script" in text or re.search(
            r"(?:href|xlink:href|src)\s*=\s*[\"']\s*(?:https?:|file:|//)"
            r"|url\(\s*(?:https?:|file:|//)",
            text,
        ):
            raise RenderError("unsafe_svg")
    return mime, base64.b64encode(data).decode("ascii")


def _font_asset(reference: str, asset_root: str | Path | None) -> tuple[str, str, str, str]:
    if asset_root is None:
        raise RenderError("missing_font_asset_root")
    try:
        path = validate_local_reference(reference, asset_root, field="font_ref")
    except AssetReferenceError as exc:
        raise RenderError("invalid_font_reference") from exc
    suffix = path.suffix.lower()
    formats = {".ttf": ("font/ttf", "truetype"), ".otf": ("font/otf", "opentype")}
    if suffix not in formats:
        raise RenderError("unsupported_font_type")
    try:
        data = path.read_bytes()
        ImageFont.truetype(str(path), 12)
    except (OSError, ValueError) as exc:
        raise RenderError("font_invalid") from exc
    mime, css_format = formats[suffix]
    family = f"graphics-font-{hashlib.sha256(data).hexdigest()[:16]}"
    encoded = base64.b64encode(data).decode("ascii")
    return family, mime, css_format, encoded


def render_svg(ir: GraphicIR, *, asset_root: str | Path | None = None) -> str:
    """Return an editable SVG with custom fonts embedded as local font data."""

    canvas = ir.canvas
    font_assets: dict[str, str] = {}
    families_by_reference: dict[str, str] = {}
    for node in ir.nodes:
        if isinstance(node, TextNode) and node.font_ref != "builtin:sans" and node.font_ref not in families_by_reference:
            family, mime, css_format, encoded = _font_asset(node.font_ref, asset_root)
            families_by_reference[node.font_ref] = family
            font_assets[family] = f"@font-face{{font-family:'{family}';src:url(data:{mime};base64,{encoded}) format('{css_format}');}}"
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{canvas.width}" height="{canvas.height}" viewBox="0 0 {canvas.width} {canvas.height}">',
        f"<title>{_attr(ir.template_ref)}</title>",
        f"<desc>{_attr(' '.join(node.text for node in ir.nodes if isinstance(node, TextNode)))}</desc>",
    ]
    if font_assets:
        font_faces = "".join(font_assets[family] for family in sorted(font_assets))
        parts.append(f"<defs><style>{font_faces}</style></defs>")
    for node in ir.nodes:
        if isinstance(node, ShapeNode):
            if node.kind == "line":
                parts.append(f'<line x1="{_attr(node.x)}" y1="{_attr(node.y)}" x2="{_attr(node.x + node.width)}" y2="{_attr(node.y + node.height)}" stroke="{_attr(node.stroke or node.fill)}" stroke-width="{_attr(node.stroke_width or 1)}" />')
            else:
                stroke = f' stroke="{_attr(node.stroke)}" stroke-width="{_attr(node.stroke_width)}"' if node.stroke else ""
                parts.append(f'<rect id="{_attr(node.node_id)}" x="{_attr(node.x)}" y="{_attr(node.y)}" width="{_attr(node.width)}" height="{_attr(node.height)}" fill="{_attr(node.fill)}"{stroke} />')
        elif isinstance(node, TextNode):
            anchor = {"left": "start", "center": "middle", "right": "end"}[node.align]
            x = node.x if node.align == "left" else node.x + node.width if node.align == "right" else node.x + node.width / 2
            family = "Arial, sans-serif" if node.font_ref == "builtin:sans" else families_by_reference[node.font_ref]
            parts.append(f'<text id="{_attr(node.node_id)}" x="{_attr(x)}" y="{_attr(node.y)}" text-anchor="{anchor}" dominant-baseline="text-before-edge" font-family="{_attr(family)}" font-size="{_attr(node.font_size)}" fill="{_attr(node.color)}">')
            for index, line in enumerate(_lines(node)):
                dy = node.font_size * 1.2 if index else 0
                parts.append(f'<tspan x="{_attr(x)}" dy="{_attr(dy)}">{_attr(line)}</tspan>')
            parts.append("</text>")
        else:
            mime, encoded = _image_data(node)
            parts.append(f'<image id="{_attr(node.node_id)}" x="{_attr(node.x)}" y="{_attr(node.y)}" width="{_attr(node.width)}" height="{_attr(node.height)}" preserveAspectRatio="xMidYMid meet" href="data:{mime};base64,{encoded}" />')
    parts.append("</svg>")
    return "".join(parts)


def _find_browser() -> str | None:
    configured = os.environ.get("GRAPHICS_PRODUCTION_BROWSER")
    if configured:
        return shutil.which(configured) or (configured if Path(configured).is_file() else None)
    for name in ("msedge", "msedge.exe", "google-chrome", "chrome", "chromium", "chromium-browser"):
        found = shutil.which(name)
        if found:
            return found
    if os.name == "nt":
        candidates = (
            Path(os.environ.get("PROGRAMFILES(X86)", "C:/Program Files (x86)")) / "Microsoft/Edge/Application/msedge.exe",
            Path(os.environ.get("PROGRAMFILES", "C:/Program Files")) / "Microsoft/Edge/Application/msedge.exe",
            Path(os.environ.get("PROGRAMFILES", "C:/Program Files")) / "Google/Chrome/Application/chrome.exe",
            Path(os.environ.get("PROGRAMFILES(X86)", "C:/Program Files (x86)")) / "Google/Chrome/Application/chrome.exe",
        )
        return next((str(path) for path in candidates if path.is_file()), None)
    return None


def _browser_profile() -> Path:
    configured = os.environ.get("GRAPHICS_PRODUCTION_BROWSER_PROFILE")
    profile = Path(configured) if configured else Path(tempfile.gettempdir()) / "book-graphics-production-browser-profile"
    try:
        profile.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise RenderError("browser_profile_unavailable") from exc
    return profile


def render_png(ir: GraphicIR, *, asset_root: str | Path | None = None) -> bytes:
    """Rasterize the SVG through a local browser engine with complex-script shaping."""

    browser = _find_browser()
    if browser is None:
        raise RenderError("browser_not_found")
    canvas = ir.canvas
    with tempfile.TemporaryDirectory(prefix="graphics-production-") as temporary:
        root = Path(temporary)
        svg_path = root / "render.svg"
        png_path = root / "render.png"
        svg_path.write_text(render_svg(ir, asset_root=asset_root), encoding="utf-8")
        command = [
            browser,
            "--headless",
            "--disable-gpu",
            "--disable-background-networking",
            "--disable-extensions",
            "--disable-sync",
            "--disable-background-mode",
            "--disable-breakpad",
            "--disable-crash-reporter",
            "--no-first-run",
            "--hide-scrollbars",
            "--force-device-scale-factor=1",
            f"--window-size={canvas.width},{canvas.height}",
            f"--user-data-dir={_browser_profile()}",
            f"--screenshot={png_path}",
            svg_path.as_uri(),
        ]
        try:
            with _BROWSER_RENDER_LOCK:
                result = subprocess.run(command, capture_output=True, check=False, timeout=60)
                if result.returncode != 0:
                    raise RenderError("browser_render_failed")
                deadline = time.monotonic() + 30
                while time.monotonic() < deadline:
                    if png_path.is_file():
                        try:
                            data = png_path.read_bytes()
                            with Image.open(io.BytesIO(data)) as rendered:
                                rendered.verify()
                            return data
                        except (OSError, ValueError):
                            pass
                    time.sleep(0.1)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise RenderError("browser_render_failed") from exc
        raise RenderError("browser_render_failed")


__all__ = ["RenderError", "render_png", "render_svg"]
