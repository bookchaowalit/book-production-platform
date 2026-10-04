"""Pydantic models for the versioned Graphics contract.

``graphic-ir.v1`` (flat rect/line/text/image nodes) is what the three original
templates compile to and stays byte-for-byte stable. ``graphic-ir.v2`` adds
what data-defined themes need: ellipses and paths, linear and radial gradient
paints, opacity, styled text, and named groups (layers in editors such as
Affinity) with optional clipping. Every v1 IR is also a valid v2 IR.
"""

from __future__ import annotations

import re
from typing import Annotated, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# Template ids come from the theme registry (graphics_production/themes); the
# compiler rejects an unknown id. The three original templates keep their ids.
TemplateId = Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]{1,47}$")]
LEGACY_TEMPLATE_IDS = ("quote_card", "product_card", "announcement")
VariantId = Literal["square", "portrait", "story", "landscape"]
OverflowPolicy = Literal["wrap", "reject"]
TextAlign = Literal["left", "center", "right"]
ShapeKind = Literal["rect", "line", "ellipse", "path"]
FontWeight = Literal["normal", "bold", "black"]
_PAINT_REF = re.compile(r"^paint:[a-z][a-z0-9_-]{0,47}$")
# Absolute and relative path commands with numbers only: no external
# references, no expressions. Arcs are allowed.
_PATH_DATA = re.compile(r"^[MmLlHhVvCcSsQqTtAaZz0-9eE+\-.,\s]{1,200000}$")


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


def _hex_or_paint(value: str | None) -> str | None:
    if value is None or value == "none" or _PAINT_REF.match(value):
        return value
    return _hex_color(value)


def _hex_color(value: str) -> str:
    if len(value) != 7 or not value.startswith("#"):
        raise ValueError("color must be a six-digit hex value")
    try:
        int(value[1:], 16)
    except ValueError as exc:
        raise ValueError("color must be a six-digit hex value") from exc
    return value.upper()


class CanvasSpec(ContractModel):
    width: int = Field(ge=1, le=4096)
    height: int = Field(ge=1, le=4096)
    background: str = "#FFFFFF"

    _validate_background = field_validator("background")(_hex_color)


class BrandTokens(ContractModel):
    primary: str
    accent: str
    foreground: str = "#111111"
    background: str = "#FFFFFF"

    _validate_colors = field_validator("primary", "accent", "foreground", "background")(_hex_color)


DesignValue = Union[float, int, str, bool]  # noqa: UP007


class LayerOverride(ContractModel):
    """Per-layer adjustment of a theme: visibility, opacity, and a move in canvas pixels."""

    visible: bool = True
    opacity: float | None = Field(default=None, ge=0, le=1)
    dx: float = Field(default=0, ge=-4096, le=4096)
    dy: float = Field(default=0, ge=-4096, le=4096)


class ItemOverride(ContractModel):
    """Per-item adjustment: visibility and parameter values (numbers, ``V``-relative expressions, colors)."""

    visible: bool = True
    params: dict[str, DesignValue] = Field(default_factory=dict, max_length=24)


class DesignOverrides(ContractModel):
    """Changes applied on top of a theme without editing the theme file.

    ``style`` picks one of the theme's style presets; ``palette`` replaces
    palette colors; ``layers`` and ``items`` (keyed ``"Layer/item"``) adjust
    individual parts. Every key is checked against the theme when compiled.
    """

    style: str | None = Field(default=None, max_length=48)
    palette: dict[str, str] = Field(default_factory=dict, max_length=32)
    layers: dict[str, LayerOverride] = Field(default_factory=dict, max_length=32)
    items: dict[str, ItemOverride] = Field(default_factory=dict, max_length=96)

    @field_validator("palette")
    @classmethod
    def _palette_colors(cls, value: dict[str, str]) -> dict[str, str]:
        return {name: _hex_color(color) for name, color in value.items()}


class GraphicRequest(ContractModel):
    schema_version: Literal["graphic-request.v1"] = "graphic-request.v1"
    request_id: str
    template_id: TemplateId
    variant: VariantId
    canvas: CanvasSpec
    brand: BrandTokens
    content: dict[str, str] = Field(default_factory=dict)
    assets: dict[str, str] = Field(default_factory=dict)
    asset_root: str | None = None
    font_ref: str = "builtin:sans"
    design: DesignOverrides | None = None


class NodeBase(ContractModel):
    node_id: str
    x: float = Field(ge=0)
    y: float = Field(ge=0)
    width: float = Field(gt=0)
    height: float = Field(gt=0)


class TextNode(NodeBase):
    node_type: Literal["text"] = "text"
    text: str
    font_ref: str = "builtin:sans"
    font_size: float = Field(gt=0, le=512)
    color: str = "#111111"
    align: TextAlign = "left"
    overflow_policy: OverflowPolicy = "wrap"
    max_lines: int = Field(default=8, ge=1, le=64)
    line_height: float = Field(default=1.2, gt=0, le=3)
    # v2 styling; the defaults render exactly like v1.
    font_weight: FontWeight = "normal"
    letter_spacing: float = Field(default=0, ge=-20, le=200)
    stroke: str | None = None
    stroke_width: float = Field(default=0, ge=0, le=32)
    opacity: float = Field(default=1, ge=0, le=1)

    _validate_color = field_validator("color")(_hex_or_paint)
    _validate_stroke = field_validator("stroke")(_hex_or_paint)


class ShapeNode(NodeBase):
    node_type: Literal["shape"] = "shape"
    kind: ShapeKind = "rect"
    fill: str = "#FFFFFF"
    stroke: str | None = None
    stroke_width: float = Field(default=0, ge=0, le=64)
    # v2: ``d`` holds path data for kind="path" (x/y/width/height are its
    # bounding box, clamped to the canvas); ``rx`` rounds rect corners.
    d: str | None = Field(default=None, max_length=200_000)
    rx: float = Field(default=0, ge=0, le=2048)
    opacity: float = Field(default=1, ge=0, le=1)
    stroke_opacity: float = Field(default=1, ge=0, le=1)

    _validate_fill = field_validator("fill")(_hex_or_paint)
    _validate_stroke = field_validator("stroke")(_hex_or_paint)

    @field_validator("d")
    @classmethod
    def _validate_path(cls, value: str | None) -> str | None:
        if value is not None and not _PATH_DATA.match(value):
            raise ValueError("path data may contain only path commands and numbers")
        return value

    @model_validator(mode="after")
    def _path_needs_data(self) -> ShapeNode:
        if (self.kind == "path") != (self.d is not None):
            raise ValueError("kind='path' requires d, and only paths may set d")
        return self


class ImageNode(NodeBase):
    node_type: Literal["image"] = "image"
    asset_key: str
    source_ref: str
    preserve_aspect: bool = True


class GroupNode(ContractModel):
    """A named layer. ``clip`` shapes (union) clip every child."""

    node_type: Literal["group"] = "group"
    node_id: str
    label: str | None = Field(default=None, max_length=80)
    opacity: float = Field(default=1, ge=0, le=1)
    clip: list[ShapeNode] = Field(default_factory=list, max_length=200)
    # A design override can move a whole layer; rendered as a translate.
    offset_x: float = Field(default=0, ge=-4096, le=4096)
    offset_y: float = Field(default=0, ge=-4096, le=4096)
    children: list[GraphicNode] = Field(default_factory=list, max_length=5000)


GraphicNode = Annotated[Union[TextNode, ShapeNode, ImageNode, GroupNode], Field(discriminator="node_type")]  # noqa: UP007


class GradientStop(ContractModel):
    offset: float = Field(ge=0, le=1)
    color: str
    opacity: float = Field(default=1, ge=0, le=1)

    _validate_color = field_validator("color")(_hex_color)


class LinearGradient(ContractModel):
    paint_type: Literal["linear"] = "linear"
    paint_id: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,47}$")
    x1: float = Field(default=0, ge=-1, le=2)
    y1: float = Field(default=0, ge=-1, le=2)
    x2: float = Field(default=0, ge=-1, le=2)
    y2: float = Field(default=1, ge=-1, le=2)
    stops: list[GradientStop] = Field(min_length=2, max_length=16)


class RadialGradient(ContractModel):
    paint_type: Literal["radial"] = "radial"
    paint_id: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,47}$")
    cx: float = Field(default=0.5, ge=-1, le=2)
    cy: float = Field(default=0.5, ge=-1, le=2)
    r: float = Field(default=0.5, gt=0, le=2)
    stops: list[GradientStop] = Field(min_length=2, max_length=16)


Paint = Annotated[Union[LinearGradient, RadialGradient], Field(discriminator="paint_type")]  # noqa: UP007


class GraphicIR(ContractModel):
    schema_version: Literal["graphic-ir.v1", "graphic-ir.v2"] = "graphic-ir.v1"
    request_id: str
    template_ref: str
    variant: VariantId
    canvas: CanvasSpec
    nodes: list[GraphicNode]
    paints: list[Paint] = Field(default_factory=list, max_length=64)
    source_refs: list[str] = Field(default_factory=list)
    assumptions: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _version_matches_features(self) -> GraphicIR:
        if self.schema_version == "graphic-ir.v1" and (self.paints or any(_uses_v2(node) for node in self.nodes)):
            raise ValueError("graphic-ir.v1 cannot use groups, paths, ellipses, paints, or styled text; use graphic-ir.v2")
        known = {paint.paint_id for paint in self.paints}
        for ref in _paint_refs(self.nodes):
            if ref not in known:
                raise ValueError(f"unknown paint reference: paint:{ref}")
        return self


def iter_nodes(nodes: list[TextNode | ShapeNode | ImageNode | GroupNode]) -> list[TextNode | ShapeNode | ImageNode]:
    """Every leaf node in paint order (groups flattened, clip shapes excluded)."""

    leaves: list[TextNode | ShapeNode | ImageNode] = []
    for node in nodes:
        if isinstance(node, GroupNode):
            leaves.extend(iter_nodes(node.children))
        else:
            leaves.append(node)
    return leaves


def _uses_v2(node: TextNode | ShapeNode | ImageNode | GroupNode) -> bool:
    if isinstance(node, GroupNode):
        return True
    if isinstance(node, ShapeNode):
        return (
            node.kind in ("ellipse", "path") or node.rx != 0 or node.opacity != 1 or node.stroke_opacity != 1
            or node.fill.startswith("paint:") or node.fill == "none" or bool(node.stroke and node.stroke.startswith("paint:"))
        )
    if isinstance(node, TextNode):
        return (
            node.font_weight != "normal" or node.letter_spacing != 0 or node.stroke is not None or node.opacity != 1
            or node.color.startswith("paint:")
        )
    return False


def _paint_refs(nodes: list[TextNode | ShapeNode | ImageNode | GroupNode]) -> set[str]:
    refs: set[str] = set()
    for node in nodes:
        values: list[str | None] = []
        if isinstance(node, GroupNode):
            refs |= _paint_refs(node.children)
            refs |= _paint_refs(list(node.clip))
            continue
        if isinstance(node, ShapeNode):
            values = [node.fill, node.stroke]
        elif isinstance(node, TextNode):
            values = [node.color, node.stroke]
        refs |= {value[len("paint:"):] for value in values if value and value.startswith("paint:")}
    return refs


GroupNode.model_rebuild()


class ArtifactRef(ContractModel):
    filename: str
    media_type: str
    content_hash: str
    byte_size: int
    width: int
    height: int


class ValidationIssue(ContractModel):
    code: str
    path: str
    message: str
