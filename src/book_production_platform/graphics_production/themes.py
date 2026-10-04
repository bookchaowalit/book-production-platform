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
    LinearGradient,
    RadialGradient,
    ShapeNode,
    TextNode,
)

THEME_SCHEMA = "graphic-theme.v1"
THEMES_DIR = Path(__file__).with_name("themes")
VARIANT_SIZES: dict[str, tuple[int, int]] = {
    "square": (1080, 1080),
    "portrait": (1080, 1350),
    "story": (1080, 1920),
    "landscape": (1920, 1080),
}
BUILTIN_FONTS = frozenset({"builtin:sans", "builtin:display", "builtin:serif", "builtin:mono", "builtin:thai"})
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
        }


_THEME_KEYS = frozenset({
    "schema_version", "theme_id", "title", "description", "ir_version", "template_version", "variants", "slots",
    "assets", "evidence", "palette", "paints", "vars", "layers", "renderer",
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
        source=source,
    )


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
    paints: list[LinearGradient | RadialGradient] = field(default_factory=list)
    _paint_ids: set[str] = field(default_factory=set)

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
            if key in seen or key not in self.theme.palette:
                raise ThemeError(f"palette_unknown:{key}")
            seen.add(key)
            resolved = self.theme.palette[key]
        if resolved.startswith("brand."):
            token = resolved[len("brand."):]
            if token not in ("primary", "accent", "foreground", "background"):
                raise ThemeError(f"brand_token_unknown:{token}")
            return str(getattr(self.request.brand, token))
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


def compile_theme(
    theme: Theme,
    request: GraphicRequest,
    *,
    product_image: Callable[[dict[str, Any]], ImageNode | None],
) -> GraphicIR:
    from .motifs import MOTIFS  # local import: motifs import Context from here

    if request.variant not in theme.variants:
        raise ThemeError("variant_unsupported")
    width, height = float(request.canvas.width), float(request.canvas.height)
    names: dict[str, float] = {
        "W": width, "H": height, "CX": width / 2, "CY": height / 2, "MIN": min(width, height), "MAX": max(width, height),
        "PI": math.pi,
    }
    ctx = Context(theme=theme, request=request, names=names, product_image=product_image)
    for name, value in theme.variables.items():
        if name in names:
            raise ThemeError(f"var_shadows_builtin:{name}")
        names[name] = ctx.num(value, name=name)
    for paint in theme.paints:
        ctx.add_paint(paint)

    layers: list[GroupNode] = []
    flat: list[TextNode | ShapeNode | ImageNode] = []
    for layer in theme.layers:
        if not _condition_holds(layer.get("when"), ctx):
            continue
        children: list[Any] = []
        for item in layer["items"]:
            if not isinstance(item, Mapping) or not isinstance(item.get("motif"), str):
                raise ThemeError(f"item_invalid:{layer['id']}")
            if not _condition_holds(item.get("when"), ctx):
                continue
            motif = MOTIFS.get(item["motif"])
            if motif is None:
                raise ThemeError(f"motif_unknown:{item['motif']}")
            children.extend(motif(ctx, item))
        if not children:
            continue
        if theme.ir_version == "graphic-ir.v1":
            flat.extend(children)
        else:
            clip = [node for node in (MOTIFS[c["motif"]](ctx, c) for c in layer.get("clip", [])) for node in node]
            layers.append(GroupNode(
                node_id=str(layer["id"]),
                label=str(layer.get("label", layer["id"])),
                opacity=ctx.num(layer.get("opacity", 1), name="opacity"),
                clip=[node for node in clip if isinstance(node, ShapeNode)],
                children=children,
            ))
    return GraphicIR(
        schema_version=theme.ir_version,  # type: ignore[arg-type]
        request_id=request.request_id,
        template_ref=f"{theme.theme_id}@{theme.template_version}",
        variant=request.variant,
        canvas=request.canvas,
        nodes=flat if theme.ir_version == "graphic-ir.v1" else layers,  # type: ignore[arg-type]
        paints=ctx.paints,
        source_refs=[f"request:{request.request_id}"],
        assumptions={"model_calls": "0", "renderer": theme.renderer},
    )


__all__ = [
    "BUILTIN_FONTS",
    "THEMES_DIR",
    "VARIANT_SIZES",
    "Context",
    "Theme",
    "ThemeError",
    "ThemeSlotMissing",
    "compile_theme",
    "evaluate",
    "get_theme",
    "load_themes",
    "parse_theme",
]
