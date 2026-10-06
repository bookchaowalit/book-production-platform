"""Data-defined graphic themes: load, validate, and compile to GraphicIR.

A theme is a JSON file in ``graphics_production/themes/`` (``graphic-theme.v1``).
It names the text slots and assets a request fills, a palette, layout
variables, and ordered layers of motif items. Layout values are numbers or
safe arithmetic expressions over the canvas (``W``, ``H``, ``CX``, ``CY``,
``MIN``, ``MAX``) and the theme's own variables, so one theme serves every
canvas variant. Adding a look means adding a theme file, not code; adding a new
kind of drawing means adding one motif to :mod:`.motifs`.

Compilation is deterministic: random motifs draw from a generator seeded by
the theme, the request id, and the item id.
"""

from __future__ import annotations

import ast
import hashlib
import json
import math
import operator
import random
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

from book_production_platform.graphics_ir import (
    GradientStop,
    GraphicIR,
    GraphicRequest,
    GroupNode,
    ImageNode,
    ImagePlacement,
    LinearGradient,
    RadialGradient,
    ResolvedImageEffects,
    ShapeNode,
    TextNode,
    iter_nodes,
)

from .art_styles import (
    ART_FONTS,
    ROLES,
    ArtStyle,
    ArtStyleError,
    get_art_style,
    load_art_styles,
)

THEME_SCHEMA = "graphic-theme.v1"
THEMES_DIR = Path(__file__).with_name("themes")
VARIANT_SIZES: dict[str, tuple[int, int]] = {
    "square": (1080, 1080),
    "portrait": (1080, 1350),
    "story": (1080, 1920),
    "landscape": (1920, 1080),
}
BUILTIN_FONTS = frozenset(ART_FONTS)
DEFAULT_CHAR_WIDTH = 0.55
MAX_EXPRESSION_CHARS = 400


class ThemeError(ValueError):
    """A theme file or a theme item is invalid; the message is a stable code."""


# --- safe layout expressions ------------------------------------------------------------------

_BINARY: dict[type[ast.operator], Callable[[float, float], float]] = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}
_FUNCTIONS: dict[str, Callable[..., float]] = {
    "min": min,
    "max": max,
    "round": round,
    "abs": abs,
    "sqrt": math.sqrt,
    "sin": lambda degrees: math.sin(math.radians(degrees)),
    "cos": lambda degrees: math.cos(math.radians(degrees)),
}


def evaluate(expression: str, names: Mapping[str, float]) -> float:
    """Evaluate arithmetic over numbers and known names; anything else is rejected."""

    if len(expression) > MAX_EXPRESSION_CHARS:
        raise ThemeError("expression_too_long")
    try:
        tree = ast.parse(expression, mode="eval")
    except SyntaxError as exc:
        raise ThemeError(f"expression_invalid:{expression}") from exc

    def walk(node: ast.AST) -> float:
        if isinstance(node, ast.Expression):
            return walk(node.body)
        if isinstance(node, ast.Constant) and isinstance(node.value, int | float) and not isinstance(node.value, bool):
            return float(node.value)
        if isinstance(node, ast.Name):
            if node.id not in names:
                raise ThemeError(f"expression_unknown_name:{node.id}")
            return float(names[node.id])
        if isinstance(node, ast.BinOp) and type(node.op) in _BINARY:
            left, right = walk(node.left), walk(node.right)
            if isinstance(node.op, ast.Pow) and abs(right) > 8:
                raise ThemeError("expression_power_too_large")
            return _BINARY[type(node.op)](left, right)
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub | ast.UAdd):
            value = walk(node.operand)
            return -value if isinstance(node.op, ast.USub) else value
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id in _FUNCTIONS
            and not node.keywords
            and 1 <= len(node.args) <= 4
        ):
            return float(_FUNCTIONS[node.func.id](*(walk(arg) for arg in node.args)))
        raise ThemeError(f"expression_not_allowed:{expression}")

    try:
        result = walk(tree)
    except (ZeroDivisionError, OverflowError, ValueError) as exc:
        if isinstance(exc, ThemeError):
            raise
        raise ThemeError(f"expression_failed:{expression}") from exc
    if not math.isfinite(result):
        raise ThemeError(f"expression_failed:{expression}")
    return result


# --- theme files ------------------------------------------------------------------------------


@dataclass(frozen=True)
class Theme:
    theme_id: str
    title: str
    description: str
    ir_version: str
    template_version: str
    variants: tuple[str, ...]
    slots: dict[str, dict[str, Any]]
    assets: dict[str, dict[str, Any]]
    evidence: tuple[str, ...]
    palette: dict[str, str]
    paints: tuple[dict[str, Any], ...]
    variables: dict[str, Any]
    layers: tuple[dict[str, Any], ...]
    renderer: str
    styles: dict[str, dict[str, Any]] = field(default_factory=dict)
    roles: dict[str, str] = field(default_factory=dict)
    source: str = ""

    @property
    def slot_names(self) -> tuple[str, ...]:
        return tuple(self.slots)

    def summary(self) -> dict[str, Any]:
        return {
            "theme_id": self.theme_id,
            "title": self.title,
            "description": self.description,
            "variants": list(self.variants),
            "slots": {name: dict(spec) for name, spec in self.slots.items()},
            "assets": {name: dict(spec) for name, spec in self.assets.items()},
            "evidence": list(self.evidence),
            "styles": {name: str(spec.get("title", name)) for name, spec in self.styles.items()},
        }


_THEME_KEYS = frozenset({
    "schema_version", "theme_id", "title", "description", "ir_version", "template_version", "variants", "slots",
    "assets", "evidence", "palette", "paints", "vars", "layers", "renderer", "styles", "roles",
})


def parse_theme(raw: Mapping[str, Any], *, source: str = "") -> Theme:
    """Validate the theme's structure (motif parameters are checked when compiled)."""

    if not isinstance(raw, Mapping) or raw.get("schema_version") != THEME_SCHEMA:
        raise ThemeError("theme_schema_invalid")
    unknown = set(raw) - _THEME_KEYS
    if unknown:
        raise ThemeError(f"theme_unknown_keys:{','.join(sorted(unknown))}")
    theme_id = raw.get("theme_id")
    if not isinstance(theme_id, str) or not theme_id.replace("_", "").isalnum() or not theme_id[:1].isalpha():
        raise ThemeError("theme_id_invalid")
    variants = tuple(raw.get("variants") or ())
    if not variants or any(variant not in VARIANT_SIZES for variant in variants):
        raise ThemeError(f"theme_variants_invalid:{theme_id}")
    ir_version = raw.get("ir_version", "graphic-ir.v2")
    if ir_version not in ("graphic-ir.v1", "graphic-ir.v2"):
        raise ThemeError(f"theme_ir_version_invalid:{theme_id}")
    slots = raw.get("slots") or {}
    if not isinstance(slots, Mapping) or not slots or not all(isinstance(spec, Mapping) for spec in slots.values()):
        raise ThemeError(f"theme_slots_invalid:{theme_id}")
    layers = raw.get("layers") or []
    if not isinstance(layers, list) or not layers:
        raise ThemeError(f"theme_layers_invalid:{theme_id}")
    for layer in layers:
        if not isinstance(layer, Mapping) or not isinstance(layer.get("id"), str) or not isinstance(layer.get("items"), list):
            raise ThemeError(f"theme_layer_invalid:{theme_id}")
    palette = raw.get("palette") or {}
    if not isinstance(palette, Mapping) or not all(isinstance(value, str) for value in palette.values()):
        raise ThemeError(f"theme_palette_invalid:{theme_id}")
    roles = raw.get("roles") or {}
    if not isinstance(roles, Mapping) or not set(roles) <= set(palette) or not set(roles.values()) <= set(ROLES):
        raise ThemeError(f"theme_roles_invalid:{theme_id}")
    return Theme(
        theme_id=theme_id,
        title=str(raw.get("title", theme_id)),
        description=str(raw.get("description", "")),
        ir_version=ir_version,
        template_version=str(raw.get("template_version", "1")),
        variants=variants,
        slots={str(name): dict(spec) for name, spec in slots.items()},
        assets={str(name): dict(spec) for name, spec in (raw.get("assets") or {}).items()},
        evidence=tuple(str(word) for word in raw.get("evidence") or ()),
        palette={str(name): value for name, value in palette.items()},
        paints=tuple(dict(paint) for paint in raw.get("paints") or ()),
        variables=dict(raw.get("vars") or {}),
        layers=tuple(dict(layer) for layer in layers),
        renderer=str(raw.get("renderer", "graphics-svg-v2")),
        styles=_parse_styles(raw.get("styles") or {}, palette, theme_id),
        roles={str(key): str(role) for key, role in roles.items()},
        source=source,
    )


def _parse_styles(raw: Any, palette: Mapping[str, str], theme_id: str) -> dict[str, dict[str, Any]]:
    """Style presets: named palette swaps (``{"title": ..., "palette": {name: color}}``)."""

    if not isinstance(raw, Mapping):
        raise ThemeError(f"theme_styles_invalid:{theme_id}")
    styles: dict[str, dict[str, Any]] = {}
    for name, spec in raw.items():
        colors = spec.get("palette") if isinstance(spec, Mapping) else None
        if not isinstance(colors, Mapping) or not set(colors) <= set(palette):
            raise ThemeError(f"theme_style_invalid:{theme_id}:{name}")
        styles[str(name)] = {"title": str(spec.get("title", name)), "palette": dict(colors)}
    return styles


@lru_cache(maxsize=1)
def load_themes(directory: str | None = None) -> dict[str, Theme]:
    """All registered themes keyed by id (cached; pass a directory only in tests)."""

    root = Path(directory) if directory else THEMES_DIR
    themes: dict[str, Theme] = {}
    for path in sorted(root.glob("*.json")):
        theme = parse_theme(json.loads(path.read_text(encoding="utf-8")), source=path.name)
        if theme.theme_id in themes:
            raise ThemeError(f"theme_duplicate:{theme.theme_id}")
        themes[theme.theme_id] = theme
    return themes


def get_theme(theme_id: str) -> Theme:
    theme = load_themes().get(theme_id)
    if theme is None and theme_id.startswith("lay_"):
        from .layouts import (
            find_layout_theme,  # composed per-job layouts (layout library or candidate)
        )

        theme = find_layout_theme(theme_id)
    if theme is None:
        raise ThemeError("template_unknown")
    return theme


# --- compile context --------------------------------------------------------------------------


@dataclass
class Context:
    theme: Theme
    request: GraphicRequest
    names: dict[str, float]
    product_image: Callable[[dict[str, Any]], ImageNode | None]
    palette: dict[str, str] = field(default_factory=dict)
    paints: list[LinearGradient | RadialGradient] = field(default_factory=list)
    art: ArtStyle | None = None
    role_colors: dict[str, str] = field(default_factory=dict)
    brand_colors: dict[str, str] = field(default_factory=dict)
    assumptions: dict[str, str] = field(default_factory=dict)
    # Text boxes (x0, y0, x1, y1) that scattered art-style decoration keeps clear of; empty while theme layers compile.
    avoid_boxes: list[tuple[float, float, float, float]] = field(default_factory=list)
    _paint_ids: set[str] = field(default_factory=set)

    def clear_of_text(self, x: float, y: float, radius: float) -> bool:
        """True when a decoration centred at (x, y) with ``radius`` touches no avoided text box."""

        return not any(x0 - radius <= x <= x1 + radius and y0 - radius <= y <= y1 + radius for x0, y0, x1, y1 in self.avoid_boxes)

    @property
    def width(self) -> float:
        return float(self.request.canvas.width)

    @property
    def height(self) -> float:
        return float(self.request.canvas.height)

    def num(self, value: Any, *, name: str = "value") -> float:
        resolved = self._choose(value)
        if isinstance(resolved, bool):
            raise ThemeError(f"number_invalid:{name}")
        if isinstance(resolved, int | float):
            return float(resolved)
        if isinstance(resolved, str):
            return evaluate(resolved, self.names)
        raise ThemeError(f"number_invalid:{name}")

    def opt_num(self, spec: Mapping[str, Any], key: str, default: float) -> float:
        return self.num(spec[key], name=key) if key in spec else default

    def choose(self, value: Any) -> Any:
        """A literal value, or the entry for this request's size from a ``by_variant`` table."""

        return self._choose(value)

    def _choose(self, value: Any) -> Any:
        """Resolve ``by_variant`` / ``if_asset`` / ``if_slot`` selectors."""

        while isinstance(value, Mapping):
            if "by_variant" in value:
                table = value["by_variant"]
                value = table.get(self.request.variant, table.get("default"))
                if value is None:
                    raise ThemeError("by_variant_missing_default")
            elif "if_asset" in value:
                value = value["then"] if value["if_asset"] in self.request.assets else value["else"]
            elif "if_slot" in value:
                value = value["then"] if self.request.content.get(value["if_slot"], "").strip() else value["else"]
            else:
                raise ThemeError("selector_invalid")
        return value

    def color(self, value: Any, *, name: str = "color") -> str:
        resolved = self._choose(value)
        if not isinstance(resolved, str):
            raise ThemeError(f"color_invalid:{name}")
        seen: set[str] = set()
        while resolved.startswith("palette."):
            key = resolved[len("palette."):]
            if key in seen or key not in self.palette:
                raise ThemeError(f"palette_unknown:{key}")
            seen.add(key)
            resolved = self.palette[key]
        if resolved.startswith("brand."):
            token = resolved[len("brand."):]
            if token not in ("primary", "accent", "foreground", "background"):
                raise ThemeError(f"brand_token_unknown:{token}")
            return self.brand_colors.get(token) or str(getattr(self.request.brand, token))
        if resolved.startswith("role."):
            role = resolved[len("role."):]
            if role not in self.role_colors:
                raise ThemeError(f"role_unknown:{role}")
            return self.role_colors[role]
        if resolved.startswith("paint:"):
            paint_id = resolved[len("paint:"):]
            if paint_id not in self._paint_ids:
                raise ThemeError(f"paint_unknown:{paint_id}")
            return resolved
        if resolved == "none":
            return resolved
        if len(resolved) == 7 and resolved.startswith("#"):
            return resolved.upper()
        raise ThemeError(f"color_invalid:{name}")

    def solid(self, value: Any, *, name: str = "color") -> str:
        resolved = self.color(value, name=name)
        if not resolved.startswith("#"):
            raise ThemeError(f"solid_color_required:{name}")
        return resolved

    def add_paint(self, spec: Mapping[str, Any]) -> str:
        paint_id = str(spec.get("paint_id", ""))
        if paint_id in self._paint_ids:
            return f"paint:{paint_id}"
        stops = [
            GradientStop(
                offset=self.num(stop.get("offset", 0), name="offset"),
                color=self.solid(stop["color"], name="stop"),
                opacity=self.num(stop.get("opacity", 1), name="opacity"),
            )
            for stop in spec.get("stops", [])
        ]
        kind = spec.get("type", "linear")
        if kind == "linear":
            angle = spec.get("angle")
            if angle is not None:
                radians = math.radians(self.num(angle, name="angle"))
                dx, dy = math.cos(radians) / 2, math.sin(radians) / 2
                coords = {"x1": 0.5 - dx, "y1": 0.5 - dy, "x2": 0.5 + dx, "y2": 0.5 + dy}
            else:
                coords = {key: self.num(spec.get(key, default), name=key) for key, default in (("x1", 0), ("y1", 0), ("x2", 0), ("y2", 1))}
            paint: LinearGradient | RadialGradient = LinearGradient(
                paint_id=paint_id, stops=stops, x1=coords["x1"], y1=coords["y1"], x2=coords["x2"], y2=coords["y2"]
            )
        elif kind == "radial":
            paint = RadialGradient(
                paint_id=paint_id,
                stops=stops,
                cx=self.num(spec.get("cx", 0.5), name="cx"),
                cy=self.num(spec.get("cy", 0.5), name="cy"),
                r=self.num(spec.get("r", 0.5), name="r"),
            )
        else:
            raise ThemeError(f"paint_type_invalid:{kind}")
        self.paints.append(paint)
        self._paint_ids.add(paint_id)
        return f"paint:{paint_id}"

    def text(self, spec: Mapping[str, Any]) -> str | None:
        """The item's text: a slot value (``None`` when an optional slot is empty) or literal text."""

        if "slot" in spec:
            slot = str(spec["slot"])
            if slot not in self.theme.slots:
                raise ThemeError(f"slot_unknown:{slot}")
            value = self.request.content.get(slot, "").strip()
            if not value:
                if self.theme.slots[slot].get("required", True):
                    raise ThemeSlotMissing(slot)
                return None
        elif "text" in spec:
            value = str(spec["text"])
        else:
            raise ThemeError("text_source_missing")
        template = spec.get("format")
        return str(template).replace("{value}", value) if template else value

    def rng(self, item_id: str, salt: Any = 0) -> random.Random:
        seed = f"{self.theme.theme_id}|{self.request.request_id}|{item_id}|{salt}"
        return random.Random(int.from_bytes(hashlib.sha256(seed.encode("utf-8")).digest()[:8], "big"))

    def font(self, spec: Mapping[str, Any]) -> str:
        font = str(spec.get("font", "request"))
        if font == "request":
            return self.request.font_ref
        if font not in BUILTIN_FONTS:
            raise ThemeError(f"font_unknown:{font}")
        return font

    def text_role(self, spec: Mapping[str, Any], font_size: float) -> str:
        """``display`` (titles), ``body``, or ``label`` (mono captions, dates, tags) for art styling."""

        if spec.get("text_role") in ("display", "body", "label"):
            return str(spec["text_role"])
        if str(spec.get("font", "")) == "builtin:mono":
            return "label"
        if spec.get("motif") == "display_text" or font_size >= self.names["MIN"] * 0.07:
            return "display"
        return "body"

    def text_style(self, spec: Mapping[str, Any], font_size: float) -> dict[str, Any]:
        """Font, weight, case, letter spacing (em), and title shadow for a text item.

        Without an art style (or with a custom font file) this is exactly the theme's own settings.
        """

        style: dict[str, Any] = {
            "role": None, "font": self.font(spec), "weight": spec.get("weight", "normal"), "upper": False,
            "spacing_em": None, "shadow": None,
        }
        if self.art is None or style["font"] not in BUILTIN_FONTS:
            return style
        role = self.text_role(spec, font_size)
        text = self.art.text
        style["role"] = role
        style["font"] = self.art.fonts[role]
        if role == "display":
            style["weight"] = text.get("display_weight", style["weight"])
            style["upper"] = text.get("display_case") == "upper"
            style["spacing_em"] = float(text.get("display_letter_spacing", 0))
            style["shadow"] = text.get("display_shadow")
        elif role == "body":
            style["weight"] = text.get("body_weight", "bold" if style["weight"] == "black" else style["weight"])
        else:
            style["upper"] = text.get("label_case") == "upper"
            style["weight"] = "bold" if style["weight"] == "black" else style["weight"]
        return style


class ThemeSlotMissing(ThemeError):
    def __init__(self, slot: str) -> None:
        super().__init__(f"content_required:{slot}")
        self.slot = slot


def _condition_holds(when: Any, ctx: Context) -> bool:
    if when is None:
        return True
    if not isinstance(when, Mapping):
        raise ThemeError("when_invalid")
    for key, value in when.items():
        values = value if isinstance(value, list) else [value]
        if key == "asset" and not all(item in ctx.request.assets for item in values):
            return False
        if key == "no_asset" and any(item in ctx.request.assets for item in values):
            return False
        if key == "slot" and not all(ctx.request.content.get(item, "").strip() for item in values):
            return False
        if key == "no_slot" and any(ctx.request.content.get(item, "").strip() for item in values):
            return False
        if key == "variant" and ctx.request.variant not in values:
            return False
        if key not in ("asset", "no_asset", "slot", "no_slot", "variant"):
            raise ThemeError(f"when_unknown:{key}")
    return True


# Parameters a design override (and the AI design editor) may change, by kind.
EDITABLE_NUMBERS = frozenset({
    "x", "y", "width", "height", "font_size", "min_font_size", "max_lines", "letter_spacing", "opacity", "stroke_width",
    "stroke_opacity", "rx", "r", "ry", "cx", "cy", "x1", "y1", "x2", "y2", "count", "sparkles", "sparkle_size", "horizon",
    "bottom", "vanish_x", "spread", "near_spread", "verticals", "horizontals", "power", "amplitude", "wavelength", "step",
    "spacing", "max_radius", "min_radius", "direction", "angle", "band", "gap", "inset", "length", "base", "min_height",
    "max_height", "peaks", "strength", "size", "rotation", "inner", "outer", "radius", "slat_start", "cut_y", "wobble",
    "border_width", "border_opacity", "blur", "motion_blur", "outline_width", "span", "bend", "tip", "max_font_size",
    "streaks", "fade_amount",
})
EDITABLE_COLORS = frozenset({
    "color", "fill", "stroke", "horizon_color", "horizontal_color", "vertical_color", "floor", "border", "accent_color",
})
EDITABLE_CHOICES: dict[str, frozenset[str | bool]] = {
    "weight": frozenset({"normal", "bold", "black"}),
    "align": frozenset({"left", "center", "right"}),
    "font": frozenset(BUILTIN_FONTS | {"request"}),
    "outline": frozenset({True, False}),
    "upper": frozenset({True, False}),
}


def _context(theme: Theme, request: GraphicRequest, product_image: Callable[[dict[str, Any]], ImageNode | None]) -> Context:
    if request.variant not in theme.variants:
        raise ThemeError("variant_unsupported")
    width, height = float(request.canvas.width), float(request.canvas.height)
    names: dict[str, float] = {
        "W": width, "H": height, "CX": width / 2, "CY": height / 2, "MIN": min(width, height), "MAX": max(width, height),
        "PI": math.pi,
    }
    palette = dict(theme.palette)
    design = request.design
    if design is not None:
        if design.style is not None:
            if design.style not in theme.styles:
                raise ThemeError(f"design_style_unknown:{design.style}")
            palette.update(theme.styles[design.style]["palette"])
        for key, color in design.palette.items():
            if key not in theme.palette:
                raise ThemeError(f"design_palette_unknown:{key}")
            palette[key] = color
        layer_ids = {str(layer["id"]) for layer in theme.layers}
        item_keys = {f"{layer['id']}/{item.get('id')}" for layer in theme.layers for item in layer["items"]}
        for key in design.layers:
            if key not in layer_ids:
                raise ThemeError(f"design_layer_unknown:{key}")
        for key in design.items:
            if key not in item_keys:
                raise ThemeError(f"design_item_unknown:{key}")
    # Role colors: from the art style, else derived from the brand (used by photo effects such as duotone).
    role_colors = {
        "bg": request.brand.background, "bg2": request.brand.background, "surface": request.brand.background,
        "ink": request.brand.foreground, "accent": request.brand.primary, "accent2": request.brand.accent,
        "highlight": request.brand.accent, "on_accent": request.brand.background, "muted": request.brand.foreground,
    }
    art: ArtStyle | None = None
    brand_colors: dict[str, str] = {}
    if design is not None and design.art_style is not None:
        try:
            art = get_art_style(design.art_style)
        except ArtStyleError as exc:
            raise ThemeError(str(exc)) from None
        role_colors = dict(art.colors)
        if design.keep_brand_colors:
            role_colors["accent"], role_colors["accent2"] = request.brand.primary, request.brand.accent
        for key, role in theme.roles.items():
            palette[key] = role_colors[role]
        for key, color in design.palette.items():  # explicit palette edits still win
            palette[key] = color
        brand_colors = {
            "primary": role_colors["accent"], "accent": role_colors["accent2"],
            "foreground": role_colors["ink"], "background": role_colors["bg"],
        }
    ctx = Context(theme=theme, request=request, names=names, product_image=product_image, palette=palette,
                  art=art, role_colors=role_colors, brand_colors=brand_colors)
    for name, value in theme.variables.items():
        if name in names:
            raise ThemeError(f"var_shadows_builtin:{name}")
        names[name] = ctx.num(value, name=name)
    for paint in theme.paints:
        ctx.add_paint(paint)
    return ctx


def _apply_item_override(ctx: Context, item: Mapping[str, Any], params: Mapping[str, Any], target: str) -> dict[str, Any]:
    """The item with overridden parameters; numbers may use ``V`` (the current value)."""

    merged = dict(item)
    for key, value in params.items():
        if key in EDITABLE_NUMBERS:
            if isinstance(value, bool):
                raise ThemeError(f"design_value_invalid:{target}.{key}")
            current = ctx.num(item[key], name=key) if key in item else 0.0
            number = evaluate(value, {**ctx.names, "V": current}) if isinstance(value, str) else float(value)
            if key in ("opacity", "stroke_opacity", "border_opacity", "strength") and not 0 <= number <= 1:
                raise ThemeError(f"design_value_out_of_range:{target}.{key}")
            if key in ("width", "height", "font_size", "min_font_size", "r", "rx", "ry", "size", "radius", "spacing") and number < 0:
                raise ThemeError(f"design_value_out_of_range:{target}.{key}")
            if abs(number) > 20_000:
                raise ThemeError(f"design_value_out_of_range:{target}.{key}")
            merged[key] = number
        elif key in EDITABLE_COLORS:
            if not isinstance(value, str):
                raise ThemeError(f"design_value_invalid:{target}.{key}")
            ctx.color(value, name=key)  # validates hex / palette / brand / paint
            merged[key] = value
        elif key in EDITABLE_CHOICES:
            if value not in EDITABLE_CHOICES[key]:
                raise ThemeError(f"design_value_invalid:{target}.{key}")
            merged[key] = value
        else:
            raise ThemeError(f"design_param_not_editable:{target}.{key}")
    return merged


def resolve_effects(ctx: Context, placement_effects: Any | None) -> ResolvedImageEffects:
    """Concrete photo effects: the art style's photo treatment under ``filter=style``, role colors resolved.

    Fields the placement sets explicitly win over the art style's defaults.
    ``cutout=ai`` falls back to ``plain`` (recorded in the IR assumptions) when
    the optional rembg model is not installed.
    """

    from .image_fx import (
        ai_cutout_available,  # local import: Pillow work stays out of plain theme compiles
    )

    explicit = placement_effects.model_dump(exclude_unset=True) if placement_effects is not None else {}
    values: dict[str, Any] = {}
    if explicit.get("filter", "style") == "style":
        values.update(ctx.art.photo if ctx.art is not None else {})
        values.setdefault("filter", "none")
        explicit.pop("filter", None)
    values.update(explicit)
    if values.get("filter") == "duotone":
        values.setdefault("duotone_dark", "role.bg" if ctx.art is not None else "role.ink")
        values.setdefault("duotone_light", "role.highlight" if ctx.art is not None else "role.bg")
    for key in ("duotone_dark", "duotone_light", "outline_color", "glow_color"):
        if values.get(key) is not None:
            values[key] = ctx.solid(values[key], name=key)
    if values.get("cutout") == "ai" and not ai_cutout_available():
        values["cutout"] = "plain"
        ctx.assumptions["cutout"] = "ai_unavailable_used_plain"
    return ResolvedImageEffects.model_validate(values)


def _placement_nodes(ctx: Context, placement: ImagePlacement) -> list[Any]:
    if placement.asset not in ctx.request.assets:
        raise ThemeError(f"design_image_asset_unknown:{placement.asset}")
    width = placement.width * ctx.width
    height = width if placement.square else placement.height * ctx.height
    x, y = placement.x * ctx.width, placement.y * ctx.height
    # Boxes may hang off the canvas (collage bleed); the node box is clamped like every other node.
    left, top = max(0.0, x), max(0.0, y)
    node = ctx.product_image({
        "node_id": f"photo-{placement.id}", "asset": placement.asset, "x": round(left, 2), "y": round(top, 2),
        "width": round(max(1.0, width - (left - x)), 2), "height": round(max(1.0, height - (top - y)), 2),
    })
    if node is None:
        return []
    # A box hanging off the top/left edge is clamped like every node; the photo is cropped to the visible part.
    effects = resolve_effects(ctx, placement.effects)
    return [node.model_copy(update={"rotation": placement.rotation, "opacity": placement.opacity, "effects": effects})]


def _art_items(ctx: Context, items: tuple[dict[str, Any], ...], prefix: str) -> list[Any]:
    from .motifs import MOTIFS

    nodes: list[Any] = []
    for item in items:
        if not _condition_holds(item.get("when"), ctx):
            continue
        nodes.extend(MOTIFS[item["motif"]](ctx, {**item, "id": f"{prefix}-{item['id']}"}))
    return nodes


def compile_theme(
    theme: Theme,
    request: GraphicRequest,
    *,
    product_image: Callable[[dict[str, Any]], ImageNode | None],
) -> GraphicIR:
    from .motifs import MOTIFS  # local import: motifs import Context from here

    ctx = _context(theme, request, product_image)
    design = request.design
    art = ctx.art
    # A design override always produces layered output so moved or hidden layers stay editable.
    ir_version = "graphic-ir.v2" if design is not None else theme.ir_version

    layers: list[GroupNode] = []
    flat: list[TextNode | ShapeNode | ImageNode] = []
    text_layer_index: int | None = None
    for layer in theme.layers:
        if not _condition_holds(layer.get("when"), ctx):
            continue
        is_decor = bool(layer.get("decor", False))
        if art is not None and is_decor and art.hide_decor:
            continue
        layer_id = str(layer["id"])
        layer_override = design.layers.get(layer_id) if design else None
        if layer_override is not None and not layer_override.visible:
            continue
        children: list[Any] = []
        for item in layer["items"]:
            if not isinstance(item, Mapping) or not isinstance(item.get("motif"), str):
                raise ThemeError(f"item_invalid:{layer_id}")
            if not _condition_holds(item.get("when"), ctx):
                continue
            target = f"{layer_id}/{item.get('id')}"
            item_override = design.items.get(target) if design else None
            if item_override is not None:
                if not item_override.visible:
                    continue
                item = _apply_item_override(ctx, item, item_override.params, target)
            motif = MOTIFS.get(item["motif"])
            if motif is None:
                raise ThemeError(f"motif_unknown:{item['motif']}")
            if art is not None and item.get("decor") and not is_decor:
                # A decorative item inside a structural layer follows the art style like a decor layer.
                if art.hide_decor:
                    continue
                children.extend(
                    node.model_copy(update={"opacity": round(node.opacity * art.decor_opacity, 3)}) if isinstance(node, ShapeNode) else node
                    for node in motif(ctx, item)
                )
                continue
            children.extend(motif(ctx, item))
        if not children:
            continue
        if ir_version == "graphic-ir.v1":
            flat.extend(children)
        else:
            if text_layer_index is None and any(isinstance(child, TextNode) for child in children):
                text_layer_index = len(layers)
            clip = [node for node in (MOTIFS[c["motif"]](ctx, c) for c in layer.get("clip", [])) for node in node]
            opacity = ctx.num(layer.get("opacity", 1), name="opacity")
            if art is not None and is_decor:
                opacity *= art.decor_opacity
            if layer_override is not None and layer_override.opacity is not None:
                opacity = layer_override.opacity
            layers.append(GroupNode(
                node_id=layer_id,
                label=str(layer.get("label", layer_id)),
                opacity=opacity,
                offset_x=layer_override.dx if layer_override else 0,
                offset_y=layer_override.dy if layer_override else 0,
                clip=[node for node in clip if isinstance(node, ShapeNode)],
                children=children,
            ))

    if ir_version == "graphic-ir.v2" and design is not None:
        pad = ctx.names["MIN"] * 0.012
        ctx.avoid_boxes = [
            (node.x + group.offset_x - pad, node.y + group.offset_y - pad,
             node.x + group.offset_x + node.width + pad, node.y + group.offset_y + node.height + pad)
            for group in layers for node in iter_nodes(list(group.children)) if isinstance(node, TextNode)
        ]
        # Extra layers, bottom to top: theme background, art backdrop, background photos, ...,
        # photos behind text, text layers, ..., front photos, art overlay.
        first_above_background = 1 if layers else 0
        inserts: list[tuple[int, GroupNode]] = []
        if art is not None:
            backdrop = _art_items(ctx, art.backdrop, "art-backdrop")
            if backdrop:
                inserts.append((first_above_background, GroupNode(node_id="ArtBackdrop", label=f"Art: {art.title}", children=backdrop)))
        by_z: dict[str, list[Any]] = {"background": [], "behind_text": [], "front": []}
        for placement in design.images:
            by_z[placement.z].extend(_placement_nodes(ctx, placement))
        if by_z["background"]:
            inserts.append((first_above_background, GroupNode(node_id="PhotosBack", label="Photos (back)", children=by_z["background"])))
        if by_z["behind_text"]:
            index = text_layer_index if text_layer_index is not None else len(layers)
            inserts.append((max(index, first_above_background), GroupNode(node_id="Photos", label="Photos", children=by_z["behind_text"])))
        # Highest index first; at equal indexes the later entry goes in first so the listed order is kept.
        for _, index, group in sorted(((order, index, group) for order, (index, group) in enumerate(inserts)),
                                      key=lambda entry: (entry[1], entry[0]), reverse=True):
            layers.insert(index, group)
        if by_z["front"]:
            layers.append(GroupNode(node_id="PhotosFront", label="Photos (front)", children=by_z["front"]))
        if art is not None:
            overlay = _art_items(ctx, art.overlay, "art-overlay")
            if overlay:
                layers.append(GroupNode(node_id="ArtOverlay", label=f"Texture: {art.title}", children=overlay))

    from .box_text import center_text_on_boxes

    if ir_version == "graphic-ir.v1":
        flat = center_text_on_boxes(flat, request.canvas.width, request.canvas.height)
    else:
        layers = center_text_on_boxes(layers, request.canvas.width, request.canvas.height)
    assumptions = {"model_calls": "0", "renderer": theme.renderer}
    if art is not None:
        assumptions["art_style"] = art.style_id
    assumptions.update(ctx.assumptions)
    return GraphicIR(
        schema_version=ir_version,  # type: ignore[arg-type]
        request_id=request.request_id,
        template_ref=f"{theme.theme_id}@{theme.template_version}",
        variant=request.variant,
        canvas=request.canvas,
        nodes=flat if ir_version == "graphic-ir.v1" else layers,  # type: ignore[arg-type]
        paints=ctx.paints,
        source_refs=[f"request:{request.request_id}"],
        assumptions=assumptions,
    )


def design_controls(
    theme: Theme,
    request: GraphicRequest,
    *,
    product_image: Callable[[dict[str, Any]], ImageNode | None],
) -> dict[str, Any]:
    """Everything a design override can change, with current values resolved for this request.

    Used as the menu for people and as the only vocabulary the AI design editor may use.
    """

    ctx = _context(theme, request, product_image)
    design = request.design
    layers: list[dict[str, Any]] = []
    for layer in theme.layers:
        layer_id = str(layer["id"])
        override = design.layers.get(layer_id) if design else None
        items: list[dict[str, Any]] = []
        for item in layer["items"]:
            target = f"{layer_id}/{item.get('id')}"
            item_override = design.items.get(target) if design else None
            current = dict(item)
            if item_override is not None:
                current = _apply_item_override(ctx, item, item_override.params, target)
            params: dict[str, Any] = {}
            for key, value in current.items():
                if key in EDITABLE_NUMBERS:
                    try:
                        params[key] = round(ctx.num(value, name=key), 2)
                    except ThemeError:
                        continue
                elif key in EDITABLE_COLORS and not isinstance(value, Mapping):
                    params[key] = ctx.color(value, name=key)
                elif key in EDITABLE_CHOICES:
                    params[key] = value
            entry: dict[str, Any] = {"target": target, "motif": item["motif"], "params": params,
                                     "visible": item_override.visible if item_override else True}
            if "slot" in item:
                entry["slot"] = item["slot"]
            items.append(entry)
        layers.append({
            "id": layer_id,
            "label": str(layer.get("label", layer_id)),
            "visible": override.visible if override else True,
            "opacity": override.opacity if override and override.opacity is not None else layer.get("opacity", 1),
            "dx": override.dx if override else 0,
            "dy": override.dy if override else 0,
            "items": items,
        })
    return {
        "schema_version": "graphic-design-controls.v1",
        "theme_id": theme.theme_id,
        "variant": request.variant,
        "canvas": {"width": request.canvas.width, "height": request.canvas.height},
        "palette": dict(ctx.palette),
        "styles": {name: spec["title"] for name, spec in theme.styles.items()},
        "style": design.style if design else None,
        "art_styles": {style_id: style.title for style_id, style in load_art_styles().items()},
        "art_style": design.art_style if design else None,
        "keep_brand_colors": design.keep_brand_colors if design else False,
        "images": [placement.model_dump(mode="json", exclude_defaults=True) | {"id": placement.id, "asset": placement.asset}
                   for placement in (design.images if design else [])],
        "image_assets": sorted(request.assets),
        "fonts": sorted(BUILTIN_FONTS),
        "slots": {
            name: {"required": bool(spec.get("required", True)), "labels": list(spec.get("labels", [name]))}
            for name, spec in theme.slots.items()
        },
        "layers": layers,
    }


__all__ = [
    "BUILTIN_FONTS",
    "DEFAULT_CHAR_WIDTH",
    "EDITABLE_CHOICES",
    "EDITABLE_COLORS",
    "EDITABLE_NUMBERS",
    "THEMES_DIR",
    "VARIANT_SIZES",
    "Context",
    "Theme",
    "ThemeError",
    "ThemeSlotMissing",
    "compile_theme",
    "design_controls",
    "evaluate",
    "get_theme",
    "load_themes",
    "parse_theme",
    "resolve_effects",
]
