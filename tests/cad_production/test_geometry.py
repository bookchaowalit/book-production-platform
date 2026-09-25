from __future__ import annotations

import math
import struct
from pathlib import Path

import pytest

from book_production_platform.cad_production.errors import CadProductionError
from book_production_platform.cad_production.geometry import (
    MAX_STL_TRIANGLES,
    TriangleMesh,
    read_stl_mesh,
    read_tessellation,
)


def cube_triangles() -> list[tuple[tuple[float, float, float], ...]]:
    a = (0.0, 0.0, 0.0)
    b = (10.0, 0.0, 0.0)
    c = (10.0, 8.0, 0.0)
    d = (0.0, 8.0, 0.0)
    e = (0.0, 0.0, 2.0)
    f = (10.0, 0.0, 2.0)
    g = (10.0, 8.0, 2.0)
    h = (0.0, 8.0, 2.0)
    return [
        (a, c, b), (a, d, c),
        (e, f, g), (e, g, h),
        (a, b, f), (a, f, e),
        (b, c, g), (b, g, f),
        (c, d, h), (c, h, g),
        (d, a, e), (d, e, h),
    ]


def write_binary_stl(path: Path, triangles: list[tuple[tuple[float, float, float], ...]]) -> None:
    with path.open("wb") as stream:
        stream.write(b"fixture".ljust(80, b" "))
        stream.write(struct.pack("<I", len(triangles)))
        for triangle in triangles:
            stream.write(struct.pack("<3f", 0.0, 0.0, 1.0))
            for vertex in triangle:
                stream.write(struct.pack("<3f", *vertex))
            stream.write(struct.pack("<H", 0))


def write_ascii_stl(path: Path, triangles: list[tuple[tuple[float, float, float], ...]]) -> None:
    lines = ["solid part"]
    for first, second, third in triangles:
        lines.extend((" facet normal 0 0 1", "  outer loop"))
        for x, y, z in (first, second, third):
            lines.append(f"   vertex {x} {y} {z}")
        lines.extend(("  endloop", " endfacet"))
    lines.append("endsolid part")
    path.write_text("\n".join(lines) + "\n", encoding="ascii")


def test_binary_stl_is_bounded_closed_and_measured(tmp_path: Path) -> None:
    path = tmp_path / "part.stl"
    write_binary_stl(path, cube_triangles())

    mesh = read_stl_mesh(path)

    assert len(mesh.triangles) == 12
    assert mesh.bounds_mm == (0.0, 10.0, 0.0, 8.0, 0.0, 2.0)
    assert mesh.volume_mm3 == pytest.approx(160.0)


def test_ascii_stl_is_supported(tmp_path: Path) -> None:
    path = tmp_path / "part.stl"
    write_ascii_stl(path, cube_triangles())

    mesh = read_stl_mesh(path)

    assert len(mesh.triangles) == 12
    assert mesh.volume_mm3 == pytest.approx(160.0)


def test_stl_rejects_open_edges_nonfinite_and_zero_volume(tmp_path: Path) -> None:
    open_path = tmp_path / "open.stl"
    write_binary_stl(open_path, cube_triangles()[:-1])
    with pytest.raises(CadProductionError) as open_error:
        read_stl_mesh(open_path)
    assert open_error.value.code == "stl_export_invalid"

    nonfinite_path = tmp_path / "nonfinite.stl"
    triangles = cube_triangles()
    triangles[0] = ((math.nan, 0.0, 0.0), *triangles[0][1:])
    write_binary_stl(nonfinite_path, triangles)
    with pytest.raises(CadProductionError) as nonfinite_error:
        read_stl_mesh(nonfinite_path)
    assert nonfinite_error.value.code == "stl_export_invalid"

    flat_path = tmp_path / "flat.stl"
    flat = [(a, b, c) for a, b, c in cube_triangles()]
    flat = [tuple((x, y, 0.0) for x, y, _ in face) for face in flat]
    write_binary_stl(flat_path, flat)
    with pytest.raises(CadProductionError) as flat_error:
        read_stl_mesh(flat_path)
    assert flat_error.value.code == "stl_export_invalid"


def test_stl_rejects_triangle_count_above_cap(tmp_path: Path) -> None:
    path = tmp_path / "oversized.stl"
    path.write_bytes(b"x" * 80 + struct.pack("<I", MAX_STL_TRIANGLES + 1))

    with pytest.raises(CadProductionError) as error:
        read_stl_mesh(path)

    assert error.value.code == "stl_export_invalid"


def test_tessellation_json_checks_schema_indices_and_finite_coordinates(tmp_path: Path) -> None:
    path = tmp_path / "tessellation.json"
    path.write_text(
        '{"schema_version":"cad-tessellation.v1","units":"mm",'
        '"vertices":[[0,0,0],[1,0,0],[0,1,0]],"triangles":[[0,1,2]]}',
        encoding="utf-8",
    )

    mesh = read_tessellation(path)
    assert mesh.triangles == ((0, 1, 2),)

    path.write_text(
        '{"schema_version":"cad-tessellation.v1","units":"mm",'
        '"vertices":[[0,0,0],[1,0,0],[0,1,0]],"triangles":[[0,1,3]]}',
        encoding="utf-8",
    )
    with pytest.raises(CadProductionError) as error:
        read_tessellation(path)
    assert error.value.code == "geometry_invalid"


def test_triangle_mesh_rejects_invalid_indices() -> None:
    with pytest.raises(ValueError):
        TriangleMesh(vertices=((0.0, 0.0, 0.0),), triangles=((0, 1, 2),))
