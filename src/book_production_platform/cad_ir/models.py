"""Strict, versioned PartIR input contract for CAD production."""

from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Annotated, Literal, NoReturn

from pydantic import BaseModel, ConfigDict, Field, ValidationError

PART_IR_SCHEMA_VERSION = "part-ir.v1"
MOUNTING_PLATE_TEMPLATE = "mounting-plate.v1"
FOUR_CORNER_PATTERN = "four-corner.v1"
MAX_PART_IR_BYTES = 64 * 1024
DECIMAL_MM_PATTERN = r"^(0|[1-9][0-9]{0,2})(\.[0-9]{1,3})?$"
PART_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$"

DecimalMM = Annotated[str, Field(pattern=DECIMAL_MM_PATTERN)]
PartIdentifier = Annotated[str, Field(pattern=PART_ID_PATTERN)]


class PartIRError(ValueError):
    """A PartIR request failed a stable validation rule."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class PartParameters(_StrictModel):
    """Decimal-string dimensions for one rectangular mounting plate."""

    width_mm: DecimalMM
    height_mm: DecimalMM
    thickness_mm: DecimalMM
    hole_diameter_mm: DecimalMM
    hole_offset_x_mm: DecimalMM
    hole_offset_y_mm: DecimalMM


class PartIR(_StrictModel):
    """Versioned, declarative mounting-plate request."""

    schema_version: Literal["part-ir.v1"]
    part_id: PartIdentifier
    template: Literal["mounting-plate.v1"]
    units: Literal["mm"]
    parameters: PartParameters
    hole_pattern: Literal["four-corner.v1"]


@dataclass(frozen=True, slots=True)
class PartDimensions:
    """Validated millimeter dimensions represented exactly as Decimal."""

    width_mm: Decimal
    height_mm: Decimal
    thickness_mm: Decimal
    hole_diameter_mm: Decimal
    hole_offset_x_mm: Decimal
    hole_offset_y_mm: Decimal


_RANGES: dict[str, tuple[Decimal, Decimal]] = {
    "width_mm": (Decimal(20), Decimal(500)),
    "height_mm": (Decimal(20), Decimal(500)),
    "thickness_mm": (Decimal(1), Decimal(50)),
    "hole_diameter_mm": (Decimal(2), Decimal(25)),
    "hole_offset_x_mm": (Decimal(5), Decimal(200)),
    "hole_offset_y_mm": (Decimal(5), Decimal(200)),
}
_MIN_WEB_MM = Decimal(2)


def _invalid_part_ir() -> NoReturn:
    raise PartIRError("invalid_part_ir")


def _reject_non_finite(value: str) -> NoReturn:
    raise ValueError(f"non-finite JSON constant: {value}")


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON object key")
        result[key] = value
    return result


def _validated_dimensions(parameters: PartParameters) -> PartDimensions:
    values = {
        name: Decimal(getattr(parameters, name))
        for name in _RANGES
    }
    if any(not low <= values[name] <= high for name, (low, high) in _RANGES.items()):
        raise PartIRError("dimension_out_of_range")

    diameter = values["hole_diameter_mm"]
    offset_x = values["hole_offset_x_mm"]
    offset_y = values["hole_offset_y_mm"]
    if (
        offset_x - diameter / 2 < _MIN_WEB_MM
        or offset_y - diameter / 2 < _MIN_WEB_MM
        or values["width_mm"] - 2 * offset_x - diameter < _MIN_WEB_MM
        or values["height_mm"] - 2 * offset_y - diameter < _MIN_WEB_MM
    ):
        raise PartIRError("feature_constraint_violation")

    return PartDimensions(**values)


def parse_part_ir(value: object) -> PartIR:
    """Validate structure, exact decimals, supported features, and minimum webs."""

    if not isinstance(value, dict):
        _invalid_part_ir()
    if "template" in value and value["template"] != MOUNTING_PLATE_TEMPLATE:
        raise PartIRError("unsupported_template")
    if "units" in value and value["units"] != "mm":
        raise PartIRError("unsupported_units")
    if "hole_pattern" in value and value["hole_pattern"] != FOUR_CORNER_PATTERN:
        raise PartIRError("feature_constraint_violation")

    try:
        part = PartIR.model_validate(value)
    except ValidationError:
        raise PartIRError("invalid_part_ir") from None
    _validated_dimensions(part.parameters)
    return part


def load_part_ir(path: Path) -> PartIR:
    """Load bounded UTF-8 JSON, rejecting links, duplicate keys, and non-finite values."""

    if not isinstance(path, Path):
        _invalid_part_ir()
    try:
        if path.is_symlink() or path.is_junction():
            _invalid_part_ir()
        with path.open("rb") as stream:
            payload = stream.read(MAX_PART_IR_BYTES + 1)
        if len(payload) > MAX_PART_IR_BYTES:
            _invalid_part_ir()
        value = json.loads(
            payload.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_non_finite,
        )
    except PartIRError:
        raise
    except (OSError, UnicodeDecodeError, ValueError, TypeError, RecursionError):
        raise PartIRError("invalid_part_ir") from None
    return parse_part_ir(value)


def canonical_part_ir(part: PartIR) -> bytes:
    """Return canonical UTF-8 JSON bytes without changing decimal precision."""

    validated = parse_part_ir(part.model_dump(mode="python"))
    return json.dumps(
        validated.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def part_dimensions(part: PartIR) -> PartDimensions:
    """Convert only a structurally and geometrically valid part into Decimal units."""

    validated = parse_part_ir(part.model_dump(mode="python"))
    return _validated_dimensions(validated.parameters)
