"""Semantic geometry checks and deterministic GameIR encodings."""

from __future__ import annotations

import hashlib
import json
from collections import deque

from .errors import GameInputError
from .models import Cell, GameIR, WorldIR

MAX_GAME_WALL_CELLS = 192
MAX_GAME_ENTITIES = 256


def collect_entity_ids(ir: GameIR) -> tuple[str, ...]:
    """Return all entity identifiers, retaining duplicates for validation."""

    return (
        ir.player.id,
        *(wall.id for wall in ir.walls),
        *(objective.id for objective in ir.objectives),
        ir.exit.id,
    )


def reachable_cells(
    start: Cell,
    world: WorldIR,
    walls: frozenset[Cell],
) -> frozenset[Cell]:
    """Return cells reachable from start using four-way grid movement."""

    if not _within_world(start, world) or start in walls:
        return frozenset()
    visited = {start}
    pending = deque([start])
    while pending:
        x, y = pending.popleft()
        for neighbor in ((x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1)):
            if neighbor in visited or neighbor in walls or not _within_world(neighbor, world):
                continue
            visited.add(neighbor)
            pending.append(neighbor)
    return frozenset(visited)


def _within_world(cell: Cell, world: WorldIR) -> bool:
    x, y = cell
    return 0 <= x < world.width and 0 <= y < world.height


def canonicalize_game_ir(ir: GameIR) -> GameIR:
    """Sort repeated values so equivalent inputs have one representation."""

    walls = tuple(
        wall.model_copy(update={"cells": tuple(sorted(wall.cells, key=lambda cell: (cell[1], cell[0])))})
        for wall in sorted(ir.walls, key=lambda item: item.id)
    )
    objectives = tuple(sorted(ir.objectives, key=lambda item: item.id))
    return ir.model_copy(update={"walls": walls, "objectives": objectives})


def validate_game_ir(ir: GameIR) -> GameIR:
    """Reject invalid identities, occupancy, bounds, caps, and disconnected goals."""

    ids = collect_entity_ids(ir)
    if len(ids) != len(set(ids)):
        raise GameInputError("invalid_game_geometry")

    all_cells = (
        ir.player.spawn,
        *(cell for wall in ir.walls for cell in wall.cells),
        *(objective.cell for objective in ir.objectives),
        ir.exit.cell,
    )
    if any(not _within_world(cell, ir.world) for cell in all_cells):
        raise GameInputError("invalid_game_geometry")

    wall_cells = [cell for wall in ir.walls for cell in wall.cells]
    wall_cell_set = set(wall_cells)
    if len(wall_cell_set) != len(wall_cells):
        raise GameInputError("invalid_game_geometry")

    occupied_cells = (
        ir.player.spawn,
        *(objective.cell for objective in ir.objectives),
        ir.exit.cell,
    )
    if len(set(occupied_cells)) != len(occupied_cells) or wall_cell_set.intersection(occupied_cells):
        raise GameInputError("invalid_game_geometry")

    if len(wall_cells) > MAX_GAME_WALL_CELLS:
        raise GameInputError("resource_cap_exceeded")
    entity_count = len(wall_cells) + len(ir.objectives) + 2
    if entity_count > MAX_GAME_ENTITIES:
        raise GameInputError("resource_cap_exceeded")

    reachable = reachable_cells(ir.player.spawn, ir.world, frozenset(wall_cell_set))
    goals = {*(objective.cell for objective in ir.objectives), ir.exit.cell}
    if not goals <= reachable:
        raise GameInputError("invalid_game_geometry")

    return canonicalize_game_ir(ir)


def canonical_game_ir_bytes(ir: GameIR) -> bytes:
    """Serialize validated GameIR with canonical ordering and JSON encoding."""

    normalized = validate_game_ir(ir)
    return json.dumps(
        normalized.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def game_ir_sha256(ir: GameIR) -> str:
    return hashlib.sha256(canonical_game_ir_bytes(ir)).hexdigest()


def _cell_object(cell: Cell) -> dict[str, int]:
    return {"x": cell[0], "y": cell[1]}


def worker_dto_with_cell_objects(ir: GameIR) -> dict[str, object]:
    """Project GameIR into object-cell DTOs supported by Unity JsonUtility."""

    return {
        "schema_version": ir.schema_version,
        "scene_id": ir.scene_id,
        "template": ir.template,
        "palette": ir.palette,
        "world": {
            "width": ir.world.width,
            "height": ir.world.height,
            "tile_size": ir.world.tile_size,
        },
        "player": {
            "id": ir.player.id,
            "spawn": _cell_object(ir.player.spawn),
            "speed": ir.player.speed,
        },
        "walls": [
            {"id": wall.id, "cells": [_cell_object(cell) for cell in wall.cells]}
            for wall in ir.walls
        ],
        "objectives": [
            {"id": objective.id, "cell": _cell_object(objective.cell)}
            for objective in ir.objectives
        ],
        "exit": {"id": ir.exit.id, "cell": _cell_object(ir.exit.cell)},
    }


def to_unity_worker_json(ir: GameIR) -> bytes:
    """Serialize the canonical DTO consumed by the trusted Unity worker."""

    normalized = validate_game_ir(ir)
    return json.dumps(
        worker_dto_with_cell_objects(normalized),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


__all__ = [
    "MAX_GAME_ENTITIES",
    "MAX_GAME_WALL_CELLS",
    "canonical_game_ir_bytes",
    "canonicalize_game_ir",
    "collect_entity_ids",
    "game_ir_sha256",
    "reachable_cells",
    "to_unity_worker_json",
    "validate_game_ir",
    "worker_dto_with_cell_objects",
]
