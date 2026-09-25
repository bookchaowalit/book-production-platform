"""Bounded STL and FreeCAD tessellation readers for CAD output QA."""

from __future__ import annotations

import json
import math
import struct
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import NoReturn

from book_production_platform.cad_production.errors import CadProductionError

MAX_STL_TRIANGLES = 100_000
MAX_STL_BYTES = 32 * 1024 * 1024
MAX_TESSELLATION_BYTES = 16 * 1024 * 1024
MAX_TESSELLATION_ITEMS = 100_000
_STL_VERTEX_TOLERANCE_MM = 0.001

Point3 = tuple[float, float, float]
Triangle = tuple[int, int, int]
Bounds = tuple[float, float, float, float, float, float]


def _invalid_stl() -> NoReturn:
    raise CadProductionError("stl_export_invalid") from None


def _invalid_geometry() -> NoReturn:
    raise CadProductionError("geometry_invalid") from None


def _is_link(path: Path) -> bool:
    try:
        return path.is_symlink() or path.is_junction()
    except (OSError, AttributeError):
        return True


@dataclass(frozen=True, slots=True)
class TriangleMesh:
    """Immutable triangle soup in millimeters with checked finite coordinates."""

    vertices: tuple[Point3, ...]
    triangles: tuple[Triangle, ...]

    def __post_init__(self) -> None:
        if not self.vertices or not self.triangles:
            raise ValueError("mesh cannot be empty")
        if len(self.vertices) > MAX_TESSELLATION_ITEMS or len(self.triangles) > MAX_TESSELLATION_ITEMS:
            raise ValueError("mesh exceeds item cap")
        for vertex in self.vertices:
            if len(vertex) != 3 or any(
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
                for value in vertex
            ):
                raise ValueError("mesh coordinate is invalid")
        for triangle in self.triangles:
            if (
                len(triangle) != 3
                or any(type(index) is not int for index in triangle)
                or len(set(triangle)) != 3
                or any(index < 0 or index >= len(self.vertices) for index in triangle)
            ):
                raise ValueError("mesh index is invalid")

    @property
    def bounds_mm(self) -> Bounds:
        xs = [vertex[0] for vertex in self.vertices]
        ys = [vertex[1] for vertex in self.vertices]
        zs = [vertex[2] for vertex in self.vertices]
        return (min(xs), max(xs), min(ys), max(ys), min(zs), max(zs))

    @property
    def volume_mm3(self) -> float:
        bounds = self.bounds_mm
        center = (
            (bounds[0] + bounds[1]) / 2,
            (bounds[2] + bounds[3]) / 2,
            (bounds[4] + bounds[5]) / 2,
        )
        terms: list[float] = []
        for first, second, third in self.triangles:
            a = tuple(self.vertices[first][i] - center[i] for i in range(3))
            b = tuple(self.vertices[second][i] - center[i] for i in range(3))
            c = tuple(self.vertices[third][i] - center[i] for i in range(3))
            terms.append(
                (
                    a[0] * (b[1] * c[2] - b[2] * c[1])
                    + a[1] * (b[2] * c[0] - b[0] * c[2])
                    + a[2] * (b[0] * c[1] - b[1] * c[0])
                )
                / 6
            )
        return abs(math.fsum(terms))


def _decode_ascii_stl(payload: bytes) -> TriangleMesh:
    try:
        text = payload.decode("ascii")
    except UnicodeDecodeError:
        _invalid_stl()
    points: list[Point3] = []
    for line in text.splitlines():
        fields = line.strip().split()
        if fields and fields[0].lower() == "vertex":
            if len(fields) != 4 or len(points) >= MAX_STL_TRIANGLES * 3:
                _invalid_stl()
            try:
                point = (float(fields[1]), float(fields[2]), float(fields[3]))
            except ValueError:
                _invalid_stl()
            if not all(math.isfinite(value) for value in point):
                _invalid_stl()
            points.append(point)
    if not points or len(points) % 3:
        _invalid_stl()
    vertices = tuple(points)
    triangles = tuple(
        (index, index + 1, index + 2) for index in range(0, len(points), 3)
    )
    if len(triangles) > MAX_STL_TRIANGLES:
        _invalid_stl()
    try:
        return TriangleMesh(vertices=vertices, triangles=triangles)
    except ValueError:
        _invalid_stl()


def _decode_binary_stl(payload: bytes) -> TriangleMesh | None:
    if len(payload) < 84:
        return None
    count = struct.unpack_from("<I", payload, 80)[0]
    if 84 + count * 50 != len(payload):
        return None
    if count == 0 or count > MAX_STL_TRIANGLES:
        _invalid_stl()
    vertices: list[Point3] = []
    triangles: list[Triangle] = []
    offset = 84
    for _ in range(count):
        values = struct.unpack_from("<12f", payload, offset)
        offset += 50
        point_values = values[3:]
        for index in range(0, 9, 3):
            point = (
                float(point_values[index]),
                float(point_values[index + 1]),
                float(point_values[index + 2]),
            )
            if not all(math.isfinite(value) for value in point):
                _invalid_stl()
            vertices.append(point)
        base = len(vertices) - 3
        triangles.append((base, base + 1, base + 2))
    try:
        return TriangleMesh(vertices=tuple(vertices), triangles=tuple(triangles))
    except ValueError:
        _invalid_stl()


def _vertex_key(point: Point3) -> tuple[int, int, int]:
    return tuple(round(value / _STL_VERTEX_TOLERANCE_MM) for value in point)  # type: ignore[return-value]


def _validate_closed_mesh(mesh: TriangleMesh) -> None:
    edge_counts: Counter[tuple[tuple[int, int, int], tuple[int, int, int]]] = Counter()
    for triangle in mesh.triangles:
        first, second, third = (mesh.vertices[index] for index in triangle)
        ab = tuple(second[i] - first[i] for i in range(3))
        ac = tuple(third[i] - first[i] for i in range(3))
        cross = (
            ab[1] * ac[2] - ab[2] * ac[1],
            ab[2] * ac[0] - ab[0] * ac[2],
            ab[0] * ac[1] - ab[1] * ac[0],
        )
        if sum(value * value for value in cross) <= 1e-18:
            _invalid_stl()
        keys = tuple(_vertex_key(point) for point in (first, second, third))
        for start, end in ((keys[0], keys[1]), (keys[1], keys[2]), (keys[2], keys[0])):
            low, high = sorted((start, end))
            edge_counts[(low, high)] += 1
    if not edge_counts or any(count != 2 for count in edge_counts.values()):
        _invalid_stl()
    if not math.isfinite(mesh.volume_mm3) or mesh.volume_mm3 <= 1e-9:
        _invalid_stl()


def read_stl_mesh(path: Path) -> TriangleMesh:
    """Read a bounded binary or ASCII STL and require a closed positive-volume mesh."""

    if not isinstance(path, Path) or _is_link(path):
        _invalid_stl()
    try:
        if not path.is_file() or path.stat().st_size > MAX_STL_BYTES:
            _invalid_stl()
        payload = path.read_bytes()
    except OSError:
        _invalid_stl()
    if len(payload) > MAX_STL_BYTES:
        _invalid_stl()
    mesh = _decode_binary_stl(payload)
    if mesh is None:
        mesh = _decode_ascii_stl(payload)
    _validate_closed_mesh(mesh)
    return mesh


def _reject_duplicate_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate key")
        result[key] = value
    return result


def _reject_constant(value: str) -> NoReturn:
    raise ValueError(value)


def read_tessellation(path: Path) -> TriangleMesh:
    """Read a capped, strict FreeCAD tessellation JSON document in millimeters."""

    if not isinstance(path, Path) or _is_link(path):
        _invalid_geometry()
    try:
        if not path.is_file() or path.stat().st_size > MAX_TESSELLATION_BYTES:
            _invalid_geometry()
        payload = path.read_bytes()
        if len(payload) > MAX_TESSELLATION_BYTES:
            _invalid_geometry()
        value = json.loads(
            payload.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_pairs,
            parse_constant=_reject_constant,
        )
    except CadProductionError:
        raise
    except (OSError, UnicodeDecodeError, ValueError, TypeError, RecursionError):
        _invalid_geometry()
    if (
        not isinstance(value, dict)
        or set(value) != {"schema_version", "units", "vertices", "triangles"}
        or value.get("schema_version") != "cad-tessellation.v1"
        or value.get("units") != "mm"
        or not isinstance(value.get("vertices"), list)
        or not isinstance(value.get("triangles"), list)
    ):
        _invalid_geometry()
    vertices_raw = value["vertices"]
    triangles_raw = value["triangles"]
    if (
        not 1 <= len(vertices_raw) <= MAX_TESSELLATION_ITEMS
        or not 1 <= len(triangles_raw) <= MAX_TESSELLATION_ITEMS
    ):
        _invalid_geometry()
    vertices: list[Point3] = []
    for vertex in vertices_raw:
        if (
            not isinstance(vertex, list)
            or len(vertex) != 3
            or any(
                isinstance(coordinate, bool)
                or not isinstance(coordinate, (int, float))
                or not math.isfinite(coordinate)
                for coordinate in vertex
            )
        ):
            _invalid_geometry()
        vertices.append((float(vertex[0]), float(vertex[1]), float(vertex[2])))
    triangles: list[Triangle] = []
    for triangle in triangles_raw:
        if (
            not isinstance(triangle, list)
            or len(triangle) != 3
            or any(type(index) is not int for index in triangle)
        ):
            _invalid_geometry()
        triangles.append((triangle[0], triangle[1], triangle[2]))
    try:
        return TriangleMesh(vertices=tuple(vertices), triangles=tuple(triangles))
    except ValueError:
        _invalid_geometry()
