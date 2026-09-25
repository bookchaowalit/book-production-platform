from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from book_production_platform.cad_ir import canonical_part_ir, parse_part_ir

BASE_PART_IR: dict[str, Any] = {
    "schema_version": "part-ir.v1",
    "part_id": "mounting-plate-asymmetric",
    "template": "mounting-plate.v1",
    "units": "mm",
    "parameters": {
        "width_mm": "137.000",
        "height_mm": "83.000",
        "thickness_mm": "7.500",
        "hole_diameter_mm": "8.000",
        "hole_offset_x_mm": "13.000",
        "hole_offset_y_mm": "17.000",
    },
    "hole_pattern": "four-corner.v1",
}


@pytest.fixture
def request_file(tmp_path: Path) -> Path:
    request_root = tmp_path / "worker"
    request_root.mkdir()
    request = request_root / "request.json"
    request.write_bytes(canonical_part_ir(parse_part_ir(BASE_PART_IR)))
    return request


@pytest.fixture
def request_root(request_file: Path) -> Path:
    return request_file.parent


@pytest.fixture
def valid_worker_result() -> dict[str, object]:
    return {
        "protocol": "freecad-worker.v1",
        "mode": "build",
        "status": "ok",
        "gui_loaded": False,
        "shape_valid": True,
        "solid_count": 1,
        "volume_positive": True,
        "feature_names": ["Body", "PlateSketch", "Pad", "HoleSketch", "Pocket"],
        "tessellation_vertices": 128,
        "tessellation_faces": 256,
    }


def write_worker_result(root: Path, value: object) -> None:
    mode = value.get("mode", "build") if isinstance(value, dict) else "build"
    (root / f"worker-result-{mode}.json").write_text(json.dumps(value), encoding="utf-8")
