from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from book_production_platform.cad_ir import PartIR, PartParameters, parse_part_ir
from tests.cad_ir.conftest import BASE_PART_IR


def test_parse_accepts_valid_mounting_plate(valid_part_ir: dict[str, object]) -> None:
    part = parse_part_ir(valid_part_ir)

    assert isinstance(part, PartIR)
    assert isinstance(part.parameters, PartParameters)
    assert part.schema_version == "part-ir.v1"
    assert part.part_id == "mounting-plate-demo"
    assert part.parameters.width_mm == "120.000"
    assert part.template == "mounting-plate.v1"
    assert part.units == "mm"
    assert part.hole_pattern == "four-corner.v1"


@pytest.mark.parametrize(
    ("model", "extra"),
    [
        (PartIR, {"surprise": True}),
        (PartParameters, {"surprise": "1.000"}),
    ],
)
def test_models_reject_extra_fields(
    model: type[PartIR | PartParameters], extra: dict[str, object]
) -> None:
    payload = dict(BASE_PART_IR)
    if model is PartParameters:
        payload = dict(payload["parameters"])
    payload.update(extra)

    with pytest.raises(ValidationError):
        model.model_validate(payload)


def test_models_are_frozen(valid_part_ir: dict[str, object]) -> None:
    part = parse_part_ir(valid_part_ir)

    with pytest.raises(ValidationError):
        part.parameters.width_mm = "121.000"


@pytest.mark.parametrize(
    "value",
    [120, 120.0, True, "01.000", "+1.000", "-1.000", "1e2", "NaN", "Infinity", "1.0000"],
)
def test_parameter_model_requires_bounded_decimal_strings(value: object) -> None:
    payload = dict(BASE_PART_IR["parameters"])
    payload["width_mm"] = value

    with pytest.raises(ValidationError):
        PartParameters.model_validate(payload)


def test_json_schema_is_strict_and_versioned() -> None:
    schema_path = (
        Path(__file__).parents[2]
        / "src"
        / "book_production_platform"
        / "cad_ir"
        / "part-ir.v1.schema.json"
    )
    schema = json.loads(schema_path.read_text(encoding="utf-8"))

    assert schema["$id"] == "part-ir.v1.schema.json"
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == {
        "schema_version",
        "part_id",
        "template",
        "units",
        "parameters",
        "hole_pattern",
    }
    assert schema["properties"]["schema_version"]["const"] == "part-ir.v1"
    assert schema["properties"]["parameters"]["additionalProperties"] is False
    assert schema["properties"]["parameters"]["properties"]["width_mm"]["pattern"] == (
        "^(0|[1-9][0-9]{0,2})(\\.[0-9]{1,3})?$"
    )
