from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from book_production_platform.cad_ir import (
    PartIRError,
    canonical_part_ir,
    load_part_ir,
    parse_part_ir,
    part_dimensions,
)
from tests.cad_ir.conftest import BASE_PART_IR


def set_path(value: dict[str, Any], path: tuple[str, ...], replacement: object) -> None:
    current: Any = value
    for key in path[:-1]:
        current = current[key]
    current[path[-1]] = replacement


def assert_error_code(value: object, expected: str) -> None:
    with pytest.raises(PartIRError) as error:
        parse_part_ir(value)
    assert error.value.code == expected


@pytest.mark.parametrize(
    ("path", "replacement", "code"),
    [
        (("schema_version",), "part-ir.v2", "invalid_part_ir"),
        (("template",), "round-plate.v1", "unsupported_template"),
        (("units",), "inch", "unsupported_units"),
        (("hole_pattern",), "two-hole.v1", "feature_constraint_violation"),
        (("part_id",), "../plate", "invalid_part_ir"),
        (("part_id",), "", "invalid_part_ir"),
    ],
)
def test_rejects_unsupported_contract_values(
    valid_part_ir: dict[str, Any],
    path: tuple[str, ...],
    replacement: object,
    code: str,
) -> None:
    set_path(valid_part_ir, path, replacement)

    assert_error_code(valid_part_ir, code)


@pytest.mark.parametrize(
    ("path", "replacement"),
    [
        (("unexpected",), True),
        (("parameters", "unexpected"), "1.000"),
        (("parameters", "width_mm"), 120),
        (("parameters", "width_mm"), True),
        (("parameters", "width_mm"), "001.000"),
        (("parameters", "width_mm"), "1e2"),
        (("parameters", "width_mm"), "-1.000"),
        (("parameters", "width_mm"), "120.0000"),
    ],
)
def test_rejects_extra_and_noncanonical_values(
    valid_part_ir: dict[str, Any], path: tuple[str, ...], replacement: object
) -> None:
    set_path(valid_part_ir, path, replacement)

    assert_error_code(valid_part_ir, "invalid_part_ir")


def test_rejects_missing_required_field(valid_part_ir: dict[str, Any]) -> None:
    del valid_part_ir["parameters"]["hole_offset_y_mm"]

    assert_error_code(valid_part_ir, "invalid_part_ir")


@pytest.mark.parametrize(
    ("path", "replacement"),
    [
        (("parameters", "width_mm"), "19.999"),
        (("parameters", "width_mm"), "500.001"),
        (("parameters", "height_mm"), "19.999"),
        (("parameters", "height_mm"), "500.001"),
        (("parameters", "thickness_mm"), "0.999"),
        (("parameters", "thickness_mm"), "50.001"),
        (("parameters", "hole_diameter_mm"), "1.999"),
        (("parameters", "hole_diameter_mm"), "25.001"),
        (("parameters", "hole_offset_x_mm"), "4.999"),
        (("parameters", "hole_offset_x_mm"), "200.001"),
        (("parameters", "hole_offset_y_mm"), "4.999"),
        (("parameters", "hole_offset_y_mm"), "200.001"),
    ],
)
def test_rejects_dimensions_outside_inclusive_ranges(
    valid_part_ir: dict[str, Any], path: tuple[str, ...], replacement: str
) -> None:
    set_path(valid_part_ir, path, replacement)

    assert_error_code(valid_part_ir, "dimension_out_of_range")


def test_accepts_inclusive_dimension_range_boundaries(valid_part_ir: dict[str, Any]) -> None:
    valid_part_ir["parameters"] = {
        "width_mm": "20.000",
        "height_mm": "500.000",
        "thickness_mm": "1.000",
        "hole_diameter_mm": "2.000",
        "hole_offset_x_mm": "5.000",
        "hole_offset_y_mm": "5.000",
    }

    parse_part_ir(valid_part_ir)


@pytest.mark.parametrize("axis", ["x", "y"])
def test_minimum_edge_clearance_accepts_exact_two_mm(
    valid_part_ir: dict[str, Any], axis: str
) -> None:
    valid_part_ir["parameters"]["hole_diameter_mm"] = "25.000"
    valid_part_ir["parameters"][f"hole_offset_{axis}_mm"] = "14.500"
    valid_part_ir["parameters"][f"hole_offset_{'y' if axis == 'x' else 'x'}_mm"] = "14.500"

    parse_part_ir(valid_part_ir)


@pytest.mark.parametrize("axis", ["x", "y"])
def test_minimum_edge_clearance_rejects_one_thousandth_below_limit(
    valid_part_ir: dict[str, Any], axis: str
) -> None:
    valid_part_ir["parameters"]["hole_diameter_mm"] = "25.000"
    valid_part_ir["parameters"][f"hole_offset_{axis}_mm"] = "14.499"
    valid_part_ir["parameters"][f"hole_offset_{'y' if axis == 'x' else 'x'}_mm"] = "14.500"

    assert_error_code(valid_part_ir, "feature_constraint_violation")


@pytest.mark.parametrize(
    ("axis", "size_field", "offset"),
    [("x", "width_mm", "56.000"), ("y", "height_mm", "36.000")],
)
def test_minimum_inner_web_accepts_exact_two_mm(
    valid_part_ir: dict[str, Any], axis: str, size_field: str, offset: str
) -> None:
    valid_part_ir["parameters"]["hole_diameter_mm"] = "6.000"
    valid_part_ir["parameters"][f"hole_offset_{axis}_mm"] = offset
    valid_part_ir["parameters"][size_field] = "120.000" if axis == "x" else "80.000"

    parse_part_ir(valid_part_ir)


@pytest.mark.parametrize(
    ("axis", "size_field", "offset"),
    [("x", "width_mm", "56.000"), ("y", "height_mm", "36.000")],
)
def test_minimum_inner_web_rejects_one_thousandth_below_limit(
    valid_part_ir: dict[str, Any], axis: str, size_field: str, offset: str
) -> None:
    valid_part_ir["parameters"]["hole_diameter_mm"] = "6.001"
    valid_part_ir["parameters"][f"hole_offset_{axis}_mm"] = offset
    valid_part_ir["parameters"][size_field] = "120.000" if axis == "x" else "80.000"

    assert_error_code(valid_part_ir, "feature_constraint_violation")


def test_canonical_json_sorts_keys_without_changing_decimal_precision(
    valid_part_ir: dict[str, Any],
) -> None:
    reordered = {
        "hole_pattern": valid_part_ir["hole_pattern"],
        "parameters": dict(reversed(list(valid_part_ir["parameters"].items()))),
        "units": valid_part_ir["units"],
        "template": valid_part_ir["template"],
        "part_id": valid_part_ir["part_id"],
        "schema_version": valid_part_ir["schema_version"],
    }
    left = canonical_part_ir(parse_part_ir(valid_part_ir))
    right = canonical_part_ir(parse_part_ir(reordered))

    assert left == right
    assert json.loads(left)["parameters"]["width_mm"] == "120.000"
    assert left == json.dumps(
        json.loads(left), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def test_dimensions_convert_only_after_validation(valid_part_ir: dict[str, Any]) -> None:
    dimensions = part_dimensions(parse_part_ir(valid_part_ir))

    assert dimensions.width_mm == Decimal("120.000")
    assert dimensions.hole_offset_y_mm == Decimal("12.000")


def test_load_part_ir_accepts_a_valid_file(tmp_path: Path) -> None:
    path = tmp_path / "part.json"
    path.write_text(json.dumps(BASE_PART_IR), encoding="utf-8")

    assert load_part_ir(path).part_id == "mounting-plate-demo"


def test_checked_in_example_parses() -> None:
    example = Path(__file__).parents[2] / "examples" / "cad" / "part-ir.json"

    assert load_part_ir(example).part_id == "mounting-plate-demo"


@pytest.mark.parametrize(
    "content",
    [b"{", b"\xff", b'{"width":NaN}', b'{"units":"mm","units":"inch"}'],
)
def test_load_part_ir_rejects_malformed_or_ambiguous_json(tmp_path: Path, content: bytes) -> None:
    path = tmp_path / "invalid.json"
    path.write_bytes(content)

    with pytest.raises(PartIRError) as error:
        load_part_ir(path)
    assert error.value.code == "invalid_part_ir"


def test_load_part_ir_rejects_oversized_input(tmp_path: Path) -> None:
    path = tmp_path / "oversized.json"
    encoded = json.dumps(BASE_PART_IR).encode("utf-8")
    path.write_bytes(encoded + b" " * (64 * 1024 + 1 - len(encoded)))

    with pytest.raises(PartIRError) as error:
        load_part_ir(path)
    assert error.value.code == "invalid_part_ir"


def test_load_part_ir_error_does_not_echo_path(tmp_path: Path) -> None:
    path = tmp_path / "private-design-plate.json"

    with pytest.raises(PartIRError) as error:
        load_part_ir(path)

    assert error.value.code == "invalid_part_ir"
    assert str(error.value) == "invalid_part_ir"
    assert "private-design" not in str(error.value)


def test_load_part_ir_rejects_symlink_input(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "target.json"
    target.write_text(json.dumps(BASE_PART_IR), encoding="utf-8")
    link = tmp_path / "input.json"
    link.write_text(target.read_text(encoding="utf-8"), encoding="utf-8")
    monkeypatch.setattr(Path, "is_symlink", lambda candidate: candidate == link)

    with pytest.raises(PartIRError) as error:
        load_part_ir(link)
    assert error.value.code == "invalid_part_ir"
