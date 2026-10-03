"""Dimensioned 2D shop drawing (SVG + DXF R12) generated from PartIR alone.

One list of drawing primitives in part coordinates (millimetres, origin at the
plate's lower-left corner, +Y up - the same frame as the FreeCAD sketch) is
rendered twice: an SVG at 1:1 scale (``width``/``height`` in mm) for review and
printing, and an ASCII DXF R12 for CAM/laser/CNC import. Dimensions are drawn as
annotation geometry (extension lines, arrows, text) on the ``DIMENSIONS``
layer, not as associative DXF ``DIMENSION`` entities.

Output is byte-deterministic for one PartIR, so the trace hash of a drawing
identifies the drawing exactly.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from xml.sax.saxutils import escape

from book_production_platform.cad_ir import PartIR
from book_production_platform.cad_production.properties import (
    PlateProperties,
    mm,
    plate_properties,
)

DRAWING_PROFILE = "cad-drawing.v1"
DRAWING_FILES = ("drawing.svg", "drawing.dxf")
LAYERS = ("OUTLINE", "HOLES", "CENTER", "DIMENSIONS", "TEXT")
_TEXT_HEIGHT = 3.0
_ARROW = 2.0
_MARGIN = 30.0


@dataclass(frozen=True, slots=True)
class Line:
    layer: str
    x1: float
    y1: float
    x2: float
    y2: float


@dataclass(frozen=True, slots=True)
class Circle:
    layer: str
    cx: float
    cy: float
    r: float


@dataclass(frozen=True, slots=True)
class Text:
    layer: str
    x: float
    y: float
    text: str
    height: float = _TEXT_HEIGHT
    rotation: float = 0.0
    anchor: str = "middle"


Primitive = Line | Circle | Text


def _horizontal_dimension(x1: float, x2: float, y_ref: float, y_dim: float, label: str) -> list[Primitive]:
    sign = 1.0 if y_dim >= y_ref else -1.0
    items: list[Primitive] = [
        Line("DIMENSIONS", x1, y_ref, x1, y_dim + sign * 1.5),
        Line("DIMENSIONS", x2, y_ref, x2, y_dim + sign * 1.5),
        Line("DIMENSIONS", x1, y_dim, x2, y_dim),
        Line("DIMENSIONS", x1, y_dim, x1 + _ARROW, y_dim + _ARROW / 3),
        Line("DIMENSIONS", x1, y_dim, x1 + _ARROW, y_dim - _ARROW / 3),
        Line("DIMENSIONS", x2, y_dim, x2 - _ARROW, y_dim + _ARROW / 3),
        Line("DIMENSIONS", x2, y_dim, x2 - _ARROW, y_dim - _ARROW / 3),
        Text("DIMENSIONS", (x1 + x2) / 2, y_dim + 1.0, label),
    ]
    return items


def _vertical_dimension(y1: float, y2: float, x_ref: float, x_dim: float, label: str) -> list[Primitive]:
    sign = 1.0 if x_dim >= x_ref else -1.0
    return [
        Line("DIMENSIONS", x_ref, y1, x_dim + sign * 1.5, y1),
        Line("DIMENSIONS", x_ref, y2, x_dim + sign * 1.5, y2),
        Line("DIMENSIONS", x_dim, y1, x_dim, y2),
        Line("DIMENSIONS", x_dim, y1, x_dim + _ARROW / 3, y1 + _ARROW),
        Line("DIMENSIONS", x_dim, y1, x_dim - _ARROW / 3, y1 + _ARROW),
        Line("DIMENSIONS", x_dim, y2, x_dim + _ARROW / 3, y2 - _ARROW),
        Line("DIMENSIONS", x_dim, y2, x_dim - _ARROW / 3, y2 - _ARROW),
        Text("DIMENSIONS", x_dim - 1.0, (y1 + y2) / 2, label, rotation=90.0),
    ]


def drawing_primitives(part: PartIR, properties: PlateProperties | None = None) -> list[Primitive]:
    """Top view, side view, dimensions, hole table, and title block."""

    props = properties or plate_properties(part)
    d = props.dimensions
    width, height, thickness = float(d.width_mm), float(d.height_mm), float(d.thickness_mm)
    radius = float(d.hole_diameter_mm) / 2
    items: list[Primitive] = [
        Line("OUTLINE", 0, 0, width, 0),
        Line("OUTLINE", width, 0, width, height),
        Line("OUTLINE", width, height, 0, height),
        Line("OUTLINE", 0, height, 0, 0),
    ]
    mark = radius + 1.5
    for hole in props.hole_centers:
        x, y = float(hole.x_mm), float(hole.y_mm)
        items.append(Circle("HOLES", x, y, radius))
        items.append(Line("CENTER", x - mark, y, x + mark, y))
        items.append(Line("CENTER", x, y - mark, x, y + mark))
        items.append(Text("TEXT", x + radius + 1.0, y + radius + 1.0, hole.label, height=2.5, anchor="start"))

    first = props.hole_centers[0]
    hole_x, hole_y = float(first.x_mm), float(first.y_mm)
    items += _horizontal_dimension(0, hole_x, 0, -8, mm(d.hole_offset_x_mm))
    items += _horizontal_dimension(0, width, 0, -16, mm(d.width_mm))
    items += _vertical_dimension(0, hole_y, 0, -8, mm(d.hole_offset_y_mm))
    items += _vertical_dimension(0, height, 0, -16, mm(d.height_mm))
    callout = f"4x Ø{mm(d.hole_diameter_mm)} THRU ALL"
    top_right = props.hole_centers[2]
    items.append(Line("DIMENSIONS", float(top_right.x_mm), float(top_right.y_mm), float(top_right.x_mm) + 6, height + 6))
    items.append(Text("DIMENSIONS", float(top_right.x_mm) + 7, height + 6, callout, anchor="start"))

    side_x = width + 24
    items += [
        Line("OUTLINE", side_x, 0, side_x + thickness, 0),
        Line("OUTLINE", side_x + thickness, 0, side_x + thickness, height),
        Line("OUTLINE", side_x + thickness, height, side_x, height),
        Line("OUTLINE", side_x, height, side_x, 0),
        Text("TEXT", side_x + thickness / 2, height + 4, "SIDE"),
    ]
    items += _horizontal_dimension(side_x, side_x + thickness, 0, -8, mm(d.thickness_mm))
    items.append(Text("TEXT", width / 2, height + 14, "TOP"))

    table_y = -30.0
    columns = (0.0, 20.0, 50.0, 80.0)
    header = ("HOLE", "X (mm)", "Y (mm)", "Ø (mm)")
    for x, label in zip(columns, header, strict=True):
        items.append(Text("TEXT", x, table_y, label, anchor="start"))
    for row, hole in enumerate(props.hole_centers, start=1):
        y = table_y - 5.0 * row
        values = (hole.label, mm(hole.x_mm), mm(hole.y_mm), mm(d.hole_diameter_mm))
        for x, value in zip(columns, values, strict=True):
            items.append(Text("TEXT", x, y, value, anchor="start"))

    title_y = table_y - 5.0 * 6
    lines = (
        f"PART {part.part_id}",
        f"TEMPLATE {part.template} / {part.hole_pattern}",
        f"UNITS mm  SCALE 1:1  {width:.3f} x {height:.3f} x {thickness:.3f}",
        f"VOLUME {props.volume_mm3:.3f} mm3  (analytical)",
        f"DRAWING {DRAWING_PROFILE}  EDGES SHARP (NO FILLET/CHAMFER)",
    )
    for index, text in enumerate(lines):
        items.append(Text("TEXT", 0, title_y - 5.0 * index, text, anchor="start"))
    return items


def _extent(items: list[Primitive]) -> tuple[float, float, float, float]:
    xs: list[float] = []
    ys: list[float] = []
    for item in items:
        if isinstance(item, Line):
            xs += [item.x1, item.x2]
            ys += [item.y1, item.y2]
        elif isinstance(item, Circle):
            xs += [item.cx - item.r, item.cx + item.r]
            ys += [item.cy - item.r, item.cy + item.r]
        else:
            width = len(item.text) * item.height * 0.6
            if item.rotation:
                xs += [item.x - item.height, item.x]
                ys += [item.y - width / 2, item.y + width / 2]
            elif item.anchor == "start":
                xs += [item.x, item.x + width]
                ys += [item.y, item.y + item.height]
            else:
                xs += [item.x - width / 2, item.x + width / 2]
                ys += [item.y, item.y + item.height]
    return min(xs), min(ys), max(xs), max(ys)


def _f(value: float) -> str:
    text = f"{value:.3f}"
    return "0.000" if text == "-0.000" else text


def render_svg(part: PartIR, items: list[Primitive] | None = None) -> bytes:
    primitives = items if items is not None else drawing_primitives(part)
    min_x, min_y, max_x, max_y = _extent(primitives)
    page_w = max_x - min_x + 2 * _MARGIN
    page_h = max_y - min_y + 2 * _MARGIN
    ox = _MARGIN - min_x
    oy = _MARGIN + max_y
    styles = {
        "OUTLINE": 'stroke="#111" stroke-width="0.5" fill="none"',
        "HOLES": 'stroke="#111" stroke-width="0.5" fill="none"',
        "CENTER": 'stroke="#555" stroke-width="0.18" stroke-dasharray="3 1 0.5 1" fill="none"',
        "DIMENSIONS": 'stroke="#0050a0" stroke-width="0.18" fill="none"',
    }
    out = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        (
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{_f(page_w)}mm" height="{_f(page_h)}mm" '
            f'viewBox="0 0 {_f(page_w)} {_f(page_h)}" data-profile="{DRAWING_PROFILE}" '
            f'data-part-id="{escape(part.part_id)}">'
        ),
        f'<title>{escape(part.part_id)} - {DRAWING_PROFILE}</title>',
    ]
    for layer in LAYERS:
        out.append(f'<g id="{layer}">')
        for item in primitives:
            if item.layer != layer:
                continue
            if isinstance(item, Line):
                out.append(
                    f'<line x1="{_f(item.x1 + ox)}" y1="{_f(oy - item.y1)}" x2="{_f(item.x2 + ox)}" '
                    f'y2="{_f(oy - item.y2)}" {styles.get(layer, styles["DIMENSIONS"])}/>'
                )
            elif isinstance(item, Circle):
                out.append(
                    f'<circle cx="{_f(item.cx + ox)}" cy="{_f(oy - item.cy)}" r="{_f(item.r)}" {styles[layer]}/>'
                )
            else:
                x, y = _f(item.x + ox), _f(oy - item.y)
                rotate = f' transform="rotate(-{item.rotation:g} {x} {y})"' if item.rotation else ""
                fill = "#0050a0" if layer == "DIMENSIONS" else "#111"
                out.append(
                    f'<text x="{x}" y="{y}" font-family="DejaVu Sans, Arial, sans-serif" '
                    f'font-size="{_f(item.height)}" text-anchor="{item.anchor}" fill="{fill}"{rotate}>'
                    f"{escape(item.text)}</text>"
                )
        out.append("</g>")
    out.append("</svg>")
    return ("\n".join(out) + "\n").encode("utf-8")


def _pairs(*values: tuple[int, str]) -> list[str]:
    lines: list[str] = []
    for code, value in values:
        lines += [str(code), value]
    return lines


def render_dxf(part: PartIR, items: list[Primitive] | None = None) -> bytes:
    """ASCII DXF R12 (AC1009), millimetres, plate frame (origin lower-left)."""

    primitives = items if items is not None else drawing_primitives(part)
    lines = _pairs((0, "SECTION"), (2, "HEADER"), (9, "$ACADVER"), (1, "AC1009"), (9, "$INSUNITS"), (70, "4"))
    lines += _pairs((0, "ENDSEC"), (0, "SECTION"), (2, "TABLES"), (0, "TABLE"), (2, "LAYER"), (70, str(len(LAYERS))))
    colors = {"OUTLINE": "7", "HOLES": "7", "CENTER": "8", "DIMENSIONS": "5", "TEXT": "7"}
    for layer in LAYERS:
        linetype = "CENTER" if layer == "CENTER" else "CONTINUOUS"
        lines += _pairs((0, "LAYER"), (2, layer), (70, "0"), (62, colors[layer]), (6, linetype))
    lines += _pairs((0, "ENDTAB"), (0, "ENDSEC"), (0, "SECTION"), (2, "ENTITIES"))
    for item in primitives:
        if isinstance(item, Line):
            lines += _pairs(
                (0, "LINE"), (8, item.layer), (10, _f(item.x1)), (20, _f(item.y1)), (30, "0.000"),
                (11, _f(item.x2)), (21, _f(item.y2)), (31, "0.000"),
            )
        elif isinstance(item, Circle):
            lines += _pairs(
                (0, "CIRCLE"), (8, item.layer), (10, _f(item.cx)), (20, _f(item.cy)), (30, "0.000"), (40, _f(item.r)),
            )
        else:
            text = item.text.replace("Ø", "%%c")
            values: list[tuple[int, str]] = [
                (0, "TEXT"), (8, item.layer), (10, _f(item.x)), (20, _f(item.y)), (30, "0.000"),
                (40, _f(item.height)), (1, text),
            ]
            if item.rotation:
                values.append((50, f"{item.rotation:g}"))
            if item.anchor == "middle":
                values += [(72, "1"), (11, _f(item.x)), (21, _f(item.y)), (31, "0.000")]
            lines += _pairs(*values)
    lines += _pairs((0, "ENDSEC"), (0, "EOF"))
    return ("\n".join(lines) + "\n").encode("ascii")


def write_drawings(part: PartIR, directory: Path) -> tuple[Path, Path]:
    """Write ``drawing.svg`` and ``drawing.dxf`` into ``directory``."""

    items = drawing_primitives(part)
    svg_path = directory / "drawing.svg"
    dxf_path = directory / "drawing.dxf"
    svg_path.write_bytes(render_svg(part, items))
    dxf_path.write_bytes(render_dxf(part, items))
    return svg_path, dxf_path


def dimension_labels(part: PartIR) -> dict[str, str]:
    """Every dimension text the drawing must carry, keyed by PartIR parameter."""

    d = plate_properties(part).dimensions
    return {
        "width_mm": mm(d.width_mm),
        "height_mm": mm(d.height_mm),
        "thickness_mm": mm(d.thickness_mm),
        "hole_diameter_mm": mm(d.hole_diameter_mm),
        "hole_offset_x_mm": mm(d.hole_offset_x_mm),
        "hole_offset_y_mm": mm(d.hole_offset_y_mm),
    }


__all__ = [
    "DRAWING_FILES",
    "DRAWING_PROFILE",
    "LAYERS",
    "Circle",
    "Line",
    "Text",
    "dimension_labels",
    "drawing_primitives",
    "render_dxf",
    "render_svg",
    "write_drawings",
]
