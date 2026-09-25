from __future__ import annotations

from pathlib import Path

import pytest

from book_production_platform.cad_ir import canonical_part_ir, parse_part_ir
from book_production_platform.cad_production import qa
from book_production_platform.cad_production.errors import CadProductionError
from book_production_platform.cad_production.geometry import TriangleMesh
from book_production_platform.cad_production.runtime import run_freecad_worker
from tests.cad_production.conftest import BASE_PART_IR


def native_result() -> dict[str, object]:
    return {
        "protocol": "freecad-worker.v1",
        "mode": "verify-native",
        "status": "ok",
        "gui_loaded": False,
        "shape_valid": True,
        "solid_count": 1,
        "volume_mm3": 83774.5355,
        "bounds_mm": [0, 137, 0, 83, 0, 7.5],
        "feature_names": ["Body", "PlateSketch", "Pad", "HoleSketch", "Pocket"],
        "cylindrical_bores": [
            {"center_x_mm": x, "center_y_mm": y, "radius_mm": 4, "through": True}
            for x, y in ((13, 17), (124, 17), (124, 66), (13, 66))
        ],
    }


def step_result() -> dict[str, object]:
    return {
        "protocol": "freecad-worker.v1",
        "mode": "verify-step",
        "status": "ok",
        "gui_loaded": False,
        "shape_valid": True,
        "solid_count": 1,
        "volume_mm3": 83774.5355,
        "bounds_mm": [0, 137, 0, 83, 0, 7.5],
        "units": "mm",
    }


def install_qa_fakes(monkeypatch: pytest.MonkeyPatch, *, native: dict[str, object] | None = None, step: dict[str, object] | None = None) -> None:
    results = {"verify-native": native or native_result(), "verify-step": step or step_result()}
    monkeypatch.setattr(
        qa,
        "run_freecad_worker",
        lambda _binary, mode, *_args, **_kwargs: qa.WorkerResult(
            exit_code=0,
            elapsed_seconds=0.01,
            diagnostics_bytes=0,
            error_code=None,
            result_json=results[mode],
        ),
    )
    mesh = TriangleMesh(
        vertices=(
            (0.0, 0.0, 0.0),
            (137.0, 0.0, 0.0),
            (137.0, 83.0, 0.0),
            (0.0, 83.0, 0.0),
            (0.0, 0.0, 7.5),
            (137.0, 0.0, 7.5),
            (137.0, 83.0, 7.5),
            (0.0, 83.0, 7.5),
        ),
        triangles=(
            (0, 2, 1), (0, 3, 2), (4, 5, 6), (4, 6, 7),
            (0, 1, 5), (0, 5, 4), (1, 2, 6), (1, 6, 5),
            (2, 3, 7), (2, 7, 6), (3, 0, 4), (3, 4, 7),
        ),
    )
    monkeypatch.setattr(qa, "read_stl_mesh", lambda _path: mesh)
    monkeypatch.setattr(qa, "read_tessellation", lambda _path: mesh)
    monkeypatch.setattr(qa, "render_isometric_preview", lambda _mesh, path: path.write_bytes(b"png"))
    monkeypatch.setattr(qa, "verify_preview", lambda _path: None)


def output_root(tmp_path: Path) -> Path:
    root = tmp_path / "artifacts"
    root.mkdir()
    for name in ("part.FCStd", "part.step", "part.stl", "tessellation.json"):
        (root / name).write_bytes(b"fixture")
    return root


def test_cad_qa_verifies_native_step_mesh_and_preview(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    install_qa_fakes(monkeypatch)
    root = output_root(tmp_path)

    result = qa.verify_cad_outputs(parse_part_ir(BASE_PART_IR), root, tmp_path / "FreeCADCmd.exe", 30)

    assert result.bounds_mm == (0.0, 137.0, 0.0, 83.0, 0.0, 7.5)
    assert result.solid_count == 1
    assert result.bore_count == 4
    assert result.step_relative_volume_error <= 0.001
    assert result.preview_profile == "isometric-1024x768.v1"
    assert (root / "preview.png").read_bytes() == b"png"


@pytest.mark.parametrize(
    ("result_key", "replacement", "error_code"),
    [
        ("bounds_mm", [0, 136.98, 0, 83, 0, 7.5], "native_document_invalid"),
        ("cylindrical_bores", [], "geometry_invalid"),
    ],
)
def test_native_qa_rejects_wrong_bounds_or_missing_bores(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    result_key: str,
    replacement: object,
    error_code: str,
) -> None:
    native = native_result()
    native[result_key] = replacement
    install_qa_fakes(monkeypatch, native=native)

    with pytest.raises(CadProductionError) as error:
        qa.verify_cad_outputs(parse_part_ir(BASE_PART_IR), output_root(tmp_path), tmp_path / "FreeCADCmd.exe", 30)
    assert error.value.code == error_code


def test_step_qa_rejects_non_mm_units_and_volume_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    step = step_result()
    step["units"] = "inch"
    install_qa_fakes(monkeypatch, step=step)

    with pytest.raises(CadProductionError) as error:
        qa.verify_cad_outputs(parse_part_ir(BASE_PART_IR), output_root(tmp_path), tmp_path / "FreeCADCmd.exe", 30)
    assert error.value.code == "step_export_invalid"

    step = step_result()
    step["volume_mm3"] = 83900.0
    install_qa_fakes(monkeypatch, step=step)
    volume_parent = tmp_path / "volume"
    volume_parent.mkdir()
    with pytest.raises(CadProductionError) as volume_error:
        qa.verify_cad_outputs(parse_part_ir(BASE_PART_IR), output_root(volume_parent), tmp_path / "FreeCADCmd.exe", 30)
    assert volume_error.value.code == "step_export_invalid"


def test_qa_rejects_missing_stl_with_stable_code(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    install_qa_fakes(monkeypatch)
    root = output_root(tmp_path)
    (root / "part.stl").unlink()

    with pytest.raises(CadProductionError) as error:
        qa.verify_cad_outputs(parse_part_ir(BASE_PART_IR), root, tmp_path / "FreeCADCmd.exe", 30)
    assert error.value.code == "stl_export_invalid"


def test_real_freecad_build_passes_independent_qa(tmp_path: Path) -> None:
    repository_root = Path(__file__).parents[2]
    freecad = (
        repository_root
        / ".runtime"
        / "cad"
        / "freecad-1.1.3"
        / "extracted"
        / "FreeCAD_1.1.3-Windows-x86_64-py311"
        / "FreeCADCmd.exe"
    )
    if not freecad.is_file():
        pytest.skip("the pinned local FreeCAD runtime is unavailable")
    part = parse_part_ir(BASE_PART_IR)
    root = tmp_path / "real-cad-output"
    root.mkdir()
    request = root / "request.json"
    request.write_bytes(canonical_part_ir(part))

    build = run_freecad_worker(freecad, "build", request, root, timeout_s=120)
    assert build.error_code is None

    result = qa.verify_cad_outputs(part, root, freecad, timeout_s=120)

    assert result.solid_count == 1
    assert result.bore_count == 4
    assert result.step_relative_volume_error <= 0.001
    assert result.stl_triangle_count > 0
    assert result.tessellation_faces > 0
    assert (root / "preview.png").is_file()
