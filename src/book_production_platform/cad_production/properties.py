"""Analytical geometry invariants for one validated mounting plate.

Everything here is computed from the PartIR dimensions alone (no FreeCAD), so
the manual guide, the 2D drawing, and a person checking their own model all
compare against the same numbers. Volume and area use ``math.pi``; mass uses
nominal handbook densities and is an estimate for quoting, not a QA value.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from decimal import Decimal

from book_production_platform.cad_ir import PartDimensions, PartIR, part_dimensions

#: Nominal densities in g/cm3 (= mg/mm3) for common plate stock. Values are
#: typical handbook figures; a real quote must use the supplier's datasheet.
MATERIAL_DENSITIES_G_CM3: dict[str, Decimal] = {
    "aluminium-6061": Decimal("2.70"),
    "steel-s235": Decimal("7.85"),
    "stainless-304": Decimal("8.00"),
    "acrylic-pmma": Decimal("1.18"),
    "abs": Decimal("1.04"),
    "pla": Decimal("1.24"),
}


@dataclass(frozen=True, slots=True)
class HoleCenter:
    label: str
    x_mm: Decimal
    y_mm: Decimal


@dataclass(frozen=True, slots=True)
class PlateProperties:
    dimensions: PartDimensions
    hole_centers: tuple[HoleCenter, ...]
    bounds_mm: tuple[Decimal, Decimal, Decimal, Decimal, Decimal, Decimal]
    volume_mm3: float
    surface_area_mm2: float
    hole_pitch_x_mm: Decimal
    hole_pitch_y_mm: Decimal
    edge_web_x_mm: Decimal
    edge_web_y_mm: Decimal
    inner_web_x_mm: Decimal
    inner_web_y_mm: Decimal
    center_of_mass_mm: tuple[Decimal, Decimal, Decimal]

    def mass_g(self, material: str) -> float:
        density = MATERIAL_DENSITIES_G_CM3[material]
        return self.volume_mm3 * float(density) / 1000.0


def hole_centers(dimensions: PartDimensions) -> tuple[HoleCenter, ...]:
    """The four bore centers in the order the FreeCAD worker sketches them."""

    width, height = dimensions.width_mm, dimensions.height_mm
    offset_x, offset_y = dimensions.hole_offset_x_mm, dimensions.hole_offset_y_mm
    return (
        HoleCenter("H1", offset_x, offset_y),
        HoleCenter("H2", width - offset_x, offset_y),
        HoleCenter("H3", width - offset_x, height - offset_y),
        HoleCenter("H4", offset_x, height - offset_y),
    )


def plate_properties(part: PartIR) -> PlateProperties:
    dimensions = part_dimensions(part)
    width = dimensions.width_mm
    height = dimensions.height_mm
    thickness = dimensions.thickness_mm
    diameter = dimensions.hole_diameter_mm
    radius = float(diameter) / 2
    hole_area = 4 * math.pi * radius * radius
    volume = (float(width) * float(height) - hole_area) * float(thickness)
    surface = (
        2 * (float(width) * float(height) - hole_area)
        + 2 * (float(width) + float(height)) * float(thickness)
        + 4 * math.pi * float(diameter) * float(thickness)
    )
    pitch_x = width - 2 * dimensions.hole_offset_x_mm
    pitch_y = height - 2 * dimensions.hole_offset_y_mm
    return PlateProperties(
        dimensions=dimensions,
        hole_centers=hole_centers(dimensions),
        bounds_mm=(Decimal(0), width, Decimal(0), height, Decimal(0), thickness),
        volume_mm3=volume,
        surface_area_mm2=surface,
        hole_pitch_x_mm=pitch_x,
        hole_pitch_y_mm=pitch_y,
        edge_web_x_mm=dimensions.hole_offset_x_mm - diameter / 2,
        edge_web_y_mm=dimensions.hole_offset_y_mm - diameter / 2,
        inner_web_x_mm=pitch_x - diameter,
        inner_web_y_mm=pitch_y - diameter,
        center_of_mass_mm=(width / 2, height / 2, thickness / 2),
    )


def mm(value: Decimal | float) -> str:
    """Format millimetres with exactly three decimals (the PartIR precision)."""

    if isinstance(value, Decimal):
        return f"{value.quantize(Decimal('0.001'))}"
    return f"{value:.3f}"


__all__ = [
    "MATERIAL_DENSITIES_G_CM3",
    "HoleCenter",
    "PlateProperties",
    "hole_centers",
    "mm",
    "plate_properties",
]
