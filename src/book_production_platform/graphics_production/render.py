"""Editable SVG and local PNG preview renderers for GraphicIR.

SVG for a ``graphic-ir.v1`` IR is unchanged from V1A. ``graphic-ir.v2`` adds
gradient paints in ``<defs>``, paths and ellipses, styled text, and one
``<g id=...>`` per group, which editors such as Affinity import as named
layers. PNG previews come from a local Chromium-family browser or, when none
is installed (or ``GRAPHICS_PRODUCTION_RASTERIZER=resvg``), from the optional
``resvg-py`` package; both shape complex scripts such as Thai.
"""

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
    GroupNode,
    ImageNode,
    LinearGradient,
    RadialGradient,
    ShapeNode,
    TextNode,
    iter_nodes,
)

from .art_styles import ART_FONTS
from .assets import AssetReferenceError, validate_local_reference
from .image_fx import ImageEffectError, process_image
from .layout import line_runs, wrap_text


class RenderError(RuntimeError):
    """Raised when a local artifact cannot be produced."""


_BROWSER_RENDER_LOCK = threading.Lock()


def _attr(value: object) -> str:
    return escape(str(value), {"\"": "&quot;"})


def _lines(node: TextNode) -> tuple[str, ...]:
    return wrap_text(node.text, node.width, node.font_size, node.char_width)


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


# Font stacks for theme and art-style fonts; editors substitute the first installed family.
BUILTIN_FONT_STACKS: dict[str, str] = {font: stack for font, (stack, _width) in ART_FONTS.items()}
_WEIGHTS = {"normal": None, "bold": "700", "black": "900"}


def _paint(value: str) -> str:
    # Paint element ids are namespaced so they never collide with node ids.
    return f"url(#paint-{value[len('paint:'):]})" if value.startswith("paint:") else value


def _gradient(paint: LinearGradient | RadialGradient) -> str:
    stops = "".join(
        f'<stop offset="{_attr(stop.offset)}" stop-color="{_attr(stop.color)}"'
        + (f' stop-opacity="{_attr(stop.opacity)}"' if stop.opacity != 1 else "")
        + " />"
        for stop in paint.stops
    )
    if isinstance(paint, LinearGradient):
        return (
            f'<linearGradient id="paint-{_attr(paint.paint_id)}" x1="{_attr(paint.x1)}" y1="{_attr(paint.y1)}" '
            f'x2="{_attr(paint.x2)}" y2="{_attr(paint.y2)}">{stops}</linearGradient>'
        )
    return f'<radialGradient id="paint-{_attr(paint.paint_id)}" cx="{_attr(paint.cx)}" cy="{_attr(paint.cy)}" r="{_attr(paint.r)}">{stops}</radialGradient>'


def _shape(node: ShapeNode, *, clip: bool = False) -> str:
    if node.kind == "line" and not clip:
        return f'<line x1="{_attr(node.x)}" y1="{_attr(node.y)}" x2="{_attr(node.x + node.width)}" y2="{_attr(node.y + node.height)}" stroke="{_attr(node.stroke or node.fill)}" stroke-width="{_attr(node.stroke_width or 1)}" />'
    style = ""
    if not clip:
        style = f' fill="{_attr(_paint(node.fill))}"'
        if node.stroke:
            style += f' stroke="{_attr(_paint(node.stroke))}" stroke-width="{_attr(node.stroke_width)}"'
            if node.kind == "path":
                style += ' stroke-linejoin="round" stroke-linecap="round"'
            if node.stroke_opacity != 1:
                style += f' stroke-opacity="{_attr(node.stroke_opacity)}"'
        if node.opacity != 1:
            style += f' opacity="{_attr(node.opacity)}"'
    if node.kind == "path":
        return f'<path id="{_attr(node.node_id)}" d="{_attr(node.d)}"{style} />'
    if node.kind == "ellipse":
        return (
            f'<ellipse id="{_attr(node.node_id)}" cx="{_attr(node.x + node.width / 2)}" cy="{_attr(node.y + node.height / 2)}" '
            f'rx="{_attr(node.width / 2)}" ry="{_attr(node.height / 2)}"{style} />'
        )
    radius = f' rx="{_attr(node.rx)}"' if node.rx else ""
    return f'<rect id="{_attr(node.node_id)}" x="{_attr(node.x)}" y="{_attr(node.y)}" width="{_attr(node.width)}" height="{_attr(node.height)}"{radius}{style} />'


def _text(node: TextNode, family: str) -> list[str]:
    anchor = {"left": "start", "center": "middle", "right": "end"}[node.align]
    x = node.x if node.align == "left" else node.x + node.width if node.align == "right" else node.x + node.width / 2
    extra = ""
    weight = _WEIGHTS[node.font_weight]
    if weight:
        extra += f' font-weight="{weight}"'
    if node.letter_spacing:
        extra += f' letter-spacing="{_attr(node.letter_spacing)}"'
    outline_width = node.stroke_width or max(1.0, round(node.font_size * 0.025, 2))
    if node.outline_only:
        extra += f' stroke="{_attr(_paint(node.color))}" stroke-width="{_attr(outline_width)}" stroke-linejoin="round"'
    elif node.stroke:
        extra += f' stroke="{_attr(_paint(node.stroke))}" stroke-width="{_attr(node.stroke_width)}" paint-order="stroke"'
    if node.opacity != 1:
        extra += f' opacity="{_attr(node.opacity)}"'
    parts: list[str] = []
    if node.blur or node.motion_blur:
        # Horizontal motion blur stretches along x only; ``blur`` softens both directions.
        parts.append(
            f'<defs><filter id="blur-{_attr(node.node_id)}" x="-30%" y="-50%" width="160%" height="200%">'
            f'<feGaussianBlur stdDeviation="{_attr(round(node.motion_blur + node.blur, 2))} {_attr(round(node.blur, 2))}" />'
            "</filter></defs>"
        )
        extra += f' filter="url(#blur-{_attr(node.node_id)})"'
    if node.rotation:
        extra += (f' transform="rotate({_attr(node.rotation)} {_attr(round(node.x + node.width / 2, 2))} '
                  f'{_attr(round(node.y + node.height / 2, 2))})"')
    fill = "none" if node.outline_only else _paint(node.color)
    parts.append(f'<text id="{_attr(node.node_id)}" x="{_attr(x)}" y="{_attr(node.y)}" text-anchor="{anchor}" dominant-baseline="text-before-edge" font-family="{_attr(family)}" font-size="{_attr(node.font_size)}" fill="{_attr(fill)}"{extra}>')
    lines = _lines(node)
    pieces = line_runs(node.text, lines, [(run.start, run.end, index) for index, run in enumerate(node.runs)]) if node.runs else None
    for index, line in enumerate(lines):
        dy = node.font_size * 1.2 if index else 0
        if pieces is None:
            parts.append(f'<tspan x="{_attr(x)}" dy="{_attr(dy)}">{_attr(line)}</tspan>')
            continue
        # Sibling tspans (never nested): the first one starts the line, the rest continue it.
        segments = pieces[index] or [("", -1)]
        for position, (segment, run_index) in enumerate(segments):
            place = f' x="{_attr(x)}" dy="{_attr(dy)}"' if position == 0 else ""
            style = ""
            if run_index >= 0:
                run = node.runs[run_index]
                if run.font_weight:
                    style += f' font-weight="{_WEIGHTS[run.font_weight] or "400"}"'
                color = run.color or node.color
                if run.outline:
                    style += f' fill="none" stroke="{_attr(_paint(color))}" stroke-width="{_attr(outline_width)}" stroke-linejoin="round"'
                elif run.color:
                    style += f' fill="{_attr(_paint(run.color))}"'
            parts.append(f"<tspan{place}{style}>{_attr(segment)}</tspan>")
    parts.append("</text>")
    return parts


def render_svg(ir: GraphicIR, *, asset_root: str | Path | None = None, embed_fonts: bool = True) -> str:
    """Return an editable SVG with custom fonts embedded as local font data.

    ``embed_fonts=False`` names custom fonts by their own family name instead
    (for rasterizers that load font files directly).
    """

    canvas = ir.canvas
    leaves = iter_nodes(list(ir.nodes))
    font_assets: dict[str, str] = {}
    families_by_reference: dict[str, str] = dict(BUILTIN_FONT_STACKS)
    for node in leaves:
        if isinstance(node, TextNode) and node.font_ref not in families_by_reference:
            if embed_fonts:
                family, mime, css_format, encoded = _font_asset(node.font_ref, asset_root)
                families_by_reference[node.font_ref] = family
                font_assets[family] = f"@font-face{{font-family:'{family}';src:url(data:{mime};base64,{encoded}) format('{css_format}');}}"
            else:
                families_by_reference[node.font_ref] = _font_family_name(node.font_ref, asset_root)
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{canvas.width}" height="{canvas.height}" viewBox="0 0 {canvas.width} {canvas.height}">',
        f"<title>{_attr(ir.template_ref)}</title>",
        f"<desc>{_attr(' '.join(node.text for node in leaves if isinstance(node, TextNode)))}</desc>",
    ]
    defs = [_gradient(paint) for paint in ir.paints]
    if font_assets:
        defs.insert(0, "<style>" + "".join(font_assets[family] for family in sorted(font_assets)) + "</style>")
    if defs:
        parts.append("<defs>" + "".join(defs) + "</defs>")

    def emit(nodes: list[TextNode | ShapeNode | ImageNode | GroupNode]) -> None:
        for node in nodes:
            if isinstance(node, GroupNode):
                attrs = f' id="{_attr(node.node_id)}"'
                if node.opacity != 1:
                    attrs += f' opacity="{_attr(node.opacity)}"'
                if node.offset_x or node.offset_y:
                    attrs += f' transform="translate({_attr(node.offset_x)} {_attr(node.offset_y)})"'
                if node.clip:
                    parts.append(f'<clipPath id="{_attr(node.node_id)}-clip">' + "".join(_shape(shape, clip=True) for shape in node.clip) + "</clipPath>")
                    attrs += f' clip-path="url(#{_attr(node.node_id)}-clip)"'
                parts.append(f"<g{attrs}>")
                emit(list(node.children))
                parts.append("</g>")
            elif isinstance(node, ShapeNode):
                parts.append(_shape(node))
            elif isinstance(node, TextNode):
                parts.extend(_text(node, families_by_reference[node.font_ref]))
            else:
                parts.append(_image(node))

    emit(list(ir.nodes))
    parts.append("</svg>")
    return "".join(parts)


def _image(node: ImageNode) -> str:
    """An ``<image>``; photos with effects are baked to a PNG of exactly the node box."""

    if node.effects is not None:
        try:
            data = process_image(node.source_ref, node.effects, node.width, node.height)
        except ImageEffectError as exc:
            raise RenderError(f"{exc}:{node.asset_key}") from exc
        mime, encoded, aspect = "image/png", base64.b64encode(data).decode("ascii"), "none"
    else:
        mime, encoded = _image_data(node)
        aspect = "xMidYMid meet"
    extra = ""
    if node.opacity != 1:
        extra += f' opacity="{_attr(node.opacity)}"'
    if node.rotation:
        cx, cy = node.x + node.width / 2, node.y + node.height / 2
        extra += f' transform="rotate({_attr(node.rotation)} {_attr(round(cx, 2))} {_attr(round(cy, 2))})"'
    return (
        f'<image id="{_attr(node.node_id)}" x="{_attr(node.x)}" y="{_attr(node.y)}" width="{_attr(node.width)}" '
        f'height="{_attr(node.height)}" preserveAspectRatio="{aspect}"{extra} href="data:{mime};base64,{encoded}" />'
    )


def _font_family_name(reference: str, asset_root: str | Path | None) -> str:
    if asset_root is None:
        raise RenderError("missing_font_asset_root")
    try:
        path = validate_local_reference(reference, asset_root, field="font_ref")
        family, _style = ImageFont.truetype(str(path), 12).getname()
    except (AssetReferenceError, OSError, ValueError) as exc:
        raise RenderError("font_invalid") from exc
    return f"'{family}'"


def _font_files(ir: GraphicIR, asset_root: str | Path | None) -> list[str]:
    files: list[str] = []
    for node in iter_nodes(list(ir.nodes)):
        if isinstance(node, TextNode) and node.font_ref not in BUILTIN_FONT_STACKS and asset_root is not None:
            try:
                path = str(validate_local_reference(node.font_ref, asset_root, field="font_ref"))
            except AssetReferenceError as exc:
                raise RenderError("invalid_font_reference") from exc
            if path not in files:
                files.append(path)
    return files


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


def _resvg_available() -> bool:
    try:
        import resvg_py  # type: ignore[import-not-found,import-untyped,unused-ignore]  # noqa: F401
    except ImportError:
        return False
    return True


def rasterizer() -> str:
    """``browser`` or ``resvg``: which local engine ``render_png`` uses on this host."""

    choice = os.environ.get("GRAPHICS_PRODUCTION_RASTERIZER", "auto").strip().lower()
    if choice == "resvg":
        if not _resvg_available():
            raise RenderError("resvg_not_installed")
        return "resvg"
    if choice == "browser" or _find_browser() is not None:
        return "browser"
    if _resvg_available():
        return "resvg"
    raise RenderError("browser_not_found")


def rasterize_svg(svg: str, *, width: int, font_files: list[str] | None = None) -> bytes:
    """Rasterize SVG text with resvg at ``width`` pixels (height keeps the aspect ratio)."""

    import resvg_py  # type: ignore[import-not-found,import-untyped,unused-ignore]

    extra = [part for part in os.environ.get("GRAPHICS_PRODUCTION_FONT_DIRS", "").split(os.pathsep) if part]
    candidates = [*extra, "/usr/share/fonts", "/usr/local/share/fonts", str(Path.home() / ".fonts"), str(Path.home() / ".local/share/fonts")]
    font_dirs = [path for path in candidates if Path(path).is_dir()]
    try:
        fallback = os.environ.get("GRAPHICS_PRODUCTION_FALLBACK_FONT", "").strip() or None
        data = bytes(resvg_py.svg_to_bytes(
            svg_string=svg, width=width, font_files=font_files or [], font_dirs=font_dirs,
            font_family=fallback, serif_family=fallback, sans_serif_family=fallback, monospace_family=fallback,
        ))
    except Exception as exc:
        raise RenderError("resvg_render_failed") from exc
    try:
        with Image.open(io.BytesIO(data)) as rendered:
            rendered.verify()
    except (OSError, ValueError) as exc:
        raise RenderError("resvg_render_failed") from exc
    return data


def render_png(ir: GraphicIR, *, asset_root: str | Path | None = None) -> bytes:
    """Rasterize the SVG through a local engine with complex-script shaping (see :func:`rasterizer`)."""

    if rasterizer() == "resvg":
        svg = render_svg(ir, asset_root=asset_root, embed_fonts=False)
        return rasterize_svg(svg, width=ir.canvas.width, font_files=_font_files(ir, asset_root))
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
                import sys as _s; print("DIAG pid", os.getpid(), "rc", result.returncode, "png", png_path.is_file(), "err", result.stderr.decode(errors="replace")[-600:], "out", result.stdout.decode(errors="replace")[-300:], file=_s.stderr, flush=True)
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


__all__ = ["BUILTIN_FONT_STACKS", "RenderError", "rasterize_svg", "rasterizer", "render_png", "render_svg"]
