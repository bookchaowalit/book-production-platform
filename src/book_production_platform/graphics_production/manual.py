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

from collections.abc import Sequence
from pathlib import Path

from book_production_platform.graphics_ir import (
    GraphicIR,
    GroupNode,
    ImageNode,
    ShapeNode,
    TextNode,
    iter_nodes,
)
from book_production_platform.production_trace import ManualGuide

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
    "builtin:display": "Anton",
    "builtin:serif": "Playfair Display",
    "builtin:mono": "Space Mono",
    "builtin:thai": "Sarabun",
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

    return wrap_text(node.text, node.width, node.font_size)


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


def _add_layered_item(guide: ManualGuide, name: str, ir: GraphicIR) -> None:
    """graphic-ir.v2 (themes): open the editable SVG; its named groups are the layers."""

    canvas = ir.canvas
    section = f"{name} — {canvas.width}×{canvas.height} ({ir.template_ref}, {ir.variant})"
    fonts = sorted({font_family(node) for node in iter_nodes(list(ir.nodes)) if isinstance(node, TextNode)})
    guide.layer(name, "Document", content=f"{ir.template_ref} · {ir.variant} · {len(ir.nodes)} layers", position=f"W={canvas.width}, H={canvas.height}")
    for group in ir.nodes:
        if isinstance(group, GroupNode):
            texts = [node.node_id for node in iter_nodes(list(group.children)) if isinstance(node, TextNode)]
            content = f"{len(group.children)} objects" + (f" · text: {', '.join(texts)}" if texts else "")
            guide.layer(group.label or group.node_id, "Layer (group)", content=content, position="vector, editable")
    guide.step("ติดตั้งฟอนต์ที่ธีมใช้ก่อนเปิดไฟล์ (ไม่มีจะถูกแทนด้วยฟอนต์อื่น)", tool="Google Fonts / Font Book", value=", ".join(fonts) or "-", section=section)
    guide.step(f"เปิด `{name}.svg` ใน Affinity Designer/Photo หรือลากเข้า Figma", tool="Affinity: File ▸ Open… · Figma: ลากไฟล์ลง canvas", value=f"{name}.svg", section=section)
    guide.step("ตรวจว่าเลเยอร์ตรงตามตาราง (group ชื่อเดียวกับใน SVG) และตัวอักษรยังแก้ไขได้", tool="Layers panel", section=section)
    guide.step("แก้ข้อความ สี หรือ gradient ได้โดยตรง; ตำแหน่งและขนาดมาจาก theme แล้ว", tool="Text tool / Fill panel", section=section)
    guide.step("Export เป็น PNG ขนาดเดิม", tool="Affinity: File ▸ Export ▸ PNG · Figma: Export ▸ PNG 1x", value=f"{name}.png, {canvas.width}×{canvas.height}", section=section)


def _add_item(guide: ManualGuide, name: str, ir: GraphicIR) -> None:
    if ir.schema_version == "graphic-ir.v2":
        _add_layered_item(guide, name, ir)
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


def build_figma_guide(items: Sequence[tuple[str, GraphicIR]], *, batch_id: str) -> ManualGuide:
    """Translate the accepted items of one batch into numbered Figma steps and a layer map."""

    guide = ManualGuide(
        program="Figma",
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
    for name, ir in items:
        _add_item(guide, name, ir)
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
