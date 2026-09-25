"""Independent verification of FreeCAD native and neutral CAD exports."""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import NoReturn

from book_production_platform.cad_ir import PartIR, canonical_part_ir, part_dimensions
from book_production_platform.cad_production.errors import CadProductionError
from book_production_platform.cad_production.geometry import (
    TriangleMesh,
    read_stl_mesh,
    read_tessellation,
)
from book_production_platform.cad_production.preview import (
    PREVIEW_PROFILE,
    render_isometric_preview,
    verify_preview,
)
from book_production_platform.cad_production.runtime import (
    WorkerResult,
    run_freecad_worker,
)

_EXPECTED_FEATURES = ("Body", "PlateSketch", "Pad", "HoleSketch", "Pocket")
_BOUNDS_TOLERANCE_MM = 0.01
_MAX_WORKER_REQUEST_BYTES = 64 * 1024


@dataclass(frozen=True, slots=True)
class CadQAResult:
    bounds_mm: tuple[float, float, float, float, float, float]
    volume_mm3: float
    solid_count: int
    bore_count: int
    step_relative_volume_error: float
    stl_triangle_count: int
    stl_volume_mm3: float
    tessellation_vertices: int
    tessellation_faces: int
    preview_profile: str


def _raise(code: str) -> NoReturn:
    raise CadProductionError(code) from None


def _is_link(path: Path) -> bool:
    try:
        return path.is_symlink() or path.is_junction()
    except (OSError, AttributeError):
        return True


def _regular_file(root: Path, name: str, error_code: str) -> Path:
    path = root / name
    try:
        if _is_link(path) or not path.is_file():
            _raise(error_code)
    except OSError:
        _raise(error_code)
    return path


def _finite_number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _worker_bounds(value: object, error_code: str) -> tuple[float, float, float, float, float, float]:
    if not isinstance(value, list) or len(value) != 6:
        _raise(error_code)
    numbers = tuple(_finite_number(item) for item in value)
    if any(item is None for item in numbers):
        _raise(error_code)
    return numbers  # type: ignore[return-value]


def _worker_volume(value: object, error_code: str) -> float:
    number = _finite_number(value)
    if number is None or number <= 0:
        _raise(error_code)
    return number


def _expected_geometry(part: PartIR) -> tuple[tuple[float, ...], float, tuple[tuple[float, float], ...], float]:
    dimensions = part_dimensions(part)
    width = float(dimensions.width_mm)
    height = float(dimensions.height_mm)
    thickness = float(dimensions.thickness_mm)
    radius = float(dimensions.hole_diameter_mm) / 2
    offset_x = float(dimensions.hole_offset_x_mm)
    offset_y = float(dimensions.hole_offset_y_mm)
    centers = (
        (offset_x, offset_y),
        (width - offset_x, offset_y),
        (width - offset_x, height - offset_y),
        (offset_x, height - offset_y),
    )
    expected_bounds = (0.0, width, 0.0, height, 0.0, thickness)
    expected_volume = (width * height - 4 * math.pi * radius * radius) * thickness
    return expected_bounds, expected_volume, centers, radius


def _assert_bounds(
    actual: tuple[float, float, float, float, float, float],
    expected: tuple[float, ...],
    error_code: str,
) -> None:
    if any(abs(found - wanted) > _BOUNDS_TOLERANCE_MM for found, wanted in zip(actual, expected)):
        _raise(error_code)


def _native_bores(value: object) -> tuple[tuple[float, float, float, bool], ...]:
    if not isinstance(value, list) or len(value) != 4:
        _raise("geometry_invalid")
    bores: list[tuple[float, float, float, bool]] = []
    for item in value:
        if not isinstance(item, dict) or set(item) != {
            "center_x_mm",
            "center_y_mm",
            "radius_mm",
            "through",
        }:
            _raise("geometry_invalid")
        x = _finite_number(item["center_x_mm"])
        y = _finite_number(item["center_y_mm"])
        radius = _finite_number(item["radius_mm"])
        through = item["through"]
        if x is None or y is None or radius is None or through is not True:
            _raise("geometry_invalid")
        bores.append((x, y, radius, through))
    return tuple(bores)


def _assert_bores(
    actual: tuple[tuple[float, float, float, bool], ...],
    expected_centers: tuple[tuple[float, float], ...],
    expected_radius: float,
) -> None:
    unmatched = list(actual)
    for expected_x, expected_y in expected_centers:
        match_index = next(
            (
                index
                for index, (x, y, radius, through) in enumerate(unmatched)
                if through
                and abs(x - expected_x) <= _BOUNDS_TOLERANCE_MM
                and abs(y - expected_y) <= _BOUNDS_TOLERANCE_MM
                and abs(radius - expected_radius) <= _BOUNDS_TOLERANCE_MM
            ),
            None,
        )
        if match_index is None:
            _raise("geometry_invalid")
        unmatched.pop(match_index)
    if unmatched:
        _raise("geometry_invalid")


def _worker_result(
    binary: Path,
    mode: str,
    request_path: Path,
    root: Path,
    timeout_s: int,
    error_code: str,
) -> dict[str, object]:
    result: WorkerResult = run_freecad_worker(binary, mode, request_path, root, timeout_s)
    if result.error_code is not None or result.result_json is None:
        _raise(error_code)
    return result.result_json


def _write_request(part: PartIR, root: Path) -> Path:
    request_path = root / ".cad-qa-request.json"
    if _is_link(request_path) or request_path.exists():
        _raise("publication_failed")
    payload = canonical_part_ir(part)
    if len(payload) > _MAX_WORKER_REQUEST_BYTES:
        _raise("invalid_part_ir")
    try:
        with request_path.open("xb") as stream:
            stream.write(payload)
            stream.flush()
    except OSError:
        _raise("publication_failed")
    return request_path


def _check_tessellation_bounds(mesh: TriangleMesh, expected: tuple[float, ...]) -> None:
    _assert_bounds(mesh.bounds_mm, expected, "geometry_invalid")


def verify_cad_outputs(
    part: PartIR,
    artifact_root: Path,
    freecadcmd: Path,
    timeout_s: int,
) -> CadQAResult:
    """Reopen exports in fresh FreeCAD processes and independently verify all outputs."""

    if not isinstance(part, PartIR) or not isinstance(artifact_root, Path) or not isinstance(freecadcmd, Path):
        _raise("invalid_part_ir")
    if _is_link(artifact_root):
        _raise("unsafe_output_path")
    try:
        root = artifact_root.resolve(strict=True)
        if not root.is_dir():
            _raise("unsafe_output_path")
    except OSError:
        _raise("unsafe_output_path")
    _regular_file(root, "part.FCStd", "native_document_invalid")
    _regular_file(root, "part.step", "step_export_invalid")
    stl_path = _regular_file(root, "part.stl", "stl_export_invalid")
    tessellation_path = _regular_file(root, "tessellation.json", "geometry_invalid")
    preview_path = root / "preview.png"
    if _is_link(preview_path) or preview_path.exists():
        _raise("preview_invalid")

    expected_bounds, expected_volume, expected_centers, expected_radius = _expected_geometry(part)
    request_path = _write_request(part, root)
    try:
        native = _worker_result(
            freecadcmd, "verify-native", request_path, root, timeout_s, "native_document_invalid"
        )
        if (
            native.get("shape_valid") is not True
            or native.get("solid_count") != 1
            or native.get("feature_names") != list(_EXPECTED_FEATURES)
        ):
            _raise("native_document_invalid")
        native_bounds = _worker_bounds(native.get("bounds_mm"), "native_document_invalid")
        _assert_bounds(native_bounds, expected_bounds, "native_document_invalid")
        native_volume = _worker_volume(native.get("volume_mm3"), "native_document_invalid")
        native_relative_error = abs(native_volume - expected_volume) / expected_volume
        if not math.isfinite(native_relative_error) or native_relative_error > 0.001:
            _raise("geometry_invalid")
        bores = _native_bores(native.get("cylindrical_bores"))
        _assert_bores(bores, expected_centers, expected_radius)

        step = _worker_result(
            freecadcmd, "verify-step", request_path, root, timeout_s, "step_export_invalid"
        )
        if step.get("shape_valid") is not True or step.get("solid_count") != 1 or step.get("units") != "mm":
            _raise("step_export_invalid")
        step_bounds = _worker_bounds(step.get("bounds_mm"), "step_export_invalid")
        _assert_bounds(step_bounds, expected_bounds, "step_export_invalid")
        step_volume = _worker_volume(step.get("volume_mm3"), "step_export_invalid")
        relative_error = abs(step_volume - native_volume) / native_volume
        if not math.isfinite(relative_error) or relative_error > 0.001:
            _raise("step_export_invalid")

        stl_mesh = read_stl_mesh(stl_path)
        _assert_bounds(stl_mesh.bounds_mm, expected_bounds, "stl_export_invalid")
        if stl_mesh.volume_mm3 <= 0:
            _raise("stl_export_invalid")

        tessellation = read_tessellation(tessellation_path)
        _check_tessellation_bounds(tessellation, expected_bounds)
        render_isometric_preview(tessellation, preview_path)
        verify_preview(preview_path)
        return CadQAResult(
            bounds_mm=native_bounds,
            volume_mm3=native_volume,
            solid_count=1,
            bore_count=len(bores),
            step_relative_volume_error=relative_error,
            stl_triangle_count=len(stl_mesh.triangles),
            stl_volume_mm3=stl_mesh.volume_mm3,
            tessellation_vertices=len(tessellation.vertices),
            tessellation_faces=len(tessellation.triangles),
            preview_profile=PREVIEW_PROFILE,
        )
    finally:
        try:
            request_path.unlink(missing_ok=True)
        except OSError:
            pass
