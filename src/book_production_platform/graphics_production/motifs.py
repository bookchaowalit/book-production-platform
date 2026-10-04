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

from .layout import LayoutError, fit_text
from .themes import Context, ThemeError

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


def text_block(ctx: Context, spec: Mapping[str, Any]) -> list[Any]:
    """Text fitted into a box (font shrinks to ``min_font_size``; overflow is reported, never cut)."""

    node_id = str(spec.get("node_id") or _id(spec))
    value = ctx.text(spec)
    if value is None:
        return []
    width = ctx.num(spec["width"], name="width")
    height = ctx.num(spec["height"], name="height")
    overflow = str(spec.get("overflow", "reject"))
    font_size = ctx.num(spec["font_size"], name="font_size")
    max_lines = int(ctx.opt_num(spec, "max_lines", 8))
    try:
        layout = fit_text(
            value,
            width=width,
            height=height,
            font_size=font_size,
            min_font_size=ctx.opt_num(spec, "min_font_size", font_size),
            max_lines=max_lines,
            overflow_policy=overflow,
        )
    except LayoutError as exc:
        exc.node_id = node_id
        raise
    stroke = ctx.color(spec["stroke"], name="stroke") if spec.get("stroke") else None
    return [TextNode(
        node_id=node_id,
        x=ctx.num(spec["x"], name="x"),
        y=ctx.num(spec["y"], name="y"),
        width=width,
        height=height,
        text=value,
        font_size=layout.font_size,
        font_ref=ctx.font(spec),
        color=_paint_param(ctx, spec, "color", "#111111", node_id),
        align=spec.get("align", "left"),
        overflow_policy=overflow,  # type: ignore[arg-type]
        max_lines=max_lines,
        font_weight=spec.get("weight", "normal"),
        letter_spacing=ctx.opt_num(spec, "letter_spacing", 0),
        stroke=stroke,
        stroke_width=ctx.opt_num(spec, "stroke_width", 0 if stroke is None else 1),
        opacity=ctx.opt_num(spec, "opacity", 1),
    )]


def display_text(ctx: Context, spec: Mapping[str, Any]) -> list[Any]:
    """A title with offset echo copies behind it (``shadows``: list of ``{dx, dy, color, opacity}``)."""

    node_id = _id(spec)
    main = text_block(ctx, {**spec, "node_id": node_id})
    if not main:
        return []
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
}

__all__ = ["MOTIFS", "Motif"]
