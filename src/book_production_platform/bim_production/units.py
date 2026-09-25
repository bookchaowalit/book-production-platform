"""Bounded unit resolution for the worker and exact SI normalization for the parent."""

from __future__ import annotations

import math
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

MEASURE_KINDS = (
    "IfcQuantityLength",
    "IfcQuantityArea",
    "IfcQuantityVolume",
    "IfcQuantityCount",
)

_DIMENSION_BY_KIND = {
    "IfcQuantityLength": 1,
    "IfcQuantityArea": 2,
    "IfcQuantityVolume": 3,
    "IfcQuantityCount": 0,
}
_DIMENSION_BY_SI_NAME = {
    "METRE": 1,
    "SQUARE_METRE": 2,
    "CUBIC_METRE": 3,
}
_PREFIX_EXPONENTS = {
    "EXA": 18,
    "PETA": 15,
    "TERA": 12,
    "GIGA": 9,
    "MEGA": 6,
    "KILO": 3,
    "HECTO": 2,
    "DECA": 1,
    None: 0,
    "DECI": -1,
    "CENTI": -2,
    "MILLI": -3,
    "MICRO": -6,
    "NANO": -9,
    "PICO": -12,
}
VALUE_ATTRIBUTE = {
    "IfcQuantityLength": "LengthMeasure",
    "IfcQuantityArea": "AreaValue",
    "IfcQuantityVolume": "VolumeValue",
    "IfcQuantityCount": "NumberCount",
}
COVERAGE_REASONS = (
    "quantity_missing",
    "quantity_ambiguous",
    "quantity_type_unsupported",
    "quantity_unit_missing",
    "quantity_unit_unsupported",
)


@dataclass(frozen=True, slots=True)
class UnitMetadata:
    """Bounded worker-resolved unit description with an exact decimal scale."""

    kind: str
    name: str
    prefix: str | None
    scale: str
    dimension: int


@dataclass(frozen=True, slots=True)
class CoverageGap:
    """A stable, non-numeric coverage finding; never a zero or an estimate."""

    reason: str


@dataclass(frozen=True, slots=True)
class NormalizedQuantity:
    """A quantity already converted to its canonical SI value and unit."""

    value: Decimal
    unit: str


CANONICAL_UNITS = {"1": "m", "2": "m2", "3": "m3"}


def measure_dimension(measure_kind: str) -> int | None:
    """Return the dimensional exponent a measure kind requires, if supported."""

    return _DIMENSION_BY_KIND.get(measure_kind)


def quantity_raw_value(quantity: Any, measure_kind: str) -> float:
    """Read the explicit numeric value of one supported quantity entity."""

    attribute = VALUE_ATTRIBUTE[measure_kind]
    value = getattr(quantity, attribute, None)
    if value is None:
        value = quantity[3]
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError("quantity value is not a real number")
    if not math.isfinite(float(value)):
        raise ValueError("quantity value is not finite")
    return float(value)


def _prefix_scale(prefix: str | None, dimension: int) -> Decimal:
    exponent = _PREFIX_EXPONENTS.get(prefix if prefix is not None else None)
    if exponent is None:
        raise ValueError("unsupported SI prefix")
    return Decimal(10) ** (exponent * dimension)


def _si_unit_metadata(unit: Any, dimension: int) -> UnitMetadata | None:
    name = str(unit.Name)
    if _DIMENSION_BY_SI_NAME.get(name) != dimension:
        return None
    prefix_value = getattr(unit, "Prefix", None)
    prefix = str(prefix_value) if prefix_value else None
    if prefix not in _PREFIX_EXPONENTS:
        return None
    scale = _prefix_scale(prefix, dimension)
    return UnitMetadata(
        kind="si", name=name, prefix=prefix, scale=str(scale), dimension=dimension
    )


def _conversion_unit_metadata(unit: Any, dimension: int) -> UnitMetadata | None:
    factor = Decimal(1)
    current = unit
    while current.is_a("IfcConversionBasedUnit"):
        conversion = getattr(current, "ConversionFactor", None)
        value = getattr(conversion, "ValueComponent", None)
        raw = getattr(value, "wrappedValue", value)
        if (
            isinstance(raw, bool)
            or not isinstance(raw, (int, float))
            or not math.isfinite(float(raw))
            or float(raw) <= 0
        ):
            return None
        factor *= Decimal(repr(float(raw)))
        current = getattr(conversion, "UnitComponent", None)
        if current is None:
            return None
    if not current.is_a("IfcSIUnit"):
        return None
    base = _si_unit_metadata(current, dimension)
    if base is None:
        return None
    scale = factor * Decimal(base.scale)
    return UnitMetadata(
        kind="conversion",
        name=str(unit.Name),
        prefix=None,
        scale=str(scale),
        dimension=dimension,
    )


def resolve_source_unit(model: Any, quantity: Any) -> UnitMetadata | CoverageGap:
    """Resolve the explicit quantity unit before the project default (worker side).

    Runs only inside the pinned FreeCAD runtime; ``model`` and ``quantity``
    stay opaque. Counts stay unitless and a unit on a count is unsupported.
    """

    try:
        from ifcopenshell.util import unit as unit_api  # type: ignore[import-not-found]
    except ImportError:  # pragma: no cover - worker-only dependency
        raise RuntimeError("resolve_source_unit requires the pinned worker runtime")

    measure_class = quantity.is_a()
    dimension = _DIMENSION_BY_KIND.get(measure_class)
    if dimension is None:
        return CoverageGap("quantity_type_unsupported")
    unit = unit_api.get_property_unit(quantity, model)
    if dimension == 0:
        if unit is None:
            return UnitMetadata(
                kind="si", name="", prefix=None, scale="1", dimension=0
            )
        return CoverageGap("quantity_unit_unsupported")
    if unit is None:
        return CoverageGap("quantity_unit_missing")
    if unit.is_a("IfcSIUnit"):
        metadata = _si_unit_metadata(unit, dimension)
    elif unit.is_a("IfcConversionBasedUnit"):
        metadata = _conversion_unit_metadata(unit, dimension)
    else:
        metadata = None
    if metadata is None:
        return CoverageGap("quantity_unit_unsupported")
    return metadata


def normalize_quantity(
    raw_value: str,
    measure_kind: str,
    unit: UnitMetadata | None,
) -> NormalizedQuantity | CoverageGap:
    """Convert one finite source value to canonical SI with exact decimals."""

    dimension = _DIMENSION_BY_KIND.get(measure_kind)
    if dimension is None:
        return CoverageGap("quantity_type_unsupported")
    if isinstance(raw_value, bool) or not isinstance(raw_value, (str, int, float)):
        raise TypeError("quantity value must be a decimal string")
    try:
        value = Decimal(str(raw_value))
    except ArithmeticError:
        raise ValueError("quantity value is not a decimal") from None
    if not value.is_finite():
        raise ValueError("quantity value is not finite")
    if dimension == 0:
        if unit is not None:
            return CoverageGap("quantity_unit_unsupported")
        return NormalizedQuantity(value=value, unit="count")
    if unit is None:
        return CoverageGap("quantity_unit_missing")
    if unit.dimension != dimension:
        return CoverageGap("quantity_unit_unsupported")
    scale = Decimal(unit.scale)
    if not scale.is_finite() or scale <= 0:
        return CoverageGap("quantity_unit_unsupported")
    canonical = CANONICAL_UNITS[str(dimension)]
    return NormalizedQuantity(value=value * scale, unit=canonical)
