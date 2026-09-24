"""Strict, versioned models and parser for GameIR v1."""

from __future__ import annotations

import json
from typing import Annotated, Literal, NoReturn

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, ValidationError

from .errors import GameInputError

MAX_GAME_IR_BYTES = 256 * 1024
GAME_IR_SCHEMA_VERSION = "game-ir.v1"
GAME_TEMPLATE = "top-down-collect.v1"
GAME_PALETTE: Literal["palette-minimal.v1"] = "palette-minimal.v1"


def _array_to_tuple(value: object) -> object:
    if isinstance(value, list):
        return tuple(value)
    return value


Cell = Annotated[tuple[int, int], BeforeValidator(_array_to_tuple)]
EntityId = Annotated[str, Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$", max_length=64)]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class WorldIR(StrictModel):
    width: int = Field(ge=3, le=64)
    height: int = Field(ge=3, le=64)
    tile_size: float = Field(ge=0.25, le=4.0)


class PlayerIR(StrictModel):
    id: EntityId
    spawn: Cell
    speed: float = Field(ge=0.5, le=12.0)


class WallIR(StrictModel):
    id: EntityId
    cells: Annotated[tuple[Cell, ...], BeforeValidator(_array_to_tuple)]


class ObjectiveIR(StrictModel):
    id: EntityId
    cell: Cell


class ExitIR(StrictModel):
    id: EntityId
    cell: Cell


class GameIR(StrictModel):
    schema_version: Literal["game-ir.v1"]
    scene_id: EntityId
    template: Literal["top-down-collect.v1"]
    palette: Literal["palette-minimal.v1"] = GAME_PALETTE
    world: WorldIR
    player: PlayerIR
    walls: Annotated[tuple[WallIR, ...], BeforeValidator(_array_to_tuple)]
    objectives: Annotated[tuple[ObjectiveIR, ...], BeforeValidator(_array_to_tuple)] = Field(
        min_length=1,
        max_length=32,
    )
    exit: ExitIR


def _reject_json_constant(value: str) -> NoReturn:
    raise ValueError(f"non-finite JSON constant: {value}")


def parse_game_ir(payload: bytes) -> GameIR:
    """Parse UTF-8 JSON bytes while enforcing the bounded strict contract."""

    if len(payload) > MAX_GAME_IR_BYTES:
        raise GameInputError("resource_cap_exceeded")
    try:
        value = json.loads(payload.decode("utf-8"), parse_constant=_reject_json_constant)
        return GameIR.model_validate(value)
    except (UnicodeDecodeError, ValueError, ValidationError) as exc:
        raise GameInputError("invalid_game_ir") from exc
