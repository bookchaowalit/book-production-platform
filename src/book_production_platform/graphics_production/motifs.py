"""Reusable, parameterised drawing motifs for data-defined themes.

Each motif turns one theme item (a mapping of parameters) into GraphicIR
nodes. Parameters are numbers or layout expressions (see :mod:`.themes`),
colors are ``#RRGGBB``, ``palette.<name>``, ``brand.<token>``, or
``paint:<id>``. Random motifs are deterministic for a given theme, request id,
and item id. Motifs never read files except ``image`` (through the template's
validated asset loader) and never call a model.

Add a motif by writing one function and registering it in :data:`MOTIFS`;
themes can then use it without other code changes.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Iterable, Mapping
from typing import Any

from book_production_platform.graphics_ir import ImageNode, ShapeNode, TextNode

from .art_styles import ART_FONTS
from .layout import LayoutError, fit_text
from .themes import DEFAULT_CHAR_WIDTH, Context, ThemeError, resolve_effects

Node = TextNode | ShapeNode | ImageNode
Motif = Callable[[Context, Mapping[str, Any]], list[Any]]
MAX_GENERATED_NODES = 2000


def _id(spec: Mapping[str, Any]) -> str:
    value = spec.get("id")
    if not isinstance(value, str) or not value:
        raise ThemeError(f"item_id_required:{spec.get('motif')}")
    return value


def _fmt(value: float) -> str:
    text = f"{value:.2f}".rstrip("0").rstrip(".")
    return "0" if text in ("-0", "") else text


def _path_node(
    ctx: Context,
    node_id: str,
    d: str,
    points: Iterable[tuple[float, float]],
    *,
    fill: str = "none",
    stroke: str | None = None,
    stroke_width: float = 0,
    opacity: float = 1,
    stroke_opacity: float = 1,
) -> ShapeNode | None:
    """A path node whose box is the points' bounds clamped to the canvas (``None`` if fully outside)."""

    xs, ys = zip(*points, strict=True)
    pad = stroke_width / 2
    left = max(0.0, min(xs) - pad)
    top = max(0.0, min(ys) - pad)
    right = min(ctx.width, max(xs) + pad)
    bottom = min(ctx.height, max(ys) + pad)
    if right <= left or bottom <= top:
        if right < left or bottom < top:
            return None
        right, bottom = max(right, left + 1), max(bottom, top + 1)
    return ShapeNode(
        node_id=node_id,
        kind="path",
        d=d,
        x=round(left, 2),
        y=round(top, 2),
        width=max(round(right - left, 2), 1),
        height=max(round(bottom - top, 2), 1),
        fill=fill,
        stroke=stroke,
        stroke_width=stroke_width,
        opacity=opacity,
        stroke_opacity=stroke_opacity,
    )


def _polyline(points: list[tuple[float, float]], *, closed: bool = False) -> str:
    head, *rest = points
    d = f"M{_fmt(head[0])} {_fmt(head[1])}" + "".join(f" L{_fmt(x)} {_fmt(y)}" for x, y in rest)
    return d + (" Z" if closed else "")


def _stroke(ctx: Context, spec: Mapping[str, Any]) -> tuple[str | None, float]:
    stroke = ctx.color(spec["stroke"], name="stroke") if spec.get("stroke") else None
    return stroke, ctx.opt_num(spec, "stroke_width", 0 if stroke is None else 2)


def _paint_param(ctx: Context, spec: Mapping[str, Any], key: str, default: str, item_id: str) -> str:
    """A color, or an inline gradient ``{"type": "linear", "stops": [...]}`` registered for this item."""

    value = spec.get(key, default)
    if isinstance(value, Mapping) and "stops" in value:
        return ctx.add_paint({**value, "paint_id": value.get("paint_id", f"{item_id}-{key}")})
    return ctx.color(value, name=key)


def _count(ctx: Context, spec: Mapping[str, Any], key: str, default: int, *, cap: int = MAX_GENERATED_NODES) -> int:
    value = round(ctx.opt_num(spec, key, default))
    if value < 0 or value > cap:
        raise ThemeError(f"count_out_of_range:{key}")
    return value


# --- primitives ---------------------------------------------------------------------------------


def rect(ctx: Context, spec: Mapping[str, Any]) -> list[Any]:
    item_id = _id(spec)
    stroke, stroke_width = _stroke(ctx, spec)
    x, y = ctx.num(spec.get("x", 0), name="x"), ctx.num(spec.get("y", 0), name="y")
    width = ctx.num(spec.get("width", "W"), name="width")
    height = ctx.num(spec.get("height", "H"), name="height")
    return [ShapeNode(
        node_id=item_id,
        x=max(0.0, x),
        y=max(0.0, y),
        width=width + min(0.0, x),
        height=height + min(0.0, y),
        fill=_paint_param(ctx, spec, "fill", "#FFFFFF", item_id),
        stroke=stroke,
        stroke_width=stroke_width,
        rx=ctx.opt_num(spec, "rx", 0),
        opacity=ctx.opt_num(spec, "opacity", 1),
        stroke_opacity=ctx.opt_num(spec, "stroke_opacity", 1),
    )]


def ellipse(ctx: Context, spec: Mapping[str, Any]) -> list[Any]:
    item_id = _id(spec)
    cx, cy = ctx.num(spec["cx"], name="cx"), ctx.num(spec["cy"], name="cy")
    rx = ctx.num(spec.get("rx", spec.get("r")), name="rx")
    ry = ctx.num(spec.get("ry", spec.get("r")), name="ry")
    stroke, stroke_width = _stroke(ctx, spec)
    d = _ellipse_path(cx, cy, rx, ry)
    node = _path_node(
        ctx, item_id, d, [(cx - rx, cy - ry), (cx + rx, cy + ry)],
        fill=_paint_param(ctx, spec, "fill", "#FFFFFF", item_id), stroke=stroke, stroke_width=stroke_width,
        opacity=ctx.opt_num(spec, "opacity", 1), stroke_opacity=ctx.opt_num(spec, "stroke_opacity", 1),
    )
    return [node] if node else []


def _ellipse_path(cx: float, cy: float, rx: float, ry: float) -> str:
    return (
        f"M{_fmt(cx - rx)} {_fmt(cy)} A{_fmt(rx)} {_fmt(ry)} 0 1 0 {_fmt(cx + rx)} {_fmt(cy)} "
        f"A{_fmt(rx)} {_fmt(ry)} 0 1 0 {_fmt(cx - rx)} {_fmt(cy)} Z"
    )


def line(ctx: Context, spec: Mapping[str, Any]) -> list[Any]:
    item_id = _id(spec)
    points = [(ctx.num(spec["x1"], name="x1"), ctx.num(spec["y1"], name="y1")),
              (ctx.num(spec["x2"], name="x2"), ctx.num(spec["y2"], name="y2"))]
    node = _path_node(
        ctx, item_id, _polyline(points), points,
        stroke=_paint_param(ctx, spec, "stroke", "#111111", item_id),
        stroke_width=ctx.opt_num(spec, "stroke_width", 2), opacity=ctx.opt_num(spec, "opacity", 1),
    )
    return [node] if node else []


def polygon(ctx: Context, spec: Mapping[str, Any]) -> list[Any]:
    """``points``: a list of ``[x, y]`` expressions; ``closed`` (default true)."""

    item_id = _id(spec)
    raw = spec.get("points")
    if not isinstance(raw, list) or len(raw) < 2:
        raise ThemeError(f"points_invalid:{item_id}")
    points = [(ctx.num(x, name="x"), ctx.num(y, name="y")) for x, y in raw]
    stroke, stroke_width = _stroke(ctx, spec)
    closed = bool(spec.get("closed", True))
    node = _path_node(
        ctx, item_id, _polyline(points, closed=closed), points,
        fill=_paint_param(ctx, spec, "fill", "none" if not closed else "#FFFFFF", item_id),
        stroke=stroke, stroke_width=stroke_width, opacity=ctx.opt_num(spec, "opacity", 1),
    )
    return [node] if node else []


def glow(ctx: Context, spec: Mapping[str, Any]) -> list[Any]:
    """A soft radial glow: an ellipse filled with a color fading to transparent."""

    item_id = _id(spec)
    color = ctx.solid(spec.get("color", "#FFFFFF"), name="color")
    strength = ctx.opt_num(spec, "strength", 0.55)
    paint = ctx.add_paint({
        "paint_id": f"{item_id}-glow",
        "type": "radial",
        "stops": [
            {"offset": 0, "color": color, "opacity": strength},
            {"offset": 0.55, "color": color, "opacity": strength * 0.3},
            {"offset": 1, "color": color, "opacity": 0},
        ],
    })
    return ellipse(ctx, {**spec, "fill": paint})


# --- text ---------------------------------------------------------------------------------------


def _char_width(text: str, font: str, spacing_em: float) -> float:
    """Average advance (em) for wrapping: Thai falls back to a Thai face (~0.55 em), Latin uses the font's width."""

    visible = [char for char in text if not char.isspace()]
    thai = sum(1 for char in visible if "\u0e00" <= char <= "\u0e7f") / max(len(visible), 1)
    latin = ART_FONTS.get(font, ("", DEFAULT_CHAR_WIDTH))[1]
    return round(min(max(DEFAULT_CHAR_WIDTH * thai + latin * (1 - thai) + spacing_em, 0.3), 2.0), 3)


def text_block(ctx: Context, spec: Mapping[str, Any]) -> list[Any]:
    """Text fitted into a box (font shrinks to ``min_font_size``; overflow is reported, never cut).

    With an art style the font, weight, case, letter spacing, and title shadow
    come from the style (see :meth:`Context.text_style`).
    """

    node_id = str(spec.get("node_id") or _id(spec))
    value = ctx.text(spec)
    if value is None:
        return []
    width = ctx.num(spec["width"], name="width")
    height = ctx.num(spec["height"], name="height")
    overflow = str(spec.get("overflow", "reject"))
    font_size = ctx.num(spec["font_size"], name="font_size")
    max_lines = int(ctx.opt_num(spec, "max_lines", 8))
    style = ctx.text_style(spec, font_size)
    if style["upper"]:
        value = value.upper()
    styled = style["role"] is not None
    spacing_em = style["spacing_em"] or 0.0
    char_width = _char_width(value, style["font"], spacing_em) if styled else DEFAULT_CHAR_WIDTH
    try:
        layout = fit_text(
            value,
            width=width,
            height=height,
            font_size=font_size,
            min_font_size=ctx.opt_num(spec, "min_font_size", font_size),
            max_lines=max_lines,
            overflow_policy=overflow,
            char_width=char_width,
        )
    except LayoutError as exc:
        exc.node_id = node_id
        raise
    stroke = ctx.color(spec["stroke"], name="stroke") if spec.get("stroke") else None
    letter_spacing = ctx.opt_num(spec, "letter_spacing", 0)
    if styled and style["spacing_em"] is not None:
        letter_spacing = round(spacing_em * layout.font_size, 2)
    node = TextNode(
        node_id=node_id,
        x=ctx.num(spec["x"], name="x"),
        y=ctx.num(spec["y"], name="y"),
        width=width,
        height=height,
        text=value,
        font_size=layout.font_size,
        font_ref=style["font"],
        color=_paint_param(ctx, spec, "color", "#111111", node_id),
        align=spec.get("align", "left"),
        overflow_policy=overflow,  # type: ignore[arg-type]
        max_lines=max_lines,
        font_weight=style["weight"],
        letter_spacing=letter_spacing,
        stroke=stroke,
        stroke_width=ctx.opt_num(spec, "stroke_width", 0 if stroke is None else 1),
        opacity=ctx.opt_num(spec, "opacity", 1),
        char_width=char_width,
    )
    shadow = style["shadow"]
    if not shadow:
        return [node]
    echo = node.model_copy(update={
        "node_id": f"{node_id}-shadow",
        "x": max(0.0, node.x + ctx.num(shadow.get("dx", "MIN * 0.006"), name="dx")),
        "y": max(0.0, node.y + ctx.num(shadow.get("dy", "MIN * 0.006"), name="dy")),
        "color": ctx.solid(shadow.get("color", "role.accent"), name="shadow"),
        "stroke": None,
        "stroke_width": 0,
        "opacity": float(shadow.get("opacity", 1)),
    })
    return [echo, node]


def display_text(ctx: Context, spec: Mapping[str, Any]) -> list[Any]:
    """A title with offset echo copies behind it (``shadows``: list of ``{dx, dy, color, opacity}``).

    Under an art style the style's title shadow replaces the theme's echoes.
    """

    node_id = _id(spec)
    main = text_block(ctx, {**spec, "node_id": node_id})
    if not main or ctx.art is not None:
        return main
    echoes: list[Any] = []
    base = main[0]
    for index, shadow in enumerate(spec.get("shadows", []), start=1):
        dx, dy = ctx.num(shadow.get("dx", 0), name="dx"), ctx.num(shadow.get("dy", 0), name="dy")
        echoes.append(base.model_copy(update={
            "node_id": f"{node_id}-echo-{index}",
            "x": max(0.0, base.x + dx),
            "y": max(0.0, base.y + dy),
            "color": ctx.color(shadow.get("color", "#000000"), name="shadow"),
            "stroke": None,
            "stroke_width": 0,
            "opacity": ctx.num(shadow.get("opacity", 1), name="opacity"),
        }))
    return [*reversed(echoes), *main]


# --- image --------------------------------------------------------------------------------------


def image(ctx: Context, spec: Mapping[str, Any]) -> list[Any]:
    node = ctx.product_image({
        "node_id": str(spec.get("node_id") or _id(spec)),
        "asset": str(spec.get("asset", "product_image")),
        "x": ctx.num(spec["x"], name="x"),
        "y": ctx.num(spec["y"], name="y"),
        "width": ctx.num(spec["width"], name="width"),
        "height": ctx.num(spec["height"], name="height"),
    })
    if node is not None and ctx.art is not None and ctx.art.photo:
        node = node.model_copy(update={"effects": resolve_effects(ctx, None)})
    return [node] if node else []


# --- generative ---------------------------------------------------------------------------------


def starfield(ctx: Context, spec: Mapping[str, Any]) -> list[Any]:
    """Random dots in a region plus a few four-point sparkles."""

    item_id = _id(spec)
    rng = ctx.rng(item_id)
    x0, y0 = ctx.num(spec.get("x", 0), name="x"), ctx.num(spec.get("y", 0), name="y")
    width = ctx.num(spec.get("width", "W"), name="width")
    height = ctx.num(spec.get("height", "H"), name="height")
    color = ctx.solid(spec.get("color", "#FFFFFF"))
    sizes = [ctx.num(size, name="size") for size in spec.get("sizes", [0.8, 1.2, 1.6, 2.2])]
    nodes: list[Any] = []
    parts: list[str] = []
    for _ in range(_count(ctx, spec, "count", 120)):
        cx, cy, r = x0 + rng.random() * width, y0 + rng.random() * height, rng.choice(sizes)
        parts.append(_ellipse_path(cx, cy, r, r))
    if parts:
        # One compound path keeps the layer light in editors; per-star opacity becomes two brightness tiers.
        half = len(parts) // 2
        for tier, (chunk, opacity) in enumerate(((parts[:half], 0.55), (parts[half:], 1.0))):
            if chunk:
                node = _path_node(ctx, f"{item_id}-{tier + 1}", " ".join(chunk), [(x0, y0), (x0 + width, y0 + height)],
                                  fill=color, opacity=opacity * ctx.opt_num(spec, "opacity", 1))
                if node:
                    nodes.append(node)
    sparkle = ctx.opt_num(spec, "sparkle_size", 14)
    for index in range(_count(ctx, spec, "sparkles", 3, cap=50)):
        cx, cy = x0 + rng.random() * width, y0 + rng.random() * height
        for _ in range(30 if ctx.avoid_boxes else 0):  # art-style sparkles stay off the text
            if ctx.clear_of_text(cx, cy, sparkle):
                break
            cx, cy = x0 + rng.random() * width, y0 + rng.random() * height
        else:
            if ctx.avoid_boxes:
                continue
        s, k = sparkle, sparkle * 0.22
        points = [(cx, cy - s), (cx + k, cy - k), (cx + s, cy), (cx + k, cy + k), (cx, cy + s), (cx - k, cy + k), (cx - s, cy), (cx - k, cy - k)]
        node = _path_node(ctx, f"{item_id}-sparkle-{index + 1}", _polyline(points, closed=True), points, fill=color)
        if node:
            nodes.append(node)
    return nodes


def perspective_grid(ctx: Context, spec: Mapping[str, Any]) -> list[Any]:
    """A retro floor grid: lines fanning from a vanishing point plus horizontals that widen toward the viewer."""

    item_id = _id(spec)
    horizon = ctx.num(spec.get("horizon", "H*0.64"), name="horizon")
    bottom = ctx.num(spec.get("bottom", "H"), name="bottom")
    vx = ctx.num(spec.get("vanish_x", "CX"), name="vanish_x")
    spread = ctx.num(spec.get("spread", "W*0.25"), name="spread")
    near_spread = ctx.num(spec.get("near_spread", "W*0.35"), name="near_spread")
    verticals = _count(ctx, spec, "verticals", 14, cap=80)
    horizontals = _count(ctx, spec, "horizontals", 16, cap=80)
    power = ctx.opt_num(spec, "power", 2.1)
    stroke_width = ctx.opt_num(spec, "stroke_width", 2)
    nodes: list[Any] = []
    if spec.get("floor"):
        nodes.extend(rect(ctx, {"id": f"{item_id}-floor", "x": 0, "y": horizon, "width": "W", "height": bottom - horizon,
                               "fill": spec["floor"]}))
    vertical_paint = _paint_param(ctx, spec, "vertical_color", "#22E4FF", item_id)
    segments: list[str] = []
    points: list[tuple[float, float]] = []
    for k in range(-verticals, verticals + 1):
        top_x = vx + k * spread / max(verticals, 1) * 0.35
        bottom_x = vx + k * near_spread / max(verticals, 1) * 4
        segments.append(f"M{_fmt(top_x)} {_fmt(horizon)} L{_fmt(bottom_x)} {_fmt(bottom)}")
        points.extend([(top_x, horizon), (bottom_x, bottom)])
    node = _path_node(ctx, f"{item_id}-verticals", " ".join(segments), points, stroke=vertical_paint, stroke_width=stroke_width)
    if node:
        nodes.append(node)
    color = ctx.color(spec.get("horizontal_color", "#22E4FF"), name="horizontal_color")
    for n in range(1, horizontals + 1):
        t = n / horizontals
        y = horizon + (bottom - horizon) * t ** power
        node = _path_node(ctx, f"{item_id}-h{n}", f"M0 {_fmt(y)} L{_fmt(ctx.width)} {_fmt(y)}", [(0, y), (ctx.width, y)],
                          stroke=color, stroke_width=1 + stroke_width * t, stroke_opacity=round(0.25 + 0.75 * t, 2))
        if node:
            nodes.append(node)
    if spec.get("horizon_color"):
        nodes.extend(line(ctx, {"id": f"{item_id}-horizon", "x1": 0, "y1": horizon, "x2": "W", "y2": horizon,
                                "stroke": spec["horizon_color"], "stroke_width": stroke_width + 1}))
    return nodes


def striped_sun(ctx: Context, spec: Mapping[str, Any]) -> list[Any]:
    """A disc cut into horizontal slats that thicken toward the bottom (built from bands, no masks)."""

    item_id = _id(spec)
    cx, cy, r = ctx.num(spec["cx"], name="cx"), ctx.num(spec["cy"], name="cy"), ctx.num(spec["r"], name="r")
    cut = ctx.num(spec.get("cut_y", cy + r), name="cut_y")
    start = ctx.num(spec.get("slat_start", f"{cy} - {r} * 0.5"), name="slat_start")
    fill = _paint_param(ctx, spec, "fill", "#FFB347", item_id)
    gap, band = ctx.opt_num(spec, "first_gap", 5), ctx.opt_num(spec, "first_band", 24)
    growth, shrink = ctx.opt_num(spec, "gap_growth", 3.4), ctx.opt_num(spec, "band_shrink", 2.2)
    bands: list[tuple[float, float]] = [(cy - r, start - (cy - r))]
    y = start
    while y < min(cy + r, cut) and len(bands) < 60:
        y += gap
        bands.append((y, band))
        y += band
        gap += growth
        band = max(ctx.opt_num(spec, "min_band", 10), band - shrink)
    parts: list[str] = []
    for top, height in bands:
        bottom = min(top + height, cut, cy + r)
        if bottom <= top:
            continue
        parts.append(_disc_band(cx, cy, r, top, bottom))
    node = _path_node(ctx, item_id, " ".join(parts), [(cx - r, cy - r), (cx + r, min(cy + r, cut))], fill=fill,
                      opacity=ctx.opt_num(spec, "opacity", 1))
    return [node] if node else []


def _disc_band(cx: float, cy: float, r: float, top: float, bottom: float) -> str:
    """The part of a disc between two horizontal lines, as a closed path (arcs on both sides)."""

    def half_width(y: float) -> float:
        return math.sqrt(max(r * r - (y - cy) ** 2, 0.0))

    top, bottom = max(top, cy - r), min(bottom, cy + r)
    wt, wb = half_width(top), half_width(bottom)
    # Sample the arcs: robust for bands that straddle the disc's equator.
    steps = max(6, min(48, int((bottom - top) / 4)))
    right = [(cx + half_width(top + (bottom - top) * i / steps), top + (bottom - top) * i / steps) for i in range(steps + 1)]
    left = [(cx - half_width(bottom - (bottom - top) * i / steps), bottom - (bottom - top) * i / steps) for i in range(steps + 1)]
    points = [(cx - wt, top), *right, (cx + wb, bottom), *left]
    return _polyline(points, closed=True)


def ridge(ctx: Context, spec: Mapping[str, Any]) -> list[Any]:
    """A mountain silhouette: explicit ``points`` or ``peaks`` random peaks between ``x_from`` and ``x_to``."""

    item_id = _id(spec)
    base = ctx.num(spec["base"], name="base")
    if "points" in spec:
        peaks = [(ctx.num(x, name="x"), ctx.num(y, name="y")) for x, y in spec["points"]]
    else:
        rng = ctx.rng(item_id)
        x_from, x_to = ctx.num(spec.get("x_from", 0), name="x_from"), ctx.num(spec.get("x_to", "W"), name="x_to")
        low, high = ctx.num(spec["min_height"], name="min_height"), ctx.num(spec["max_height"], name="max_height")
        count = max(2, _count(ctx, spec, "peaks", 5, cap=60))
        step = (x_to - x_from) / (count * 2)
        peaks = []
        for index in range(count * 2 + 1):
            x = x_from + index * step + (rng.uniform(-0.3, 0.3) * step if 0 < index < count * 2 else 0)
            height = rng.uniform(high * 0.6, high) if index % 2 else rng.uniform(low, low + (high - low) * 0.4)
            peaks.append((x, base - height))
    points = [(peaks[0][0], base), *peaks, (peaks[-1][0], base)]
    stroke, stroke_width = _stroke(ctx, spec)
    node = _path_node(ctx, item_id, _polyline(points, closed=True), points,
                      fill=_paint_param(ctx, spec, "fill", "#20103A", item_id), stroke=stroke, stroke_width=stroke_width,
                      opacity=ctx.opt_num(spec, "opacity", 1))
    return [node] if node else []


def waves(ctx: Context, spec: Mapping[str, Any]) -> list[Any]:
    """Stacked filled sine bands from ``y`` downward (``colors`` cycle per band)."""

    item_id = _id(spec)
    rng = ctx.rng(item_id)
    y0 = ctx.num(spec["y"], name="y")
    step = ctx.num(spec.get("step", "H*0.06"), name="step")
    amplitude = ctx.num(spec.get("amplitude", "H*0.02"), name="amplitude")
    wavelength = ctx.num(spec.get("wavelength", "W*0.5"), name="wavelength")
    colors = [ctx.color(color, name="colors") for color in spec.get("colors", ["#FFFFFF"])]
    nodes: list[Any] = []
    samples = 48
    for index in range(_count(ctx, spec, "count", 4, cap=40)):
        phase = rng.uniform(0, math.tau)
        base = y0 + index * step
        points = [(ctx.width * i / samples, base + amplitude * math.sin(math.tau * (ctx.width * i / samples) / wavelength + phase))
                  for i in range(samples + 1)]
        outline = [*points, (ctx.width, ctx.height), (0, ctx.height)]
        node = _path_node(ctx, f"{item_id}-{index + 1}", _polyline(outline, closed=True), outline,
                          fill=colors[index % len(colors)], opacity=ctx.opt_num(spec, "opacity", 1))
        if node:
            nodes.append(node)
    return nodes


def blobs(ctx: Context, spec: Mapping[str, Any]) -> list[Any]:
    """Soft organic shapes (smooth closed curves through jittered radii)."""

    item_id = _id(spec)
    rng = ctx.rng(item_id)
    x0, y0 = ctx.num(spec.get("x", 0), name="x"), ctx.num(spec.get("y", 0), name="y")
    width, height = ctx.num(spec.get("width", "W"), name="width"), ctx.num(spec.get("height", "H"), name="height")
    r_min, r_max = ctx.num(spec["min_radius"], name="min_radius"), ctx.num(spec["max_radius"], name="max_radius")
    colors = [ctx.color(color, name="colors") for color in spec.get("colors", ["#FFFFFF"])]
    wobble = ctx.opt_num(spec, "wobble", 0.28)
    nodes: list[Any] = []
    for index in range(_count(ctx, spec, "count", 3, cap=40)):
        cx, cy = x0 + rng.random() * width, y0 + rng.random() * height
        radius = rng.uniform(r_min, r_max)
        lobes = rng.randint(5, 8)
        pts = []
        for k in range(lobes):
            angle = math.tau * k / lobes
            rr = radius * (1 + rng.uniform(-wobble, wobble))
            pts.append((cx + rr * math.cos(angle), cy + rr * math.sin(angle)))
        node = _path_node(ctx, f"{item_id}-{index + 1}", _smooth_closed(pts), pts,
                          fill=colors[index % len(colors)], opacity=ctx.opt_num(spec, "opacity", 1))
        if node:
            nodes.append(node)
    return nodes


def _smooth_closed(points: list[tuple[float, float]]) -> str:
    """Closed Catmull-Rom spline through ``points`` as cubic Béziers."""

    n = len(points)
    d = f"M{_fmt(points[0][0])} {_fmt(points[0][1])}"
    for i in range(n):
        p0, p1, p2, p3 = points[i - 1], points[i], points[(i + 1) % n], points[(i + 2) % n]
        c1 = (p1[0] + (p2[0] - p0[0]) / 6, p1[1] + (p2[1] - p0[1]) / 6)
        c2 = (p2[0] - (p3[0] - p1[0]) / 6, p2[1] - (p3[1] - p1[1]) / 6)
        d += f" C{_fmt(c1[0])} {_fmt(c1[1])} {_fmt(c2[0])} {_fmt(c2[1])} {_fmt(p2[0])} {_fmt(p2[1])}"
    return d + " Z"


def halftone(ctx: Context, spec: Mapping[str, Any]) -> list[Any]:
    """A dot grid whose dots grow along ``direction`` (degrees) — print-style shading."""

    item_id = _id(spec)
    x0, y0 = ctx.num(spec.get("x", 0), name="x"), ctx.num(spec.get("y", 0), name="y")
    width, height = ctx.num(spec.get("width", "W"), name="width"), ctx.num(spec.get("height", "H"), name="height")
    spacing = max(ctx.num(spec.get("spacing", 24), name="spacing"), 4)
    r_min, r_max = ctx.opt_num(spec, "min_radius", 0.5), ctx.num(spec.get("max_radius", spacing * 0.45), name="max_radius")
    angle = math.radians(ctx.opt_num(spec, "direction", 90))
    ux, uy = math.cos(angle), math.sin(angle)
    columns, rows = int(width / spacing) + 1, int(height / spacing) + 1
    if columns * rows > 20000:
        raise ThemeError(f"halftone_too_dense:{item_id}")
    span = abs(ux) * width + abs(uy) * height or 1
    parts = []
    for row in range(rows):
        for column in range(columns):
            x = x0 + column * spacing + (spacing / 2 if row % 2 else 0)
            y = y0 + row * spacing
            t = ((x - x0) * ux + (y - y0) * uy) / span
            t = min(max(t if ux >= 0 and uy >= 0 else t + 1, 0), 1)
            r = r_min + (r_max - r_min) * t
            if r > 0.3:
                parts.append(_ellipse_path(x, y, r, r))
    if not parts:
        return []
    node = _path_node(ctx, item_id, " ".join(parts), [(x0, y0), (x0 + width, y0 + height)],
                      fill=ctx.color(spec.get("color", "#111111")), opacity=ctx.opt_num(spec, "opacity", 1))
    return [node] if node else []


def rays(ctx: Context, spec: Mapping[str, Any]) -> list[Any]:
    """A sunburst: ``count`` wedges around a center, every other one filled."""

    item_id = _id(spec)
    cx, cy = ctx.num(spec["cx"], name="cx"), ctx.num(spec["cy"], name="cy")
    inner, outer = ctx.opt_num(spec, "inner", 0), ctx.num(spec.get("outer", "MAX"), name="outer")
    count = max(4, _count(ctx, spec, "count", 24, cap=180))
    rotation = math.radians(ctx.opt_num(spec, "rotation", 0))
    parts = []
    for k in range(0, count, 2):
        a0 = rotation + math.tau * k / count
        a1 = rotation + math.tau * (k + 1) / count
        pts = [(cx + inner * math.cos(a0), cy + inner * math.sin(a0)), (cx + outer * math.cos(a0), cy + outer * math.sin(a0)),
               (cx + outer * math.cos(a1), cy + outer * math.sin(a1)), (cx + inner * math.cos(a1), cy + inner * math.sin(a1))]
        parts.append(_polyline(pts, closed=True))
    node = _path_node(ctx, item_id, " ".join(parts), [(cx - outer, cy - outer), (cx + outer, cy + outer)],
                      fill=ctx.color(spec.get("color", "#FFFFFF")), opacity=ctx.opt_num(spec, "opacity", 0.15))
    return [node] if node else []


def rings(ctx: Context, spec: Mapping[str, Any]) -> list[Any]:
    item_id = _id(spec)
    cx, cy = ctx.num(spec["cx"], name="cx"), ctx.num(spec["cy"], name="cy")
    r0, step = ctx.num(spec["radius"], name="radius"), ctx.num(spec.get("step", 24), name="step")
    parts = [_ellipse_path(cx, cy, r0 + i * step, r0 + i * step) for i in range(_count(ctx, spec, "count", 6, cap=120))]
    outer = r0 + step * max(len(parts) - 1, 0)
    node = _path_node(ctx, item_id, " ".join(parts), [(cx - outer, cy - outer), (cx + outer, cy + outer)],
                      stroke=ctx.color(spec.get("stroke", "#FFFFFF")), stroke_width=ctx.opt_num(spec, "stroke_width", 2),
                      opacity=ctx.opt_num(spec, "opacity", 1))
    return [node] if node else []


def stripes(ctx: Context, spec: Mapping[str, Any]) -> list[Any]:
    """Parallel bands at ``angle`` degrees across a region."""

    item_id = _id(spec)
    x0, y0 = ctx.num(spec.get("x", 0), name="x"), ctx.num(spec.get("y", 0), name="y")
    width, height = ctx.num(spec.get("width", "W"), name="width"), ctx.num(spec.get("height", "H"), name="height")
    band, gap = ctx.num(spec.get("band", 18), name="band"), ctx.num(spec.get("gap", 18), name="gap")
    angle = math.radians(ctx.opt_num(spec, "angle", 45))
    diagonal = math.hypot(width, height)
    cx, cy = x0 + width / 2, y0 + height / 2
    dx, dy = math.cos(angle), math.sin(angle)
    nx, ny = -dy, dx
    parts: list[str] = []
    offset = -diagonal / 2
    while offset < diagonal / 2 and len(parts) < 400:
        a = (cx + nx * offset - dx * diagonal, cy + ny * offset - dy * diagonal)
        b = (cx + nx * offset + dx * diagonal, cy + ny * offset + dy * diagonal)
        c = (b[0] + nx * band, b[1] + ny * band)
        e = (a[0] + nx * band, a[1] + ny * band)
        clipped = _clip_to_rect([a, b, c, e], x0, y0, x0 + width, y0 + height)
        if len(clipped) >= 3:
            parts.append(_polyline(clipped, closed=True))
        offset += band + gap
    node = _path_node(ctx, item_id, " ".join(parts), [(x0, y0), (x0 + width, y0 + height)],
                      fill=ctx.color(spec.get("color", "#FFFFFF")), opacity=ctx.opt_num(spec, "opacity", 0.12))
    return [node] if node else []


def _clip_to_rect(polygon: list[tuple[float, float]], left: float, top: float, right: float, bottom: float) -> list[tuple[float, float]]:
    """Sutherland–Hodgman clip of a convex polygon to an axis-aligned rectangle."""

    def clip(points: list[tuple[float, float]], inside: Callable[[tuple[float, float]], bool],
             cross: Callable[[tuple[float, float], tuple[float, float]], tuple[float, float]]) -> list[tuple[float, float]]:
        result: list[tuple[float, float]] = []
        for index, current in enumerate(points):
            previous = points[index - 1]
            if inside(current):
                if not inside(previous):
                    result.append(cross(previous, current))
                result.append(current)
            elif inside(previous):
                result.append(cross(previous, current))
        return result

    def at_x(x: float) -> Callable[[tuple[float, float], tuple[float, float]], tuple[float, float]]:
        return lambda p, q: (x, p[1] + (q[1] - p[1]) * (x - p[0]) / ((q[0] - p[0]) or 1e-9))

    def at_y(y: float) -> Callable[[tuple[float, float], tuple[float, float]], tuple[float, float]]:
        return lambda p, q: (p[0] + (q[0] - p[0]) * (y - p[1]) / ((q[1] - p[1]) or 1e-9), y)

    points = polygon
    for inside, cross in (
        (lambda p: p[0] >= left, at_x(left)),
        (lambda p: p[0] <= right, at_x(right)),
        (lambda p: p[1] >= top, at_y(top)),
        (lambda p: p[1] <= bottom, at_y(bottom)),
    ):
        if not points:
            break
        points = clip(points, inside, cross)
    return points


def grain(ctx: Context, spec: Mapping[str, Any]) -> list[Any]:
    """Fine random speckle for texture."""

    item_id = _id(spec)
    rng = ctx.rng(item_id)
    size = ctx.opt_num(spec, "size", 1.2)
    parts = []
    for _ in range(_count(ctx, spec, "count", 900, cap=6000)):
        x, y = rng.random() * ctx.width, rng.random() * ctx.height
        parts.append(f"M{_fmt(x)} {_fmt(y)} h{_fmt(size)} v{_fmt(size)} h{_fmt(-size)} Z")
    node = _path_node(ctx, item_id, " ".join(parts), [(0, 0), (ctx.width, ctx.height)],
                      fill=ctx.color(spec.get("color", "#FFFFFF")), opacity=ctx.opt_num(spec, "opacity", 0.08))
    return [node] if node else []


def corner_frame(ctx: Context, spec: Mapping[str, Any]) -> list[Any]:
    """An inset hairline border plus bold corner ticks."""

    item_id = _id(spec)
    inset = ctx.num(spec.get("inset", 28), name="inset")
    length = ctx.num(spec.get("length", 60), name="length")
    nodes: list[Any] = []
    if spec.get("border"):
        nodes.extend(rect(ctx, {"id": f"{item_id}-border", "x": inset, "y": inset, "width": ctx.width - 2 * inset,
                               "height": ctx.height - 2 * inset, "fill": "none", "stroke": spec["border"],
                               "stroke_width": spec.get("border_width", 2), "stroke_opacity": spec.get("border_opacity", 0.35)}))
    w, h = ctx.width, ctx.height
    corners = [(inset, inset, 1, 1), (w - inset, inset, -1, 1), (inset, h - inset, 1, -1), (w - inset, h - inset, -1, -1)]
    stroke = ctx.color(spec.get("color", "#FFFFFF"))
    segments: list[str] = []
    points: list[tuple[float, float]] = []
    for x, y, sx, sy in corners:
        segments.append(f"M{_fmt(x)} {_fmt(y + sy * length)} L{_fmt(x)} {_fmt(y)} L{_fmt(x + sx * length)} {_fmt(y)}")
        points.extend([(x, y), (x + sx * length, y + sy * length)])
    node = _path_node(ctx, f"{item_id}-corners", " ".join(segments), points, stroke=stroke,
                      stroke_width=ctx.opt_num(spec, "stroke_width", 5))
    if node:
        nodes.append(node)
    return nodes


# --- art-style vocabulary (used by art style packs; any theme may use them too) -----------------


def _region(ctx: Context, spec: Mapping[str, Any]) -> tuple[float, float, float, float]:
    x0, y0 = ctx.num(spec.get("x", 0), name="x"), ctx.num(spec.get("y", 0), name="y")
    return x0, y0, ctx.num(spec.get("width", "W"), name="width"), ctx.num(spec.get("height", "H"), name="height")


def _one(node: ShapeNode | None) -> list[Any]:
    return [node] if node else []


def scanlines(ctx: Context, spec: Mapping[str, Any]) -> list[Any]:
    """Thin horizontal lines every ``spacing`` px (CRT / screen-print texture)."""

    item_id = _id(spec)
    x0, y0, width, height = _region(ctx, spec)
    spacing = max(ctx.opt_num(spec, "spacing", 6), 2)
    band = max(ctx.opt_num(spec, "band", 1.5), 0.2)
    if height / spacing > 4000:
        raise ThemeError(f"scanlines_too_dense:{item_id}")
    parts, y = [], y0
    while y < y0 + height:
        parts.append(f"M{_fmt(x0)} {_fmt(y)} h{_fmt(width)} v{_fmt(band)} h{_fmt(-width)} Z")
        y += spacing
    return _one(_path_node(ctx, item_id, " ".join(parts), [(x0, y0), (x0 + width, y0 + height)],
                           fill=ctx.color(spec.get("color", "#000000")), opacity=ctx.opt_num(spec, "opacity", 0.12)))


def checker(ctx: Context, spec: Mapping[str, Any]) -> list[Any]:
    """A checkerboard of ``size`` px squares (retro diner, racing, Y2K)."""

    item_id = _id(spec)
    x0, y0, width, height = _region(ctx, spec)
    size = max(ctx.opt_num(spec, "size", 60), 6)
    columns, rows = math.ceil(width / size), math.ceil(height / size)
    if columns * rows > 20000:
        raise ThemeError(f"checker_too_dense:{item_id}")
    parts = []
    for row in range(rows):
        for column in range(row % 2, columns, 2):
            x, y = x0 + column * size, y0 + row * size
            w, h = min(size, x0 + width - x), min(size, y0 + height - y)
            parts.append(f"M{_fmt(x)} {_fmt(y)} h{_fmt(w)} v{_fmt(h)} h{_fmt(-w)} Z")
    return _one(_path_node(ctx, item_id, " ".join(parts), [(x0, y0), (x0 + width, y0 + height)],
                           fill=ctx.color(spec.get("color", "#000000")), opacity=ctx.opt_num(spec, "opacity", 1)))


def dots(ctx: Context, spec: Mapping[str, Any]) -> list[Any]:
    """An even polka-dot grid (``spacing``, ``radius``; ``stagger`` offsets every other row)."""

    item_id = _id(spec)
    x0, y0, width, height = _region(ctx, spec)
    spacing = max(ctx.opt_num(spec, "spacing", 40), 4)
    radius = ctx.opt_num(spec, "radius", spacing * 0.12)
    columns, rows = int(width / spacing) + 1, int(height / spacing) + 1
    if columns * rows > 20000:
        raise ThemeError(f"dots_too_dense:{item_id}")
    stagger = bool(spec.get("stagger", True))
    parts = [
        _ellipse_path(x0 + column * spacing + (spacing / 2 if stagger and row % 2 else 0), y0 + row * spacing, radius, radius)
        for row in range(rows) for column in range(columns)
    ]
    return _one(_path_node(ctx, item_id, " ".join(parts), [(x0, y0), (x0 + width, y0 + height)],
                           fill=ctx.color(spec.get("color", "#000000")), opacity=ctx.opt_num(spec, "opacity", 0.2)))


def confetti_shapes(ctx: Context, spec: Mapping[str, Any]) -> list[Any]:
    """Scattered geometric bits — triangles, rings, squiggles, crosses (Memphis, kids, party).

    ``colors`` cycle per shape kind; ``size`` is the typical shape size;
    ``avoid_center`` keeps the middle (where text sits) clear.
    """

    item_id = _id(spec)
    rng = ctx.rng(item_id)
    x0, y0, width, height = _region(ctx, spec)
    size = ctx.opt_num(spec, "size", ctx.names["MIN"] * 0.04)
    stroke_width = ctx.opt_num(spec, "stroke_width", size * 0.18)
    colors = [ctx.solid(color) for color in spec.get("colors", ["#000000"])] or ["#000000"]
    avoid = ctx.opt_num(spec, "avoid_center", 0)
    kinds = ("triangle", "ring", "squiggle", "cross", "dot")
    buckets: dict[str, list[str]] = {kind: [] for kind in kinds}
    count = _count(ctx, spec, "count", 26, cap=400)
    placed = 0
    attempts = 0
    while placed < count and attempts < count * 20:
        attempts += 1
        cx, cy = x0 + rng.random() * width, y0 + rng.random() * height
        if avoid and abs(cx - ctx.width / 2) < ctx.width * avoid / 2 and abs(cy - ctx.height / 2) < ctx.height * avoid / 2:
            continue
        if not ctx.clear_of_text(cx, cy, size * 1.4):
            continue
        placed += 1
        kind = kinds[rng.randrange(len(kinds))]
        s = size * (0.6 + rng.random() * 0.8)
        turn = rng.random() * math.tau
        if kind == "triangle":
            pts = [(cx + s * math.cos(turn + k * math.tau / 3), cy + s * math.sin(turn + k * math.tau / 3)) for k in range(3)]
            buckets[kind].append(_polyline(pts, closed=True))
        elif kind == "ring":
            buckets[kind].append(_ellipse_path(cx, cy, s * 0.7, s * 0.7))
        elif kind == "squiggle":
            pts = [(cx - s * 1.4 + i * s * 0.35, cy + (s * 0.35 if i % 2 else -s * 0.35)) for i in range(9)]
            cos_t, sin_t = math.cos(turn), math.sin(turn)
            pts = [(cx + (px - cx) * cos_t - (py - cy) * sin_t, cy + (px - cx) * sin_t + (py - cy) * cos_t) for px, py in pts]
            buckets[kind].append(_polyline(pts))
        elif kind == "cross":
            a = s * 0.6
            buckets[kind].append(f"M{_fmt(cx - a)} {_fmt(cy)} L{_fmt(cx + a)} {_fmt(cy)} M{_fmt(cx)} {_fmt(cy - a)} L{_fmt(cx)} {_fmt(cy + a)}")
        else:
            buckets[kind].append(_ellipse_path(cx, cy, s * 0.3, s * 0.3))
    nodes: list[Any] = []
    opacity = ctx.opt_num(spec, "opacity", 1)
    for index, kind in enumerate(kinds):
        if not buckets[kind]:
            continue
        color = colors[index % len(colors)]
        filled = kind in ("triangle", "dot")
        nodes += _one(_path_node(
            ctx, f"{item_id}-{kind}", " ".join(buckets[kind]), [(x0, y0), (x0 + width, y0 + height)],
            fill=color if filled else "none", stroke=None if filled else color,
            stroke_width=0 if filled else stroke_width, opacity=opacity,
        ))
    return nodes


def squiggle(ctx: Context, spec: Mapping[str, Any]) -> list[Any]:
    """A hand-drawn wavy stroke from (x1, y1) to (x2, y2) (``amplitude``, ``wavelength``)."""

    item_id = _id(spec)
    x1, y1 = ctx.num(spec["x1"], name="x1"), ctx.num(spec["y1"], name="y1")
    x2, y2 = ctx.num(spec["x2"], name="x2"), ctx.num(spec["y2"], name="y2")
    amplitude = ctx.opt_num(spec, "amplitude", 12)
    wavelength = max(ctx.opt_num(spec, "wavelength", 60), 8)
    length = math.hypot(x2 - x1, y2 - y1) or 1
    ux, uy = (x2 - x1) / length, (y2 - y1) / length
    steps = max(8, min(int(length / wavelength * 12), 1200))
    pts = []
    for i in range(steps + 1):
        t = length * i / steps
        offset = amplitude * math.sin(t / wavelength * math.tau)
        pts.append((x1 + ux * t - uy * offset, y1 + uy * t + ux * offset))
    return _one(_path_node(ctx, item_id, _polyline(pts), pts, stroke=ctx.color(spec.get("stroke", "#000000")),
                           stroke_width=ctx.opt_num(spec, "stroke_width", 6), opacity=ctx.opt_num(spec, "opacity", 1)))


def zigzag(ctx: Context, spec: Mapping[str, Any]) -> list[Any]:
    """A horizontal zigzag band at ``y`` (``amplitude``, ``step``) — 80s/Memphis trim."""

    item_id = _id(spec)
    x0 = ctx.num(spec.get("x", 0), name="x")
    width = ctx.num(spec.get("width", "W"), name="width")
    y = ctx.num(spec["y"], name="y")
    amplitude, step = ctx.opt_num(spec, "amplitude", 14), max(ctx.opt_num(spec, "step", 28), 4)
    count = min(int(width / step) + 1, 2000)
    pts = [(x0 + i * step, y + (amplitude if i % 2 else -amplitude)) for i in range(count + 1)]
    return _one(_path_node(ctx, item_id, _polyline(pts), pts, stroke=ctx.color(spec.get("stroke", "#000000")),
                           stroke_width=ctx.opt_num(spec, "stroke_width", 5), opacity=ctx.opt_num(spec, "opacity", 1)))


def arcs(ctx: Context, spec: Mapping[str, Any]) -> list[Any]:
    """Concentric half-rings rising from (cx, cy) — Art Deco fan / sunrise (``count``, ``radius``, ``step``)."""

    item_id = _id(spec)
    cx, cy = ctx.num(spec["cx"], name="cx"), ctx.num(spec["cy"], name="cy")
    radius, step = ctx.num(spec.get("radius", "MIN * 0.1"), name="radius"), ctx.num(spec.get("step", "MIN * 0.05"), name="step")
    count = _count(ctx, spec, "count", 6, cap=80)
    up = ctx.opt_num(spec, "direction", -1) < 0
    parts = []
    sweep = 1 if up else 0
    for i in range(count):
        r = radius + i * step
        parts.append(f"M{_fmt(cx - r)} {_fmt(cy)} A{_fmt(r)} {_fmt(r)} 0 0 {sweep} {_fmt(cx + r)} {_fmt(cy)}")
    outer = radius + step * max(count - 1, 0)
    return _one(_path_node(ctx, item_id, " ".join(parts), [(cx - outer, cy - outer), (cx + outer, cy + outer)],
                           stroke=ctx.color(spec.get("stroke", "#000000")), stroke_width=ctx.opt_num(spec, "stroke_width", 3),
                           opacity=ctx.opt_num(spec, "opacity", 1)))


def deco_frame(ctx: Context, spec: Mapping[str, Any]) -> list[Any]:
    """A double inset border with stepped corners (Art Deco, luxury, certificates)."""

    item_id = _id(spec)
    inset = ctx.num(spec.get("inset", "MIN * 0.04"), name="inset")
    gap = ctx.opt_num(spec, "gap", ctx.names["MIN"] * 0.014)
    notch = ctx.opt_num(spec, "length", ctx.names["MIN"] * 0.05)
    w, h = ctx.width, ctx.height
    parts = []
    for k, d in enumerate((inset, inset + gap)):
        n = notch if k == 0 else notch * 0.6
        pts = [(d + n, d), (w - d - n, d), (w - d - n, d + n * 0.4), (w - d - n * 0.4, d + n * 0.4), (w - d - n * 0.4, d + n),
               (w - d, d + n), (w - d, h - d - n), (w - d - n * 0.4, h - d - n), (w - d - n * 0.4, h - d - n * 0.4),
               (w - d - n, h - d - n * 0.4), (w - d - n, h - d), (d + n, h - d), (d + n, h - d - n * 0.4),
               (d + n * 0.4, h - d - n * 0.4), (d + n * 0.4, h - d - n), (d, h - d - n), (d, d + n), (d + n * 0.4, d + n),
               (d + n * 0.4, d + n * 0.4), (d + n, d + n * 0.4)]
        parts.append(_polyline(pts, closed=True))
    return _one(_path_node(ctx, item_id, " ".join(parts), [(inset, inset), (w - inset, h - inset)],
                           stroke=ctx.color(spec.get("color", "#C9A227")), stroke_width=ctx.opt_num(spec, "stroke_width", 3),
                           opacity=ctx.opt_num(spec, "opacity", 1)))


def distress(ctx: Context, spec: Mapping[str, Any]) -> list[Any]:
    """Grunge wear: ink splats and scratches scattered over a region (``count``, ``size``)."""

    item_id = _id(spec)
    rng = ctx.rng(item_id)
    x0, y0, width, height = _region(ctx, spec)
    size = ctx.opt_num(spec, "size", ctx.names["MIN"] * 0.012)
    splats, scratches = [], []
    for _ in range(_count(ctx, spec, "count", 160, cap=3000)):
        cx, cy = x0 + rng.random() * width, y0 + rng.random() * height
        if rng.random() < 0.7:
            r = size * (0.2 + rng.random() ** 3 * 1.6)
            pts = [(cx + r * (0.6 + rng.random() * 0.6) * math.cos(a), cy + r * (0.6 + rng.random() * 0.6) * math.sin(a))
                   for a in (k * math.tau / 7 for k in range(7))]
            splats.append(_polyline(pts, closed=True))
        else:
            length, turn = size * (2 + rng.random() * 8), rng.random() * math.pi
            scratches.append(f"M{_fmt(cx)} {_fmt(cy)} L{_fmt(cx + length * math.cos(turn))} {_fmt(cy + length * math.sin(turn))}")
    color = ctx.color(spec.get("color", "#000000"))
    opacity = ctx.opt_num(spec, "opacity", 0.35)
    box = [(x0, y0), (x0 + width, y0 + height)]
    return [
        *_one(_path_node(ctx, f"{item_id}-splats", " ".join(splats), box, fill=color, opacity=opacity) if splats else None),
        *_one(_path_node(ctx, f"{item_id}-scratches", " ".join(scratches), box, stroke=color,
                         stroke_width=max(size * 0.12, 0.6), opacity=opacity) if scratches else None),
    ]


def notes(ctx: Context, spec: Mapping[str, Any]) -> list[Any]:
    """Scattered music notes (eighth notes and beamed pairs) — jazz, live music, karaoke."""

    item_id = _id(spec)
    rng = ctx.rng(item_id)
    x0, y0, width, height = _region(ctx, spec)
    size = ctx.opt_num(spec, "size", ctx.names["MIN"] * 0.05)
    heads, stems = [], []
    for _ in range(_count(ctx, spec, "count", 8, cap=200)):
        s = size * (0.7 + rng.random() * 0.6)
        x, y = x0 + rng.random() * width, y0 + rng.random() * height
        if not ctx.clear_of_text(x + s * 0.45, y - s * 0.4, s * 1.1):
            continue
        rx, ry = s * 0.32, s * 0.22
        beamed = rng.random() < 0.4
        for k in range(2 if beamed else 1):
            hx = x + k * s * 0.9
            hy = y + (k * s * 0.15)
            heads.append(_ellipse_path(hx, hy, rx, ry))
            stems.append(f"M{_fmt(hx + rx * 0.9)} {_fmt(hy)} L{_fmt(hx + rx * 0.9)} {_fmt(hy - s * 1.1 + k * s * 0.15)}")
        if beamed:
            stems.append(f"M{_fmt(x + rx * 0.9)} {_fmt(y - s * 1.1)} L{_fmt(x + s * 0.9 + rx * 0.9)} {_fmt(y - s * 0.95)}")
        else:
            stems.append(f"M{_fmt(x + rx * 0.9)} {_fmt(y - s * 1.1)} Q{_fmt(x + s * 0.7)} {_fmt(y - s * 0.8)} {_fmt(x + s * 0.55)} {_fmt(y - s * 0.4)}")
    color = ctx.color(spec.get("color", "#000000"))
    opacity = ctx.opt_num(spec, "opacity", 0.8)
    box = [(x0 - size, y0 - size * 1.2), (x0 + width + size, y0 + height + size)]
    return [
        *_one(_path_node(ctx, f"{item_id}-heads", " ".join(heads), box, fill=color, opacity=opacity) if heads else None),
        *_one(_path_node(ctx, f"{item_id}-stems", " ".join(stems), box, stroke=color, stroke_width=max(size * 0.07, 1),
                         opacity=opacity) if stems else None),
    ]


def glitch_bars(ctx: Context, spec: Mapping[str, Any]) -> list[Any]:
    """Offset horizontal slices in two ``colors`` (digital glitch, cyberpunk, dystopia)."""

    item_id = _id(spec)
    rng = ctx.rng(item_id)
    x0, y0, width, height = _region(ctx, spec)
    colors = [ctx.solid(color) for color in spec.get("colors", ["#FF0055", "#00E5FF"])] or ["#FF0055"]
    max_height = ctx.opt_num(spec, "max_height", ctx.height * 0.012)
    buckets: list[list[str]] = [[] for _ in colors]
    for i in range(_count(ctx, spec, "count", 22, cap=600)):
        y = y0 + rng.random() * height
        h = max(1.0, rng.random() * max_height)
        x = x0 + rng.random() * width * 0.7
        w = width * (0.08 + rng.random() * 0.45)
        w = min(w, x0 + width - x)
        buckets[i % len(colors)].append(f"M{_fmt(x)} {_fmt(y)} h{_fmt(w)} v{_fmt(h)} h{_fmt(-w)} Z")
    opacity = ctx.opt_num(spec, "opacity", 0.55)
    return [
        node for index, parts in enumerate(buckets) if parts
        for node in _one(_path_node(ctx, f"{item_id}-{index + 1}", " ".join(parts), [(x0, y0), (x0 + width, y0 + height)],
                                    fill=colors[index], opacity=opacity))
    ]


def barcode(ctx: Context, spec: Mapping[str, Any]) -> list[Any]:
    """A decorative (not scannable) barcode block — dystopian ID, streetwear tags, tickets."""

    item_id = _id(spec)
    rng = ctx.rng(item_id)
    x0, y0 = ctx.num(spec["x"], name="x"), ctx.num(spec["y"], name="y")
    width, height = ctx.num(spec["width"], name="width"), ctx.num(spec["height"], name="height")
    if not ctx.clear_of_text(x0 + width / 2, y0 + height / 2, max(width, height) / 2):
        return []  # an art-style barcode never sits on text
    parts, x = [], x0
    unit = max(width / 90, 0.6)
    while x < x0 + width - unit:
        bar = unit * rng.choice((1, 1, 2, 3))
        bar = min(bar, x0 + width - x)
        parts.append(f"M{_fmt(x)} {_fmt(y0)} h{_fmt(bar)} v{_fmt(height)} h{_fmt(-bar)} Z")
        x += bar + unit * rng.choice((1, 1, 2))
    return _one(_path_node(ctx, item_id, " ".join(parts), [(x0, y0), (x0 + width, y0 + height)],
                           fill=ctx.color(spec.get("color", "#000000")), opacity=ctx.opt_num(spec, "opacity", 1)))


def vignette(ctx: Context, spec: Mapping[str, Any]) -> list[Any]:
    """Darkened (or tinted) edges fading to clear in the middle (noir, film, moody)."""

    item_id = _id(spec)
    color = ctx.solid(spec.get("color", "#000000"), name="color")
    strength = ctx.opt_num(spec, "strength", 0.55)
    paint = ctx.add_paint({
        "paint_id": f"{item_id}-vignette",
        "type": "radial",
        "r": 0.75,
        "stops": [
            {"offset": 0, "color": color, "opacity": 0},
            {"offset": 0.6, "color": color, "opacity": strength * 0.25},
            {"offset": 1, "color": color, "opacity": strength},
        ],
    })
    return rect(ctx, {"id": item_id, "fill": paint})


def torn_edge(ctx: Context, spec: Mapping[str, Any]) -> list[Any]:
    """A paper strip with a ripped edge (``y``, ``height``; ``side``: top or bottom) — collage, zine, scrapbook."""

    item_id = _id(spec)
    rng = ctx.rng(item_id)
    y = ctx.num(spec["y"], name="y")
    band = ctx.num(spec.get("height", "H * 0.1"), name="height")
    rough = ctx.opt_num(spec, "amplitude", ctx.names["MIN"] * 0.012)
    step = max(ctx.opt_num(spec, "step", ctx.names["MIN"] * 0.018), 3)
    top = str(spec.get("side", "top")) == "top"
    edge_y = y if top else y + band
    pts = [(0.0, edge_y)]
    x = 0.0
    while x < ctx.width:
        x = min(x + step * (0.5 + rng.random()), ctx.width)
        pts.append((x, edge_y + (rng.random() - 0.5) * 2 * rough))
    other = y + band if top else y
    poly = [*pts, (ctx.width, other), (0.0, other)]
    return _one(_path_node(ctx, item_id, _polyline(poly, closed=True), poly,
                           fill=ctx.color(spec.get("fill", "#FFFFFF")), opacity=ctx.opt_num(spec, "opacity", 1)))


MOTIFS: dict[str, Motif] = {
    "rect": rect,
    "ellipse": ellipse,
    "line": line,
    "polygon": polygon,
    "glow": glow,
    "text_block": text_block,
    "display_text": display_text,
    "image": image,
    "starfield": starfield,
    "perspective_grid": perspective_grid,
    "striped_sun": striped_sun,
    "ridge": ridge,
    "waves": waves,
    "blobs": blobs,
    "halftone": halftone,
    "rays": rays,
    "rings": rings,
    "stripes": stripes,
    "grain": grain,
    "corner_frame": corner_frame,
    "scanlines": scanlines,
    "checker": checker,
    "dots": dots,
    "confetti_shapes": confetti_shapes,
    "squiggle": squiggle,
    "zigzag": zigzag,
    "arcs": arcs,
    "deco_frame": deco_frame,
    "distress": distress,
    "notes": notes,
    "glitch_bars": glitch_bars,
    "barcode": barcode,
    "vignette": vignette,
    "torn_edge": torn_edge,
}

# Motifs that only decorate (no text, no photos): what art style packs may use.
DECOR_MOTIFS = frozenset(set(MOTIFS) - {"text_block", "display_text", "image"})

__all__ = ["DECOR_MOTIFS", "MOTIFS", "Motif"]
