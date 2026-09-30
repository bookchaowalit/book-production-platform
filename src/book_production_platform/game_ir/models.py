"""Strict, versioned models and parser for GameIR v1 and v2.

v2 adds optional audio from a local asset root (``assets`` + ``audio``);
everything else is identical to v1, and a v1 document never carries
either field so its canonical bytes and fingerprint stay unchanged.
"""

from __future__ import annotations

import json
from typing import Annotated, Literal, NoReturn

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    ValidationError,
    model_validator,
)

from .errors import GameInputError

MAX_GAME_IR_BYTES = 256 * 1024
GAME_IR_SCHEMA_VERSION = "game-ir.v1"
GAME_IR_V2_SCHEMA_VERSION = "game-ir.v2"
GAME_IR_SCHEMA_VERSIONS = (GAME_IR_SCHEMA_VERSION, GAME_IR_V2_SCHEMA_VERSION)
MAX_GAME_ASSETS = 4
AUDIO_EXTENSIONS = (".wav", ".ogg")
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


AssetFilename = Annotated[
    str, Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,62}\.(?:wav|ogg)$", max_length=68)
]
Sha256Hex = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class AssetIR(StrictModel):
    """One audio file inside the build's asset root, pinned by content hash."""

    asset_id: EntityId
    kind: Literal["audio"]
    filename: AssetFilename
    sha256: Sha256Hex


class AudioIR(StrictModel):
    music: EntityId | None = None
    collect_sfx: EntityId | None = None


class GameIR(StrictModel):
    schema_version: Literal["game-ir.v1", "game-ir.v2"]
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
    assets: Annotated[tuple[AssetIR, ...], BeforeValidator(_array_to_tuple)] = Field(
        default=(),
        max_length=MAX_GAME_ASSETS,
    )
    audio: AudioIR | None = None

    @model_validator(mode="after")
    def _assets_match_version(self) -> GameIR:
        if self.schema_version == GAME_IR_SCHEMA_VERSION:
            if self.assets or self.audio is not None:
                raise ValueError("game-ir.v1 does not accept assets or audio")
            return self
        ids = [asset.asset_id for asset in self.assets]
        if len(ids) != len(set(ids)):
            raise ValueError("asset ids must be unique")
        audio_ids = {asset.asset_id for asset in self.assets if asset.kind == "audio"}
        refs = [] if self.audio is None else [self.audio.music, self.audio.collect_sfx]
        if any(ref is not None and ref not in audio_ids for ref in refs):
            raise ValueError("audio references must name an audio asset")
        return self


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
