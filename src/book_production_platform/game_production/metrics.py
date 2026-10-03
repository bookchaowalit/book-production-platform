"""Deterministic level metrics: shortest collect-all route and a difficulty band.

Everything is computed from validated GameIR on the same four-way grid the
validator uses for reachability. The route visits every objective and then
the exit with the fewest grid steps: exact (Held-Karp) for up to
``EXACT_ROUTE_LIMIT`` objectives, nearest-neighbour above that (reported as
``heuristic``). Time is ``steps x tile_size / speed`` seconds, an estimate for
a player who walks the grid route; the Unity controller also moves diagonally,
so a real run can be shorter. The difficulty band is a documented heuristic,
not a playtest result.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from itertools import combinations, pairwise

from book_production_platform.game_ir import Cell, GameIR, validate_game_ir

METRICS_PROFILE = "game-level-metrics.v1"
EXACT_ROUTE_LIMIT = 12
_DIRECTIONS = ((1, 0), (-1, 0), (0, 1), (0, -1))


@dataclass(frozen=True, slots=True)
class LevelMetrics:
    profile: str
    world_cells: int
    wall_cells: int
    walkable_cells: int
    reachable_cells: int
    open_ratio: float
    dead_ends: int
    junctions: int
    objective_count: int
    route_order: tuple[str, ...]
    route_steps: int
    route_method: str
    route_cells: tuple[Cell, ...]
    estimated_seconds: float
    farthest_objective_steps: int
    detour_ratio: float
    difficulty_score: float
    difficulty_band: str

    def to_dict(self) -> dict[str, object]:
        return {
            "profile": self.profile,
            "world_cells": self.world_cells,
            "wall_cells": self.wall_cells,
            "walkable_cells": self.walkable_cells,
            "reachable_cells": self.reachable_cells,
            "open_ratio": self.open_ratio,
            "dead_ends": self.dead_ends,
            "junctions": self.junctions,
            "objective_count": self.objective_count,
            "route_order": list(self.route_order),
            "route_steps": self.route_steps,
            "route_method": self.route_method,
            "estimated_seconds": self.estimated_seconds,
            "farthest_objective_steps": self.farthest_objective_steps,
            "detour_ratio": self.detour_ratio,
            "difficulty_score": self.difficulty_score,
            "difficulty_band": self.difficulty_band,
        }


def _neighbors(cell: Cell, width: int, height: int, walls: frozenset[Cell]) -> list[Cell]:
    x, y = cell
    found: list[Cell] = []
    for dx, dy in _DIRECTIONS:
        nx, ny = x + dx, y + dy
        if 0 <= nx < width and 0 <= ny < height and (nx, ny) not in walls:
            found.append((nx, ny))
    return found


def _bfs(start: Cell, width: int, height: int, walls: frozenset[Cell]) -> dict[Cell, Cell | None]:
    parents: dict[Cell, Cell | None] = {start: None}
    pending = deque([start])
    while pending:
        current = pending.popleft()
        for neighbor in _neighbors(current, width, height, walls):
            if neighbor not in parents:
                parents[neighbor] = current
                pending.append(neighbor)
    return parents


def _path(parents: dict[Cell, Cell | None], goal: Cell) -> list[Cell]:
    path: list[Cell] = []
    current: Cell | None = goal
    while current is not None:
        path.append(current)
        current = parents[current]
    return path[::-1]


def _best_order(distance: list[list[int]], count: int) -> tuple[list[int], str]:
    """Node 0 = spawn, 1..count = objectives, count+1 = exit."""

    exit_node = count + 1
    if count == 0:
        return [], "exact"
    if count <= EXACT_ROUTE_LIMIT:
        best: dict[tuple[int, int], tuple[int, int]] = {}
        for node in range(1, count + 1):
            best[(1 << (node - 1), node)] = (distance[0][node], 0)
        for size in range(2, count + 1):
            for subset in combinations(range(1, count + 1), size):
                mask = 0
                for node in subset:
                    mask |= 1 << (node - 1)
                for last in subset:
                    previous_mask = mask & ~(1 << (last - 1))
                    options = [
                        (best[(previous_mask, prior)][0] + distance[prior][last], prior)
                        for prior in subset
                        if prior != last
                    ]
                    best[(mask, last)] = min(options)
        full = (1 << count) - 1
        _, last = min((best[(full, node)][0] + distance[node][exit_node], node) for node in range(1, count + 1))
        order = [last]
        mask = full
        while len(order) < count:
            prior = best[(mask, order[-1])][1]
            mask &= ~(1 << (order[-1] - 1))
            order.append(prior)
        return order[::-1], "exact"
    remaining = set(range(1, count + 1))
    order = []
    current = 0
    while remaining:
        current = min(remaining, key=lambda node: (distance[current][node], node))
        order.append(current)
        remaining.remove(current)
    return order, "heuristic"


def level_metrics(ir: GameIR) -> LevelMetrics:
    """Compute route, reachability, and layout metrics for one validated level."""

    level = validate_game_ir(ir)
    width, height = level.world.width, level.world.height
    walls = frozenset(cell for wall in level.walls for cell in wall.cells)
    objectives = list(level.objectives)
    nodes: list[Cell] = [level.player.spawn, *(item.cell for item in objectives), level.exit.cell]
    trees = [_bfs(node, width, height, walls) for node in nodes]
    distance = [[len(_path(tree, goal)) - 1 for goal in nodes] for tree in trees]
    order, method = _best_order(distance, len(objectives))
    sequence = [0, *order, len(objectives) + 1]
    route: list[Cell] = [level.player.spawn]
    steps = 0
    for start, goal in pairwise(sequence):
        segment = _path(trees[start], nodes[goal])
        route.extend(segment[1:])
        steps += len(segment) - 1

    reachable = trees[0]
    walkable = width * height - len(walls)
    degrees = [len(_neighbors(cell, width, height, walls)) for cell in reachable]
    dead_ends = sum(1 for degree in degrees if degree == 1)
    junctions = sum(1 for degree in degrees if degree >= 3)
    direct = distance[0][len(objectives) + 1]
    detour = round(steps / direct, 3) if direct else float(steps)
    seconds = round(steps * level.world.tile_size / level.player.speed, 2)
    farthest = max(distance[0][index] for index in range(1, len(objectives) + 1))
    score = round(
        min(1.0, steps / 120) * 40
        + min(1.0, len(objectives) / 16) * 20
        + min(1.0, max(0.0, detour - 1) / 3) * 20
        + min(1.0, dead_ends / 12) * 10
        + min(1.0, len(walls) / max(1, width * height) / 0.4) * 10,
        1,
    )
    band = "easy" if score < 30 else "medium" if score < 55 else "hard"
    return LevelMetrics(
        profile=METRICS_PROFILE,
        world_cells=width * height,
        wall_cells=len(walls),
        walkable_cells=walkable,
        reachable_cells=len(reachable),
        open_ratio=round(walkable / (width * height), 4),
        dead_ends=dead_ends,
        junctions=junctions,
        objective_count=len(objectives),
        route_order=tuple(objectives[node - 1].id for node in order),
        route_steps=steps,
        route_method=method,
        route_cells=tuple(route),
        estimated_seconds=seconds,
        farthest_objective_steps=farthest,
        detour_ratio=detour,
        difficulty_score=score,
        difficulty_band=band,
    )


__all__ = ["EXACT_ROUTE_LIMIT", "METRICS_PROFILE", "LevelMetrics", "level_metrics"]
