"""Centre text optically on the button, badge, or box it sits on.

A text box placed over a shape is positioned by its line box, but what the eye
sees is the ink: "85.-" starts far below the ascender line, "สั่ง" carries tone
marks above it. So text that looks centred in the numbers sits high or low on
its button. After a design compiles, every text that sits on a small, tight
shape drawn before it that holds no other text (a CTA pill, a price tag, a
label chip) is moved so its
visible glyphs are centred vertically on that shape (and horizontally, when the
text is centre-aligned). Big panels and cards are left alone: they hold a
designed stack, not a single label.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from book_production_platform.graphics_ir import GroupNode, ShapeNode, TextNode

from .fonts import ink_extent_em
from .layout import wrap_text

LINE = 1.2  # line advance used by the renderer (em)
MAX_SHAPE_AREA = 0.08  # of the canvas: anything bigger is a panel, not a button
FALLBACK_INK = (0.24, 1.0)  # em, typical when the font files are not installed


def _ink(node: TextNode, lines: Sequence[str]) -> tuple[float, float]:
    first = ink_extent_em(lines[0], node.font_ref, node.font_weight)
    last = ink_extent_em(lines[-1], node.font_ref, node.font_weight) if len(lines) > 1 else first
    top = first[0] if first else FALLBACK_INK[0]
    bottom = last[1] if last else FALLBACK_INK[1]
    return top, bottom


def _container(box: tuple[float, float, float, float], shapes: list[tuple[int, tuple[float, float, float, float]]],
               order: int, ink_height: float, centres: list[tuple[float, float]]) -> tuple[float, float, float, float] | None:
    x0, y0, x1, y1 = box
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    best = None
    for index, (sx0, sy0, sx1, sy1) in shapes:
        if index >= order or not (sx0 <= cx <= sx1 and sy0 <= cy <= sy1):
            continue
        overlap = max(0.0, min(x1, sx1) - max(x0, sx0)) * max(0.0, min(y1, sy1) - max(y0, sy0))
        if overlap < 0.6 * max((x1 - x0) * (y1 - y0), 1):
            continue
        if sy1 - sy0 > max(ink_height * 6, 1):
            continue  # far taller than the label: a panel with room for more
        if sum(1 for tx, ty in centres if sx0 <= tx <= sx1 and sy0 <= ty <= sy1) != 1:
            continue  # holds a stack of texts, not one label
        if best is None or (sx1 - sx0) * (sy1 - sy0) < (best[2] - best[0]) * (best[3] - best[1]):
            best = (sx0, sy0, sx1, sy1)
    return best


def center_text_on_boxes(nodes: list[Any], width: float, height: float) -> list[Any]:
    """Return ``nodes`` with labels re-centred on their buttons/boxes (groups are walked; order is draw order)."""

    flat: list[tuple[Any, float, float]] = []

    def walk(items: list[Any], dx: float, dy: float) -> None:
        for node in items:
            if isinstance(node, GroupNode):
                walk(list(node.children), dx + node.offset_x, dy + node.offset_y)
            else:
                flat.append((node, dx, dy))

    walk(nodes, 0.0, 0.0)
    canvas = width * height
    shapes = [
        (index, (node.x + dx, node.y + dy, node.x + dx + node.width, node.y + dy + node.height))
        for index, (node, dx, dy) in enumerate(flat)
        if isinstance(node, ShapeNode) and node.kind in ("rect", "ellipse") and node.node_id != "bg" and node.fill != "none"
        and node.opacity >= 0.5 and node.width * node.height <= canvas * MAX_SHAPE_AREA
    ]
    centres = [
        (node.x + dx + node.width / 2, node.y + dy + min(node.height, node.font_size * LINE) / 2)
        for node, dx, dy in flat
        if isinstance(node, TextNode) and not node.bleed and node.text.strip() and not node.node_id.endswith("-shadow")
    ]
    moves: dict[str, tuple[float, float]] = {}
    for index, (node, dx, dy) in enumerate(flat):
        if (not isinstance(node, TextNode) or node.bleed or node.rotation or node.node_id.endswith("-shadow")
                or not node.text.strip()):
            continue
        lines = wrap_text(node.text, node.width, node.font_size, node.char_width) or [node.text]
        top, bottom = _ink(node, lines)
        ink_height = (len(lines) - 1) * LINE * node.font_size + (bottom - top) * node.font_size
        used = (node.x + dx, node.y + dy, node.x + dx + node.width, node.y + dy + min(node.height, len(lines) * LINE * node.font_size))
        box = _container(used, shapes, index, ink_height, centres)
        if box is None:
            continue
        centre_y = (box[1] + box[3]) / 2
        new_y = centre_y - (top * node.font_size + (len(lines) - 1) * LINE * node.font_size + bottom * node.font_size) / 2
        shift_y = new_y - (node.y + dy)
        shift_x = 0.0
        if node.align == "center":
            shift_x = (box[0] + box[2]) / 2 - (node.x + dx + node.width / 2)
        if abs(shift_x) >= 0.5 or abs(shift_y) >= 0.5:
            moves[node.node_id] = (shift_x, shift_y)
            moves[f"{node.node_id}-shadow"] = (shift_x, shift_y)

    if not moves:
        return nodes

    def move(items: list[Any]) -> list[Any]:
        out: list[Any] = []
        for node in items:
            if isinstance(node, GroupNode):
                out.append(node.model_copy(update={"children": move(list(node.children))}))
            elif isinstance(node, TextNode) and node.node_id in moves:
                sx, sy = moves[node.node_id]
                new_y = node.y + sy
                if new_y < 0 or new_y >= height:
                    out.append(node)  # never push a label off the canvas
                    continue
                out.append(node.model_copy(update={"x": round(node.x + sx, 2), "y": round(new_y, 2),
                                                   "height": round(max(1.0, min(node.height, height - new_y)), 2)}))
            else:
                out.append(node)
        return out

    return move(nodes)


__all__ = ["center_text_on_boxes"]
