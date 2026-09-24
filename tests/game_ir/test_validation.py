from __future__ import annotations

import copy
import json
from collections.abc import Sequence
from typing import Any

import pytest

from book_production_platform.game_ir import GameInputError, GameIR, parse_game_ir
from book_production_platform.game_ir.validation import (
    canonical_game_ir_bytes,
    canonicalize_game_ir,
    collect_entity_ids,
    game_ir_sha256,
    to_unity_worker_json,
    validate_game_ir,
    worker_dto_with_cell_objects,
)

BASE_GAME: dict[str, Any] = {
    "schema_version": "game-ir.v1",
    "scene_id": "crystal-room",
    "template": "top-down-collect.v1",
    "world": {"width": 8, "height": 6, "tile_size": 1.0},
    "player": {"id": "player", "spawn": [1, 1], "speed": 4.0},
    "walls": [{"id": "wall-a", "cells": [[3, 1], [3, 2]]}],
    "objectives": [{"id": "crystal-a", "cell": [4, 1]}],
    "exit": {"id": "exit", "cell": [6, 1]},
}


def parsed(value: dict[str, Any]) -> GameIR:
    return parse_game_ir(json.dumps(value, separators=(",", ":")).encode("utf-8"))


def set_path(value: dict[str, Any], path: Sequence[str | int], replacement: Any) -> None:
    current: Any = value
    for key in path[:-1]:
        current = current[key]
    current[path[-1]] = replacement


@pytest.mark.parametrize(
    ("path", "replacement"),
    [
        (("walls", 0, "id"), "player"),
        (("objectives", 0, "id"), "player"),
        (("exit", "id"), "player"),
    ],
    ids=["wall-id", "objective-id", "exit-id"],
)
def test_rejects_duplicate_ids_across_entity_kinds(
    path: Sequence[str | int], replacement: Any
) -> None:
    value = copy.deepcopy(BASE_GAME)
    set_path(value, path, replacement)

    with pytest.raises(GameInputError) as error:
        validate_game_ir(parsed(value))

    assert error.value.code == "invalid_game_geometry"


@pytest.mark.parametrize(
    ("path", "replacement"),
    [
        (("player", "spawn"), [4, 1]),
        (("player", "spawn"), [6, 1]),
        (("walls", 0, "cells", 0), [1, 1]),
        (("walls", 0, "cells", 0), [4, 1]),
        (("walls", 0, "cells", 0), [6, 1]),
        (("objectives", 0, "cell"), [1, 1]),
        (("objectives", 0, "cell"), [6, 1]),
    ],
    ids=[
        "spawn-objective",
        "spawn-exit",
        "wall-spawn",
        "wall-objective",
        "wall-exit",
        "objective-spawn",
        "objective-exit",
    ],
)
def test_rejects_overlapping_occupied_cells(
    path: Sequence[str | int], replacement: Any
) -> None:
    value = copy.deepcopy(BASE_GAME)
    set_path(value, path, replacement)

    with pytest.raises(GameInputError) as error:
        validate_game_ir(parsed(value))

    assert error.value.code == "invalid_game_geometry"


def test_rejects_two_objectives_in_the_same_cell() -> None:
    value = copy.deepcopy(BASE_GAME)
    value["objectives"].append({"id": "crystal-b", "cell": [4, 1]})

    with pytest.raises(GameInputError) as error:
        validate_game_ir(parsed(value))

    assert error.value.code == "invalid_game_geometry"


@pytest.mark.parametrize(
    ("path", "replacement"),
    [
        (("player", "spawn"), [8, 1]),
        (("walls", 0, "cells", 0), [8, 1]),
        (("objectives", 0, "cell"), [8, 1]),
        (("exit", "cell"), [8, 1]),
    ],
    ids=["player", "wall", "objective", "exit"],
)
def test_rejects_every_entity_kind_outside_world_bounds(
    path: Sequence[str | int], replacement: Any
) -> None:
    value = copy.deepcopy(BASE_GAME)
    set_path(value, path, replacement)

    with pytest.raises(GameInputError) as error:
        validate_game_ir(parsed(value))

    assert error.value.code == "invalid_game_geometry"


def test_rejects_duplicate_wall_cells() -> None:
    value = copy.deepcopy(BASE_GAME)
    value["walls"][0]["cells"].append([3, 1])

    with pytest.raises(GameInputError) as error:
        validate_game_ir(parsed(value))

    assert error.value.code == "invalid_game_geometry"


def test_rejects_more_than_192_wall_cells() -> None:
    value = copy.deepcopy(BASE_GAME)
    value["world"] = {"width": 64, "height": 64, "tile_size": 1.0}
    value["walls"] = [
        {"id": "wall-limit", "cells": [[x, y] for y in range(10, 14) for x in range(64)]}
    ]

    with pytest.raises(GameInputError) as error:
        validate_game_ir(parsed(value))

    assert error.value.code == "resource_cap_exceeded"


def test_accepts_maximum_representable_entity_count() -> None:
    value = copy.deepcopy(BASE_GAME)
    value["world"] = {"width": 64, "height": 64, "tile_size": 1.0}
    value["walls"] = [
        {"id": "wall-limit", "cells": [[x, y] for y in range(10, 13) for x in range(64)]}
    ]
    value["objectives"] = [
        {"id": f"objective-{index}", "cell": [index + 1, 2]}
        for index in range(32)
    ]

    result = validate_game_ir(parsed(value))

    assert len(result.walls[0].cells) + len(result.objectives) + 2 == 226


@pytest.mark.parametrize("unreachable_kind", ["objective", "exit"])
def test_rejects_unreachable_objective_or_exit_before_unity_launch(
    unreachable_kind: str,
) -> None:
    value = copy.deepcopy(BASE_GAME)
    value["walls"] = [
        {"id": "barrier", "cells": [[3, y] for y in range(value["world"]["height"])]}
    ]
    if unreachable_kind == "objective":
        value["objectives"] = [{"id": "crystal-a", "cell": [4, 1]}]
    else:
        value["objectives"] = [{"id": "crystal-a", "cell": [2, 2]}]
        value["exit"]["cell"] = [6, 1]

    with pytest.raises(GameInputError) as error:
        validate_game_ir(parsed(value))

    assert error.value.code == "invalid_game_geometry"


def test_canonicalization_makes_array_order_irrelevant() -> None:
    first = copy.deepcopy(BASE_GAME)
    first["walls"].append({"id": "wall-b", "cells": [[5, 4], [5, 3]]})
    first["objectives"].append({"id": "crystal-b", "cell": [2, 4]})
    second = copy.deepcopy(first)
    second["walls"].reverse()
    second["walls"][1]["cells"].reverse()
    second["objectives"].reverse()

    left = parsed(first)
    right = parsed(second)

    assert canonical_game_ir_bytes(left) == canonical_game_ir_bytes(right)
    assert game_ir_sha256(left) == game_ir_sha256(right)
    assert canonicalize_game_ir(right).walls[0].id == "wall-a"
    assert canonicalize_game_ir(right).walls[1].cells == ((5, 3), (5, 4))


def test_worker_json_projects_cells_as_json_utility_objects() -> None:
    ir = parsed(BASE_GAME)

    projection = worker_dto_with_cell_objects(ir)
    encoded = json.loads(to_unity_worker_json(ir))

    assert projection["player"]["spawn"] == {"x": 1, "y": 1}
    assert projection["walls"][0]["cells"][0] == {"x": 3, "y": 1}
    assert encoded["objectives"][0]["cell"] == {"x": 4, "y": 1}
    assert encoded["exit"]["cell"] == {"x": 6, "y": 1}


def test_collect_entity_ids_returns_every_scene_entity() -> None:
    ir = parsed(BASE_GAME)

    assert set(collect_entity_ids(ir)) == {"player", "wall-a", "crystal-a", "exit"}
