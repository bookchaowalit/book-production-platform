from __future__ import annotations

import math
from decimal import Decimal

import pytest

from book_production_platform.bim_production.units import (
    CoverageGap,
    NormalizedQuantity,
    UnitMetadata,
    normalize_quantity,
    quantity_raw_value,
)


def si_unit(scale: str, dimension: int) -> UnitMetadata:
    return UnitMetadata(kind="si", name="METRE", prefix="MILLI", scale=scale, dimension=dimension)


def test_normalize_converts_millimetre_lengths_to_metres() -> None:
    result = normalize_quantity("2500.0", "IfcQuantityLength", si_unit("0.001", 1))

    assert isinstance(result, NormalizedQuantity)
    assert result.value == Decimal("2.5000")
    assert result.unit == "m"


def test_normalize_converts_square_and_cubic_metres() -> None:
    area = normalize_quantity("12.5", "IfcQuantityArea", si_unit("1", 2))
    volume = normalize_quantity("3.125", "IfcQuantityVolume", si_unit("1", 3))

    assert isinstance(area, NormalizedQuantity) and area.unit == "m2"
    assert isinstance(volume, NormalizedQuantity) and volume.unit == "m3"
    assert area.value == Decimal("12.5")
    assert volume.value == Decimal("3.125")


def test_normalize_counts_stay_unitless() -> None:
    result = normalize_quantity("4.0", "IfcQuantityCount", None)

    assert isinstance(result, NormalizedQuantity)
    assert result.value == Decimal("4.0")
    assert result.unit == "count"


def test_normalize_converts_millimetre_squares_exactly() -> None:
    result = normalize_quantity("2500000.0", "IfcQuantityArea", si_unit("0.000001", 2))

    assert isinstance(result, NormalizedQuantity)
    assert result.value == Decimal("2.500000")
    assert result.unit == "m2"


def test_normalize_rejects_counts_that_carry_a_unit() -> None:
    result = normalize_quantity("4.0", "IfcQuantityCount", si_unit("1", 3))

    assert isinstance(result, CoverageGap)
    assert result.reason == "quantity_unit_unsupported"


def test_normalize_reports_a_gap_when_a_physical_unit_is_missing() -> None:
    result = normalize_quantity("5.0", "IfcQuantityLength", None)

    assert isinstance(result, CoverageGap)
    assert result.reason == "quantity_unit_missing"


def test_normalize_rejects_dimension_mismatches() -> None:
    result = normalize_quantity("5.0", "IfcQuantityLength", si_unit("1", 2))

    assert isinstance(result, CoverageGap)
    assert result.reason == "quantity_unit_unsupported"


def test_normalize_rejects_unsupported_measure_kinds() -> None:
    result = normalize_quantity("5.0", "IfcQuantityWeight", None)

    assert isinstance(result, CoverageGap)
    assert result.reason == "quantity_type_unsupported"


@pytest.mark.parametrize("raw", [True, False])
def test_normalize_rejects_boolean_values(raw: bool) -> None:
    with pytest.raises(TypeError):
        normalize_quantity(raw, "IfcQuantityLength", si_unit("0.001", 1))


def test_normalize_rejects_non_finite_values() -> None:
    with pytest.raises(ValueError):
        normalize_quantity("NaN", "IfcQuantityLength", si_unit("0.001", 1))
    with pytest.raises(ValueError):
        normalize_quantity("Infinity", "IfcQuantityLength", si_unit("0.001", 1))


def test_quantity_raw_value_reads_the_typed_measure_attribute() -> None:
    class FakeQuantity:
        LengthMeasure = 2500.0

        def __getitem__(self, index: int) -> float:
            return 2500.0

    assert quantity_raw_value(FakeQuantity(), "IfcQuantityLength") == 2500.0


def test_quantity_raw_value_rejects_non_finite_numbers() -> None:
    class BrokenQuantity:
        LengthMeasure = float("nan")

        def __getitem__(self, index: int) -> float:
            return float("nan")

    with pytest.raises(ValueError) as error:
        quantity_raw_value(BrokenQuantity(), "IfcQuantityLength")

    assert "finite" in str(error.value)
    assert not math.isfinite(float("nan"))
