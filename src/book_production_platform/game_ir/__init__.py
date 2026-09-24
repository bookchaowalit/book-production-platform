"""Game Intermediate Representation contracts and validation."""

from .errors import GameInputError
from .models import (
    GAME_IR_SCHEMA_VERSION,
    GAME_PALETTE,
    GAME_TEMPLATE,
    MAX_GAME_IR_BYTES,
    Cell,
    ExitIR,
    GameIR,
    ObjectiveIR,
    PlayerIR,
    WallIR,
    WorldIR,
    parse_game_ir,
)

__all__ = [
    "GAME_IR_SCHEMA_VERSION",
    "GAME_PALETTE",
    "GAME_TEMPLATE",
    "MAX_GAME_IR_BYTES",
    "Cell",
    "ExitIR",
    "GameIR",
    "GameInputError",
    "ObjectiveIR",
    "PlayerIR",
    "WallIR",
    "WorldIR",
    "parse_game_ir",
]
