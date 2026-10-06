"""Model-free Graphics template compiler.

Every template is a data-defined theme (``graphics_production/themes/*.json``)
compiled by :mod:`.themes` from reusable :mod:`.motifs`. The three original
templates (quote_card, product_card, announcement) are themes too and compile
to the same ``graphic-ir.v1`` they always did.
"""

from __future__ import annotations

import re
from typing import Any

from book_production_platform.graphics_ir import (
    GraphicIR,
    GraphicRequest,
    ImageNode,
    validate_request,
)

from .assets import AssetReferenceError, validate_local_reference
from .layout import LayoutError
from .themes import (
    VARIANT_SIZES,
    ThemeError,
    compile_theme,
    design_controls,
    get_theme,
    load_themes,
)

# Version of the original V1A templates; data themes carry their own version.
TEMPLATE_VERSION = "2"
_UNSAFE_SVG_REFERENCE = re.compile(
    r"(?:href|xlink:href|src)\s*=\s*[\"']\s*(?:https?:|file:|//)"
    r"|url\(\s*(?:https?:|file:|//)",
)


class TemplateError(ValueError):
    """Raised when a request cannot enter a template recipe."""


def _request(request: GraphicRequest | dict[str, Any]) -> GraphicRequest:
    parsed = request if isinstance(request, GraphicRequest) else GraphicRequest.model_validate(request)
    issues = validate_request(parsed)
    if issues:
        raise TemplateError(issues[0].code)
    expected = VARIANT_SIZES[parsed.variant]
    if (parsed.canvas.width, parsed.canvas.height) != expected:
        raise TemplateError("canvas_dimensions_unsupported")
    if parsed.font_ref != "builtin:sans":
        if parsed.asset_root is None:
            raise TemplateError("missing_font")
        try:
            validate_local_reference(parsed.font_ref, parsed.asset_root, field="font_ref")
        except AssetReferenceError as exc:
            raise TemplateError("missing_font") from exc
    return parsed


def _image_loader(request: GraphicRequest) -> Any:
    def load(spec: dict[str, Any]) -> ImageNode | None:
        key = spec["asset"]
        reference = request.assets.get(key)
        if reference is None:
            return None
        if request.asset_root is None:
            raise TemplateError("missing_asset_root")
        try:
            path = validate_local_reference(reference, request.asset_root, field=f"assets.{key}")
        except AssetReferenceError as exc:
            raise TemplateError(exc.code) from exc
        if path.suffix.lower() == ".svg":
            text = path.read_text(encoding="utf-8", errors="replace").lower()
            if re.search(r"<script\b|\son[a-z]+\s*=", text) or _UNSAFE_SVG_REFERENCE.search(text):
                raise TemplateError("unsafe_svg")
        return ImageNode(
            node_id=spec["node_id"],
            x=spec["x"],
            y=spec["y"],
            width=spec["width"],
            height=spec["height"],
            asset_key=key,
            source_ref=str(path),
        )

    return load


def compile_template(request: GraphicRequest | dict[str, Any]) -> GraphicIR:
    """Compile a validated request into a deterministic GraphicIR."""

    parsed = _request(request)
    try:
        theme = get_theme(parsed.template_id)
    except ThemeError as exc:
        raise TemplateError(str(exc)) from None
    if parsed.variant not in theme.variants:
        raise TemplateError("variant_unsupported")
    for slot, spec in theme.slots.items():
        if spec.get("required", True) and not parsed.content.get(slot, "").strip():
            raise TemplateError(f"content_required:{slot}")
    for asset, spec in theme.assets.items():
        if spec.get("required", False) and asset not in parsed.assets:
            raise TemplateError(f"asset_required:{asset}")
    try:
        return compile_theme(theme, parsed, product_image=_image_loader(parsed))
    except LayoutError:
        raise
    except ThemeError as exc:
        raise TemplateError(str(exc)) from None


def controls_for(request: GraphicRequest | dict[str, Any]) -> dict[str, Any]:
    """The design controls (adjustable layers, items, palette, styles) of a request's theme."""

    parsed = _request(request)
    try:
        theme = get_theme(parsed.template_id)
        return design_controls(theme, parsed, product_image=_image_loader(parsed))
    except ThemeError as exc:
        raise TemplateError(str(exc)) from None


def template_ids() -> tuple[str, ...]:
    return tuple(load_themes())


__all__ = ["TEMPLATE_VERSION", "VARIANT_SIZES", "LayoutError", "TemplateError", "compile_template", "controls_for", "template_ids"]
