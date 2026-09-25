"""Strict PartIR contracts and deterministic mounting-plate validation."""

from .models import (
    FOUR_CORNER_PATTERN,
    MAX_PART_IR_BYTES,
    MOUNTING_PLATE_TEMPLATE,
    PART_IR_SCHEMA_VERSION,
    PartDimensions,
    PartIR,
    PartIRError,
    PartParameters,
    canonical_part_ir,
    load_part_ir,
    parse_part_ir,
    part_dimensions,
)

__all__ = [
    "FOUR_CORNER_PATTERN",
    "MAX_PART_IR_BYTES",
    "MOUNTING_PLATE_TEMPLATE",
    "PART_IR_SCHEMA_VERSION",
    "PartDimensions",
    "PartIR",
    "PartIRError",
    "PartParameters",
    "canonical_part_ir",
    "load_part_ir",
    "parse_part_ir",
    "part_dimensions",
]
