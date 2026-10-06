"""Composed layouts: a fresh composition per job instead of one fixed template.

A ``graphic-layout.v1`` spec describes *one* design: where each text, giant word,
photo, shape, and decoration sits (boxes in canvas fractions, optionally per
size), its stacking order, and its treatment. :func:`layout_to_theme` turns it
into an ordinary theme, so every engine feature (art styles, photo cut-outs,
inline emphasis, real font metrics, the editor, the Affinity manual) works on it.

Specs come from the procedural composer (:mod:`.composer`, no model) or the AI
art director (:mod:`.composer_ai`). Either way :func:`check_layout` compiles the
spec in every requested size and rejects it when text does not fit, text
overlaps text, a photo covers text, text leaves the canvas, or text contrast
fails. :func:`fingerprint` reduces a composition to a coarse map of what sits
where; the layout library remembers recent fingerprints so a new job never gets
a near-copy of an earlier one (:func:`too_similar`).
"""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from book_production_platform.graphics_ir import (
    GraphicIR,
    GroupNode,
    ImageNode,
    ShapeNode,
    TextNode,
)

from .art_styles import ART_FONTS, ROLES
from .motifs import DECOR_MOTIFS
from .themes import VARIANT_SIZES, Theme, ThemeError, parse_theme

LAYOUT_SCHEMA = "graphic-layout.v1"
LAYOUT_DIR_ENV = "GRAPHICS_PRODUCTION_LAYOUT_DIR"
KINDS = ("text", "big_type", "photo", "shape", "motif")
TEXT_SLOTS = ("headline", "subhead", "body", "kicker", "price", "cta", "footer", "big")
GRID = 8
SIMILAR = 0.8
DEFAULT_PALETTE = {
    "bg": "#F3F0EA", "bg2": "#E5DED2", "surface": "#FFFFFF", "ink": "#151515", "accent": "brand.primary",
    "accent2": "brand.accent", "highlight": "brand.accent", "on_accent": "#FFFFFF", "muted": "#8C8C8C",
}
_TEXT_KEYS = frozenset({
    "id", "kind", "slot", "box", "boxes", "z", "font", "size", "weight", "align", "color", "accent_color", "upper", "outline",
    "rotation", "letter_spacing", "opacity", "max_lines", "behind", "per_size",
})
_BIG_KEYS = frozenset({"id", "kind", "slot", "x", "y", "align", "span", "font", "color", "outline", "motion_blur", "opacity", "z",
                       "upper", "rotation", "max_size", "ys"})
_PHOTO_KEYS = frozenset({"id", "kind", "asset", "box", "boxes", "z", "effects", "rotation", "opacity"})
_SHAPE_KEYS = frozenset({"id", "kind", "shape", "box", "boxes", "z", "fill", "stroke", "stroke_width", "radius", "opacity"})
_MOTIF_KEYS = frozenset({"id", "kind", "motif", "params", "z"})
_CANDIDATE: ContextVar[Theme | None] = ContextVar("layout_candidate", default=None)


class LayoutError(ValueError):
    """Raised when a layout spec is malformed."""


# --- spec -> theme -----------------------------------------------------------------------------------


def _num(value: Any, low: float, high: float, name: str) -> float:
    """A number in range; a number slightly outside is clamped (layout quality is checked after compiling)."""

    if isinstance(value, bool) or not isinstance(value, int | float):
        # The message names the field and its range so a model (or person) can fix it in one go.
        raise LayoutError(f"layout_value_invalid:{name} must be a number from {low} to {high} (got {value!r})")
    return min(max(float(value), low), high)


def _box(raw: Any, name: str, bleed: float = 0.3) -> dict[str, float]:
    if not isinstance(raw, Mapping):
        raise LayoutError(f"layout_box_invalid:{name}")
    x = _num(raw.get("x"), -bleed, 1 + bleed, f"{name}.x")
    y = _num(raw.get("y"), -bleed, 1 + bleed, f"{name}.y")
    w = _num(raw.get("w"), 0.01, 1 + 2 * bleed, f"{name}.w")
    h = _num(raw.get("h"), 0.01, 1 + 2 * bleed, f"{name}.h")
    return {"x": x, "y": y, "w": w, "h": h}


def _per_variant(element: Mapping[str, Any], name: str, bleed: float) -> dict[str, dict[str, float]]:
    """Variant -> box; ``boxes`` entries override ``box``; ``default`` covers the rest."""

    boxes: dict[str, dict[str, float]] = {}
    if "box" in element:
        boxes["default"] = _box(element["box"], name, bleed)
    for variant, raw in (element.get("boxes") or {}).items():
        if variant not in VARIANT_SIZES:
            raise LayoutError(f"layout_variant_invalid:{name}")
        boxes[variant] = _box(raw, f"{name}.{variant}", bleed)
    if not boxes:
        raise LayoutError(f"layout_box_missing:{name}")
    return boxes


def _expr(boxes: dict[str, dict[str, float]], key: str, axis: str) -> Any:
    values = {variant: f"{axis} * {round(box[key], 4)}" for variant, box in boxes.items()}
    if len(values) == 1 and "default" in values:
        return values["default"]
    if "default" not in values:
        values["default"] = next(iter(values.values()))
    return {"by_variant": values}


def _color(value: Any, name: str) -> str:
    if isinstance(value, str):
        value = value.strip()
        if value in ROLES:
            value = f"role.{value}"
        elif value.startswith("palette.") and value[8:] in ROLES:
            value = f"role.{value[8:]}"
        elif len(value) == 9 and value.startswith("#"):
            value = value[:7]  # drop an alpha channel; opacity is a separate field
    if not isinstance(value, str):
        raise LayoutError(f"layout_color_invalid:{name} colors are role.<{'|'.join(ROLES)}> or #RRGGBB (got {value!r})")
    if value.startswith("role."):
        role = value[5:]
        if role not in ROLES:
            raise LayoutError(f"layout_color_invalid:{name} unknown role {value!r}")
        return f"palette.{role}"
    if len(value) == 7 and value.startswith("#"):
        return value.upper()
    raise LayoutError(f"layout_color_invalid:{name} colors are role.<name> or #RRGGBB (got {value!r})")


def _clean_effects(raw: Any) -> dict[str, Any]:
    """Photo effects with each field checked on its own: unknown or invalid fields are dropped, not fatal."""

    from book_production_platform.graphics_ir import ImageEffects

    cleaned: dict[str, Any] = {}
    if not isinstance(raw, Mapping):
        return cleaned
    for key, value in raw.items():
        if key not in ImageEffects.model_fields:
            continue
        if isinstance(value, str) and key.endswith(("_color", "_dark", "_light")):
            value = value.strip()
            value = f"role.{value}" if value in ROLES else value[:7] if len(value) == 9 and value.startswith("#") else value
        try:
            ImageEffects.model_validate({key: value})
        except ValueError:
            continue
        cleaned[key] = value
    if cleaned.get("cutout", "none") != "none":
        # A cut-out subject is shown whole: cropping it to fill a box slices through the subject.
        cleaned["fit"] = "contain"
        cleaned["zoom"] = min(float(cleaned.get("zoom", 1)), 1.2)
    return cleaned


def _roles_to_palette(value: Any) -> Any:
    """Inside motif params and photo effects, ``role.x`` means this layout's palette entry (so art styles still recolor it)."""

    if isinstance(value, str) and value.startswith("role.") and value[5:] in ROLES:
        return f"palette.{value[5:]}"
    if isinstance(value, list):
        return [_roles_to_palette(item) for item in value]
    if isinstance(value, Mapping):
        return {key: _roles_to_palette(item) for key, item in value.items()}
    return value


def _sized(element: Mapping[str, Any], key: str, default: Any) -> Any:
    """``element[key]``, or a ``by_variant`` table when ``per_size`` overrides it for some sizes."""

    base = element.get(key, default)
    overrides = {variant: values[key] for variant, values in (element.get("per_size") or {}).items()
                 if variant in VARIANT_SIZES and isinstance(values, Mapping) and key in values}
    if not overrides:
        return base
    return {"by_variant": {**overrides, "default": base}}


def _edge(boxes: dict[str, dict[str, float]], first: str, second: str | None, axis: str) -> Any:
    """``axis * (box[first] + box[second])`` per size (e.g. a line's far end)."""

    values = {variant: f"{axis} * {round(box[first] + (box[second] if second else 0), 4)}" for variant, box in boxes.items()}
    if len(values) == 1 and "default" in values:
        return values["default"]
    values.setdefault("default", next(iter(values.values())))
    return {"by_variant": values}


def layout_id(spec: Mapping[str, Any]) -> str:
    body = json.dumps({key: spec[key] for key in sorted(spec) if key not in ("layout_id", "created")}, sort_keys=True, ensure_ascii=False)
    return "lay_" + hashlib.sha256(body.encode()).hexdigest()[:12]


def layout_to_theme(spec: Mapping[str, Any]) -> dict[str, Any]:
    """Validate the structure of a layout spec and build the equivalent theme JSON."""

    if not isinstance(spec, Mapping) or spec.get("schema_version") != LAYOUT_SCHEMA:
        raise LayoutError("layout_schema_invalid")
    elements = spec.get("elements")
    if not isinstance(elements, list) or not 1 <= len(elements) <= 40:
        raise LayoutError("layout_elements_invalid")
    palette = dict(DEFAULT_PALETTE)
    for role, value in (spec.get("palette") or {}).items():
        if role not in ROLES or not isinstance(value, str) or not (value.startswith("#") and len(value) == 7):
            raise LayoutError(f"layout_palette_invalid:{role}")
        palette[role] = value.upper()
    slots: dict[str, dict[str, Any]] = {}
    assets: dict[str, dict[str, Any]] = {}
    layers: dict[int, list[dict[str, Any]]] = {}
    ids: set[str] = set()
    background = spec.get("background") or {}
    paints: list[dict[str, Any]] = []
    bg_fill = _color(background.get("color", "role.bg"), "background")
    if background.get("gradient_to"):
        paints.append({"paint_id": "bg", "type": "linear", "angle": _num(background.get("angle", 90), -360, 360, "background.angle"),
                       "stops": [{"offset": 0, "color": bg_fill}, {"offset": 1, "color": _color(background["gradient_to"], "background")}]})
        bg_fill = "paint:bg"
    for index, element in enumerate(elements):
        if not isinstance(element, Mapping) or element.get("kind") not in KINDS:
            raise LayoutError(f"layout_kind_invalid:{index}")
        kind = str(element["kind"])
        item_id = str(element.get("id") or f"e{index + 1}")
        if not item_id.replace("-", "").replace("_", "").isalnum() or item_id in ids or len(item_id) > 32:
            raise LayoutError(f"layout_id_invalid:{index}")
        ids.add(item_id)
        allowed = {"text": _TEXT_KEYS, "big_type": _BIG_KEYS, "photo": _PHOTO_KEYS, "shape": _SHAPE_KEYS, "motif": _MOTIF_KEYS}[kind]
        # Keys this kind does not use are ignored (a harmless "max_lines" on a giant word), never guessed at.
        element = {key: value for key, value in element.items() if key in allowed}
        z = int(_num(element.get("z", 5), 0, 20, f"{item_id}.z"))
        font = element.get("font", "builtin:thai")
        if kind in ("text", "big_type") and font not in ART_FONTS:
            raise LayoutError(f"layout_font_invalid:{item_id} font must be one of {sorted(ART_FONTS)}")
        if kind == "text":
            slot = element.get("slot")
            if slot not in TEXT_SLOTS:
                raise LayoutError(f"layout_slot_invalid:{item_id} slot must be one of {list(TEXT_SLOTS)}")
            boxes = _per_variant(element, item_id, 0.0)
            size = _num(element.get("size", 0.05), 0.012, 0.3, f"{item_id}.size")
            sizes = {variant: _num(values.get("size", size), 0.012, 0.3, f"{item_id}.size")
                     for variant, values in (element.get("per_size") or {}).items() if isinstance(values, Mapping)}
            font_size: Any = f"MIN * {size}"
            min_size: Any = f"MIN * {round(size * 0.45, 4)}"
            if sizes:
                font_size = {"by_variant": {**{v: f"MIN * {value}" for v, value in sizes.items()}, "default": font_size}}
                min_size = {"by_variant": {**{v: f"MIN * {round(value * 0.45, 4)}" for v, value in sizes.items()}, "default": min_size}}
            slots[slot] = {"required": True, "labels": [slot]}
            align = _sized(element, "align", "left")
            aligns = list(align["by_variant"].values()) if isinstance(align, Mapping) else [align]
            item: dict[str, Any] = {
                "motif": "text_block", "id": item_id, "slot": slot, "x": _expr(boxes, "x", "W"), "y": _expr(boxes, "y", "H"),
                "width": _expr(boxes, "w", "W"), "height": _expr(boxes, "h", "H"), "font_size": font_size,
                "min_font_size": min_size, "max_lines": int(_num(element.get("max_lines", 4), 1, 12, f"{item_id}.max_lines")),
                "font": font, "weight": element.get("weight", "normal"), "align": align,
                "color": _color(element.get("color", "role.ink"), item_id), "measure": True,
            }
            if element.get("weight", "normal") not in ("normal", "bold", "black") or any(a not in ("left", "center", "right") for a in aligns):
                raise LayoutError(f"layout_value_invalid:{item_id} weight must be normal|bold|black and align left|center|right")
            if element.get("accent_color"):
                item["accent_color"] = _color(element["accent_color"], item_id)
            for key in ("upper", "outline"):
                if element.get(key):
                    item[key] = True
            if element.get("rotation"):
                item["rotation"] = _num(element["rotation"], -30, 30, f"{item_id}.rotation")
            if element.get("letter_spacing"):
                item["letter_spacing"] = f"MIN * {size} * {_num(element['letter_spacing'], -0.05, 0.6, item_id)}"
            if "opacity" in element:
                item["opacity"] = _num(element["opacity"], 0.1, 1, f"{item_id}.opacity")
            if element.get("behind"):
                item["behind"] = True
        elif kind == "big_type":
            slot = element.get("slot")
            if slot not in TEXT_SLOTS:
                raise LayoutError(f"layout_slot_invalid:{item_id} slot must be one of {list(TEXT_SLOTS)}")
            slots[slot] = {"required": True, "labels": [slot]}
            y_values = element.get("ys") or {}
            y_expr: Any = f"H * {_num(element.get('y', 0.3), -0.2, 1.0, item_id)}"
            if y_values:
                table = {variant: f"H * {_num(value, -0.2, 1.0, item_id)}" for variant, value in y_values.items() if variant in VARIANT_SIZES}
                table["default"] = y_expr
                y_expr = {"by_variant": table}
            item = {
                "motif": "big_type", "id": item_id, "slot": slot, "x": f"W * {_num(element.get('x', 0.5), -0.5, 1.5, item_id)}",
                "y": y_expr, "align": element.get("align", "center"),
                "span": f"W * {_num(element.get('span', 1.0), 0.2, 2.0, item_id)}", "font": font, "text_role": "display",
                "color": _color(element.get("color", "role.accent"), item_id), "upper": bool(element.get("upper", True)),
                "max_font_size": f"H * {_num(element.get('max_size', 0.35), 0.05, 0.8, item_id)}",
            }
            if item["align"] not in ("left", "center", "right"):
                raise LayoutError(f"layout_value_invalid:{item_id} align must be left|center|right")
            if element.get("outline"):
                item.update({"outline": True, "outline_width": "MIN * 0.003"})
            if element.get("motion_blur"):
                item["motion_blur"] = f"MIN * {_num(element['motion_blur'], 0, 0.05, item_id)}"
            if "opacity" in element:
                item["opacity"] = _num(element["opacity"], 0.05, 1, f"{item_id}.opacity")
            if element.get("rotation"):
                item["rotation"] = _num(element["rotation"], -30, 30, f"{item_id}.rotation")
        elif kind == "photo":
            asset = element.get("asset")
            if not isinstance(asset, str) or not asset.replace("_", "").isalnum():
                raise LayoutError(f"layout_asset_invalid:{item_id} asset must be one of the job photos")
            assets[asset] = {"required": True}
            boxes = _per_variant(element, item_id, 0.3)
            item = {"motif": "image", "id": item_id, "asset": asset, "x": _expr(boxes, "x", "W"), "y": _expr(boxes, "y", "H"),
                    "width": _expr(boxes, "w", "W"), "height": _expr(boxes, "h", "H"),
                    "effects": _roles_to_palette(_clean_effects(element.get("effects")))}
            for key, low, high in (("rotation", -45, 45), ("opacity", 0.05, 1)):
                if key in element:
                    item[key] = _num(element[key], low, high, f"{item_id}.{key}")
        elif kind == "shape":
            shape = element.get("shape", "rect")
            if shape not in ("rect", "ellipse", "line"):
                raise LayoutError(f"layout_shape_invalid:{item_id} shape must be rect|ellipse|line")
            boxes = _per_variant(element, item_id, 0.3)
            opacity = _num(element.get("opacity", 1), 0.03, 1, f"{item_id}.opacity")
            if shape == "rect":
                item = {"motif": "rect", "id": item_id, "x": _expr(boxes, "x", "W"), "y": _expr(boxes, "y", "H"),
                        "width": _expr(boxes, "w", "W"), "height": _expr(boxes, "h", "H"),
                        "fill": _color(element.get("fill", "role.accent"), item_id) if element.get("fill") != "none" else "none",
                        "rx": f"MIN * {_num(element.get('radius', 0), 0, 0.5, item_id)}", "opacity": opacity}
            elif shape == "ellipse":
                # A fully rounded rect (SVG clamps rx to half the side): a circle when the box is square in pixels,
                # editable as one shape, and seen as a text backdrop by the contrast check.
                item = {"motif": "rect", "id": item_id, "x": _expr(boxes, "x", "W"), "y": _expr(boxes, "y", "H"),
                        "width": _expr(boxes, "w", "W"), "height": _expr(boxes, "h", "H"), "rx": "MAX",
                        "fill": _color(element.get("fill", "role.accent"), item_id) if element.get("fill") != "none" else "none",
                        "opacity": opacity}
            else:
                flat = all(box["h"] <= 0.011 for box in boxes.values())
                item = {"motif": "line", "id": item_id, "x1": _expr(boxes, "x", "W"), "y1": _expr(boxes, "y", "H"),
                        "x2": _edge(boxes, "x", "w", "W"), "y2": _expr(boxes, "y", "H") if flat else _edge(boxes, "y", "h", "H"),
                        "stroke": _color(element.get("stroke", "role.ink"), item_id),
                        "stroke_width": f"MIN * {_num(element.get('stroke_width', 0.003), 0.0005, 0.05, item_id)}", "opacity": opacity}
            if element.get("stroke") and shape != "line":
                item["stroke"] = _color(element["stroke"], item_id)
                item["stroke_width"] = f"MIN * {_num(element.get('stroke_width', 0.003), 0.0005, 0.05, item_id)}"
        else:
            motif = element.get("motif")
            if motif not in DECOR_MOTIFS:
                raise LayoutError(f"layout_motif_invalid:{item_id} motif must be one of {sorted(DECOR_MOTIFS)}")
            params = element.get("params") or {}
            if not isinstance(params, Mapping) or len(json.dumps(params)) > 3000:
                raise LayoutError(f"layout_params_invalid:{item_id} params must be an object under 3000 characters")
            item = {"motif": motif, "id": item_id, **{key: _roles_to_palette(value) for key, value in params.items() if key not in ("motif", "id")}}
        layers.setdefault(z, []).append(item)
    if "headline" not in slots and "big" not in slots:
        raise LayoutError("layout_needs_headline")
    theme_layers = [{"id": "Background", "items": [{"motif": "rect", "id": "bg", "fill": bg_fill}]}]
    for z in sorted(layers):
        theme_layers.append({"id": f"Z{z:02d}", "label": f"Layer {z}", "items": layers[z]})
    theme_id = str(spec.get("layout_id") or layout_id(spec))
    return {
        "schema_version": "graphic-theme.v1", "theme_id": theme_id, "title": str(spec.get("concept", "Composed layout"))[:80],
        "description": str(spec.get("concept", ""))[:400], "ir_version": "graphic-ir.v2", "template_version": "1",
        "variants": list(VARIANT_SIZES), "evidence": ["composed"], "slots": slots, "assets": assets,
        "palette": palette, "roles": {role: role for role in ROLES}, "paints": paints, "layers": theme_layers,
    }


def spec_theme(spec: Mapping[str, Any]) -> Theme:
    data = layout_to_theme(spec)
    try:
        return parse_theme(data, source="layout")
    except ThemeError as exc:
        raise LayoutError(str(exc)) from exc


# --- library ----------------------------------------------------------------------------------------


def layout_dir() -> Path | None:
    configured = os.environ.get(LAYOUT_DIR_ENV, "").strip()
    return Path(configured) if configured else None


@contextmanager
def candidate_layout(theme: Theme) -> Iterator[None]:
    marker = _CANDIDATE.set(theme)
    try:
        yield
    finally:
        _CANDIDATE.reset(marker)


def find_layout_theme(theme_id: str) -> Theme | None:
    """A composed layout by id: the candidate being checked, else the layout library."""

    candidate = _CANDIDATE.get()
    if candidate is not None and candidate.theme_id == theme_id:
        return candidate
    root = layout_dir()
    if root is None or not theme_id.startswith("lay_"):
        return None
    path = root / f"{theme_id}.json"
    if not path.is_file():
        return None
    try:
        return spec_theme(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError, LayoutError):
        return None


def save_layout(spec: Mapping[str, Any], fingerprints: Mapping[str, list[str]]) -> Path:
    root = layout_dir()
    if root is None:
        raise LayoutError(f"layout_library_not_configured: set {LAYOUT_DIR_ENV}")
    root.mkdir(parents=True, exist_ok=True)
    data = dict(spec)
    data["layout_id"] = str(spec.get("layout_id") or layout_id(spec))
    path = root / f"{data['layout_id']}.json"
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    with (root / "history.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"layout_id": data["layout_id"], "archetype": spec.get("archetype"), "concept": str(spec.get("concept", ""))[:120],
                                 "fingerprints": fingerprints, "created": datetime.now(UTC).isoformat(timespec="seconds")},
                                ensure_ascii=False) + "\n")
    return path


def recent_fingerprints(limit: int = 30) -> list[dict[str, Any]]:
    root = layout_dir()
    if root is None or not (root / "history.jsonl").is_file():
        return []
    rows = []
    for line in (root / "history.jsonl").read_text(encoding="utf-8").splitlines()[-limit:]:
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


# --- checks -------------------------------------------------------------------------------------------


def _leaves(nodes: list[Any], dx: float = 0, dy: float = 0) -> Iterator[tuple[Any, float, float, str]]:
    for node in nodes:
        if isinstance(node, GroupNode):
            yield from ((leaf, x, y, node.node_id if layer == "" else layer) for leaf, x, y, layer in _leaves(list(node.children), dx + node.offset_x, dy + node.offset_y))
        else:
            yield node, dx, dy, ""


def _rect(node: Any, dx: float, dy: float) -> tuple[float, float, float, float]:
    return node.x + dx, node.y + dy, node.x + dx + node.width, node.y + dy + node.height


def _overlap(a: tuple[float, float, float, float], b: tuple[float, float, float, float]) -> float:
    return max(0.0, min(a[2], b[2]) - max(a[0], b[0])) * max(0.0, min(a[3], b[3]) - max(a[1], b[1]))


STORY_TOP, STORY_BOTTOM = 0.10, 0.86


def _text_extent(node: TextNode) -> tuple[float, float]:
    """Height actually used by the laid-out lines (boxes are often taller than their text)."""

    from .layout import wrap_text

    lines = wrap_text(node.text, node.width, node.font_size, node.char_width)
    return node.width, min(node.height, len(lines) * node.font_size * 1.2)


def layout_problems(ir: GraphicIR, behind_ids: set[str]) -> list[str]:
    """Readability problems of one compiled size (empty list = fine)."""

    problems: list[str] = []
    width, height = ir.canvas.width, ir.canvas.height
    order = {group.node_id: index for index, group in enumerate(ir.nodes) if isinstance(group, GroupNode)}
    texts, photos, opaque = [], [], []
    for node, dx, dy, layer in _leaves(list(ir.nodes)):
        if isinstance(node, TextNode) and not node.bleed and node.node_id not in behind_ids and not node.node_id.endswith("-shadow"):
            used_w, used_h = _text_extent(node)
            texts.append((node.node_id, (node.x + dx, node.y + dy, node.x + dx + used_w, node.y + dy + used_h), order.get(layer, 0)))
            if node.x + dx < -1 or node.y + dy < -1 or node.x + dx + node.width > width + 1 or node.y + dy + used_h > height + 1:
                problems.append(f"text_outside_canvas:{node.node_id}")
        elif node.node_id.startswith("art-overlay"):
            continue  # an art style's texture (smoke, grain, light leak) is meant to sit over everything, text included
        elif isinstance(node, ImageNode) and node.opacity >= 0.85:
            photos.append((node.node_id, _rect(node, dx, dy), order.get(layer, 0)))
        elif (isinstance(node, ShapeNode) and node.kind in ("rect", "ellipse") and node.opacity >= 0.85
              and node.fill not in ("none",) and node.node_id != "bg"):
            opaque.append((node.node_id, _rect(node, dx, dy), order.get(layer, 0)))
    if height >= width * 1.7:
        # Story apps draw their own UI over the top ~10% and bottom ~14%; only small print may sit there.
        for name, box, _level in texts:
            if not name.endswith("footer") and (box[1] < height * STORY_TOP or box[3] > height * STORY_BOTTOM):
                problems.append(f"text_in_story_ui_zone:{name}")
    for index, (first, box_a, _) in enumerate(texts):
        for second, box_b, _ in texts[index + 1:]:
            smaller = min((box_a[2] - box_a[0]) * (box_a[3] - box_a[1]), (box_b[2] - box_b[0]) * (box_b[3] - box_b[1])) or 1
            if _overlap(box_a, box_b) / smaller > 0.06:
                problems.append(f"text_overlaps_text:{first}+{second}")
    for name, box, level in texts:
        area = max((box[2] - box[0]) * (box[3] - box[1]), 1)
        for other, other_box, other_level in photos + opaque:
            if other_level > level and _overlap(box, other_box) / area > 0.12:
                problems.append(f"covered_text:{name}<-{other}")
    from .art_styles import contrast as ratio

    for name, box, level in texts:
        node = next(node for node, _dx, _dy, _layer in _leaves(list(ir.nodes)) if node.node_id == name)
        if not isinstance(node, TextNode) or not node.color.startswith("#"):
            continue
        need = 3.0 if node.font_size >= 24 else 4.5
        area = max((box[2] - box[0]) * (box[3] - box[1]), 1)
        # Only the nearest backdrop counts: the last opaque shape painted under the text that it overlaps.
        under = [(other_level, index, other) for index, (other, other_box, other_level) in enumerate(opaque)
                 if other_level <= level and _overlap(box, other_box) / area > 0.08]
        if not under:
            continue
        nearest = max(under)[2]
        shape = next(item for item, _dx, _dy, _layer in _leaves(list(ir.nodes)) if item.node_id == nearest)
        if isinstance(shape, ShapeNode) and shape.fill.startswith("#") and ratio(node.color, shape.fill) < need:
            problems.append(f"text_on_shape_low_contrast:{name}+{nearest}")
    from .qa import contrast_report

    for row in contrast_report(ir):
        if not row["passes_aa"] and row["node_id"] not in behind_ids:
            problems.append(f"low_contrast:{row['node_id']}")
    return problems


def fingerprint(ir: GraphicIR, behind_ids: set[str] | None = None) -> list[str]:
    """A GRID x GRID map of the dominant element per cell: T text, B big type, P photo, S shape, . empty."""

    width, height = ir.canvas.width, ir.canvas.height
    cell_w, cell_h = width / GRID, height / GRID
    cover = [[{"T": 0.0, "B": 0.0, "P": 0.0, "S": 0.0} for _ in range(GRID)] for _ in range(GRID)]
    for node, dx, dy, _layer in _leaves(list(ir.nodes)):
        if isinstance(node, TextNode):
            key = "B" if node.bleed else "T"
            box = (node.x + dx, node.y + dy, node.x + dx + node.width, node.y + dy + _text_extent(node)[1])
        elif isinstance(node, ImageNode):
            key, box = "P", _rect(node, dx, dy)
        elif isinstance(node, ShapeNode) and node.node_id != "bg" and node.opacity >= 0.5 and node.kind in ("rect", "ellipse"):
            key, box = "S", _rect(node, dx, dy)
        else:
            continue
        for row in range(GRID):
            for column in range(GRID):
                cell = (column * cell_w, row * cell_h, (column + 1) * cell_w, (row + 1) * cell_h)
                cover[row][column][key] += _overlap(box, cell)
    threshold = cell_w * cell_h * 0.15
    rows = []
    for row in range(GRID):
        line = ""
        for column in range(GRID):
            # Text and photos matter most for how a composition reads.
            weights = cover[row][column]
            best = max(("T", "P", "B", "S"), key=lambda key: weights[key] * (1.6 if key == "T" else 1.0))
            line += best if weights[best] >= threshold else "."
        rows.append(line)
    return rows


def similarity(first: list[str], second: list[str]) -> float:
    cells = [(a, b) for row_a, row_b in zip(first, second, strict=False) for a, b in zip(row_a, row_b, strict=False)]
    considered = [(a, b) for a, b in cells if a != "." or b != "."]
    if not considered:
        return 1.0
    return sum(1 for a, b in considered if a == b) / len(considered)


def too_similar(prints: Mapping[str, list[str]], history: list[dict[str, Any]], threshold: float = SIMILAR) -> str | None:
    """The id of a remembered layout this one nearly copies (same size compared), else ``None``."""

    for row in history:
        for variant, grid in prints.items():
            other = (row.get("fingerprints") or {}).get(variant)
            if other and similarity(grid, other) >= threshold:
                return str(row.get("layout_id"))
    return None


def check_layout(spec: Mapping[str, Any], base: Mapping[str, Any], variants: list[str]) -> tuple[Theme | None, dict[str, list[str]], list[str]]:
    """Compile ``spec`` with the job's content and photos in each size.

    Returns the theme, fingerprints per size, and problems (empty when usable).
    """

    theme, prints, problems, _scores = assess_layout(spec, base, variants)
    return theme, prints, problems


def assess_layout(spec: Mapping[str, Any], base: Mapping[str, Any], variants: list[str]
                  ) -> tuple[Theme | None, dict[str, list[str]], list[str], dict[str, Any]]:
    """:func:`check_layout` plus the design-quality score per size (:mod:`.aesthetics`)."""

    from .aesthetics import score_ir
    from .layout import LayoutError as TextFitError
    from .templates import TemplateError, compile_template

    try:
        theme = spec_theme(spec)
    except LayoutError as exc:
        return None, {}, [str(exc)], {}
    behind = {str(item["id"]) for layer in theme.layers for item in layer["items"] if item.get("behind")}
    missing = [slot for slot in theme.slots if not str((base.get("content") or {}).get(slot, "")).strip()]
    # "big" is an optional giant word only some compositions use; every other given text must be placed.
    unused = [slot for slot, text in (base.get("content") or {}).items() if text and slot not in theme.slots and slot in TEXT_SLOTS and slot != "big"]
    problems: list[str] = [f"slot_without_text:{slot}" for slot in missing] + [f"text_not_placed:{slot}" for slot in unused]
    missing_assets = [asset for asset in theme.assets if asset not in (base.get("assets") or {})]
    problems += [f"photo_not_provided:{asset}" for asset in missing_assets]
    if problems:
        return theme, {}, problems, {}
    prints: dict[str, list[str]] = {}
    scores: dict[str, Any] = {}
    with candidate_layout(theme):
        for variant in variants:
            width, height = VARIANT_SIZES[variant]
            request = {**dict(base), "request_id": f"{base.get('request_id', 'compose')}-{variant}", "template_id": theme.theme_id,
                       "variant": variant, "canvas": {"width": width, "height": height}}
            try:
                ir = compile_template(request)
            except TextFitError as exc:
                problems.append(f"text_does_not_fit:{variant}:{exc.node_id}")
                continue
            except (TemplateError, ThemeError, ValueError) as exc:
                problems.append(f"does_not_compile:{variant}:{str(exc)[:120]}")
                continue
            problems += [f"{problem}:{variant}" for problem in layout_problems(ir, behind)]
            prints[variant] = fingerprint(ir, behind)
            scores[variant] = score_ir(ir).to_dict()
    return theme, prints, problems, scores


__all__ = [
    "LAYOUT_DIR_ENV",
    "LAYOUT_SCHEMA",
    "TEXT_SLOTS",
    "LayoutError",
    "assess_layout",
    "candidate_layout",
    "check_layout",
    "find_layout_theme",
    "fingerprint",
    "layout_id",
    "layout_problems",
    "layout_to_theme",
    "recent_fingerprints",
    "save_layout",
    "similarity",
    "spec_theme",
    "too_similar",
]
