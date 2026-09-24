"""Deterministic QA checks for Graphics V1A artifacts."""

from __future__ import annotations

import io
import re
from xml.etree import ElementTree
from xml.sax.saxutils import escape

from PIL import Image

from book_production_platform.graphics_ir import GraphicIR, TextNode


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
    for node in ir.nodes:
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


__all__ = ["run_graphic_qa"]
