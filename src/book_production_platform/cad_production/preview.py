"""Deterministic CPU isometric renderer for checked CAD triangle meshes."""

from __future__ import annotations

import math
import os
import tempfile
from array import array
from pathlib import Path
from typing import NoReturn

from PIL import Image, ImageChops, UnidentifiedImageError

from book_production_platform.cad_production.errors import CadProductionError
from book_production_platform.cad_production.geometry import TriangleMesh

PREVIEW_PROFILE = "isometric-1024x768.v1"
PREVIEW_SIZE = (1024, 768)
_BACKGROUND = (242, 244, 247)
_BASE_COLOR = (76, 127, 174)
_MAX_RASTER_WORK = 50_000_000
_MAX_PREVIEW_BYTES = 16 * 1024 * 1024


def _invalid_preview() -> NoReturn:
    raise CadProductionError("preview_invalid") from None


def _is_link(path: Path) -> bool:
    try:
        return path.is_symlink() or path.is_junction()
    except (OSError, AttributeError):
        return True


def _dot(first: tuple[float, float, float], second: tuple[float, float, float]) -> float:
    return first[0] * second[0] + first[1] * second[1] + first[2] * second[2]


def _subtract(
    first: tuple[float, float, float], second: tuple[float, float, float]
) -> tuple[float, float, float]:
    return (first[0] - second[0], first[1] - second[1], first[2] - second[2])


def _cross(
    first: tuple[float, float, float], second: tuple[float, float, float]
) -> tuple[float, float, float]:
    return (
        first[1] * second[2] - first[2] * second[1],
        first[2] * second[0] - first[0] * second[2],
        first[0] * second[1] - first[1] * second[0],
    )


def _unit(value: tuple[float, float, float]) -> tuple[float, float, float]:
    length = math.sqrt(_dot(value, value))
    if not math.isfinite(length) or length <= 1e-12:
        _invalid_preview()
    return (value[0] / length, value[1] / length, value[2] / length)


def _atomic_png(path: Path, pixels: bytes) -> None:
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb", prefix=".cad-preview-", suffix=".tmp", dir=path.parent, delete=False
        ) as stream:
            temporary = Path(stream.name)
        image = Image.frombytes("RGB", PREVIEW_SIZE, pixels)
        image.save(temporary, format="PNG", optimize=False, compress_level=9)
        with temporary.open("r+b") as stream:
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except (OSError, ValueError):
        try:
            temporary.unlink(missing_ok=True)
        except (OSError, UnboundLocalError):
            pass
        _invalid_preview()


def render_isometric_preview(mesh: TriangleMesh, path: Path) -> None:
    """Render an orthographic isometric view with a fixed camera, palette, and z-buffer."""

    if not isinstance(mesh, TriangleMesh) or not isinstance(path, Path):
        _invalid_preview()
    if _is_link(path) or _is_link(path.parent) or not path.parent.is_dir() or path.exists():
        _invalid_preview()
    width, height = PREVIEW_SIZE
    bounds = mesh.bounds_mm
    center = (
        (bounds[0] + bounds[1]) / 2,
        (bounds[2] + bounds[3]) / 2,
        (bounds[4] + bounds[5]) / 2,
    )
    toward_camera = _unit((1.0, -1.0, 1.15))
    right = _unit(_cross((0.0, 0.0, 1.0), toward_camera))
    up = _unit(_cross(toward_camera, right))
    projected: list[tuple[float, float, float]] = []
    for vertex in mesh.vertices:
        relative = _subtract(vertex, center)
        projected.append((_dot(relative, right), _dot(relative, up), _dot(relative, toward_camera)))
    min_x = min(point[0] for point in projected)
    max_x = max(point[0] for point in projected)
    min_y = min(point[1] for point in projected)
    max_y = max(point[1] for point in projected)
    span_x = max_x - min_x
    span_y = max_y - min_y
    if span_x <= 1e-12 or span_y <= 1e-12:
        _invalid_preview()
    margin = 88.0
    scale = min((width - 2 * margin) / span_x, (height - 2 * margin) / span_y)
    screen = [
        (
            (point[0] - min_x) * scale + margin,
            (max_y - point[1]) * scale + margin,
            point[2],
        )
        for point in projected
    ]
    pixels = bytearray(bytes(_BACKGROUND) * (width * height))
    depth_buffer = array("f", [-math.inf]) * (width * height)
    light = _unit((-0.45, -0.55, 0.72))
    work = 0
    painted_pixels = 0
    for triangle in mesh.triangles:
        points = tuple(screen[index] for index in triangle)
        projected_area = (
            (points[1][0] - points[0][0]) * (points[2][1] - points[0][1])
            - (points[1][1] - points[0][1]) * (points[2][0] - points[0][0])
        )
        if abs(projected_area) <= 1e-9:
            continue
        top = max(0, math.ceil(min(point[1] for point in points) - 0.5))
        bottom = min(height - 1, math.floor(max(point[1] for point in points) - 0.5))
        if top > bottom:
            continue
        first, second, third = (mesh.vertices[index] for index in triangle)
        normal = _unit(_cross(_subtract(second, first), _subtract(third, first)))
        intensity = max(0.34, min(1.0, 0.42 + 0.58 * max(0.0, _dot(normal, light))))
        color = tuple(round(channel * intensity) for channel in _BASE_COLOR)
        color_bytes = bytes(color)
        for y in range(top, bottom + 1):
            sample_y = y + 0.5
            intersections: list[tuple[float, float]] = []
            for start, end in (
                (points[0], points[1]),
                (points[1], points[2]),
                (points[2], points[0]),
            ):
                if (start[1] <= sample_y < end[1]) or (end[1] <= sample_y < start[1]):
                    fraction = (sample_y - start[1]) / (end[1] - start[1])
                    intersections.append(
                        (
                            start[0] + fraction * (end[0] - start[0]),
                            start[2] + fraction * (end[2] - start[2]),
                        )
                    )
            if len(intersections) != 2:
                continue
            left_point, right_point = sorted(intersections, key=lambda point: point[0])
            if right_point[0] - left_point[0] <= 1e-12:
                continue
            left_pixel = max(0, math.ceil(left_point[0] - 0.5))
            right_pixel = min(width - 1, math.floor(right_point[0] - 0.5))
            if left_pixel > right_pixel:
                continue
            work += right_pixel - left_pixel + 1
            if work > _MAX_RASTER_WORK:
                _invalid_preview()
            inverse_span = 1.0 / (right_point[0] - left_point[0])
            for x in range(left_pixel, right_pixel + 1):
                fraction = (x + 0.5 - left_point[0]) * inverse_span
                depth = left_point[1] + fraction * (right_point[1] - left_point[1])
                pixel_index = y * width + x
                if depth <= depth_buffer[pixel_index]:
                    continue
                depth_buffer[pixel_index] = depth
                color_offset = pixel_index * 3
                pixels[color_offset : color_offset + 3] = color_bytes
                painted_pixels += 1
    if painted_pixels == 0:
        _invalid_preview()
    _atomic_png(path, bytes(pixels))
    verify_preview(path)


def verify_preview(path: Path) -> None:
    """Require a bounded, nonblank RGB/RGBA PNG matching the fixed preview canvas."""

    if not isinstance(path, Path) or _is_link(path):
        _invalid_preview()
    try:
        if not path.is_file() or path.stat().st_size > _MAX_PREVIEW_BYTES:
            _invalid_preview()
        with Image.open(path) as image:
            if image.format != "PNG" or image.size != PREVIEW_SIZE or image.mode not in {"RGB", "RGBA"}:
                _invalid_preview()
            image.load()
            rgb = image.convert("RGB")
            background = rgb.getpixel((0, 0))
            difference = ImageChops.difference(rgb, Image.new("RGB", PREVIEW_SIZE, background))
            bbox = difference.getbbox()
            if bbox is None:
                _invalid_preview()
            left, top, right, bottom = bbox
            if left < 1 or top < 1 or right >= PREVIEW_SIZE[0] or bottom >= PREVIEW_SIZE[1]:
                _invalid_preview()
            if right - left < 32 or bottom - top < 16:
                _invalid_preview()
            histogram = difference.convert("L").histogram()
            if sum(histogram[1:]) < 250:
                _invalid_preview()
    except CadProductionError:
        raise
    except (OSError, ValueError, UnidentifiedImageError, Image.DecompressionBombError):
        _invalid_preview()
