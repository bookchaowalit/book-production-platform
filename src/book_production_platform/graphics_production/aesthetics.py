"""Design-quality score for a compiled graphic: measurable composition principles, 0-100.

Readability is a gate (:mod:`.layouts` rejects overlaps, overflow, low contrast).
This module goes further and *ranks* layouts that already read well, using
principles a designer would apply when reviewing a board:

- ``hierarchy``: one dominant text; the headline clearly larger than body copy
  (scale ratio), without competing equals.
- ``whitespace``: enough breathing room (neither empty nor crammed).
- ``balance``: the visual centre of mass sits near the optical centre, or
  deliberately to one side with a counterweight.
- ``alignment``: few distinct text edges (left / centre / right axes shared).
- ``margins``: content keeps a consistent safe margin from the canvas edge.
- ``rhythm``: vertical gaps between stacked texts are related (not random).
- ``density``: total text area for the canvas size (posters should be glanceable).
- ``color``: a restrained number of distinct colors.
- ``focus``: a clear focal element (large photo or display type) exists.
- ``spread``: no long empty band across the canvas (a tall story with all content
  in its top half and nothing below reads as unfinished).

Each criterion scores 0-1 with a short reason; the total is a weighted average
scaled to 100. Deterministic and cheap, so the composer can score every
candidate and keep the best.
"""

from __future__ import annotations

import math
from collections.abc import Iterator
from dataclasses import dataclass, field
from itertools import pairwise
from typing import Any

from book_production_platform.graphics_ir import (
    GraphicIR,
    GroupNode,
    ImageNode,
    ShapeNode,
    TextNode,
)

from .layout import wrap_text

WEIGHTS = {
    "hierarchy": 0.2, "whitespace": 0.12, "balance": 0.12, "alignment": 0.14, "margins": 0.1, "rhythm": 0.08,
    "density": 0.08, "color": 0.06, "focus": 0.1, "spread": 0.1,
}


@dataclass
class Score:
    total: float
    parts: dict[str, float] = field(default_factory=dict)
    notes: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"total": round(self.total, 1), "parts": {k: round(v, 3) for k, v in self.parts.items()}, "notes": dict(self.notes)}


def _leaves(nodes: list[Any], dx: float = 0, dy: float = 0) -> Iterator[tuple[Any, float, float]]:
    for node in nodes:
        if isinstance(node, GroupNode):
            yield from _leaves(list(node.children), dx + node.offset_x, dy + node.offset_y)
        else:
            yield node, dx, dy


def _bump(value: float, low: float, best_low: float, best_high: float, high: float) -> float:
    """1 inside [best_low, best_high], falling linearly to 0 at ``low``/``high``."""

    if best_low <= value <= best_high:
        return 1.0
    if value < best_low:
        return max(0.0, (value - low) / max(best_low - low, 1e-9))
    return max(0.0, (high - value) / max(high - best_high, 1e-9))


def score_ir(ir: GraphicIR) -> Score:
    width, height = float(ir.canvas.width), float(ir.canvas.height)
    area = width * height
    texts: list[tuple[TextNode, float, float, float, float]] = []
    blocks: list[tuple[float, float, float, float, float]] = []  # x0, y0, x1, y1, weight
    colors: set[str] = set()
    focal = 0.0
    filled: list[tuple[float, float]] = []  # vertical extents of everything that occupies space, backgrounds included
    for node, dx, dy in _leaves(list(ir.nodes)):
        occupies = isinstance(node, (TextNode, ImageNode)) or (
            isinstance(node, ShapeNode) and node.node_id != "bg" and node.opacity >= 0.3 and node.width * node.height < area * 0.9)
        if occupies and not (isinstance(node, TextNode) and node.bleed):
            filled.append((node.y + dy, node.y + dy + node.height))
        if isinstance(node, TextNode):
            if node.node_id.endswith(("-shadow", "-echo-1", "-echo-2")):
                continue
            lines = wrap_text(node.text, node.width, node.font_size, node.char_width)
            used_h = min(node.height, len(lines) * node.font_size * 1.2)
            longest = max((len(line) for line in lines), default=0)
            used_w = min(node.width, longest * node.font_size * node.char_width)
            x0 = node.x + dx
            if node.align == "center":
                x0 += (node.width - used_w) / 2
            elif node.align == "right":
                x0 += node.width - used_w
            box = (x0, node.y + dy, x0 + used_w, node.y + dy + used_h)
            if not node.bleed:
                texts.append((node, *box))
            weight = used_w * used_h * (1.6 if node.font_weight != "normal" else 1.0) * node.opacity
            blocks.append((*box, weight))
            if node.color.startswith("#"):
                colors.add(node.color)
            if node.bleed:
                focal = max(focal, used_w * used_h / area)
        elif isinstance(node, ImageNode):
            focal = max(focal, node.width * node.height / area)
            if node.width * node.height >= area * 0.85:
                continue  # a full-bleed photo is the background, not a block competing for space
            box = (node.x + dx, node.y + dy, node.x + dx + node.width, node.y + dy + node.height)
            blocks.append((*box, node.width * node.height * 1.2 * node.opacity))
        elif isinstance(node, ShapeNode) and node.node_id != "bg" and node.opacity >= 0.3:
            if node.kind in ("rect", "ellipse") and node.width * node.height < area * 0.9:
                blocks.append((node.x + dx, node.y + dy, node.x + dx + node.width, node.y + dy + node.height,
                               node.width * node.height * 0.5 * node.opacity))
            if node.fill.startswith("#"):
                colors.add(node.fill)
    parts: dict[str, float] = {}
    notes: dict[str, str] = {}

    # hierarchy: the largest text vs the next; and vs the median.
    sizes = sorted((node.font_size for node, *_ in texts), reverse=True)
    if len(sizes) >= 2:
        ratio = sizes[0] / max(sizes[1], 1)
        median = sizes[len(sizes) // 2]
        parts["hierarchy"] = 0.6 * _bump(ratio, 1.0, 1.5, 4.0, 8.0) + 0.4 * _bump(sizes[0] / max(median, 1), 1.0, 2.0, 6.0, 12.0)
        notes["hierarchy"] = f"largest/second {ratio:.1f}x, largest/median {sizes[0] / max(median, 1):.1f}x"
    else:
        parts["hierarchy"] = 0.7
        notes["hierarchy"] = "single text"

    # whitespace: share of the canvas covered by content boxes (union approximated on a grid).
    grid = 24
    covered = 0
    for row in range(grid):
        for column in range(grid):
            cx, cy = (column + 0.5) * width / grid, (row + 0.5) * height / grid
            if any(b[0] <= cx <= b[2] and b[1] <= cy <= b[3] for b in blocks):
                covered += 1
    coverage = covered / (grid * grid)
    parts["whitespace"] = _bump(coverage, 0.1, 0.3, 0.72, 0.97)
    notes["whitespace"] = f"{round(coverage * 100)}% covered"

    # balance: weighted centre of mass vs canvas centre (normalised distance).
    total_weight = sum(b[4] for b in blocks) or 1
    mx = sum((b[0] + b[2]) / 2 * b[4] for b in blocks) / total_weight
    my = sum((b[1] + b[3]) / 2 * b[4] for b in blocks) / total_weight
    offset = math.hypot((mx - width / 2) / width, (my - height * 0.48) / height)
    parts["balance"] = _bump(offset, -1, 0, 0.12, 0.38)
    notes["balance"] = f"centre of mass offset {offset:.2f}"

    # alignment: distinct anchor axes used by text (left edges, centres, right edges), tolerance 1.2% of width.
    tolerance = width * 0.012
    anchors: list[float] = []
    for node, x0, _y0, x1, _y1 in texts:
        axis = x0 if node.align == "left" else (x0 + x1) / 2 if node.align == "center" else x1
        if not any(abs(axis - existing) <= tolerance for existing in anchors):
            anchors.append(axis)
    parts["alignment"] = 1.0 if len(anchors) <= 1 else max(0.0, 1.0 - (len(anchors) - 1) * 0.22)
    notes["alignment"] = f"{len(anchors)} text axes"

    # margins: smallest distance from text to the canvas edge relative to the short side; consistent left/right.
    short = min(width, height)
    if texts:
        edge = min(min(x0, y0, width - x1, height - y1) for _node, x0, y0, x1, y1 in texts)
        parts["margins"] = _bump(edge / short, 0.0, 0.035, 0.2, 0.4)
        notes["margins"] = f"closest text {edge / short:.3f} of the short side from the edge"
    else:
        parts["margins"] = 0.5

    # rhythm: gaps between vertically stacked texts sharing a column.
    gaps = []
    ordered = sorted(texts, key=lambda item: item[2])
    for upper, lower in pairwise(ordered):
        if abs(upper[1] - lower[1]) < width * 0.15 and lower[2] >= upper[4]:
            gaps.append((lower[2] - upper[4]) / short)
    if len(gaps) >= 2:
        mean = sum(gaps) / len(gaps)
        spread = math.sqrt(sum((gap - mean) ** 2 for gap in gaps) / len(gaps))
        parts["rhythm"] = _bump(spread / max(mean, 1e-3), -1, 0, 0.6, 2.0)
        notes["rhythm"] = f"gap variation {spread / max(mean, 1e-3):.2f}"
    else:
        parts["rhythm"] = 0.8

    # density: text area share.
    text_share = sum((x1 - x0) * (y1 - y0) for _node, x0, y0, x1, y1 in texts) / area
    parts["density"] = _bump(text_share, 0.0, 0.05, 0.3, 0.6)
    notes["density"] = f"text covers {round(text_share * 100)}%"

    # color: distinct solid colors used by text and shapes.
    parts["color"] = _bump(len(colors), 0, 2, 5, 9)
    notes["color"] = f"{len(colors)} colors"

    # focus: a big photo or giant type, or a headline that is clearly display-sized.
    headline = sizes[0] / short if sizes else 0
    parts["focus"] = max(_bump(focal, 0.0, 0.18, 0.75, 1.01), _bump(headline, 0.02, 0.09, 0.3, 0.5))
    notes["focus"] = f"largest visual {round(focal * 100)}% of canvas, headline {headline:.2f} of short side"

    # spread: the longest horizontal band (share of the height) with nothing in it.
    bands = 48
    empty = [not any(top <= (row + 0.5) * height / bands <= bottom for top, bottom in filled) for row in range(bands)]
    longest = run = 0
    for is_empty in empty:
        run = run + 1 if is_empty else 0
        longest = max(longest, run)
    gap = longest / bands
    parts["spread"] = _bump(gap, -1, 0, 0.2, 0.45)
    notes["spread"] = f"longest empty band {round(gap * 100)}% of the height"

    total = sum(parts[name] * weight for name, weight in WEIGHTS.items()) / sum(WEIGHTS.values()) * 100
    return Score(total=total, parts=parts, notes=notes)


__all__ = ["WEIGHTS", "Score", "score_ir"]
