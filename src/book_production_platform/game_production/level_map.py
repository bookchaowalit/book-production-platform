"""Top-down level map (SVG + ASCII) drawn from GameIR, with the collect-all route.

Cell ``(x, y)`` uses GameIR coordinates (origin bottom-left, +Y up, the same
as Unity world space after ``CellCenter``), so row ``y = height - 1`` is drawn
first. The map is byte-deterministic for one GameIR and is a review aid, not a
render of the Unity scene.
"""

from __future__ import annotations

from xml.sax.saxutils import escape

from book_production_platform.game_ir import Cell, GameIR, validate_game_ir
from book_production_platform.game_production.metrics import LevelMetrics, level_metrics

MAP_PROFILE = "game-level-map.v1"
_CELL = 32
_COLORS = {
    "floor": "#1c2533",
    "wall": "#8a94a6",
    "player": "#3fa7ff",
    "objective": "#ffc23f",
    "exit": "#4fd18b",
    "route": "#ff5d73",
}


def ascii_map(ir: GameIR) -> str:
    """``#`` wall, ``P`` player spawn, ``o`` objective, ``E`` exit, ``.`` floor; top row first."""

    level = validate_game_ir(ir)
    grid = [["." for _ in range(level.world.width)] for _ in range(level.world.height)]
    for wall in level.walls:
        for x, y in wall.cells:
            grid[y][x] = "#"
    for objective in level.objectives:
        x, y = objective.cell
        grid[y][x] = "o"
    grid[level.exit.cell[1]][level.exit.cell[0]] = "E"
    grid[level.player.spawn[1]][level.player.spawn[0]] = "P"
    width = level.world.width
    header = "    " + "".join(str(x % 10) for x in range(width))
    rows = [f"{y:>3} " + "".join(grid[y]) for y in range(level.world.height - 1, -1, -1)]
    return "\n".join([header, *rows, header])


def render_level_svg(ir: GameIR, metrics: LevelMetrics | None = None) -> bytes:
    level = validate_game_ir(ir)
    route = (metrics or level_metrics(level)).route_cells
    width, height = level.world.width, level.world.height
    page_w, page_h = width * _CELL, height * _CELL

    def corner(cell: Cell) -> tuple[int, int]:
        return cell[0] * _CELL, (height - 1 - cell[1]) * _CELL

    def center(cell: Cell) -> str:
        x, y = corner(cell)
        return f"{x + _CELL // 2},{y + _CELL // 2}"

    out = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        (
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{page_w}" height="{page_h}" viewBox="0 0 {page_w} {page_h}" '
            f'data-profile="{MAP_PROFILE}" data-scene-id="{escape(level.scene_id)}">'
        ),
        f"<title>{escape(level.scene_id)} - {MAP_PROFILE}</title>",
        f'<rect width="{page_w}" height="{page_h}" fill="{_COLORS["floor"]}"/>',
        '<g id="grid" stroke="#2b3647" stroke-width="1">',
    ]
    out += [f'<line x1="{x * _CELL}" y1="0" x2="{x * _CELL}" y2="{page_h}"/>' for x in range(1, width)]
    out += [f'<line x1="0" y1="{y * _CELL}" x2="{page_w}" y2="{y * _CELL}"/>' for y in range(1, height)]
    out.append("</g>")
    out.append(f'<g id="walls" fill="{_COLORS["wall"]}">')
    for wall in level.walls:
        for cell in wall.cells:
            x, y = corner(cell)
            out.append(f'<rect x="{x}" y="{y}" width="{_CELL}" height="{_CELL}" data-wall="{escape(wall.id)}" data-cell="{cell[0]},{cell[1]}"/>')
    out.append("</g>")
    points = " ".join(center(cell) for cell in route)
    out.append(f'<polyline id="route" points="{points}" fill="none" stroke="{_COLORS["route"]}" stroke-width="3" stroke-dasharray="6 4"/>')
    out.append('<g id="entities" font-family="DejaVu Sans, Arial, sans-serif" font-size="10" text-anchor="middle">')
    for objective in level.objectives:
        x, y = corner(objective.cell)
        out.append(
            f'<circle cx="{x + _CELL // 2}" cy="{y + _CELL // 2}" r="{_CELL * 3 // 8}" fill="{_COLORS["objective"]}" '
            f'data-objective="{escape(objective.id)}" data-cell="{objective.cell[0]},{objective.cell[1]}"/>'
        )
    ex, ey = corner(level.exit.cell)
    out.append(
        f'<rect x="{ex + 3}" y="{ey + 3}" width="{_CELL - 6}" height="{_CELL - 6}" fill="{_COLORS["exit"]}" '
        f'data-exit="{escape(level.exit.id)}" data-cell="{level.exit.cell[0]},{level.exit.cell[1]}"/>'
    )
    px, py = corner(level.player.spawn)
    out.append(
        f'<rect x="{px + 5}" y="{py + 5}" width="{_CELL - 10}" height="{_CELL - 10}" fill="{_COLORS["player"]}" '
        f'data-player="{escape(level.player.id)}" data-cell="{level.player.spawn[0]},{level.player.spawn[1]}"/>'
    )
    out.append("</g>")
    out.append("</svg>")
    return ("\n".join(out) + "\n").encode("utf-8")


__all__ = ["MAP_PROFILE", "ascii_map", "render_level_svg"]
