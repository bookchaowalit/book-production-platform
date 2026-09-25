from __future__ import annotations

import copy
from typing import Any

import pytest

BASE_BIM_TAKEOFF_IR: dict[str, Any] = {
    "schema_version": "bim-takeoff.v1",
    "takeoff_id": "walls-demo",
    "element_classes": ["IfcWall"],
    "output_units": "SI",
}

ALL_ELEMENT_CLASSES = ["IfcWall", "IfcSlab", "IfcColumn", "IfcBeam", "IfcDoor", "IfcWindow"]


@pytest.fixture
def valid_bim_takeoff_ir() -> dict[str, Any]:
    return copy.deepcopy(BASE_BIM_TAKEOFF_IR)
