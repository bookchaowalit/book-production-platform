from __future__ import annotations

import json
from pathlib import Path

import pytest

from book_production_platform.game_ir import (
    MAX_GAME_IR_BYTES,
    GameInputError,
    parse_game_ir,
)

VALID_GAME = {
    "schema_version": "game-ir.v1",
    "scene_id": "crystal-room",
    "template": "top-down-collect.v1",
    "world": {"width": 8, "height": 6, "tile_size": 1.0},
    "player": {"id": "player", "spawn": [1, 1], "speed": 4.0},
    "walls": [{"id": "wall-a", "cells": [[3, 1], [3, 2]]}],
    "objectives": [{"id": "crystal-a", "cell": [4, 1]}],
    "exit": {"id": "exit", "cell": [6, 1]},
}


def encode_game(value: dict[str, object]) -> bytes:
    return json.dumps(value, separators=(",", ":")).encode("utf-8")


def test_parse_accepts_minimal_valid_game_and_uses_allowlisted_palette_default() -> None:
    game = parse_game_ir(encode_game(VALID_GAME))

    assert game.schema_version == "game-ir.v1"
    assert game.scene_id == "crystal-room"
    assert game.palette == "palette-minimal.v1"
    assert game.world.width == 8
    assert game.player.spawn == (1, 1)
    assert game.objectives[0].cell == (4, 1)


def test_parse_accepts_payload_exactly_at_byte_cap() -> None:
    document = encode_game(VALID_GAME)
    payload = b" " * (MAX_GAME_IR_BYTES - len(document)) + document

    assert len(payload) == MAX_GAME_IR_BYTES
    assert parse_game_ir(payload).scene_id == "crystal-room"


def test_parse_rejects_payload_over_byte_cap_before_json_parsing() -> None:
    payload = b" " * (MAX_GAME_IR_BYTES + 1)

    with pytest.raises(GameInputError) as error:
        parse_game_ir(payload)

    assert error.value.code == "resource_cap_exceeded"


@pytest.mark.parametrize(
    "payload",
    [
        b"\xff",
        b"{",
        b'{"schema_version":"game-ir.v1","value":NaN}',
        b'{"schema_version":"game-ir.v1","value":Infinity}',
        b'{"schema_version":"game-ir.v1","value":-Infinity}',
    ],
    ids=["invalid-utf8", "malformed-json", "nan", "positive-infinity", "negative-infinity"],
)
def test_parse_rejects_invalid_json_encodings_and_non_finite_constants(payload: bytes) -> None:
    with pytest.raises(GameInputError) as error:
        parse_game_ir(payload)

    assert error.value.code == "invalid_game_ir"


def test_parse_rejects_unknown_fields() -> None:
    value = {**VALID_GAME, "unexpected": True}

    with pytest.raises(GameInputError) as error:
        parse_game_ir(encode_game(value))

    assert error.value.code == "invalid_game_ir"


def test_parse_rejects_unsupported_schema_version() -> None:
    value = {**VALID_GAME, "schema_version": "game-ir.v2"}

    with pytest.raises(GameInputError) as error:
        parse_game_ir(encode_game(value))

    assert error.value.code == "invalid_game_ir"


def test_parse_rejects_missing_required_field() -> None:
    value = {key: item for key, item in VALID_GAME.items() if key != "world"}

    with pytest.raises(GameInputError) as error:
        parse_game_ir(encode_game(value))

    assert error.value.code == "invalid_game_ir"


@pytest.mark.parametrize(
    ("section", "field", "value"),
    [
        ("world", "width", True),
        ("world", "width", 8.0),
        ("world", "width", 2),
        ("world", "tile_size", 4.1),
        ("player", "speed", 0.1),
        ("player", "id", "../../scene"),
        ("player", "spawn", [1, True]),
    ],
    ids=[
        "boolean-is-not-an-integer",
        "float-is-not-an-integer",
        "world-too-small",
        "tile-size-too-large",
        "speed-too-small",
        "unsafe-id",
        "boolean-cell-coordinate",
    ],
)
def test_parse_rejects_values_outside_strict_model_contract(
    section: str, field: str, value: object
) -> None:
    changed = {**VALID_GAME, section: {**VALID_GAME[section], field: value}}

    with pytest.raises(GameInputError) as error:
        parse_game_ir(encode_game(changed))

    assert error.value.code == "invalid_game_ir"


def test_game_ir_schema_documents_version_limits_and_palette_allowlist() -> None:
    schema_path = (
        Path(__file__).parents[2]
        / "src"
        / "book_production_platform"
        / "game_ir"
        / "game-ir.v1.schema.json"
    )
    schema = json.loads(schema_path.read_text(encoding="utf-8"))

    assert schema["$id"].endswith("game-ir.v1.schema.json")
    assert schema["properties"]["schema_version"]["const"] == "game-ir.v1"
    assert schema["properties"]["palette"]["default"] == "palette-minimal.v1"
    world_schema = schema["$defs"]["WorldIR"]["properties"]
    assert world_schema["width"]["maximum"] == 64
    assert world_schema["height"]["minimum"] == 3
    assert schema["x-maxBytes"] == MAX_GAME_IR_BYTES
