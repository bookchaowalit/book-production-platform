from __future__ import annotations

import copy
from typing import Any

import pytest

BASE_PART_IR: dict[str, Any] = {
    "schema_version": "part-ir.v1",
    "part_id": "mounting-plate-demo",
    "template": "mounting-plate.v1",
    "units": "mm",
    "parameters": {
        "width_mm": "120.000",
        "height_mm": "80.000",
        "thickness_mm": "6.000",
        "hole_diameter_mm": "6.000",
        "hole_offset_x_mm": "12.000",
        "hole_offset_y_mm": "12.000",
    },
    "hole_pattern": "four-corner.v1",
}


@pytest.fixture
def valid_part_ir() -> dict[str, Any]:
    return copy.deepcopy(BASE_PART_IR)
