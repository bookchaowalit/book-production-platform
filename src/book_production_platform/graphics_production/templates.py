"""Fixed, model-free Graphics V1A template compiler."""

from __future__ import annotations

import re
from typing import Any

from book_production_platform.graphics_ir import (
    GraphicIR,
    GraphicRequest,
    ImageNode,
    ShapeNode,
    TextNode,
    validate_request,
)

from .assets import AssetReferenceError, validate_local_reference
from .layout import LayoutError, fit_text

TEMPLATE_VERSION = "2"
VARIANT_SIZES: dict[str, tuple[int, int]] = {
    "square": (1080, 1080),
    "portrait": (1080, 1350),
    "story": (1080, 1920),
}


class TemplateError(ValueError):
    """Raised when a request cannot enter a fixed template recipe."""


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


def _required(content: dict[str, str], key: str) -> str:
    value = content.get(key, "").strip()
    if not value:
        raise TemplateError(f"content_required:{key}")
    return value


def _text(
    node_id: str,
    value: str,
    *,
    x: float,
    y: float,
    width: float,
    height: float,
    font_size: float,
    min_font_size: float,
    max_lines: int,
    color: str,
    font_ref: str,
    align: str = "left",
    overflow_policy: str = "reject",
) -> TextNode:
    layout = fit_text(
        value,
        width=width,
        height=height,
        font_size=font_size,
        min_font_size=min_font_size,
        max_lines=max_lines,
        overflow_policy=overflow_policy,
    )
    return TextNode(
        node_id=node_id,
        x=x,
        y=y,
        width=width,
        height=height,
        text=value,
        font_size=layout.font_size,
        font_ref=font_ref,
        color=color,
        align=align,  # type: ignore[arg-type]
        overflow_policy=overflow_policy,  # type: ignore[arg-type]
        max_lines=max_lines,
    )


def _background(request: GraphicRequest) -> list[ShapeNode]:
    return [
        ShapeNode(
            node_id="background",
            x=0,
            y=0,
            width=request.canvas.width,
            height=request.canvas.height,
            fill=request.brand.background,
        ),
        ShapeNode(
            node_id="accent-bar",
            x=0,
            y=0,
            width=request.canvas.width,
            height=26,
            fill=request.brand.accent,
        ),
    ]


def _product_image(request: GraphicRequest) -> ImageNode | None:
    reference = request.assets.get("product_image")
    if reference is None:
        return None
    if request.asset_root is None:
        raise TemplateError("missing_asset_root")
    try:
        path = validate_local_reference(reference, request.asset_root, field="assets.product_image")
    except AssetReferenceError as exc:
        raise TemplateError(exc.code) from exc
    if path.suffix.lower() == ".svg":
        text = path.read_text(encoding="utf-8", errors="replace").lower()
        unsafe_reference = re.search(
            r"(?:href|xlink:href|src)\s*=\s*[\"']\s*(?:https?:|file:|//)"
            r"|url\(\s*(?:https?:|file:|//)",
            text,
        )
        if re.search(r"<script\b|\son[a-z]+\s*=", text) or unsafe_reference:
            raise TemplateError("unsafe_svg")
    return ImageNode(
        node_id="product-image",
        x=72,
        y=150,
        width=936,
        height=470,
        asset_key="product_image",
        source_ref=str(path),
    )


def compile_template(request: GraphicRequest | dict[str, Any]) -> GraphicIR:
    """Compile a validated request into a deterministic GraphicIR."""

    parsed = _request(request)
    nodes: list[ShapeNode | TextNode | ImageNode] = list(_background(parsed))
    color = parsed.brand.foreground
    content = parsed.content

    if parsed.template_id == "quote_card":
        quote = _required(content, "quote")
        author = _required(content, "author")
        nodes.extend([
            _text("quote", quote, x=80, y=170, width=920, height=610, font_size=72, min_font_size=28, max_lines=20, color=color, font_ref=parsed.font_ref, align="center", overflow_policy="reject"),
            _text("author", f"— {author}", x=120, y=850, width=840, height=110, font_size=34, min_font_size=22, max_lines=3, color=parsed.brand.accent, font_ref=parsed.font_ref, align="center"),
        ])
    elif parsed.template_id == "product_card":
        title = _required(content, "title")
        price = _required(content, "price")
        image = _product_image(parsed)
        if image is not None:
            nodes.append(image)
        nodes.extend([
            _text("product-title", title, x=72, y=670 if image else 260, width=936, height=160, font_size=62, min_font_size=30, max_lines=4, color=color, font_ref=parsed.font_ref, align="center"),
            _text("product-price", price, x=72, y=850 if image else 470, width=936, height=140, font_size=72, min_font_size=32, max_lines=3, color=parsed.brand.accent, font_ref=parsed.font_ref, align="center"),
        ])
    else:
        headline = _required(content, "headline")
        body = _required(content, "body")
        cta = _required(content, "cta")
        # The call to action sits a fixed margin above the bottom edge of every
        # variant; the body fills the space between headline and button.
        cta_y = parsed.canvas.height - 72 - 108
        headline_height = 260 if parsed.variant == "square" else 300
        body_y = 110 + headline_height + 40
        nodes.extend([
            _text("headline", headline, x=72, y=110, width=936, height=headline_height, font_size=80, min_font_size=32, max_lines=8, color=color, font_ref=parsed.font_ref, align="left", overflow_policy="reject"),
            _text("body", body, x=72, y=body_y, width=936, height=cta_y - 40 - body_y, font_size=42, min_font_size=24, max_lines=12, color=color, font_ref=parsed.font_ref, align="left", overflow_policy="reject"),
            ShapeNode(node_id="cta-bg", x=72, y=cta_y, width=360, height=108, fill=parsed.brand.primary),
            _text("cta", cta, x=92, y=cta_y + 22, width=320, height=64, font_size=32, min_font_size=20, max_lines=2, color=parsed.brand.background, font_ref=parsed.font_ref, align="center"),
        ])

    return GraphicIR(
        request_id=parsed.request_id,
        template_ref=f"{parsed.template_id}@{TEMPLATE_VERSION}",
        variant=parsed.variant,
        canvas=parsed.canvas,
        nodes=nodes,
        source_refs=[f"request:{parsed.request_id}"],
        assumptions={"model_calls": "0", "renderer": "graphics-svg-browser-v2"},
    )


__all__ = ["VARIANT_SIZES", "LayoutError", "TemplateError", "compile_template"]
