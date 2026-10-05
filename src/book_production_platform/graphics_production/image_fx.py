"""Local photo treatment for placed images: crop, cut out, shape, filter, outline, shadow.

Everything runs on this machine with Pillow and is baked into one PNG at the
placed box size, so the browser preview, resvg, and editors such as Affinity
all show the same pixels. Order of work:

1. ``cutout`` — remove the background: ``plain`` flood-fills a plain/studio
   backdrop from the edges (built in); ``ai`` uses the optional ``rembg`` model
   (``cutout`` extra; ``GRAPHICS_PRODUCTION_CUTOUT_MODEL``, default ``u2net``).
   The picture is then trimmed to the subject.
2. ``fit`` — ``cover`` fills the box and crops around ``focus_x``/``focus_y``
   (``zoom`` crops tighter); ``contain`` keeps the whole picture.
3. brightness / contrast / saturation, then ``filter``.
4. ``mask`` — rect, rounded, circle, ellipse, arch, blob, star, hexagon, torn.
5. ``outline_width`` (sticker edge around the visible shape) and ``shadow``.

No network access: the rembg model must already be in its local cache.
"""

from __future__ import annotations

import hashlib
import importlib.util
import io
import math
import os
import random
import threading
from functools import lru_cache
from pathlib import Path
from typing import Any

from PIL import Image, ImageChops, ImageDraw, ImageEnhance, ImageFilter, ImageOps

from book_production_platform.graphics_ir import ResolvedImageEffects

MAX_SIDE = 2400
SVG_SUFFIXES = {".svg"}
_SESSION_LOCK = threading.Lock()
_SESSIONS: dict[str, Any] = {}


class ImageEffectError(RuntimeError):
    """Raised when a photo cannot be processed (unreadable, vector input, or a failed cut-out)."""


def ai_cutout_available() -> bool:
    """True when the optional rembg model package (and its ONNX runtime) is installed."""

    return importlib.util.find_spec("rembg") is not None and importlib.util.find_spec("onnxruntime") is not None


def _hex(color: str | None, default: str) -> tuple[int, int, int]:
    value = (color or default).lstrip("#")
    return int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16)


# --- 1. cut-out ---------------------------------------------------------------------------------


def _plain_cutout(image: Image.Image, tolerance: int) -> Image.Image:
    """Remove a plain backdrop connected to the picture's edges (product shots, studio portraits)."""

    rgb = image.convert("RGB")
    width, height = rgb.size
    sentinel = (1, 254, 3)
    marked = rgb.copy()
    seeds = [(0, 0), (width - 1, 0), (0, height - 1), (width - 1, height - 1)]
    steps = 12
    seeds += [(round((width - 1) * k / steps), 0) for k in range(1, steps)]
    seeds += [(round((width - 1) * k / steps), height - 1) for k in range(1, steps)]
    seeds += [(0, round((height - 1) * k / steps)) for k in range(1, steps)]
    seeds += [(width - 1, round((height - 1) * k / steps)) for k in range(1, steps)]
    for seed in seeds:
        if marked.getpixel(seed) != sentinel:
            ImageDraw.floodfill(marked, seed, sentinel, thresh=tolerance)
    r, g, b = marked.split()
    background = ImageChops.multiply(
        ImageChops.multiply(r.point(lambda v: 255 if v == sentinel[0] else 0), g.point(lambda v: 255 if v == sentinel[1] else 0)),
        b.point(lambda v: 255 if v == sentinel[2] else 0),
    )
    alpha = ImageOps.invert(background)
    # Tidy the edge: drop a 1 px halo of backdrop color, then soften.
    alpha = alpha.filter(ImageFilter.MinFilter(3)).filter(ImageFilter.GaussianBlur(0.8))
    if image.mode == "RGBA":
        alpha = ImageChops.multiply(alpha, image.getchannel("A"))
    result = image.convert("RGBA")
    result.putalpha(alpha)
    return result


def _ai_cutout(image: Image.Image) -> Image.Image:
    try:
        from rembg import (  # type: ignore[import-not-found,import-untyped,unused-ignore]
            new_session,
            remove,
        )
    except ImportError as exc:  # pragma: no cover - guarded by ai_cutout_available()
        raise ImageEffectError("cutout_ai_unavailable") from exc
    model = os.environ.get("GRAPHICS_PRODUCTION_CUTOUT_MODEL", "u2net").strip() or "u2net"
    with _SESSION_LOCK:
        session = _SESSIONS.get(model)
        if session is None:
            try:
                session = new_session(model)
            except Exception as exc:
                raise ImageEffectError("cutout_ai_model_unavailable") from exc
            _SESSIONS[model] = session
        try:
            result = remove(image.convert("RGBA"), session=session)
        except Exception as exc:
            raise ImageEffectError("cutout_ai_failed") from exc
    if not isinstance(result, Image.Image):
        result = Image.open(io.BytesIO(result))
    converted: Image.Image = result.convert("RGBA")
    return converted


def _trim_to_subject(image: Image.Image) -> Image.Image:
    box = image.getchannel("A").point(lambda v: 255 if v > 16 else 0).getbbox()
    if box is None:
        raise ImageEffectError("cutout_removed_everything")
    return image.crop(box)


# --- 2. fit -------------------------------------------------------------------------------------


def _fit(image: Image.Image, width: int, height: int, effects: ResolvedImageEffects) -> tuple[Image.Image, tuple[int, int]]:
    """The picture resized for a ``width`` x ``height`` area and its top-left offset inside it."""

    iw, ih = image.size
    if effects.fit == "cover":
        scale = max(width / iw, height / ih) * effects.zoom
        sw, sh = max(1, round(iw * scale)), max(1, round(ih * scale))
        resized = image.resize((sw, sh), Image.Resampling.LANCZOS)
        left = round((sw - width) * effects.focus_x)
        top = round((sh - height) * effects.focus_y)
        return resized.crop((left, top, left + width, top + height)), (0, 0)
    scale = min(width / iw, height / ih) * effects.zoom
    sw, sh = max(1, round(iw * scale)), max(1, round(ih * scale))
    resized = image.resize((sw, sh), Image.Resampling.LANCZOS)
    if sw > width or sh > height:
        left = max(0, round((sw - width) * effects.focus_x))
        top = max(0, round((sh - height) * effects.focus_y))
        resized = resized.crop((left, top, left + min(sw, width), top + min(sh, height)))
        sw, sh = resized.size
    return resized, ((width - sw) // 2, (height - sh) // 2)


# --- 3. adjustments and filters -----------------------------------------------------------------


def _with_alpha(image: Image.Image, rgb: Image.Image) -> Image.Image:
    result = rgb.convert("RGBA")
    result.putalpha(image.getchannel("A"))
    return result


def _halftone(gray: Image.Image, dark: tuple[int, int, int], light: tuple[int, int, int]) -> Image.Image:
    width, height = gray.size
    cell = max(6, round(min(width, height) / 70))
    small = gray.resize((max(1, width // cell), max(1, height // cell)), Image.Resampling.BOX)
    out = Image.new("RGB", (width, height), light)
    draw = ImageDraw.Draw(out)
    for row in range(small.height):
        for column in range(small.width):
            darkness = 1 - small.getpixel((column, row)) / 255  # type: ignore[operator]
            radius = cell * 0.72 * math.sqrt(max(darkness, 0))
            if radius < 0.4:
                continue
            cx, cy = column * cell + cell / 2, row * cell + cell / 2
            draw.ellipse((cx - radius, cy - radius, cx + radius, cy + radius), fill=dark)
    return out


def _filter(image: Image.Image, effects: ResolvedImageEffects) -> Image.Image:
    rgb = image.convert("RGB")
    if effects.brightness != 1:
        rgb = ImageEnhance.Brightness(rgb).enhance(effects.brightness)
    if effects.contrast != 1:
        rgb = ImageEnhance.Contrast(rgb).enhance(effects.contrast)
    if effects.saturation != 1:
        rgb = ImageEnhance.Color(rgb).enhance(effects.saturation)
    kind = effects.filter
    dark = _hex(effects.duotone_dark, "#1A1A1A")
    light = _hex(effects.duotone_light, "#F5F0E6")
    if kind == "grayscale":
        rgb = ImageOps.grayscale(rgb).convert("RGB")
    elif kind == "sepia":
        rgb = ImageOps.colorize(ImageOps.grayscale(rgb), (43, 29, 14), (243, 227, 195))
    elif kind == "duotone":
        rgb = ImageOps.colorize(ImageOps.autocontrast(ImageOps.grayscale(rgb), cutoff=1), dark, light)
    elif kind == "posterize":
        bits = max(1, math.ceil(math.log2(effects.posterize_levels)))
        rgb = ImageOps.posterize(ImageEnhance.Color(rgb).enhance(1.3), bits)
    elif kind == "high_contrast":
        rgb = ImageEnhance.Contrast(ImageOps.autocontrast(rgb, cutoff=2)).enhance(1.6)
    elif kind == "blur":
        rgb = rgb.filter(ImageFilter.GaussianBlur(effects.blur_radius))
    elif kind == "invert":
        rgb = ImageOps.invert(rgb)
    elif kind == "vintage":
        faded = ImageEnhance.Contrast(ImageEnhance.Color(rgb).enhance(0.55)).enhance(0.88)
        warm = ImageOps.colorize(ImageOps.grayscale(faded), (40, 26, 18), (250, 236, 205))
        rgb = Image.blend(faded, warm, 0.4)
        rgb = Image.composite(rgb, Image.new("RGB", rgb.size, (30, 20, 12)), _vignette_mask(rgb.size, 0.55))
    elif kind == "halftone":
        rgb = _halftone(ImageOps.autocontrast(ImageOps.grayscale(rgb), cutoff=1), dark, light)
    return _with_alpha(image, rgb)


def _vignette_mask(size: tuple[int, int], strength: float) -> Image.Image:
    """White (keep) in the middle fading toward the edges."""

    width, height = size
    small = Image.new("L", (64, 64), 0)
    pixels = small.load()
    for y in range(64):
        for x in range(64):
            d = math.hypot((x - 31.5) / 32, (y - 31.5) / 32) / math.sqrt(2)
            pixels[x, y] = round(255 * (1 - strength * max(0.0, d - 0.35) / 0.65))  # type: ignore[index]
    return small.resize((width, height), Image.Resampling.BILINEAR)


# --- 4. masks -----------------------------------------------------------------------------------


def _mask(kind: str, width: int, height: int, radius: float, seed: str) -> Image.Image:
    scale = 2  # draw large and shrink for smooth edges
    w, h = width * scale, height * scale
    mask = Image.new("L", (w, h), 0)
    draw = ImageDraw.Draw(mask)
    rng = random.Random(int.from_bytes(hashlib.sha256(seed.encode()).digest()[:8], "big"))
    if kind == "rect":
        draw.rectangle((0, 0, w, h), fill=255)
    elif kind == "rounded":
        draw.rounded_rectangle((0, 0, w - 1, h - 1), radius=round(min(w, h) * radius), fill=255)
    elif kind == "circle":
        side = min(w, h)
        left, top = (w - side) / 2, (h - side) / 2
        draw.ellipse((left, top, left + side, top + side), fill=255)
    elif kind == "ellipse":
        draw.ellipse((0, 0, w - 1, h - 1), fill=255)
    elif kind == "arch":
        r = w / 2
        draw.rectangle((0, min(r, h), w, h), fill=255)
        draw.pieslice((0, 0, w - 1, 2 * r), 180, 360, fill=255)
    elif kind == "blob":
        cx, cy, points = w / 2, h / 2, []
        for k in range(9):
            angle = math.tau * k / 9
            jitter = 0.78 + rng.random() * 0.22
            points.append((cx + math.cos(angle) * w / 2 * jitter, cy + math.sin(angle) * h / 2 * jitter))
        draw.polygon(_smooth(points), fill=255)
    elif kind == "star":
        cx, cy, points = w / 2, h / 2, []
        for k in range(10):
            angle = -math.pi / 2 + math.pi * k / 5
            r = 1.0 if k % 2 == 0 else 0.45
            points.append((cx + math.cos(angle) * w / 2 * r, cy + math.sin(angle) * h / 2 * r))
        draw.polygon(points, fill=255)
    elif kind == "hexagon":
        cx, cy = w / 2, h / 2
        draw.polygon([(cx + math.cos(math.pi / 3 * k) * w / 2, cy + math.sin(math.pi / 3 * k) * h / 2) for k in range(6)], fill=255)
    elif kind == "torn":
        rough = min(w, h) * 0.025
        # Edges wander between 0.5x and 1.5x ``rough`` from the border, so corners are always cut.
        top_edge = [(x, rough + (rng.random() - 0.5) * rough) for x in _steps(w, rng)]
        right_edge = [(w - rough + (rng.random() - 0.5) * rough, y) for y in _steps(h, rng)]
        bottom_edge = [(x, h - rough + (rng.random() - 0.5) * rough) for x in reversed(_steps(w, rng))]
        left_edge = [(rough + (rng.random() - 0.5) * rough, y) for y in reversed(_steps(h, rng))]
        draw.polygon([*top_edge, *right_edge, *bottom_edge, *left_edge], fill=255)
    else:
        raise ImageEffectError(f"mask_unknown:{kind}")
    return mask.resize((width, height), Image.Resampling.LANCZOS)


def _steps(length: float, rng: random.Random) -> list[float]:
    values, position = [0.0], 0.0
    while position < length:
        position = min(position + length * (0.015 + rng.random() * 0.03), length)
        values.append(position)
    return values


def _smooth(points: list[tuple[float, float]], samples: int = 12) -> list[tuple[float, float]]:
    """Closed Catmull-Rom curve through ``points``, sampled as a polygon."""

    out = []
    n = len(points)
    for i in range(n):
        p0, p1, p2, p3 = points[i - 1], points[i], points[(i + 1) % n], points[(i + 2) % n]
        for step in range(samples):
            t = step / samples
            t2, t3 = t * t, t * t * t
            out.append(tuple(
                0.5 * (2 * p1[k] + (-p0[k] + p2[k]) * t + (2 * p0[k] - 5 * p1[k] + 4 * p2[k] - p3[k]) * t2
                       + (-p0[k] + 3 * p1[k] - 3 * p2[k] + p3[k]) * t3)
                for k in (0, 1)
            ))
    return out  # type: ignore[return-value]


# --- pipeline -----------------------------------------------------------------------------------


def _open(path: Path) -> Image.Image:
    if path.suffix.lower() in SVG_SUFFIXES:
        raise ImageEffectError("image_effects_need_raster_image")
    try:
        with Image.open(path) as opened:
            opened.load()
            image = ImageOps.exif_transpose(opened) or opened
            return image.convert("RGBA")
    except (OSError, ValueError) as exc:
        raise ImageEffectError("image_unreadable") from exc


def process_image(path: str | Path, effects: ResolvedImageEffects, width: float, height: float) -> bytes:
    """The treated photo as PNG bytes sized to the ``width`` x ``height`` box (cached per input)."""

    source = Path(path)
    try:
        stat = source.stat()
    except OSError as exc:
        raise ImageEffectError("image_unreadable") from exc
    return _process_cached(str(source), stat.st_mtime_ns, stat.st_size, effects.model_dump_json(), round(width), round(height))


@lru_cache(maxsize=64)
def _process_cached(path: str, mtime: int, size: int, effects_json: str, width: int, height: int) -> bytes:
    del mtime, size  # cache key only
    effects = ResolvedImageEffects.model_validate_json(effects_json)
    factor = min(1.0, MAX_SIDE / max(width, height, 1))
    width, height = max(1, round(width * factor)), max(1, round(height * factor))
    image = _open(Path(path))
    if effects.cutout == "plain":
        image = _trim_to_subject(_plain_cutout(image, effects.cutout_tolerance))
    elif effects.cutout == "ai":
        image = _trim_to_subject(_ai_cutout(image))
    if effects.flip_x:
        image = ImageOps.mirror(image)

    base = min(width, height)
    outline = round(effects.outline_width * factor)
    shadow_blur = round(base * 0.025) if effects.shadow else 0
    shadow_offset = round(base * 0.018) if effects.shadow else 0
    pad = outline + (shadow_blur * 2 + shadow_offset if effects.shadow else 0)
    inner_w, inner_h = max(1, width - 2 * pad), max(1, height - 2 * pad)

    picture, (ox, oy) = _fit(image, inner_w, inner_h, effects)
    picture = _filter(picture, effects)
    area = Image.new("RGBA", (inner_w, inner_h), (0, 0, 0, 0))
    area.paste(picture, (ox, oy), picture)
    # Seeded by the picture's name and size, not its folder, so the same photo gives the same shape anywhere.
    shape = _mask(effects.mask, inner_w, inner_h, effects.corner_radius, f"{Path(path).name}|{image.size}|{effects.mask}")
    area.putalpha(ImageChops.multiply(area.getchannel("A"), shape))

    canvas = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    alpha = Image.new("L", (width, height), 0)
    alpha.paste(area.getchannel("A"), (pad, pad))
    if outline:
        grown = alpha.filter(ImageFilter.GaussianBlur(outline * 0.55)).point(lambda v: 255 if v > 6 else 0)
        grown = grown.filter(ImageFilter.GaussianBlur(0.8))
        edge = Image.new("RGBA", (width, height), (*_hex(effects.outline_color, "#FFFFFF"), 255))
        edge.putalpha(grown)
        alpha = ImageChops.lighter(alpha, grown)
        footprint = edge
    else:
        footprint = None
    if effects.shadow:
        shadow_alpha = Image.new("L", (width, height), 0)
        shadow_alpha.paste(alpha, (shadow_offset, shadow_offset))
        shadow_alpha = shadow_alpha.filter(ImageFilter.GaussianBlur(shadow_blur)).point(lambda v: round(v * 0.55 * effects.shadow))
        shadow_layer = Image.new("RGBA", (width, height), (0, 0, 0, 255))
        shadow_layer.putalpha(shadow_alpha)
        canvas = Image.alpha_composite(canvas, shadow_layer)
    if footprint is not None:
        canvas = Image.alpha_composite(canvas, footprint)
    placed = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    placed.paste(area, (pad, pad))
    canvas = Image.alpha_composite(canvas, placed)
    buffer = io.BytesIO()
    canvas.save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()


__all__ = ["ImageEffectError", "ai_cutout_available", "process_image"]
