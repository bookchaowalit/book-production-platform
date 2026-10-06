"""Deterministic QA checks for Graphics V1A artifacts."""

from __future__ import annotations

import io
import re
from xml.etree import ElementTree
from xml.sax.saxutils import escape

from PIL import Image

from book_production_platform.graphics_ir import (
    GraphicIR,
    ShapeNode,
    TextNode,
    iter_nodes,
)

# WCAG 2.x minimum contrast: 4.5:1 for normal text, 3:1 for large text
# (24 px regular or larger). Advisory: recorded in the trace and the manual
# guide, never used to reject an artifact.
WCAG_AA_NORMAL = 4.5
WCAG_AA_LARGE = 3.0
WCAG_LARGE_TEXT_PX = 24.0


def run_graphic_qa(svg: str, png: bytes, ir: GraphicIR) -> tuple[str, ...]:
    """Return stable issue codes; an empty tuple means the artifacts pass."""

    issues: list[str] = []
    try:
        root = ElementTree.fromstring(svg)
    except ElementTree.ParseError:
        return ("svg_invalid_xml",)
    if root.attrib.get("width") != str(ir.canvas.width) or root.attrib.get("height") != str(ir.canvas.height):
        issues.append("svg_dimensions_mismatch")
    if re.search(r"(?:href|src)=[\"'](?:https?:|file:|//)", svg, re.IGNORECASE) or re.search(r"url\(\s*(?:https?:|file:|//)", svg, re.IGNORECASE):
        issues.append("external_reference_forbidden")
    if "<script" in svg.lower() or re.search(r"\son[a-z]+\s*=", svg, re.IGNORECASE):
        issues.append("unsafe_svg_content")
    for node in iter_nodes(list(ir.nodes)):
        if isinstance(node, TextNode) and escape(node.text) not in svg:
            issues.append(f"text_missing:{node.node_id}")
    try:
        with Image.open(io.BytesIO(png)) as image:
            if image.format != "PNG":
                issues.append("png_format_invalid")
            if image.size != (ir.canvas.width, ir.canvas.height):
                issues.append("png_dimensions_mismatch")
    except (OSError, ValueError):
        issues.append("png_invalid")
    return tuple(dict.fromkeys(issues))


def _luminance(color: str) -> float:
    def channel(value: int) -> float:
        srgb = value / 255
        return srgb / 12.92 if srgb <= 0.04045 else ((srgb + 0.055) / 1.055) ** 2.4

    red, green, blue = (int(color[index:index + 2], 16) for index in (1, 3, 5))
    return 0.2126 * channel(red) + 0.7152 * channel(green) + 0.0722 * channel(blue)


def contrast_ratio(foreground: str, background: str) -> float:
    """WCAG 2.x contrast ratio of two ``#RRGGBB`` colors, rounded to 2 decimals."""

    first, second = sorted((_luminance(foreground), _luminance(background)), reverse=True)
    return round((first + 0.05) / (second + 0.05), 2)


def contrast_report(ir: GraphicIR) -> list[dict[str, object]]:
    """Advisory WCAG AA check of every text layer against the shape directly behind it.

    The backdrop is the last solid rectangle drawn before the text whose bounds
    contain the text box centre (the renderer paints in IR order), falling back
    to the canvas background. Gradient-filled text or backdrops cannot be
    measured from the IR and are left out (check them on the PNG preview).
    """

    report: list[dict[str, object]] = []
    leaves = iter_nodes(list(ir.nodes))
    for index, node in enumerate(leaves):
        if not isinstance(node, TextNode) or not node.color.startswith("#"):
            continue
        centre_x, centre_y = node.x + node.width / 2, node.y + node.height / 2
        backdrop, backdrop_id = ir.canvas.background, "canvas"
        for shape in leaves[:index]:
            if (
                isinstance(shape, ShapeNode)
                and shape.kind in ("rect", "ellipse")
                and shape.fill != "none"
                and shape.x <= centre_x <= shape.x + shape.width
                and shape.y <= centre_y <= shape.y + shape.height
            ):
                # A gradient or see-through backdrop cannot be measured from the IR: it is left out (below).
                backdrop = shape.fill if shape.opacity >= 0.85 else "paint:translucent"
                backdrop_id = shape.node_id
        if not backdrop.startswith("#"):
            continue
        ratio = contrast_ratio(node.color, backdrop)
        required = WCAG_AA_LARGE if node.font_size >= WCAG_LARGE_TEXT_PX else WCAG_AA_NORMAL
        report.append({
            "node_id": node.node_id,
            "color": node.color,
            "backdrop": backdrop,
            "backdrop_node": backdrop_id,
            "ratio": ratio,
            "required": required,
            "passes_aa": ratio >= required,
        })
    return report


__all__ = ["WCAG_AA_LARGE", "WCAG_AA_NORMAL", "contrast_ratio", "contrast_report", "run_graphic_qa"]
