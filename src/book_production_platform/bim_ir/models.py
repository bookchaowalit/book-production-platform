"""Strict, versioned BimTakeoffIR input contract for BIM quantity takeoff."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, Literal, NoReturn

from pydantic import BaseModel, ConfigDict, Field, ValidationError

BIM_TAKEOFF_SCHEMA_VERSION = "bim-takeoff.v1"
MAX_BIM_TAKEOFF_IR_BYTES = 64 * 1024
TAKEOFF_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$"
SUPPORTED_ELEMENT_CLASSES: tuple[str, ...] = (
    "IfcWall",
    "IfcSlab",
    "IfcColumn",
    "IfcBeam",
    "IfcDoor",
    "IfcWindow",
)

TakeoffIdentifier = Annotated[str, Field(pattern=TAKEOFF_ID_PATTERN)]
SupportedElementClass = Literal[
    "IfcWall", "IfcSlab", "IfcColumn", "IfcBeam", "IfcDoor", "IfcWindow"
]


class BimTakeoffIRError(ValueError):
    """A BimTakeoffIR request failed a stable validation rule."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class BimTakeoffIR(_StrictModel):
    """Versioned, declarative takeoff request; quantities are never part of it."""

    schema_version: Literal["bim-takeoff.v1"]
    takeoff_id: TakeoffIdentifier
    element_classes: tuple[SupportedElementClass, ...] = Field(min_length=1)
    output_units: Literal["SI"]


def _invalid_bim_takeoff_ir() -> NoReturn:
    raise BimTakeoffIRError("invalid_bim_takeoff_ir")


def _reject_non_finite(value: str) -> NoReturn:
    raise ValueError(f"non-finite JSON constant: {value}")


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON object key")
        result[key] = value
    return result


def parse_bim_takeoff_ir(value: object) -> BimTakeoffIR:
    """Validate structure, safe identifiers, and unique allowlisted classes."""

    if not isinstance(value, dict):
        _invalid_bim_takeoff_ir()
    if "output_units" in value and value["output_units"] != "SI":
        _invalid_bim_takeoff_ir()
    classes = value.get("element_classes")
    if not isinstance(classes, (list, tuple)):
        _invalid_bim_takeoff_ir()
    try:
        ir = BimTakeoffIR.model_validate(
            {**value, "element_classes": tuple(classes)}
        )
    except ValidationError:
        _invalid_bim_takeoff_ir()
    if len(set(ir.element_classes)) != len(ir.element_classes):
        _invalid_bim_takeoff_ir()
    return ir


def load_bim_takeoff_ir(path: Path) -> BimTakeoffIR:
    """Load bounded UTF-8 JSON, rejecting links, duplicate keys, and non-finite values."""

    if not isinstance(path, Path):
        _invalid_bim_takeoff_ir()
    try:
        if path.is_symlink() or path.is_junction():
            _invalid_bim_takeoff_ir()
        with path.open("rb") as stream:
            payload = stream.read(MAX_BIM_TAKEOFF_IR_BYTES + 1)
        if len(payload) > MAX_BIM_TAKEOFF_IR_BYTES:
            _invalid_bim_takeoff_ir()
        value = json.loads(
            payload.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_non_finite,
        )
    except BimTakeoffIRError:
        raise
    except (OSError, UnicodeDecodeError, ValueError, TypeError, RecursionError):
        _invalid_bim_takeoff_ir()
    return parse_bim_takeoff_ir(value)


def canonical_bim_takeoff_ir(ir: BimTakeoffIR) -> bytes:
    """Return canonical UTF-8 JSON bytes, key-sorted, compact, newline-terminated."""

    validated = parse_bim_takeoff_ir(ir.model_dump(mode="python"))
    return (
        json.dumps(
            validated.model_dump(mode="json"),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        + b"\n"
    )
