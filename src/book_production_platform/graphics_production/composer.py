"""Procedural composer: a new composition for every job, with no model call.

Instead of filling a fixed template, the composer *designs* a layout spec from
the job's own material (which texts exist and how long they are, whether there
is a hero photo, the brand colors, the requested sizes). It samples a
composition idea (split, full bleed, type sandwich, type poster, framed card,
diagonal, circle focus, editorial column, sticker pop, centered stack), then
the idea's own choices (side, alignment, scale, font pairing, color scheme,
decoration, tilt). Every candidate is compiled and checked in every size
(:func:`.layouts.check_layout`) and compared with the layout library's memory
and the other options of the same request, so it never hands back a near-copy.

Deterministic for a given seed: the same job and seed give the same layouts.
"""

from __future__ import annotations

import hashlib
import math
import random
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .art_styles import contrast
from .layouts import (
    LAYOUT_SCHEMA,
    layout_id,
    recent_fingerprints,
    similarity,
    too_similar,
)
from .themes import VARIANT_SIZES

ARCHETYPES = ("split", "full_bleed", "sandwich", "type_poster", "frame_card", "diagonal", "circle_focus", "editorial_column",
              "sticker_pop", "center_stack", "grid", "magazine_cover", "corner_minimal")
PHOTO_ARCHETYPES = {"split", "full_bleed", "sandwich", "frame_card", "diagonal", "circle_focus", "editorial_column", "sticker_pop",
                    "magazine_cover"}
# Ideas whose elements are all boxes (no positional motifs) can also be mirrored left-right.
MIRRORABLE = {"split", "full_bleed", "frame_card", "circle_focus", "editorial_column", "center_stack", "grid", "corner_minimal", "type_poster"}
# (display, body, label) — Thai-capable display faces first; Latin display faces fall back to Thai fonts per glyph.
PAIRS_THAI = (
    ("builtin:kanit", "builtin:anuphan", "builtin:mono"),
    ("builtin:prompt", "builtin:prompt", "builtin:poppins"),
    ("builtin:serif_thai", "builtin:anuphan", "builtin:mono"),
    ("builtin:trirong", "builtin:anuphan", "builtin:typewriter"),
    ("builtin:mitr", "builtin:anuphan", "builtin:poppins"),
    ("builtin:chakra", "builtin:chakra", "builtin:mono"),
    ("builtin:sriracha", "builtin:anuphan", "builtin:poppins"),
)
PAIRS_LATIN = (
    ("builtin:archivo", "builtin:poppins", "builtin:mono"),
    ("builtin:dm_serif", "builtin:poppins", "builtin:mono"),
    ("builtin:bebas", "builtin:poppins", "builtin:mono"),
    ("builtin:anton", "builtin:poppins", "builtin:typewriter"),
    ("builtin:limelight", "builtin:trirong", "builtin:typewriter"),
    ("builtin:poppins", "builtin:poppins", "builtin:mono"),
)
DECOR = ("grain", "dots", "halftone", "stripes", "rings", "none", "none")


def _hex_mix(first: str, second: str, amount: float) -> str:
    a = [int(first[i:i + 2], 16) for i in (1, 3, 5)]
    b = [int(second[i:i + 2], 16) for i in (1, 3, 5)]
    return "#" + "".join(f"{round(x + (y - x) * amount):02X}" for x, y in zip(a, b, strict=True))


def _scheme(rng: random.Random, primary: str, accent: str) -> tuple[str, dict[str, str]]:
    """A readable palette (role -> hex) from the brand colors."""

    def readable(color: str, background: str, minimum: float, fallback: str) -> str:
        return color if contrast(color, background) >= minimum else fallback

    name = rng.choice(("paper", "dark", "brand", "tint", "paper", "dark"))
    if name == "dark":
        bg, ink = "#121316", "#F4F4F2"
        accent_role = readable(accent, bg, 3.0, readable(primary, bg, 3.0, "#F2C14E"))
        return name, {"bg": bg, "bg2": "#1E2024", "surface": "#1A1C20", "ink": ink, "accent": accent_role,
                      "accent2": readable(primary, bg, 3.0, accent_role), "highlight": accent_role, "on_accent": bg, "muted": "#5A5D63"}
    if name == "brand":
        bg = primary
        ink = "#FFFFFF" if contrast("#FFFFFF", bg) >= contrast("#111111", bg) else "#111111"
        if contrast(ink, bg) < 4.5:
            return _scheme(random.Random(rng.random()), primary, accent) if rng.random() < 0.9 else ("paper", {})
        accent_role = readable(accent, bg, 3.0, ink)
        return name, {"bg": bg, "bg2": _hex_mix(bg, "#000000", 0.18), "surface": _hex_mix(bg, ink, 0.12), "ink": ink,
                      "accent": accent_role, "accent2": accent_role, "highlight": accent_role, "on_accent": bg, "muted": _hex_mix(bg, ink, 0.4)}
    bg = "#F3F0EA" if name == "paper" else _hex_mix(primary, "#FFFFFF", 0.88)
    ink = "#151515"
    accent_role = readable(primary, bg, 3.0, readable(accent, bg, 3.0, ink))
    return name, {"bg": bg, "bg2": _hex_mix(bg, "#000000", 0.07), "surface": "#FFFFFF", "ink": ink, "accent": accent_role,
                  "accent2": readable(accent, bg, 3.0, accent_role), "highlight": accent, "on_accent": "#FFFFFF" if contrast("#FFFFFF", accent_role) >= 3 else ink,
                  "muted": "#9A958C"}


@dataclass
class Plan:
    """Choices sampled once per candidate; geometry is computed per size from them."""

    archetype: str
    seed: int
    scheme: str
    palette: dict[str, str]
    fonts: tuple[str, str, str]
    knobs: dict[str, Any] = field(default_factory=dict)


# --- text sizing ----------------------------------------------------------------------------------


def _visible(text: str) -> int:
    return max(1, sum(1 for char in text if not ("ั" == char or "ิ" <= char <= "ฺ" or "็" <= char <= "๎")))


def _lines(text: str, width_px: float, size_px: float, em: float = 0.6) -> int:
    per_line = max(1, int(width_px / max(size_px * em, 1)))
    return sum(max(1, math.ceil(_visible(part) / per_line)) for part in text.split("\n"))


@dataclass
class Block:
    slot: str
    size: float           # font size as a fraction of MIN
    font: str
    weight: str = "normal"
    color: str = "role.ink"
    upper: bool = False
    gap_after: float = 0.02  # fraction of H
    extra: dict[str, Any] = field(default_factory=dict)


def _scaled(block: Block, scale: float) -> float:
    """Big type shrinks to fit; small labels never go below a readable size."""

    return max(block.size * scale, min(block.size, 0.016))


def stack(blocks: list[Block], content: Mapping[str, str], x: float, y: float, w: float, bottom: float,
          variant: str, align: str, prefix: str = "t", z: int = 10, palette: Mapping[str, str] | None = None,
          on: str = "bg") -> list[dict[str, Any]]:
    """Text elements stacked top-down inside (x, y, w, bottom), sizes shrunk until the text fits."""

    width, height = VARIANT_SIZES[variant]
    unit = min(width, height)
    present = [block for block in blocks if str(content.get(block.slot, "")).strip()]
    if not present:
        return []
    for scale in (1.0, 0.9, 0.8, 0.7, 0.6, 0.52):
        heights = []
        for block in present:
            size_px = _scaled(block, scale) * unit
            text = str(content[block.slot]).replace("*", "").replace("~", "")
            lines = _lines(text.upper() if block.upper else text, w * width, size_px, 0.62 if block.upper else 0.56)
            heights.append(lines * size_px * 1.24 / height + 0.004)
        total = sum(heights) + sum(block.gap_after for block in present[:-1])
        if y + total <= bottom:
            break
    elements = []
    cursor = y
    background = palette.get(on) if palette else None
    for block, box_h in zip(present, heights, strict=True):
        color = block.color
        if palette and background and color.startswith("role."):
            # Small text needs 4.5:1, large text 3:1 (WCAG AA); fall back to ink when the role is too faint.
            need = 3.0 if _scaled(block, scale) * unit >= 24 else 4.5
            if contrast(palette[color[5:]], background) < need:
                color = "role.ink"
        element = {"id": f"{prefix}-{block.slot}", "kind": "text", "slot": block.slot, "z": z, "font": block.font,
                   "size": round(_scaled(block, scale), 4), "weight": block.weight, "align": align, "color": color,
                   "box": {"x": round(x, 4), "y": round(cursor, 4), "w": round(w, 4), "h": round(box_h + 0.01, 4)},
                   "max_lines": 6, **block.extra}
        if block.upper:
            element["upper"] = True
        elements.append(element)
        cursor += box_h + block.gap_after
    return elements


def _text_blocks(plan: Plan, content: Mapping[str, str], head: float, accent_words: bool = True) -> list[Block]:
    display, body, label = plan.fonts
    upper = bool(plan.knobs.get("upper"))
    blocks = [
        Block("kicker", 0.022, label, color="role.accent", upper=True, gap_after=0.012, extra={"letter_spacing": 0.15}),
        Block("headline", head, display, weight="bold" if display not in ("builtin:bebas", "builtin:anton") else "normal",
              upper=upper, gap_after=0.018, extra={"accent_color": "role.accent"} if accent_words else {}),
        Block("subhead", head * 0.42, body, weight="bold", gap_after=0.014),
        Block("body", 0.026, body, gap_after=0.018),
        Block("price", head * 0.75, display, weight="bold", color="role.accent", gap_after=0.016),
    ]
    return blocks


def _cta(plan: Plan, content: Mapping[str, str], x: float, y: float, variant: str, align: str, z: int = 11) -> list[dict[str, Any]]:
    if not str(content.get("cta", "")).strip():
        return []
    width, height = VARIANT_SIZES[variant]
    unit = min(width, height)
    w = min(0.6, max(0.18, (len(content["cta"]) * 0.03 * unit * 0.62 + unit * 0.09) / width))
    h = unit * 0.075 / height
    if align == "center":
        x -= w / 2
    elif align == "right":
        x -= w
    radius = plan.knobs.get("pill", 0.035)
    return [
        {"id": "cta-bg", "kind": "shape", "shape": "rect", "z": z, "fill": "role.accent", "radius": radius,
         "box": {"x": round(x, 4), "y": round(y, 4), "w": round(w, 4), "h": round(h, 4)}},
        {"id": "cta-text", "kind": "text", "slot": "cta", "z": z + 1, "font": plan.fonts[1], "size": 0.03, "weight": "bold",
         "align": "center", "color": "role.on_accent",
         "box": {"x": round(x + 0.01, 4), "y": round(y + h * 0.2, 4), "w": round(w - 0.02, 4), "h": round(h * 0.7, 4)}},
    ]


def _room(plan: Plan, content: Mapping[str, str], variant: str) -> float:
    """Height (canvas fraction) to keep free under a text stack: the CTA button and the footer line."""

    width, height = VARIANT_SIZES[variant]
    unit = min(width, height)
    room = 0.012
    if str(content.get("cta", "")).strip():
        room += unit * 0.075 / height + 0.025
    if str(content.get("footer", "")).strip():
        room += unit * 0.035 / height
    return room


def _decor(plan: Plan, region: tuple[float, float, float, float], z: int = 2) -> list[dict[str, Any]]:
    kind = plan.knobs.get("decor", "none")
    if kind == "none":
        return []
    x, y, w, h = region
    region_expr = {"x": f"W * {x}", "y": f"H * {y}", "width": f"W * {w}", "height": f"H * {h}"}
    params: dict[str, Any] = {
        "grain": {"count": "1600 * W * H / 1166400", "size": "MIN * 0.0016", "color": "role.ink", "opacity": 0.07},
        "dots": {**region_expr, "spacing": "MIN * 0.04", "radius": "MIN * 0.005", "color": "role.ink", "opacity": 0.18},
        "halftone": {**region_expr, "spacing": "MIN * 0.026", "max_radius": "MIN * 0.01", "direction": plan.knobs.get("decor_dir", 45),
                     "color": "role.accent", "opacity": 0.35},
        "stripes": {**region_expr, "band": "MIN * 0.01", "gap": "MIN * 0.016", "angle": plan.knobs.get("decor_dir", 45),
                    "color": "role.accent", "opacity": 0.22},
        "rings": {"cx": f"W * {x + w}", "cy": f"H * {y}", "radius": "MIN * 0.08", "step": "MIN * 0.035", "count": 7,
                  "stroke": "role.accent", "stroke_width": "MIN * 0.002", "opacity": 0.45},
    }[kind]
    return [{"id": "decor", "kind": "motif", "motif": kind, "params": params, "z": z}]


def _footer(plan: Plan, content: Mapping[str, str], variant: str) -> list[dict[str, Any]]:
    """Small print sits on its own along the bottom edge, never inside the main stack."""

    if not str(content.get("footer", "")).strip():
        return []
    mx, my = _margin(plan, variant)
    height = 0.026 * min(VARIANT_SIZES[variant]) / VARIANT_SIZES[variant][1]
    return [{"id": "footer", "kind": "text", "slot": "footer", "z": 14, "font": plan.fonts[2], "size": 0.016, "align": "right",
             "upper": True, "color": "role.ink", "opacity": 0.8,
             "box": {"x": 0.5, "y": round(1 - my * 0.55 - height, 4), "w": round(0.5 - mx, 4), "h": round(height, 4)}}]


# --- archetypes ----------------------------------------------------------------------------------------
# Each takes the plan, the content, the photo keys, and a size; returns elements in canvas fractions.


def _margin(plan: Plan, variant: str) -> tuple[float, float]:
    width, height = VARIANT_SIZES[variant]
    m = plan.knobs.get("margin", 0.065) * min(width, height)
    if height >= width * 1.7:
        return m / width, max(m / height, 0.105)  # stay clear of the story app's own UI at the top and bottom
    return m / width, m / height


def a_split(plan: Plan, content: Mapping[str, str], photos: list[str], variant: str) -> list[dict[str, Any]]:
    mx, my = _margin(plan, variant)
    ratio = plan.knobs.get("ratio", 0.5)
    first = plan.knobs.get("photo_first", True)
    elements: list[dict[str, Any]] = []
    effects = {"fit": "cover", "focus_y": 0.35}
    if variant in ("story", "portrait"):
        photo_h = ratio * (0.95 if variant == "story" else 0.85)
        py = 0.0 if first else 1 - photo_h
        elements.append({"id": "photo", "kind": "photo", "asset": photos[0], "z": 3, "effects": effects,
                         "box": {"x": 0, "y": py, "w": 1, "h": photo_h}})
        top, bottom = (photo_h + my, 1 - my * 1.6) if first else (my, py - my)
        x, w = mx, 1 - 2 * mx
    else:
        photo_w = ratio
        px = 0.0 if first else 1 - photo_w
        elements.append({"id": "photo", "kind": "photo", "asset": photos[0], "z": 3, "effects": effects,
                         "box": {"x": px, "y": 0, "w": photo_w, "h": 1}})
        x = photo_w + mx if first else mx
        w = 1 - photo_w - 2 * mx
        top, bottom = my * 1.6, 1 - my * 1.6
    if plan.knobs.get("block"):
        bx = x - mx * 0.5
        elements.append({"id": "panel", "kind": "shape", "shape": "rect", "z": 4, "fill": "role.bg2",
                         "box": {"x": round(max(0, bx), 4), "y": round(max(0, top - my * 0.5), 4), "w": round(w + mx, 4),
                                 "h": round(min(1, bottom - top + my), 4)}})
    align = plan.knobs.get("align", "left")
    anchor = x if align == "left" else x + w / 2 if align == "center" else x + w
    texts = stack(_text_blocks(plan, content, plan.knobs.get("head", 0.075)), content, x, top, w, bottom - _room(plan, content, variant), variant, align, palette=plan.palette,
                  on="bg2" if plan.knobs.get("block") else "bg")
    end = max((t["box"]["y"] + t["box"]["h"] for t in texts), default=top)
    return elements + texts + _cta(plan, content, anchor, end + 0.02, variant, align)


def a_full_bleed(plan: Plan, content: Mapping[str, str], photos: list[str], variant: str) -> list[dict[str, Any]]:
    mx, my = _margin(plan, variant)
    band_top = plan.knobs.get("band_top", False)
    band = plan.knobs.get("band", 0.46)
    elements: list[dict[str, Any]] = [
        {"id": "photo", "kind": "photo", "asset": photos[0], "z": 1, "box": {"x": 0, "y": 0, "w": 1, "h": 1},
         "effects": {"fit": "cover", "focus_y": 0.3 if not band_top else 0.7, "saturation": plan.knobs.get("sat", 1.0)}},
    ]
    by = 0.0 if band_top else 1 - band
    elements.append({"id": "veil", "kind": "shape", "shape": "rect", "z": 2, "fill": "role.bg", "opacity": 0.86,
                     "box": {"x": 0, "y": round(by, 4), "w": 1, "h": round(band, 4)}})
    align = plan.knobs.get("align", "left")
    x, w = mx, 1 - 2 * mx
    anchor = x if align == "left" else 0.5 if align == "center" else 1 - mx
    texts = stack(_text_blocks(plan, content, plan.knobs.get("head", 0.08)), content, x, by + my, w, by + band - my - _room(plan, content, variant), variant, align, palette=plan.palette)
    end = max((t["box"]["y"] + t["box"]["h"] for t in texts), default=by + my)
    return elements + texts + _cta(plan, content, anchor, end + 0.015, variant, align)


def a_sandwich(plan: Plan, content: Mapping[str, str], photos: list[str], variant: str) -> list[dict[str, Any]]:
    mx, my = _margin(plan, variant)
    word_slot = "big" if str(content.get("big", "")).strip() else "headline"
    wide = variant == "landscape"
    sub_h = {"square": 0.5, "portrait": 0.58, "story": 0.56}.get(variant, 0.96)
    sub = {"x": 0.42, "y": 0.04, "w": 0.55, "h": 0.96} if wide else {"x": 0.14, "y": 0.06, "w": 0.72, "h": sub_h}
    if plan.knobs.get("flip") and wide:
        sub["x"] = 0.03
    elements: list[dict[str, Any]] = [
        {"id": "word", "kind": "big_type", "slot": word_slot, "z": 3, "font": plan.knobs.get("word_font", "builtin:display"),
         "x": (sub["x"] + sub["w"] / 2) if wide else 0.5, "y": 0.2 if not wide else 0.18, "ys": {"story": 0.24},
         "span": 0.62 if wide else 1.1, "color": "role.accent", "opacity": plan.knobs.get("word_opacity", 0.9),
         "outline": plan.knobs.get("word_outline", False), "motion_blur": plan.knobs.get("blur", 0.0), "max_size": 0.3},
        {"id": "subject", "kind": "photo", "asset": photos[0], "z": 5, "box": sub,
         "effects": {"cutout": "ai", "fit": "contain", "focus_y": 1.0, "shadow": 0.4}},
    ]
    if plan.knobs.get("echo"):
        elements.append({"id": "word-front", "kind": "big_type", "slot": word_slot, "z": 6, "font": plan.knobs.get("word_font", "builtin:display"),
                         "x": elements[0]["x"], "y": elements[0]["y"], "ys": {"story": 0.24}, "span": elements[0]["span"], "color": "role.ink",
                         "outline": True, "opacity": 0.8, "max_size": 0.3})
    blocks = [block for block in _text_blocks(plan, content, plan.knobs.get("head", 0.06)) if block.slot != word_slot]
    align = plan.knobs.get("align", "left")
    if wide:
        x, w, top, bottom = (mx, 0.4, 0.5, 1 - my) if not plan.knobs.get("flip") else (0.6, 0.36, 0.5, 1 - my)
    else:
        x, w, top, bottom = mx, 1 - 2 * mx, sub["y"] + sub["h"] + my * 0.4, 1 - my
    anchor = x if align == "left" else x + w / 2 if align == "center" else x + w
    texts = stack(blocks, content, x, top, w, bottom - _room(plan, content, variant), variant, align, palette=plan.palette)
    end = max((t["box"]["y"] + t["box"]["h"] for t in texts), default=top)
    return elements + texts + _cta(plan, content, anchor, end + 0.015, variant, align)


def a_type_poster(plan: Plan, content: Mapping[str, str], photos: list[str], variant: str) -> list[dict[str, Any]]:
    mx, my = _margin(plan, variant)
    elements: list[dict[str, Any]] = []
    shape = plan.knobs.get("shape", "circle")
    if shape == "circle":
        size = 0.62 if variant != "landscape" else 0.42
        cx = plan.knobs.get("cx", 0.72)
        elements.append({"id": "orb", "kind": "shape", "shape": "ellipse", "z": 2, "fill": "role.accent", "opacity": 0.95,
                         "box": {"x": cx - size / 2, "y": 0.04, "w": size, "h": size * VARIANT_SIZES[variant][0] / VARIANT_SIZES[variant][1]}})
    elif shape == "block":
        elements.append({"id": "slab", "kind": "shape", "shape": "rect", "z": 2, "fill": "role.bg2",
                         "box": {"x": 0, "y": 0.0, "w": 1, "h": plan.knobs.get("slab", 0.55)}})
    if photos:
        elements.append({"id": "sticker", "kind": "photo", "asset": photos[0], "z": 6,
                         "box": {"x": 0.62, "y": 0.62 if variant != "landscape" else 0.5, "w": 0.32, "h": 0.3},
                         "effects": {"cutout": "ai", "fit": "contain", "outline_width": 10, "outline_color": "role.surface", "shadow": 0.4},
                         "rotation": plan.knobs.get("tilt", 6)})
    blocks = _text_blocks(plan, content, plan.knobs.get("head", 0.14))
    align = plan.knobs.get("align", "left")
    w = (0.56 if photos else 1 - 2 * mx) if variant != "landscape" else 0.62
    x = mx
    anchor = x if align == "left" else x + w / 2 if align == "center" else x + w
    top = my * 1.5 if shape != "block" else my * 1.2
    texts = stack(blocks, content, x, top, w, 1 - my - _room(plan, content, variant), variant, align, palette=plan.palette)
    end = max((t["box"]["y"] + t["box"]["h"] for t in texts), default=top)
    rule = [{"id": "rule", "kind": "shape", "shape": "line", "z": 5, "stroke": "role.ink", "stroke_width": 0.002,
             "box": {"x": mx, "y": round(end + 0.01, 4), "w": 1 - 2 * mx, "h": 0.01}}] if plan.knobs.get("rule") else []
    return elements + texts + rule + _cta(plan, content, anchor, end + 0.03, variant, align)


def a_frame_card(plan: Plan, content: Mapping[str, str], photos: list[str], variant: str) -> list[dict[str, Any]]:
    mx, my = _margin(plan, variant)
    inset_x, inset_y = mx * 0.6, my * 0.6
    card = {"x": inset_x, "y": inset_y, "w": 1 - 2 * inset_x, "h": 1 - 2 * inset_y}
    elements: list[dict[str, Any]] = [
        {"id": "card", "kind": "shape", "shape": "rect", "z": 3, "fill": "role.surface", "radius": plan.knobs.get("radius", 0.03), "box": card},
    ]
    pad_x, pad_y = mx * 0.55, my * 0.55
    if variant == "landscape":
        photo = {"x": card["x"] + pad_x, "y": card["y"] + pad_y, "w": card["w"] * 0.48, "h": card["h"] - 2 * pad_y}
        tx, tw, top, bottom = photo["x"] + photo["w"] + pad_x, card["x"] + card["w"] - photo["x"] - photo["w"] - 2 * pad_x, photo["y"], photo["y"] + photo["h"]
    else:
        photo = {"x": card["x"] + pad_x, "y": card["y"] + pad_y, "w": card["w"] - 2 * pad_x, "h": card["h"] * plan.knobs.get("photo_share", 0.5)}
        tx, tw, top, bottom = photo["x"], photo["w"], photo["y"] + photo["h"] + pad_y, card["y"] + card["h"] - pad_y
    elements.append({"id": "photo", "kind": "photo", "asset": photos[0], "z": 4, "box": photo,
                     "effects": {"fit": "cover", "mask": "rounded", "corner_radius": 0.05, "focus_y": 0.4}})
    align = plan.knobs.get("align", "left")
    anchor = tx if align == "left" else tx + tw / 2 if align == "center" else tx + tw
    texts = stack(_text_blocks(plan, content, plan.knobs.get("head", 0.065)), content, tx, top, tw, bottom - _room(plan, content, variant), variant, align, palette=plan.palette, on="surface")
    end = max((t["box"]["y"] + t["box"]["h"] for t in texts), default=top)
    for text in texts:
        if text["color"] == "role.ink":
            text["color"] = "role.ink"
    return _decor(plan, (0, 0, 1, 1), 1) + elements + texts + _cta(plan, content, anchor, end + 0.015, variant, align)


def a_diagonal(plan: Plan, content: Mapping[str, str], photos: list[str], variant: str) -> list[dict[str, Any]]:
    mx, my = _margin(plan, variant)
    rising = plan.knobs.get("rising", True)
    elements: list[dict[str, Any]] = [
        {"id": "blade", "kind": "motif", "motif": "swoosh", "z": 2, "params": {
            "x1": "W * 0.42" if variant == "landscape" else "W * -0.05", "y1": "H * 1.03" if rising else "H * -0.03",
            "x2": "W * 1.05", "y2": "H * 0.56" if rising else "H * 0.44",
            "width": "MIN * 0.24", "bend": 0.08 if rising else -0.08, "tip": 0.15, "fill": "role.accent", "opacity": 0.95}},
    ]
    wide = variant == "landscape"
    sub = {"x": 0.5, "y": 0.08, "w": 0.46, "h": 0.9} if wide else {"x": 0.3, "y": 0.3 if rising else 0.04, "w": 0.66, "h": 0.66}
    elements.append({"id": "subject", "kind": "photo", "asset": photos[0], "z": 5, "box": sub, "rotation": plan.knobs.get("tilt", 0),
                     "effects": {"cutout": "ai", "fit": "contain", "shadow": 0.5}})
    x, w = mx, (0.44 if wide else 1 - 2 * mx)
    top, bottom = (my * 1.4, 0.92) if wide or rising else (0.64, 1 - my)
    if not wide and rising:
        bottom = 0.34
    texts = stack(_text_blocks(plan, content, plan.knobs.get("head", 0.075)), content, x, top, w if wide else 0.6, bottom, variant, "left", z=10, palette=plan.palette)
    end = max((t["box"]["y"] + t["box"]["h"] for t in texts), default=top)
    cta_y = end + 0.015
    return elements + texts + _cta(plan, content, x, cta_y, variant, "left")


def a_circle_focus(plan: Plan, content: Mapping[str, str], photos: list[str], variant: str) -> list[dict[str, Any]]:
    mx, my = _margin(plan, variant)
    width, height = VARIANT_SIZES[variant]
    wide = variant == "landscape"
    diameter = {"square": 0.42, "portrait": 0.54, "story": 0.66, "landscape": 0.4}[variant]
    d_h = diameter * width / height
    cx = 0.5 if not wide else (0.72 if not plan.knobs.get("flip") else 0.28)
    cy_top = 0.08 if variant == "square" else 0.1 if not wide else (1 - d_h) / 2
    box = {"x": cx - diameter / 2, "y": cy_top, "w": diameter, "h": d_h}
    ring = {"x": box["x"] - 0.02, "y": box["y"] - 0.02 * width / height, "w": diameter + 0.04, "h": d_h + 0.04 * width / height}
    elements: list[dict[str, Any]] = [
        {"id": "ring", "kind": "shape", "shape": "ellipse", "z": 2, "fill": "role.accent", "box": ring},
        {"id": "photo", "kind": "photo", "asset": photos[0], "z": 3, "box": box,
         "effects": {"fit": "cover", "mask": "circle", "focus_y": 0.35, "saturation": plan.knobs.get("sat", 1.0)}},
    ]
    align = "center" if not wide else "left"
    if wide:
        x, w, top, bottom = (mx, 0.44, my * 1.5, 1 - my) if not plan.knobs.get("flip") else (0.52, 0.44, my * 1.5, 1 - my)
    else:
        x, w, top, bottom = mx, 1 - 2 * mx, ring["y"] + ring["h"] + my * 0.5, 1 - my
    anchor = x + w / 2 if align == "center" else x
    texts = stack(_text_blocks(plan, content, plan.knobs.get("head", 0.07)), content, x, top, w, bottom - _room(plan, content, variant), variant, align, palette=plan.palette)
    end = max((t["box"]["y"] + t["box"]["h"] for t in texts), default=top)
    return _decor(plan, (0, 0, 1, 0.5), 1) + elements + texts + _cta(plan, content, anchor, end + 0.015, variant, align)


def a_editorial_column(plan: Plan, content: Mapping[str, str], photos: list[str], variant: str) -> list[dict[str, Any]]:
    mx, my = _margin(plan, variant)
    tall = variant in ("story", "portrait")
    col = 0.46 if not tall else 1.0
    elements: list[dict[str, Any]] = []
    if tall:
        photo = {"x": 0.0, "y": 0.0, "w": 1.0, "h": 0.5 if variant == "story" else 0.46}
        elements.append({"id": "photo", "kind": "photo", "asset": photos[0], "z": 2, "box": photo,
                         "effects": {"fit": "cover", "fade": "bottom", "fade_amount": 0.35, "filter": plan.knobs.get("filter", "none")}})
        x, w, top, bottom = mx, 1 - 2 * mx, photo["h"] + my * 0.2, 1 - my
    else:
        photo = {"x": col - 0.04, "y": 0, "w": 1 - col + 0.04, "h": 1}
        elements.append({"id": "photo", "kind": "photo", "asset": photos[0], "z": 2, "box": photo,
                         "effects": {"fit": "cover", "fade": "left", "fade_amount": 0.25, "filter": plan.knobs.get("filter", "none")}})
        x, w, top, bottom = mx, col - 2 * mx, my * 1.5, 1 - my
    elements.append({"id": "rule-top", "kind": "shape", "shape": "line", "z": 9, "stroke": "role.ink", "stroke_width": 0.0015,
                     "box": {"x": x, "y": top, "w": w, "h": 0.01}})
    blocks = _text_blocks(plan, content, plan.knobs.get("head", 0.07))
    texts = stack(blocks, content, x, top + 0.02, w, bottom - _room(plan, content, variant), variant, "left", palette=plan.palette)
    end = max((t["box"]["y"] + t["box"]["h"] for t in texts), default=top)
    return elements + texts + _cta(plan, content, x, end + 0.015, variant, "left")


def a_sticker_pop(plan: Plan, content: Mapping[str, str], photos: list[str], variant: str) -> list[dict[str, Any]]:
    mx, my = _margin(plan, variant)
    wide = variant == "landscape"
    elements: list[dict[str, Any]] = [
        {"id": "burst", "kind": "motif", "motif": "rays", "z": 1, "params": {
            "cx": "W * 0.62" if not wide else "W * 0.7", "cy": "H * 0.55", "outer": "MAX", "count": plan.knobs.get("rays", 28),
            "color": "role.accent", "opacity": 0.14}},
    ]
    sub = ({"x": 0.3, "y": 0.28, "w": 0.62, "h": {"square": 0.42, "portrait": 0.46, "story": 0.44}[variant]}
           if not wide else {"x": 0.5, "y": 0.12, "w": 0.44, "h": 0.82})
    elements.append({"id": "subject", "kind": "photo", "asset": photos[0], "z": 5, "box": sub, "rotation": plan.knobs.get("tilt", -6),
                     "effects": {"cutout": "ai", "fit": "contain", "outline_width": 14, "outline_color": "role.surface", "shadow": 0.5}})
    if str(content.get("price", "")).strip():
        bw = 0.26 if not wide else 0.16
        bh = bw * VARIANT_SIZES[variant][0] / VARIANT_SIZES[variant][1]
        bx, by = (0.06, sub["y"] + 0.02) if not wide else (sub["x"] - bw * 0.4, 0.62)
        elements += [
            {"id": "badge", "kind": "shape", "shape": "ellipse", "z": 7, "fill": "role.accent", "box": {"x": bx, "y": by, "w": bw, "h": bh}},
            {"id": "badge-price", "kind": "text", "slot": "price", "z": 8, "font": plan.fonts[0], "size": 0.06, "weight": "bold",
             "align": "center", "color": "role.on_accent", "rotation": -8,
             "box": {"x": bx + bw * 0.1, "y": by + bh * 0.33, "w": bw * 0.8, "h": bh * 0.36}},
        ]
    blocks = [block for block in _text_blocks(plan, content, plan.knobs.get("head", 0.085)) if block.slot != "price"]
    x, w = mx, (1 - 2 * mx) if not wide else 0.42
    top, bottom = (my * 1.2, sub["y"] - 0.01) if not wide else (my * 1.5, 1 - my)
    texts = stack(blocks[:3], content, x, top, w, bottom, variant, "left" if wide else plan.knobs.get("align", "left"), palette=plan.palette)
    rest = [block for block in blocks[3:] if str(content.get(block.slot, "")).strip()]
    badge_bottom = max((e["box"]["y"] + e["box"]["h"] for e in elements if e["id"] == "badge"), default=0.0)
    lower_top = max(sub["y"] + sub["h"], badge_bottom) + 0.02 if not wide else max((t["box"]["y"] + t["box"]["h"] for t in texts), default=top) + 0.02
    lower = stack(rest, content, x, lower_top, w if wide else 1 - 2 * mx, 1 - my - _room(plan, content, variant), variant, "left", prefix="l", palette=plan.palette)
    end = max((t["box"]["y"] + t["box"]["h"] for t in texts + lower), default=lower_top)
    return elements + texts + lower + _cta(plan, content, x if wide else 1 - mx, end + 0.015, variant,
                                           "left" if wide else "right")


def a_center_stack(plan: Plan, content: Mapping[str, str], photos: list[str], variant: str) -> list[dict[str, Any]]:
    mx, my = _margin(plan, variant)
    elements: list[dict[str, Any]] = []
    blocks = _text_blocks(plan, content, plan.knobs.get("head", 0.09))
    head_blocks = [block for block in blocks if block.slot in ("kicker", "headline", "subhead")]
    tail_blocks = [block for block in blocks if block.slot not in ("kicker", "headline", "subhead")]
    if photos:
        box = {"square": {"x": 0.33, "y": 0.34, "w": 0.34, "h": 0.26}, "portrait": {"x": 0.28, "y": 0.34, "w": 0.44, "h": 0.3},
               "story": {"x": 0.22, "y": 0.34, "w": 0.56, "h": 0.32}, "landscape": {"x": 0.4, "y": 0.3, "w": 0.2, "h": 0.32}}[variant]
        elements.append({"id": "hero", "kind": "photo", "asset": photos[0], "z": 5, "box": box,
                         "effects": {"cutout": "ai", "fit": "contain", "glow": 0.4, "glow_color": "role.surface", "shadow": 0.35}})
        head_bottom, tail_top = box["y"] - 0.01, box["y"] + box["h"] + 0.015
    else:
        head_bottom, tail_top = 0.62, 0.64
    if plan.knobs.get("frame"):
        elements.append({"id": "frame", "kind": "motif", "motif": "corner_frame", "z": 1, "params": {
            "inset": "MIN * 0.035", "length": "MIN * 0.07", "color": "role.accent", "stroke_width": "MIN * 0.004"}})
    texts = stack(head_blocks, content, mx, my * 1.2, 1 - 2 * mx, head_bottom, variant, "center", palette=plan.palette)
    tail = stack(tail_blocks, content, mx, tail_top, 1 - 2 * mx, 1 - my - _room(plan, content, variant), variant, "center", prefix="l", palette=plan.palette)
    end = max((t["box"]["y"] + t["box"]["h"] for t in texts + tail), default=tail_top)
    return _decor(plan, (0, 0.6, 1, 0.4), 1) + elements + texts + tail + _cta(plan, content, 0.5, end + 0.015, variant, "center")


def a_grid(plan: Plan, content: Mapping[str, str], photos: list[str], variant: str) -> list[dict[str, Any]]:
    """A modular grid: photo zones, colour blocks, and one text zone snapped to cells (endless arrangements)."""

    width, height = VARIANT_SIZES[variant]
    cols = plan.knobs.get("cols", 3) if variant != "story" else max(2, plan.knobs.get("cols", 3) - 1)
    rows = plan.knobs.get("rows", 4) + (2 if variant == "story" else 1 if variant == "portrait" else 0)
    if variant == "landscape":
        cols, rows = cols + 1, max(3, rows - 1)
    gutter = plan.knobs.get("gutter", 0.012)
    gx, gy = gutter, gutter * width / height

    def cell(c0: int, r0: int, c1: int, r1: int) -> dict[str, float]:
        cw, rh = (1 - gx * (cols + 1)) / cols, (1 - gy * (rows + 1)) / rows
        return {"x": round(gx + c0 * (cw + gx), 4), "y": round(gy + r0 * (rh + gy), 4),
                "w": round((c1 - c0) * cw + (c1 - c0 - 1) * gx, 4), "h": round((r1 - r0) * rh + (r1 - r0 - 1) * gy, 4)}

    elements: list[dict[str, Any]] = []
    text_side = plan.knobs.get("text_side", "bottom")
    if variant == "landscape" and text_side in ("top", "bottom"):
        text_side = "left" if text_side == "top" else "right"
    if variant != "landscape" and text_side in ("left", "right"):
        text_side = "bottom" if text_side == "right" else "top"
    text_cells = max(2, rows // 2) if text_side in ("top", "bottom") else max(1, cols // 2)
    if text_side == "bottom":
        text_box, free = cell(0, rows - text_cells, cols, rows), (0, 0, cols, rows - text_cells)
    elif text_side == "top":
        text_box, free = cell(0, 0, cols, text_cells), (0, text_cells, cols, rows)
    elif text_side == "left":
        text_box, free = cell(0, 0, text_cells, rows), (text_cells, 0, cols, rows)
    else:
        text_box, free = cell(cols - text_cells, 0, cols, rows), (0, 0, cols - text_cells, rows)
    c0, r0, c1, r1 = free
    pieces = [photos[0]] if photos else []
    pieces += photos[1:3]
    if pieces:
        split = plan.knobs.get("split", "single")
        boxes: list[dict[str, float]]
        if len(pieces) == 1 or split == "single" or c1 - c0 < 2:
            boxes = [cell(c0, r0, c1, r1)]
        else:
            mid = c0 + (c1 - c0) // 2
            boxes = [cell(c0, r0, mid, r1), cell(mid, r0, c1, r1)]
            if len(pieces) >= 3 and r1 - r0 >= 2:
                mr = r0 + (r1 - r0) // 2
                boxes = [cell(c0, r0, mid, r1), cell(mid, r0, c1, mr), cell(mid, mr, c1, r1)]
        for index, box in enumerate(boxes):
            asset = pieces[index % len(pieces)]
            elements.append({"id": f"photo{index + 1}", "kind": "photo", "asset": asset, "z": 3, "box": box,
                             "effects": {"fit": "cover", "focus_y": 0.4, "mask": "rounded" if plan.knobs.get("rounded") else "rect",
                                         "corner_radius": 0.04, "filter": plan.knobs.get("filter", "none")}})
    else:
        # No photo: a Swiss-style mosaic of colour cells, one carrying a pattern, instead of one empty slab.
        if c1 - c0 >= 2 and r1 - r0 >= 2:
            mid_c, mid_r = c0 + (c1 - c0) // 2, r0 + (r1 - r0) // 2
            tiles = [(cell(c0, r0, mid_c, r1), "role.accent"), (cell(mid_c, r0, c1, mid_r), "role.ink"), (cell(mid_c, mid_r, c1, r1), "role.bg2")]
        elif c1 - c0 >= 2:
            mid_c = c0 + (c1 - c0) // 2
            tiles = [(cell(c0, r0, mid_c, r1), "role.accent"), (cell(mid_c, r0, c1, r1), "role.ink")]
        elif r1 - r0 >= 2:
            mid_r = r0 + (r1 - r0) // 2
            tiles = [(cell(c0, r0, c1, mid_r), "role.accent"), (cell(c0, mid_r, c1, r1), "role.ink")]
        else:
            tiles = [(cell(c0, r0, c1, r1), "role.accent")]
        if plan.knobs.get("flip_tiles"):
            tiles = list(reversed(tiles)) if len(tiles) == 2 else [tiles[0], tiles[2], tiles[1]]
        for index, (box, fill) in enumerate(tiles):
            elements.append({"id": f"tile{index + 1}", "kind": "shape", "shape": "rect", "z": 2, "fill": fill, "box": box,
                             "radius": plan.knobs.get("radius", 0.0)})
        pattern_box, pattern_on = tiles[-1]
        pattern = plan.knobs.get("tile_pattern", "stripes")
        region = {"x": f"W * {pattern_box['x']}", "y": f"H * {pattern_box['y']}", "width": f"W * {pattern_box['w']}",
                  "height": f"H * {pattern_box['h']}"}
        color = "role.bg" if pattern_on == "role.ink" else "role.accent" if pattern_on == "role.bg2" else "role.ink"
        params = ({**region, "band": "MIN * 0.012", "gap": "MIN * 0.018", "angle": 45, "color": color, "opacity": 0.5} if pattern == "stripes"
                  else {**region, "spacing": "MIN * 0.03", "radius": "MIN * 0.007", "color": color, "opacity": 0.55})
        elements.append({"id": "tile-pattern", "kind": "motif", "motif": pattern, "params": params, "z": 3})
    if plan.knobs.get("panel", True):
        elements.append({"id": "panel", "kind": "shape", "shape": "rect", "z": 4, "fill": "role.bg2", "box": text_box,
                         "radius": plan.knobs.get("radius", 0.0)})
    pad_x, pad_y = 0.04, 0.04 * width / height
    x, w = text_box["x"] + pad_x, text_box["w"] - 2 * pad_x
    top, bottom = text_box["y"] + pad_y, text_box["y"] + text_box["h"] - pad_y
    align = plan.knobs.get("align", "left")
    anchor = x if align == "left" else x + w / 2 if align == "center" else x + w
    texts = stack(_text_blocks(plan, content, plan.knobs.get("head", 0.075)), content, x, top, w, bottom - _room(plan, content, variant),
                  variant, align, palette=plan.palette, on="bg2" if plan.knobs.get("panel", True) else "bg")
    end = max((t["box"]["y"] + t["box"]["h"] for t in texts), default=top)
    return elements + texts + _cta(plan, content, anchor, end + 0.015, variant, align)


def a_magazine_cover(plan: Plan, content: Mapping[str, str], photos: list[str], variant: str) -> list[dict[str, Any]]:
    """Full-bleed photo, a masthead word across the top, cover lines down one side."""

    mx, my = _margin(plan, variant)
    word_slot = "big" if str(content.get("big", "")).strip() else "kicker"
    elements: list[dict[str, Any]] = [
        {"id": "photo", "kind": "photo", "asset": photos[0], "z": 1, "box": {"x": 0, "y": 0, "w": 1, "h": 1},
         "effects": {"fit": "cover", "focus_y": 0.35, "filter": plan.knobs.get("filter", "none"), "brightness": 0.9}},
        {"id": "shade", "kind": "shape", "shape": "rect", "z": 2, "fill": "role.bg", "opacity": 0.88,
         "box": {"x": 0, "y": 0.6 if variant != "landscape" else 0.0, "w": 1 if variant != "landscape" else 0.46,
                 "h": 0.4 if variant != "landscape" else 1}},
    ]
    if str(content.get(word_slot, "")).strip():
        elements.append({"id": "masthead", "kind": "big_type", "slot": word_slot, "z": 4, "font": plan.knobs.get("mast_font", "builtin:dm_serif"),
                         "x": 0.5, "y": 0.02, "span": 0.94, "color": "role.ink", "max_size": 0.2, "opacity": 0.95})
    blocks = [block for block in _text_blocks(plan, content, plan.knobs.get("head", 0.07)) if block.slot != word_slot]
    if variant == "landscape":
        x, w, top, bottom = mx, 0.46 - 2 * mx, 0.3, 1 - my
    else:
        x, w, top, bottom = mx, 1 - 2 * mx, 0.62 + my * 0.3, 1 - my
    texts = stack(blocks, content, x, top, w, bottom - _room(plan, content, variant), variant, "left", palette=plan.palette)
    end = max((t["box"]["y"] + t["box"]["h"] for t in texts), default=top)
    return elements + texts + _cta(plan, content, x, end + 0.015, variant, "left")


def a_corner_minimal(plan: Plan, content: Mapping[str, str], photos: list[str], variant: str) -> list[dict[str, Any]]:
    """Mostly empty canvas: a small photo or mark, one confident headline, details tucked into a corner."""

    mx, my = _margin(plan, variant)
    corner = plan.knobs.get("corner", "bottom_left")
    elements: list[dict[str, Any]] = []
    if photos:
        size = 0.34 if variant != "landscape" else 0.24
        box_h = size * VARIANT_SIZES[variant][0] / VARIANT_SIZES[variant][1]
        px = 1 - mx - size if corner.endswith("left") else mx
        elements.append({"id": "photo", "kind": "photo", "asset": photos[0], "z": 3,
                         "box": {"x": round(px, 4), "y": round(my * 1.4, 4), "w": size, "h": round(box_h, 4)},
                         "effects": {"fit": "cover", "mask": plan.knobs.get("mask", "rect"), "focus_y": 0.4}})
    else:
        elements.append({"id": "mark", "kind": "shape", "shape": "ellipse", "z": 3, "fill": "role.accent",
                         "box": {"x": 1 - mx - 0.08 if corner.endswith("left") else mx, "y": my * 1.4, "w": 0.08,
                                 "h": round(0.08 * VARIANT_SIZES[variant][0] / VARIANT_SIZES[variant][1], 4)}})
    align = "left" if corner.endswith("left") else "right"
    x, w = mx, (0.62 if variant != "landscape" else 0.5)
    if align == "right":
        x = 1 - mx - w
    blocks = _text_blocks(plan, content, plan.knobs.get("head", 0.06))
    top = 0.5 if variant != "story" else 0.55
    texts = stack(blocks, content, x, top, w, 1 - my - _room(plan, content, variant), variant, align, palette=plan.palette)
    end = max((t["box"]["y"] + t["box"]["h"] for t in texts), default=top)
    anchor = x if align == "left" else x + w
    return elements + texts + _cta(plan, content, anchor, end + 0.015, variant, align)


def _mirror(elements: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Left-right mirror of a composition (boxes, giant words, alignment); positional motifs are not mirrored."""

    flipped = []
    for element in elements:
        element = dict(element)
        if "box" in element:
            box = dict(element["box"])
            box["x"] = round(1 - box["x"] - box["w"], 4)
            element["box"] = box
        if element.get("kind") == "big_type" and "x" in element:
            element["x"] = round(1 - element["x"], 4)
        if element.get("align") in ("left", "right"):
            element["align"] = "right" if element["align"] == "left" else "left"
        flipped.append(element)
    return flipped


GEOMETRY: dict[str, Callable[[Plan, Mapping[str, str], list[str], str], list[dict[str, Any]]]] = {
    "split": a_split, "full_bleed": a_full_bleed, "sandwich": a_sandwich, "type_poster": a_type_poster, "frame_card": a_frame_card,
    "diagonal": a_diagonal, "circle_focus": a_circle_focus, "editorial_column": a_editorial_column, "sticker_pop": a_sticker_pop,
    "center_stack": a_center_stack, "grid": a_grid, "magazine_cover": a_magazine_cover, "corner_minimal": a_corner_minimal,
}


def _knobs(archetype: str, rng: random.Random) -> dict[str, Any]:
    knobs: dict[str, Any] = {
        "margin": rng.choice((0.05, 0.06, 0.07, 0.08)), "align": rng.choice(("left", "left", "center", "right")),
        "upper": rng.random() < 0.4, "decor": rng.choice(DECOR), "decor_dir": rng.choice((30, 45, 60, 90, 135)),
        "pill": rng.choice((0.0, 0.012, 0.035)),
    }
    if archetype == "split":
        knobs.update(ratio=rng.choice((0.45, 0.5, 0.55, 0.6)), photo_first=rng.random() < 0.6, block=rng.random() < 0.35,
                     head=rng.choice((0.065, 0.075, 0.085)))
    elif archetype == "full_bleed":
        knobs.update(band_top=rng.random() < 0.3, band=rng.choice((0.4, 0.46, 0.52)), sat=rng.choice((1.0, 0.85, 0.0)),
                     head=rng.choice((0.07, 0.08, 0.095)))
    elif archetype == "sandwich":
        knobs.update(word_font=rng.choice(("builtin:display", "builtin:archivo", "builtin:dm_serif", "builtin:bebas", "builtin:kanit")),
                     word_outline=rng.random() < 0.25, echo=rng.random() < 0.35, blur=rng.choice((0.0, 0.0, 0.012)),
                     word_opacity=rng.choice((0.9, 0.75, 1.0)), flip=rng.random() < 0.5, head=rng.choice((0.055, 0.065)), align="left")
    elif archetype == "type_poster":
        knobs.update(shape=rng.choice(("circle", "block", "none")), cx=rng.choice((0.7, 0.78, 0.3)), slab=rng.choice((0.5, 0.58)),
                     tilt=rng.choice((-8, 6, 10)), rule=rng.random() < 0.6, head=rng.choice((0.12, 0.14, 0.16)))
    elif archetype == "frame_card":
        knobs.update(radius=rng.choice((0.0, 0.02, 0.04)), photo_share=rng.choice((0.36, 0.42, 0.46)), head=rng.choice((0.065, 0.075)),
                     decor=rng.choice(("dots", "grain", "stripes", "halftone")))
    elif archetype == "diagonal":
        knobs.update(rising=rng.random() < 0.6, tilt=rng.choice((0, -4, 4)), head=rng.choice((0.07, 0.08)))
    elif archetype == "circle_focus":
        knobs.update(flip=rng.random() < 0.5, sat=rng.choice((1.0, 0.0)), head=rng.choice((0.065, 0.075)))
    elif archetype == "editorial_column":
        knobs.update(filter=rng.choice(("none", "grayscale", "none")), head=rng.choice((0.065, 0.075)), upper=rng.random() < 0.6)
    elif archetype == "sticker_pop":
        knobs.update(tilt=rng.choice((-8, -5, 5, 8)), rays=rng.choice((20, 28, 36)), head=rng.choice((0.08, 0.09)))
    elif archetype == "grid":
        knobs.update(cols=rng.choice((2, 3, 4)), rows=rng.choice((3, 4, 5)), gutter=rng.choice((0.0, 0.01, 0.02)),
                     text_side=rng.choice(("top", "bottom", "left", "right")), split=rng.choice(("single", "pair")),
                     rounded=rng.random() < 0.4, panel=rng.random() < 0.7, radius=rng.choice((0.0, 0.02)),
                     filter=rng.choice(("none", "none", "grayscale", "duotone")), head=rng.choice((0.065, 0.08)), flip_tiles=rng.random() < 0.5,
                     tile_pattern=rng.choice(("stripes", "dots")))
    elif archetype == "magazine_cover":
        knobs.update(mast_font=rng.choice(("builtin:dm_serif", "builtin:display", "builtin:archivo", "builtin:serif_thai")),
                     filter=rng.choice(("none", "none", "grayscale")), head=rng.choice((0.06, 0.07)))
    elif archetype == "corner_minimal":
        knobs.update(corner=rng.choice(("bottom_left", "bottom_right")), mask=rng.choice(("rect", "circle", "arch")),
                     head=rng.choice((0.055, 0.065)), decor="none")
    else:
        knobs.update(frame=rng.random() < 0.5, head=rng.choice((0.08, 0.1)))
    knobs["mirror"] = archetype in MIRRORABLE and rng.random() < 0.35
    return knobs


def _merge(per_variant: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    """One element list with per-size boxes (elements must keep their ids across sizes)."""

    merged: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for variant, elements in per_variant.items():
        for element in elements:
            key = element["id"]
            if key not in merged:
                base = {k: v for k, v in element.items() if k != "box"}
                if "box" in element:
                    base["boxes"] = {}
                merged[key] = base
                order.append(key)
            if "box" in element:
                merged[key].setdefault("boxes", {})[variant] = element["box"]
            for prop in ("align", "size"):
                # Sizes may need their own alignment or type size (a centred portrait, a left-aligned landscape).
                if prop in element and element[prop] != merged[key].get(prop):
                    merged[key].setdefault("per_size", {}).setdefault(variant, {})[prop] = element[prop]
    for element in merged.values():
        boxes = element.get("boxes")
        if boxes is not None:
            # Sizes where this element was absent get it hidden off-canvas is not allowed; give them the first box.
            first = next(iter(boxes.values()))
            for variant in per_variant:
                boxes.setdefault(variant, first)
    return [merged[key] for key in order]


def _framed(element: dict[str, Any]) -> dict[str, Any]:
    """A cut-out sticker turned into a framed photo (for photos that do not cut out cleanly)."""

    effects = element.get("effects") or {}
    if element.get("kind") != "photo" or effects.get("cutout") != "ai":
        return element
    kept = {key: effects[key] for key in ("filter", "focus_x", "focus_y") if key in effects}
    return {**element, "effects": {**kept, "cutout": "none", "fit": "cover", "mask": "rounded", "corner_radius": 0.05,
                                   "shadow": 0.35}}


CUTOUT_ARCHETYPES = {"sandwich", "diagonal", "sticker_pop"}  # ideas that only work with a cut-out subject


def _allowed(content: Mapping[str, str], photos: list[str], cutout_ok: bool = True) -> list[str]:
    """Composition ideas this job's material supports."""

    names = [name for name in ARCHETYPES if photos or name not in PHOTO_ARCHETYPES]
    if not cutout_ok:
        names = [name for name in names if name not in CUTOUT_ARCHETYPES]
    # A giant backdrop word needs a short word: the optional "big" text, or a short headline.
    # Thai words are long and lose their shape when cropped, so a Thai headline never becomes the backdrop word.
    headline = str(content.get("headline", ""))
    thai = any("\u0e00" <= char <= "\u0e7f" for char in headline)
    if not str(content.get("big", "")).strip() and (thai or _visible(headline) > 10):
        names = [name for name in names if name != "sandwich"]
    return names


STORY_SAFE_TOP, STORY_SAFE_BOTTOM = 0.105, 0.855


def _story_safe(elements: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Slide the text group (with its buttons and badges) out of the story app's UI bands when it fits between them."""

    texts = [e for e in elements if e.get("kind") == "text" and "box" in e]
    if not texts:
        return elements
    top = min(e["box"]["y"] for e in texts)
    bottom = max(e["box"]["y"] + e["box"]["h"] for e in texts)
    if top >= STORY_SAFE_TOP and bottom <= STORY_SAFE_BOTTOM:
        return elements
    if bottom - top > STORY_SAFE_BOTTOM - STORY_SAFE_TOP:
        return elements  # too tall to fit; the checker rejects it and another candidate is tried
    shift = STORY_SAFE_TOP - top if top < STORY_SAFE_TOP else STORY_SAFE_BOTTOM - bottom

    def carries_text(shape: dict[str, Any]) -> bool:
        box = shape["box"]
        if box["w"] * box["h"] > 0.25:
            return False  # a big panel or photo stays; only buttons, badges and labels travel with the text
        for text in texts:
            t = text["box"]
            overlap_w = min(box["x"] + box["w"], t["x"] + t["w"]) - max(box["x"], t["x"])
            overlap_h = min(box["y"] + box["h"], t["y"] + t["h"]) - max(box["y"], t["y"])
            if overlap_w > 0 and overlap_h > 0 and overlap_w * overlap_h >= 0.5 * t["w"] * t["h"]:
                return True
        return False

    moved = []
    for element in elements:
        if "box" in element and (element in texts or (element.get("kind") == "shape" and carries_text(element))):
            element = {**element, "box": {**element["box"], "y": round(element["box"]["y"] + shift, 4)}}
        moved.append(element)
    return moved


LOGO = "logo"
LOGO_CORNERS = ("top-left", "top-right", "bottom-left", "bottom-right")
LOGO_HEIGHT = 0.07  # of the short side
LOGO_MAX_WIDTH = 0.28  # of the short side
LOGO_CLEAR = 0.4  # clear space around the logo, as a share of its height


def logo_box(variant: str, corner: str, aspect: float) -> dict[str, float]:
    """Canvas-fraction box of the logo in ``corner``, sized to the logo's own aspect ratio, inside the safe margins."""

    width, height = VARIANT_SIZES[variant]
    unit = min(width, height)
    box_h = unit * LOGO_HEIGHT
    box_w = box_h * max(aspect, 0.2)
    if box_w > unit * LOGO_MAX_WIDTH:
        box_w = unit * LOGO_MAX_WIDTH
        box_h = box_w / max(aspect, 0.2)
    margin_x = unit * 0.05
    margin_y = height * STORY_SAFE_TOP if height >= width * 1.7 else unit * 0.05
    x = margin_x if corner.endswith("left") else width - margin_x - box_w
    y = margin_y if corner.startswith("top") else height - margin_y - box_h
    return {"x": round(x / width, 4), "y": round(y / height, 4), "w": round(box_w / width, 4), "h": round(box_h / height, 4)}


def _clear_zone(box: Mapping[str, float], variant: str) -> tuple[float, float, float, float]:
    width, height = VARIANT_SIZES[variant]
    pad = box["h"] * height * LOGO_CLEAR
    return (box["x"] - pad / width, box["y"] - pad / height, box["x"] + box["w"] + pad / width, box["y"] + box["h"] + pad / height)


def _hits(zone: tuple[float, float, float, float], element: Mapping[str, Any]) -> bool:
    box = element.get("box")
    if not box:
        return False
    return not (box["x"] + box["w"] <= zone[0] or box["x"] >= zone[2] or box["y"] + box["h"] <= zone[1] or box["y"] >= zone[3])


LOGO_MIN_CONTRAST = 2.2  # logo colour vs. what it sits on; below this it gets a plate


def _faint_share(tones: LogoTones | None, backdrop: str) -> float:
    """Share of the logo's pixels whose colour would barely stand out from ``backdrop``."""

    return sum(share for colour, share in (tones or ()) if contrast(colour, backdrop) < LOGO_MIN_CONTRAST)


def _needs_plate(logo_box: Mapping[str, float], variant: str, tones: LogoTones | None, backdrop: str | None,
                 elements: list[dict[str, Any]]) -> bool:
    """True when a quarter or more of the logo would vanish into the background colour, or it sits on a photo."""

    zone = _clear_zone(logo_box, variant)
    on_photo = any(e.get("kind") == "photo" and e.get("asset") != LOGO and _hits(zone, e) for e in elements)
    return on_photo or bool(tones and backdrop and _faint_share(tones, backdrop) >= 0.25)


def _plate(logo_box: Mapping[str, float], variant: str, tones: LogoTones | None) -> dict[str, Any]:
    """A small rounded plate (white, or near-black for a light logo) just around the logo."""

    fill = "#FFFFFF" if _faint_share(tones, "#FFFFFF") <= _faint_share(tones, "#151515") else "#151515"
    width, height = VARIANT_SIZES[variant]
    pad = logo_box["h"] * height * 0.28
    box = {"x": round(logo_box["x"] - pad / width, 4), "y": round(logo_box["y"] - pad / height, 4),
           "w": round(logo_box["w"] + 2 * pad / width, 4), "h": round(logo_box["h"] + 2 * pad / height, 4)}
    return {"id": "logo-plate", "kind": "shape", "shape": "rect", "z": 15, "fill": fill, "radius": 0.012, "box": box}


def place_logo(elements: list[dict[str, Any]], variant: str, preferred: str, aspect: float, locked: bool) -> dict[str, Any]:
    """The logo element for one size: the preferred corner, or (unless the brand locks it) the first corner clear of text."""

    corners = [preferred] if locked else [preferred, *[corner for corner in LOGO_CORNERS if corner != preferred]]
    texts = [e for e in elements if e.get("kind") == "text" or (e.get("kind") == "shape" and str(e.get("id", "")).startswith("cta"))]
    chosen = preferred
    for corner in corners:
        zone = _clear_zone(logo_box(variant, corner, aspect), variant)
        if not any(_hits(zone, element) for element in texts):
            chosen = corner
            break
    return {"id": "logo", "kind": "photo", "asset": LOGO, "z": 16, "box": logo_box(variant, chosen, aspect),
            "effects": {"fit": "contain", "cutout": "none"}}


def plan_layout(content: Mapping[str, str], photos: list[str], variants: list[str], brand: Mapping[str, str], seed: int,
                archetype: str | None = None, style_colors: Mapping[str, str] | None = None,
                logo_aspect: float | None = None, logo_position: str | None = None,
                logo_tone: LogoTones | None = None, cutout_ok: bool = True) -> dict[str, Any]:
    rng = random.Random(seed)
    options = _allowed(content, photos, cutout_ok)
    chosen = archetype or rng.choice(options)
    if chosen not in options:
        chosen = rng.choice(options)
    scheme, palette = _scheme(rng, str(brand.get("primary", "#14213D")).upper(), str(brand.get("accent", "#FCA311")).upper())
    if not palette:
        scheme, palette = _scheme(random.Random(seed + 1), "#14213D", "#FCA311")
    if style_colors:
        # An art style recolors every role at render time, so readable-color choices are made against its colors.
        scheme, palette = "style", {**palette, **{key: str(value).upper() for key, value in style_colors.items()}}
    headline = str(content.get("headline", "") or content.get("big", ""))
    thai = any("฀" <= char <= "๿" for char in headline)
    fonts = rng.choice(PAIRS_THAI if thai else PAIRS_LATIN + PAIRS_THAI[:3])
    plan = Plan(archetype=chosen, seed=seed, scheme=scheme, palette=palette, fonts=fonts, knobs=_knobs(chosen, rng))
    plan.knobs["logo_corner"] = random.Random(seed * 31 + 7).choice(LOGO_CORNERS)
    per_variant = {}
    for variant in variants:
        elements = GEOMETRY[chosen](plan, content, photos, variant)
        if plan.knobs.get("mirror"):
            elements = _mirror(elements)
        if not cutout_ok:
            elements = [_framed(e) for e in elements]
        if variant == "story":
            elements = _story_safe(elements)
        footer = _footer(plan, content, variant)
        if logo_aspect:
            locked = logo_position in LOGO_CORNERS
            preferred = str(logo_position) if locked else plan.knobs["logo_corner"]
            logo = place_logo(elements, variant, preferred, logo_aspect, locked)
            zone = _clear_zone(logo["box"], variant)
            if footer and _hits(zone, footer[0]):
                # Small print gives way to the logo: it moves to the other bottom corner.
                box = footer[0]["box"]
                left = logo["box"]["x"] > 0.5
                footer = [{**footer[0], "align": "left" if left else "right",
                           "box": {**box, "x": round(1 - box["x"] - box["w"], 4) if left else box["x"]}}]
            elements = elements + footer + [logo]
        else:
            elements = elements + footer
        per_variant[variant] = elements
    if logo_aspect:
        # One decision for the whole design, so every size looks alike: a plate everywhere or nowhere.
        def logo_of(items: list[dict[str, Any]]) -> dict[str, Any]:
            return next(e for e in items if e.get("asset") == LOGO)

        if any(_needs_plate(logo_of(items)["box"], variant, logo_tone, palette.get("bg"), items)
               for variant, items in per_variant.items()):
            for variant, items in per_variant.items():
                items.insert(len(items) - 1, _plate(logo_of(items)["box"], variant, logo_tone))
    spec = {
        "schema_version": LAYOUT_SCHEMA, "archetype": chosen,
        "concept": f"{chosen.replace('_', ' ')}{' (mirrored)' if plan.knobs.get('mirror') else ''} · {scheme} · {fonts[0].split(':')[1]}",
        "palette": palette, "background": {"color": "role.bg"}, "elements": _merge(per_variant),
    }
    spec["layout_id"] = layout_id(spec)
    return spec


def add_logo(spec: dict[str, Any], variants: list[str], aspect: float, position: str | None = None,
             tone: LogoTones | None = None) -> dict[str, Any]:
    """``spec`` with the job's logo placed per size (any logo element the spec already had is replaced)."""

    elements = [e for e in spec.get("elements", []) if not (e.get("kind") == "photo" and e.get("asset") == LOGO)
                and e.get("id") != "logo-plate"]
    boxes: dict[str, dict[str, float]] = {}
    need_plate = False
    backdrop = str((spec.get("palette") or {}).get("bg", "")) or None
    if backdrop and not backdrop.startswith("#"):
        backdrop = None
    preferred = position if position in LOGO_CORNERS else "top-right"
    for variant in variants:
        view = []
        for element in elements:
            box = (element.get("boxes") or {}).get(variant) or element.get("box")
            if box:
                view.append({**element, "box": box})
        placed = place_logo(view, variant, preferred, aspect, position in LOGO_CORNERS)
        boxes[variant] = placed["box"]
        need_plate = need_plate or _needs_plate(placed["box"], variant, tone, backdrop, view)
    logo = {"id": "logo", "kind": "photo", "asset": LOGO, "z": 16, "boxes": boxes, "effects": {"fit": "contain", "cutout": "none"}}
    extra: list[dict[str, Any]] = []
    if need_plate:
        plates = {variant: _plate(boxes[variant], variant, tone) for variant in variants}
        first = next(iter(plates.values()))
        extra.append({**{k: v for k, v in first.items() if k != "box"}, "boxes": {v: p["box"] for v, p in plates.items()}})
    return {**spec, "elements": [*elements, *extra, logo]}


LogoTones = tuple[tuple[str, float], ...]


def logo_tones(base: Mapping[str, Any]) -> LogoTones | None:
    """The logo's main colours (``#RRGGBB``) with their share of its visible pixels, largest first."""

    name = (base.get("assets") or {}).get(LOGO)
    if not name:
        return None
    from PIL import Image

    try:
        with Image.open(Path(str(base.get("asset_root", "."))) / str(name)) as image:
            rgba = image.convert("RGBA").resize((96, 96))
    except (OSError, ValueError):
        return None
    pixels: list[tuple[int, int, int]] = []
    for pixel in rgba.get_flattened_data():
        if isinstance(pixel, tuple) and len(pixel) == 4 and pixel[3] > 128:
            pixels.append((int(pixel[0]), int(pixel[1]), int(pixel[2])))
    if not pixels:
        return None
    swatch = Image.new("RGB", (len(pixels), 1))
    swatch.putdata(pixels)
    quantized = swatch.quantize(colors=4)
    palette: list[int] = list(quantized.getpalette() or [])
    tones: list[tuple[str, float]] = []
    colors: list[tuple[int, int]] = [(int(n), int(i)) for n, i in (quantized.getcolors() or []) if isinstance(i, int)]
    for count, index in sorted(colors, reverse=True):
        r, g, b = palette[index * 3:index * 3 + 3]
        tones.append((f"#{r:02X}{g:02X}{b:02X}", round(count / len(pixels), 3)))
    return tuple(tones)


def logo_aspect(base: Mapping[str, Any]) -> float | None:
    """Width / height of the job's logo file, or ``None`` when the job has no logo (or it cannot be read)."""

    name = (base.get("assets") or {}).get(LOGO)
    if not name:
        return None
    from PIL import Image

    try:
        with Image.open(Path(str(base.get("asset_root", "."))) / str(name)) as image:
            box = image.getchannel("A").getbbox() if "A" in image.getbands() else None
            width, height = (box[2] - box[0], box[3] - box[1]) if box else image.size
    except (OSError, ValueError):
        return None
    return round(width / max(height, 1), 3)


@dataclass
class ComposeResult:
    specs: list[dict[str, Any]]
    fingerprints: list[dict[str, list[str]]]
    attempts: int
    rejected: dict[str, int]
    scores: list[float] = field(default_factory=list)
    score_details: list[dict[str, Any]] = field(default_factory=list)


def _mean_score(scores: Mapping[str, Any]) -> float:
    values = [float(value["total"]) for value in scores.values()]
    return sum(values) / len(values) if values else 0.0


def compose(base: Mapping[str, Any], variants: list[str], *, count: int = 3, seed: int | None = None, max_attempts: int = 120,
            avoid_history: bool = True, archetypes: list[str] | None = None, pool_factor: int = 3,
            min_score: float = 60.0, logo_position: str | None = None) -> ComposeResult:
    """The ``count`` best-scoring checked layouts that differ from each other and from the library's memory.

    Candidates are generated until a pool of ``count * pool_factor`` usable layouts exists (or attempts run
    out); each is scored for design quality (:mod:`.aesthetics`, mean over sizes) and the best are picked
    greedily so no two options nearly copy each other.
    """

    from .layouts import assess_layout

    content = {key: str(value) for key, value in (base.get("content") or {}).items() if str(value).strip()}
    photos = [key for key in sorted(base.get("assets") or {}) if key != LOGO]  # the logo is never a hero photo
    aspect = logo_aspect(base)
    tone = logo_tones(base) if aspect else None
    cutout_ok = True
    if photos and base.get("asset_root"):
        from .image_fx import cutout_suitability

        cutout_ok, _why = cutout_suitability(Path(str(base["asset_root"])) / str(base["assets"][photos[0]]))
    start = seed if seed is not None else int(hashlib.sha256(repr(sorted(content.items())).encode()).hexdigest()[:8], 16)
    history = recent_fingerprints() if avoid_history else []
    style_colors = None
    style_id = (base.get("design") or {}).get("art_style")
    if style_id:
        from .art_styles import ArtStyleError, get_art_style

        try:
            style_colors = get_art_style(str(style_id)).colors
        except ArtStyleError:
            style_colors = None  # an unknown style is reported by the render step
    from .review import idea_penalties

    penalties = idea_penalties()  # ideas people keep rejecting rank lower
    pool: list[tuple[float, dict[str, Any], dict[str, list[str]], dict[str, Any]]] = []
    rejected: dict[str, int] = {}
    tried: dict[str, int] = {}
    attempts = 0
    target = max(count, count * pool_factor)
    while len(pool) < target and attempts < max_attempts:
        attempts += 1
        rng = random.Random(start + attempts * 7919)
        names = archetypes or _allowed(content, photos, cutout_ok)
        least = min(tried.get(name, 0) for name in names)
        archetype = rng.choice([name for name in names if tried.get(name, 0) == least])  # spread ideas evenly
        tried[archetype] = tried.get(archetype, 0) + 1
        spec = plan_layout(content, photos, variants, base.get("brand") or {}, start + attempts, archetype, style_colors, aspect, logo_position, tone, cutout_ok)
        _theme, fp, problems, scores = assess_layout(spec, base, variants)
        if problems:
            key = problems[0].split(":")[0]
            rejected[key] = rejected.get(key, 0) + 1
            continue
        if too_similar(fp, history):
            rejected["similar_to_history"] = rejected.get("similar_to_history", 0) + 1
            continue
        score = _mean_score(scores)
        if score < min_score:
            rejected["low_design_score"] = rejected.get("low_design_score", 0) + 1
            continue
        pool.append((score, spec, fp, scores))
    chosen: list[tuple[float, dict[str, Any], dict[str, list[str]], dict[str, Any]]] = []
    ranked = sorted(pool, key=lambda item: -(item[0] - penalties.get(str(item[1]["archetype"]), 0.0)))
    # First pass: the best option of each composition idea; second pass: fill up with the next best.
    for unique_ideas in (True, False):
        for candidate in ranked:
            if len(chosen) >= count:
                break
            if any(candidate is item for item in chosen):
                continue
            if unique_ideas and candidate[1]["archetype"] in {item[1]["archetype"] for item in chosen}:
                continue
            if any(all(similarity(candidate[2][v], other[2][v]) >= 0.8 for v in candidate[2] if v in other[2]) for other in chosen):
                rejected["similar_to_option"] = rejected.get("similar_to_option", 0) + 1
                continue
            chosen.append(candidate)
    return ComposeResult(
        specs=[item[1] for item in chosen], fingerprints=[item[2] for item in chosen], attempts=attempts, rejected=rejected,
        scores=[round(item[0], 1) for item in chosen], score_details=[item[3] for item in chosen],
    )


__all__ = ["ARCHETYPES", "ComposeResult", "compose", "plan_layout"]
