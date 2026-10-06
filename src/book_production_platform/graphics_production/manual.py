"""Manual-reproduction guide for Graphics V1A: how to build the same card in Figma.

The steps mirror :func:`graphics_production.render.render_svg` exactly: one
Frame per item at the variant's canvas size, then every IR node in paint order
(bottom first) with the same X/Y/W/H, fill hex, font size, line height
(``font_size × 1.2`` px, the renderer's ``dy``), colour, alignment, and the
text split into the same lines :func:`layout.wrap_text` produced, so a person
gets the layer structure of ``<item>.svg`` and can export ``<item>.png``.
Values are copied from the IR, never recalculated by hand.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from book_production_platform.graphics_ir import (
    GraphicIR,
    GroupNode,
    ImageNode,
    ShapeNode,
    TextNode,
    iter_nodes,
)
from book_production_platform.production_trace import ManualGuide

from .art_styles import ART_FONTS
from .layout import wrap_text
from .qa import contrast_report

FIGMA_VERSION = "Design (desktop/web, 2026)"
RENDER_LINE_HEIGHT = 1.2  # render_svg: tspan dy = font_size * 1.2
BUILTIN_FONT_FAMILY = "Arial"  # render_svg: font-family="Arial, sans-serif"
_ALIGN = {"left": "Align left", "center": "Align center", "right": "Align right"}


def _num(value: float) -> str:
    return f"{value:g}"


def _hex(value: str) -> str:
    return value.lstrip("#").upper()


# Theme font stacks: the family to install and pick in an editor.
THEME_FONT_FAMILIES = {
    font: stack.split(",")[0].strip().strip("'")
    for font, (stack, _width) in ART_FONTS.items()
    if font != "builtin:sans"
}


def font_family(node: TextNode) -> str:
    """The family a person selects in Figma for this text node."""

    if node.font_ref == "builtin:sans":
        return BUILTIN_FONT_FAMILY
    if node.font_ref in THEME_FONT_FAMILIES:
        return THEME_FONT_FAMILIES[node.font_ref]
    return f"ฟอนต์จากไฟล์ {Path(node.font_ref).name} (ติดตั้งไฟล์นี้ก่อน)"


def text_lines(node: TextNode) -> tuple[str, ...]:
    """The exact lines the renderer writes as ``<tspan>`` elements."""

    return wrap_text(node.text, node.width, node.font_size, node.char_width)


def line_height_px(node: TextNode) -> float:
    return round(node.font_size * RENDER_LINE_HEIGHT, 2)


def _box(node: ShapeNode | TextNode | ImageNode) -> str:
    return f"X={_num(node.x)}, Y={_num(node.y)}, W={_num(node.width)}, H={_num(node.height)}"


def _minutes(ir: GraphicIR) -> float:
    total = 2.0 + 1.0  # frame + export
    if ir.schema_version == "graphic-ir.v2":
        return total + 3.0  # install fonts, open the SVG, check layers
    for node in iter_nodes(list(ir.nodes)):
        if isinstance(node, ShapeNode):
            total += 1.0
        elif isinstance(node, ImageNode):
            total += 1.5
        else:
            total += 2.0 + 0.5 * len(text_lines(node))
    return total


# The Affinity Designer tool that builds each motif by hand.
_MOTIF_TOOLS = {
    "rect": "Rectangle Tool (M)",
    "ellipse": "Ellipse Tool (M)",
    "line": "Pen Tool (P)",
    "polygon": "Pen Tool (P)",
    "glow": "Ellipse Tool + Gradient Tool (radial, fade to 0% opacity)",
    "text_block": "Frame Text Tool (T)",
    "display_text": "Artistic Text Tool (T) + Edit ▸ Duplicate for each echo",
    "image": "File ▸ Place…",
    "starfield": "Ellipse Tool + Edit ▸ Duplicate (scatter by hand)",
    "perspective_grid": "Pen Tool (P) lines from the vanishing point + horizontal lines",
    "striped_sun": "Ellipse Tool + Rectangle Tool + Layer ▸ Geometry ▸ Subtract",
    "ridge": "Pen Tool (P)",
    "waves": "Pen Tool (P) with smooth nodes",
    "blobs": "Pen Tool (P) with smooth nodes",
    "halftone": "Ellipse Tool + Edit ▸ Duplicate (grid), or Filters ▸ Halftone on a raster layer",
    "rays": "Pen Tool (P) wedge + Transform ▸ Rotate duplicate",
    "rings": "Ellipse Tool + Edit ▸ Duplicate",
    "stripes": "Rectangle Tool + Edit ▸ Duplicate, then clip to the area",
    "grain": "Layer ▸ New Live Filter Layer ▸ Add Noise",
    "corner_frame": "Rectangle Tool (border) + Pen Tool (corner ticks)",
    "scanlines": "Rectangle Tool thin bar + Edit ▸ Duplicate (Ctrl/Cmd+J) down the page",
    "checker": "Rectangle Tool square + Duplicate in a grid (or a Pattern fill)",
    "dots": "Ellipse Tool + Duplicate in a staggered grid",
    "confetti_shapes": "Triangle / Ellipse / Pen Tool shapes scattered by hand",
    "squiggle": "Pen Tool (P) wave, or Vector Brush",
    "zigzag": "Pen Tool (P) with sharp nodes",
    "arcs": "Ellipse Tool rings cut in half (Layer ▸ Geometry ▸ Subtract a rectangle)",
    "deco_frame": "Pen Tool (P) stepped-corner outline, duplicated inward",
    "distress": "Grunge brushes on a pixel layer, or Pen Tool splats",
    "notes": "Ellipse Tool note heads + Pen Tool stems",
    "glitch_bars": "Rectangle Tool thin bars in two colors",
    "barcode": "Rectangle Tool bars of 1-3 widths",
    "vignette": "Rectangle full canvas + Gradient Tool (radial, clear centre → color edges)",
    "torn_edge": "Pen Tool (P) jagged edge, or a torn-paper brush",
    "big_type": "Artistic Text Tool (T) one line, scale until it spans the width; outline = Stroke only; motion blur = Live Filter ▸ Motion Blur",
    "swoosh": "Pen Tool (P) curved blade, tapered with the Pencil/Width tool",
    "brush_stroke": "Paint Brush Tool with a dry brush, or a rough Pen Tool band",
    "swatches": "Ellipse Tool (hold Shift) circles in a row, one fill each",
}
_MASK_TOOLS = {
    "rect": "no mask",
    "rounded": "Rectangle Tool with corner radius, then drag the photo onto it to clip",
    "circle": "Ellipse Tool (hold Shift) as a clipping shape",
    "ellipse": "Ellipse Tool as a clipping shape",
    "arch": "Rectangle + Ellipse ▸ Layer ▸ Geometry ▸ Add, used as a clipping shape",
    "blob": "Pen Tool smooth shape as a clipping shape",
    "star": "Star Tool (5 points, inner radius 45%) as a clipping shape",
    "hexagon": "Polygon Tool (6 sides) as a clipping shape",
    "torn": "Pen Tool jagged rectangle as a clipping shape",
}
_FILTER_TOOLS = {
    "grayscale": "Adjustment ▸ Black & White",
    "sepia": "Adjustment ▸ Black & White + Recolor (warm brown)",
    "duotone": "Adjustment ▸ Gradient Map (dark → light)",
    "posterize": "Adjustment ▸ Posterize",
    "high_contrast": "Adjustment ▸ Levels + Brightness/Contrast",
    "blur": "Live Filter ▸ Gaussian Blur",
    "invert": "Adjustment ▸ Invert",
    "vintage": "Adjustment ▸ HSL (lower saturation) + Recolor warm at 40% + vignette",
    "halftone": "Live Filter ▸ Halftone (Photo persona)",
}


def _short(value: object) -> str:
    return _num(value) if isinstance(value, float | int) and not isinstance(value, bool) else str(value)


def _bounds(nodes: list[ShapeNode | TextNode | ImageNode]) -> str:
    if not nodes:
        return ""
    left = min(node.x for node in nodes)
    top = min(node.y for node in nodes)
    right = max(node.x + node.width for node in nodes)
    bottom = max(node.y + node.height for node in nodes)
    return f"X={_num(round(left, 1))}, Y={_num(round(top, 1))}, W={_num(round(right - left, 1))}, H={_num(round(bottom - top, 1))}"


def _paint_text(ir: GraphicIR) -> list[tuple[str, str]]:
    rows = []
    for paint in ir.paints:
        stops = ", ".join(
            f"{_hex(stop.color)} {round(stop.offset * 100)}%" + (f" (opacity {round(stop.opacity * 100)}%)" if stop.opacity != 1 else "")
            for stop in paint.stops
        )
        if paint.paint_type == "linear":
            shape = f"linear ({_num(round(paint.x1, 2))},{_num(round(paint.y1, 2))}) → ({_num(round(paint.x2, 2))},{_num(round(paint.y2, 2))})"
        else:
            shape = f"radial centre ({_num(round(paint.cx, 2))},{_num(round(paint.cy, 2))}) radius {_num(round(paint.r, 2))}"
        rows.append((paint.paint_id, f"{shape}: {stops}"))
    return rows


def _add_layered_item(guide: ManualGuide, name: str, ir: GraphicIR, controls: Mapping[str, Any] | None = None) -> None:
    """graphic-ir.v2 (themes): every layer and motif with its exact values, for Affinity Designer."""

    canvas = ir.canvas
    section = f"{name} — {canvas.width}×{canvas.height} ({ir.template_ref}, {ir.variant})"
    leaves = iter_nodes(list(ir.nodes))
    fonts = sorted({font_family(node) for node in leaves if isinstance(node, TextNode)})
    guide.layer(name, "Document", content=f"{ir.template_ref} · {ir.variant} · {len(ir.nodes)} layers", position=f"W={canvas.width}, H={canvas.height}")
    guide.step("สร้างเอกสารใหม่ขนาดเท่าภาพ", tool="File ▸ New… (Units: Pixels, DPI 72, RGB)", value=f"W={canvas.width}, H={canvas.height}", section=section)
    guide.step("ติดตั้งและใช้ฟอนต์ของธีม", tool="Google Fonts / Font Book", value=", ".join(fonts) or "-", section=section)
    for paint_id, text in _paint_text(ir):
        guide.step(f"เตรียม gradient `{paint_id}` (ใช้ซ้ำใน Fill ของชิ้นที่อ้าง paint:{paint_id})", tool="Gradient Tool (G) / Fill ▸ Gradient", value=text, section=section)
    art = ir.assumptions.get("art_style")
    if art:
        guide.step(f"ใช้ art style `{art}` (สี ฟอนต์ texture ตามด้านล่างถูกแทนค่าไว้แล้ว)", tool="-", value=art, section=section)
    groups = {group.node_id: group for group in ir.nodes if isinstance(group, GroupNode)}
    theme_layers = {str(layer["id"]): layer for layer in (controls.get("layers", []) if controls else [])}
    for group in groups.values():
        layer = theme_layers.get(group.node_id)
        if layer is None:
            if controls and group.node_id not in _EXTRA_GROUPS:
                continue  # a theme layer hidden by the design
            _add_extra_group(guide, group, section)
            continue
        if not layer.get("visible", True):
            continue
        extra = []
        if group.opacity != 1:
            extra.append(f"opacity {round(group.opacity * 100)}%")
        if group.offset_x or group.offset_y:
            extra.append(f"move dx={_num(group.offset_x)}, dy={_num(group.offset_y)}")
        guide.layer(str(layer.get("label") or layer["id"]), "Layer (group)", content=" · ".join(extra) or f"{len(group.children)} objects", position="")
        guide.step(f"สร้าง layer `{layer['id']}` (ล่างสุดก่อน)", tool="Layers ▸ Add Layer", value=" · ".join(extra) or "opacity 100%", section=section)
        children = iter_nodes(list(group.children))
        item_ids = [str(entry["target"]).split("/", 1)[1] for entry in layer.get("items", [])]

        def owner(node_id: str, ids: list[str] = item_ids) -> str | None:
            matches = [candidate for candidate in ids if node_id == candidate or node_id.startswith(f"{candidate}-")]
            return max(matches, key=len) if matches else None

        for item in layer.get("items", []):
            if not item.get("visible", True):
                continue
            item_id = str(item["target"]).split("/", 1)[1]
            nodes = [node for node in children if owner(node.node_id) == item_id]
            if not nodes:
                continue
            params = " · ".join(f"{key}={_short(value)}" for key, value in item.get("params", {}).items())
            texts = [node for node in nodes if isinstance(node, TextNode) and not node.node_id.startswith(f"{item_id}-echo")]
            content = params
            if texts:
                content = f"ข้อความ: {' / '.join(text_lines(texts[0]))} · {params}"
                styled = [f"{texts[0].text[run.start:run.end]}={'outline' if run.outline else run.font_weight or run.color}"
                          for run in texts[0].runs]
                if styled:
                    content += f" · ไฮไลต์ (เลือกเฉพาะคำแล้วเปลี่ยน): {', '.join(styled)}"
                if texts[0].outline_only:
                    content += " · ตัวกลวง: Fill none + Stroke"
                if texts[0].motion_blur or texts[0].blur:
                    content += f" · เบลอ: Live Filter ▸ Motion/Gaussian Blur {round(texts[0].motion_blur + texts[0].blur)} px"
            guide.layer(str(item["target"]), str(item["motif"]), content=content, position=_bounds(nodes))
            guide.step(
                f"สร้าง `{item['target']}` ({item['motif']})",
                tool=_MOTIF_TOOLS.get(str(item["motif"]), "Pen Tool (P)"),
                target=f"layer `{layer['id']}` · {_bounds(nodes)}",
                value=content,
                section=section,
            )
    guide.step("Export เป็น PNG ขนาดเดิม และ SVG สำหรับแก้ต่อ", tool="File ▸ Export… ▸ PNG / SVG", value=f"{name}.png, {canvas.width}×{canvas.height}", section=section)


_EXTRA_GROUPS = {"ArtBackdrop", "ArtOverlay", "PhotosBack", "Photos", "PhotosFront"}


def _photo_steps(node: ImageNode) -> list[tuple[str, str, str]]:
    """(step, tool, value) to rebuild one treated photo by hand."""

    effects = node.effects
    image_name = Path(node.source_ref).name
    steps = [("วางรูป", "File ▸ Place…", f"{image_name} · {_box(node)}")]
    if effects is None:
        return steps
    if effects.cutout != "none":
        steps.append(("ลบพื้นหลัง (ตัดเฉพาะตัวแบบ)",
                      "Photo persona ▸ Select ▸ Select Subject (หรือ Selection Brush + Refine) ▸ Layer ▸ New Mask from selection",
                      "ai: ตัวแบบ (คน/สัตว์/ของ)" if effects.cutout == "ai" else f"plain: พื้นสีเรียบ tolerance {effects.cutout_tolerance}"))
    crop = "Fill (crop)" if effects.fit == "cover" else "Fit (ไม่ crop)"
    steps.append(("ครอบรูปให้เต็มกรอบ", "Crop Tool / Place ▸ Fill", f"{crop} · focus {round(effects.focus_x * 100)}%,{round(effects.focus_y * 100)}% · zoom {_num(effects.zoom)}"))
    adjust = [f"{label} {_num(value)}" for label, value in (("brightness", effects.brightness), ("contrast", effects.contrast), ("saturation", effects.saturation)) if value != 1]
    if adjust:
        steps.append(("ปรับแสงสี", "Adjustment ▸ Brightness/Contrast + HSL", " · ".join(adjust)))
    if effects.filter not in ("none", "style"):
        colors = f" · {effects.duotone_dark} → {effects.duotone_light}" if effects.filter in ("duotone", "halftone") and effects.duotone_dark else ""
        steps.append((f"ใส่ฟิลเตอร์ {effects.filter}", _FILTER_TOOLS.get(effects.filter, "Adjustment"), f"{effects.filter}{colors}"))
    if effects.mask != "rect":
        steps.append((f"ตัดรูปเป็นรูปทรง {effects.mask}", _MASK_TOOLS[effects.mask], f"corner radius {round(effects.corner_radius * 100)}%" if effects.mask == "rounded" else effects.mask))
    if effects.outline_width:
        steps.append(("ใส่ขอบสติกเกอร์", "Layer ▸ Layer Effects ▸ Outline (Outside)", f"{_num(effects.outline_width)} px · {effects.outline_color}"))
    if effects.shadow:
        steps.append(("ใส่เงา", "Layer ▸ Layer Effects ▸ Outer Shadow", f"opacity {round(effects.shadow * 55)}% · ระยะ ~2% ของด้านสั้น"))
    if node.rotation:
        steps.append(("หมุนรูป", "Transform panel ▸ R", f"{_num(node.rotation)}°"))
    if node.opacity != 1:
        steps.append(("ตั้งความโปร่งใส", "Layers panel ▸ Opacity", f"{round(node.opacity * 100)}%"))
    return steps


def _add_extra_group(guide: ManualGuide, group: GroupNode, section: str) -> None:
    """Layers added by the design (art style decoration, placed photos) that are not theme layers."""

    extra = f"opacity {round(group.opacity * 100)}%" if group.opacity != 1 else f"{len(group.children)} objects"
    guide.layer(str(group.label or group.node_id), "Layer (group)", content=extra, position="")
    guide.step(f"สร้าง layer `{group.node_id}`", tool="Layers ▸ Add Layer", value=extra, section=section)
    for node in iter_nodes(list(group.children)):
        if isinstance(node, ImageNode):
            guide.layer(node.node_id, "Photo", content=Path(node.source_ref).name, position=_box(node))
            for step, tool, value in _photo_steps(node):
                guide.step(f"{step} `{node.node_id}`", tool=tool, target=f"layer `{group.node_id}`", value=value, section=section)
        elif isinstance(node, ShapeNode):
            motif = next((key for key in sorted(_MOTIF_TOOLS, key=len, reverse=True) if f"-{key.replace('_', '-')}" in node.node_id or key in node.node_id), "")
            fill = node.fill if node.fill != "none" else (node.stroke or "-")
            guide.layer(node.node_id, "Shape", content=f"{_hex(fill) if fill.startswith('#') else fill} · opacity {round(node.opacity * 100)}%", position=_box(node))
            guide.step(f"วาด `{node.node_id}`", tool=_MOTIF_TOOLS.get(motif, "Pen Tool (P)"), target=f"layer `{group.node_id}` · {_box(node)}",
                       value=f"{_hex(fill) if fill.startswith('#') else fill} · opacity {round(node.opacity * 100)}%", section=section)


def _add_item(guide: ManualGuide, name: str, ir: GraphicIR, controls: Mapping[str, Any] | None = None) -> None:
    if ir.schema_version == "graphic-ir.v2":
        _add_layered_item(guide, name, ir, controls)
        return
    canvas = ir.canvas
    section = f"{name} — Frame {canvas.width}×{canvas.height} ({ir.template_ref}, {ir.variant})"
    guide.layer(name, "Frame", content=f"{ir.template_ref} · {ir.variant} · {len(ir.nodes)} layers", position=f"W={canvas.width}, H={canvas.height}")
    guide.step("เลือกเครื่องมือ Frame แล้วคลิกบน canvas หนึ่งครั้ง", tool="Frame (F)", target="canvas ว่าง", section=section)
    guide.step("ตั้งขนาด Frame", tool="Design panel ▸ Frame ▸ W / H", value=f"W={canvas.width}, H={canvas.height}", section=section)
    guide.step("ตั้งตำแหน่ง Frame", tool="Design panel ▸ X / Y", value="X=0, Y=0", section=section)
    guide.step("ตั้งสีพื้นของ Frame", tool="Design panel ▸ Fill", value=_hex(canvas.background), section=section)
    guide.step("ตั้งชื่อ Frame (ใช้เป็นชื่อไฟล์ตอน export)", tool="Layers panel ▸ ดับเบิลคลิกชื่อ", value=name, section=section)
    guide.step(
        "สร้าง layer ต่อไปนี้ตามลำดับ (ล่างสุดก่อน) ภายใน Frame นี้; Clip content ใช้ค่าเริ่มต้น (on)",
        target="Layers panel: layer ที่สร้างทีหลังจะอยู่บน",
        section=section,
    )
    for node in iter_nodes(list(ir.nodes)):
        if isinstance(node, ShapeNode):
            if node.kind == "line":
                guide.layer(node.node_id, "Line", content=f"stroke {_hex(node.stroke or node.fill)} · {_num(node.stroke_width or 1)} px", position=_box(node))
                guide.step(f"วาดเส้น `{node.node_id}`", tool="Line (L)", target=f"ใน Frame {name}", value=_box(node), section=section)
                guide.step("ตั้งสีและความหนาเส้น", tool="Design panel ▸ Stroke", value=f"{_hex(node.stroke or node.fill)}, {_num(node.stroke_width or 1)} px", section=section)
            else:
                stroke = f" · stroke {_hex(node.stroke)} {_num(node.stroke_width)} px" if node.stroke else ""
                guide.layer(node.node_id, "Rectangle", content=f"fill {_hex(node.fill)}{stroke}", position=_box(node))
                guide.step(f"วาดสี่เหลี่ยม `{node.node_id}`", tool="Rectangle (R)", target=f"ใน Frame {name}", section=section)
                guide.step("ตั้งตำแหน่งและขนาด", tool="Design panel ▸ X / Y / W / H", value=_box(node), section=section)
                guide.step("ตั้งสีพื้น (Corner radius = 0)", tool="Design panel ▸ Fill", value=_hex(node.fill), section=section)
                if node.stroke:
                    guide.step("เพิ่มเส้นขอบ", tool="Design panel ▸ Stroke ▸ +", value=f"{_hex(node.stroke)}, {_num(node.stroke_width)} px, Inside", section=section)
                guide.step("ตั้งชื่อ layer", tool="Layers panel ▸ ดับเบิลคลิกชื่อ", value=node.node_id, section=section)
        elif isinstance(node, ImageNode):
            image_name = Path(node.source_ref).name
            fit = "Fit" if node.preserve_aspect else "Fill"
            guide.layer(node.node_id, "Image (rectangle with image fill)", content=f"{image_name} · {fit}", position=_box(node))
            guide.step(f"วาดสี่เหลี่ยมสำหรับรูป `{node.node_id}`", tool="Rectangle (R)", target=f"ใน Frame {name}", section=section)
            guide.step("ตั้งตำแหน่งและขนาดช่องรูป", tool="Design panel ▸ X / Y / W / H", value=_box(node), section=section)
            guide.step("ใส่รูปเป็น fill", tool="Design panel ▸ Fill ▸ Image ▸ Choose image…", value=image_name, section=section)
            guide.step("ตั้งโหมดย่อรูปให้ไม่ถูกตัด (เหมือน preserveAspectRatio=xMidYMid meet)", tool="Fill ▸ Image ▸ mode", value=fit, section=section)
            guide.step("ตั้งชื่อ layer", tool="Layers panel ▸ ดับเบิลคลิกชื่อ", value=node.node_id, section=section)
        else:
            lines = text_lines(node)
            family = font_family(node)
            spec = (
                f"{family} Regular · {_num(node.font_size)} px · line height {_num(line_height_px(node))} px · "
                f"{_hex(node.color)} · {node.align}"
            )
            guide.layer(node.node_id, "Text", content=f"{len(lines)} บรรทัด · {spec}", position=_box(node))
            guide.step(f"สร้างกล่องข้อความ `{node.node_id}` (ลากเป็นกล่อง ไม่ใช่คลิก)", tool="Text (T)", target=f"ใน Frame {name}", section=section)
            guide.step("ตั้งตำแหน่งและขนาดกล่อง แล้วเลือก resizing แบบ Fixed size", tool="Design panel ▸ X / Y / W / H ▸ Resizing: Fixed size", value=_box(node), section=section)
            guide.step("ตั้งฟอนต์", tool="Design panel ▸ Text ▸ Font family / weight", value=f"{family} / Regular (400)", section=section)
            guide.step("ตั้งขนาดตัวอักษร", tool="Design panel ▸ Text ▸ Font size", value=f"{_num(node.font_size)}", section=section)
            guide.step("ตั้งระยะบรรทัดเป็นพิกเซล", tool="Design panel ▸ Text ▸ Line height", value=f"{_num(line_height_px(node))}px", section=section)
            guide.step("ตั้งระยะห่างตัวอักษร", tool="Design panel ▸ Text ▸ Letter spacing", value="0%", section=section)
            guide.step("ตั้งการจัดแนว (แนวตั้งชิดบน)", tool=f"Design panel ▸ Text ▸ {_ALIGN[node.align]} + Align top", value=node.align, section=section)
            guide.step("ตั้งสีตัวอักษร", tool="Design panel ▸ Fill", value=_hex(node.color), section=section)
            for number, line in enumerate(lines, start=1):
                ending = " แล้วกด Shift+Enter" if number < len(lines) else ""
                guide.step(f"พิมพ์บรรทัด {number}/{len(lines)} ตามนี้ทุกตัวอักษร{ending}", target=f"กล่อง `{node.node_id}`", value=line or "(บรรทัดว่าง)", section=section)
            guide.step("ตั้งชื่อ layer", tool="Layers panel ▸ ดับเบิลคลิกชื่อ", value=node.node_id, section=section)
    guide.step("เลือก Frame แล้วเพิ่มการ export PNG", tool="Design panel ▸ Export ▸ +", value="PNG, 1x", section=section)
    guide.step("เพิ่มการ export SVG (ไฟล์แก้ไขได้)", tool="Design panel ▸ Export ▸ +", value="SVG, Outline text: off, Include \"id\" attribute: on", section=section)
    guide.step("กด Export แล้วบันทึกไฟล์", tool=f"Export {name}", value=f"{name}.png, {name}.svg", section=section)


def build_figma_guide(
    items: Sequence[tuple[str, GraphicIR]],
    *,
    batch_id: str,
    controls: Mapping[str, Mapping[str, Any]] | None = None,
) -> ManualGuide:
    """Translate the accepted items of one batch into numbered steps and a layer map.

    Original templates (graphic-ir.v1) are written for Figma; themed items
    (graphic-ir.v2) for Affinity Designer 2, with every motif's exact values.
    """

    layered = [ir.schema_version == "graphic-ir.v2" for _, ir in items]
    program = "Affinity Designer 2" if all(layered) else ("Affinity Designer 2 + Figma" if any(layered) else "Figma")
    guide = ManualGuide(
        program=program,
        program_version=FIGMA_VERSION,
        alternatives=["Canva", "Adobe Photoshop"],
        title=f"Graphics batch {batch_id} ({len(items)} ชิ้น)",
        summary=(
            "ทำตามขั้นตอนนี้ใน Figma จะได้ Frame และ layer ตรงกับไฟล์ `.svg` ที่เครื่องมือสร้าง "
            "(ตำแหน่ง ขนาด สี ฟอนต์ และการตัดบรรทัดตาม IR); ข้อความให้พิมพ์ตามบรรทัดที่ให้ไว้ อย่าให้ Figma ตัดบรรทัดเอง"
        ),
        estimated_minutes=round(sum(_minutes(ir) for _, ir in items), 1),
        setup=[
            "เปิด Figma ▸ `Drafts ▸ + New ▸ Design file`",
            "ตั้งหน่วยเป็นพิกเซล (Figma ใช้ px เสมอ); ค่า X/Y นับจากมุมซ้ายบนของ Frame",
            "ถ้าข้อความเป็นภาษาไทยและใช้ Arial ให้ตรวจว่าเครื่องมีฟอนต์ไทย fallback (เหมือน browser ของเครื่องมือ) หรือใช้ไฟล์ฟอนต์เดียวกับ `font_ref`",
            "Canva: `Create a design ▸ Custom size` ใส่ W×H px เท่ากัน แล้วใช้ Elements ▸ Shapes / Text ตามตารางเดียวกัน",
            "Photoshop: `File ▸ New…` ใส่ Width/Height เป็น Pixels, Resolution 72 ppi, RGB Color; สร้าง Shape/Type layer ตามตาราง และ `File ▸ Export ▸ Export As… ▸ PNG`",
        ],
        glossary={
            "Frame": "กรอบงานขนาดเท่าภาพผลลัพธ์ (Photoshop = canvas, Canva = design)",
            "Fixed size": "กล่องข้อความไม่ยืดตามข้อความ ทำให้ตำแหน่งตรงกับ IR",
            "Line height": "ระยะบรรทัด; เครื่องมือใช้ font size × 1.2 เป็นพิกเซล",
        },
    )
    if all(layered):
        guide.program_version = ""
        guide.alternatives = ["Adobe Illustrator", "Figma", "Inkscape"]
        guide.summary = (
            "ทำตามขั้นตอนนี้ใน Affinity Designer 2 จะได้ layer และชิ้นงานตรงกับไฟล์ `.svg` ที่เครื่องมือสร้าง "
            "ทุกชิ้นบอก motif, เครื่องมือที่ใช้ และค่าจริง (ตำแหน่ง ขนาด สี gradient ฟอนต์); ชิ้นที่สุ่ม (ดาว ลายจุด เกรน) "
            "ทำตำแหน่งใกล้เคียงได้ ไม่ต้องตรงทุกจุด"
        )
        guide.setup = [
            "Affinity Designer 2 ▸ `File ▸ New…` ใส่ขนาดตามตาราง หน่วย Pixels, DPI 72, Colour Format RGB/8",
            "ติดตั้งฟอนต์ที่ระบุ (Anton, Sarabun, Playfair Display, Space Mono) ก่อนเปิดงาน",
            "ค่า X/Y นับจากมุมซ้ายบน; ใช้ Transform panel ใส่ X, Y, W, H",
            "gradient: เลือกชิ้นงาน ▸ Gradient Tool (G) ▸ Type: Linear/Radial แล้วใส่สีและตำแหน่งแต่ละ stop ตามค่า",
        ]
        guide.glossary = {
            "motif": "ชิ้นส่วนวาดที่เครื่องมือใช้ (เช่น striped_sun = ดวงอาทิตย์ตัดเป็นริ้ว)",
            "paint:<ชื่อ>": "gradient ที่เตรียมไว้ในขั้นต้น ใช้ซ้ำได้หลายชิ้น",
            "echo": "สำเนาข้อความด้านหลังหัวข้อ เลื่อนตามค่า dx/dy เพื่อทำเงาสี",
        }
    for name, ir in items:
        _add_item(guide, name, ir, (controls or {}).get(name))
    checks: list[str] = []
    for name, ir in items:
        texts = [node for node in iter_nodes(list(ir.nodes)) if isinstance(node, TextNode)]
        checks.append(f"`{name}`: Frame {ir.canvas.width}×{ir.canvas.height} px มี {len(ir.nodes)} layer เรียงตามตาราง และ `{name}.png` ที่ export มีขนาดเท่ากัน")
        for node in texts:
            checks.append(
                f"`{name}` ▸ `{node.node_id}`: {len(text_lines(node))} บรรทัด, {_num(node.font_size)} px — ตรงกับ `<tspan>` ใน `{name}.svg`"
            )
        for row in contrast_report(ir):
            verdict = "ผ่าน" if row["passes_aa"] else "ต่ำกว่าเกณฑ์ (แจ้งเตือนเท่านั้น)"
            checks.append(
                f"`{name}` ▸ `{row['node_id']}` contrast {row['ratio']}:1 บน {row['backdrop_node']} "
                f"(WCAG AA ต้อง ≥ {row['required']}:1) — {verdict}"
            )
        checks.append(f"`{name}`: วาง `{name}.png` ของเครื่องมือทับที่ opacity 50% แล้วขอบข้อความ/สีต้องซ้อนกันพอดี")
    guide.checks = checks
    return guide


__all__ = ["BUILTIN_FONT_FAMILY", "RENDER_LINE_HEIGHT", "build_figma_guide", "font_family", "line_height_px", "text_lines"]
