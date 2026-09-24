"""Pydantic models for the versioned Graphics V1A contract."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

TemplateId = Literal["quote_card", "product_card", "announcement"]
VariantId = Literal["square", "portrait", "story"]
OverflowPolicy = Literal["wrap", "reject"]
TextAlign = Literal["left", "center", "right"]
ShapeKind = Literal["rect", "line"]


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


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

    _validate_color = field_validator("color")(_hex_color)


class ShapeNode(NodeBase):
    node_type: Literal["shape"] = "shape"
    kind: ShapeKind = "rect"
    fill: str = "#FFFFFF"
    stroke: str | None = None
    stroke_width: float = Field(default=0, ge=0, le=64)

    _validate_fill = field_validator("fill")(_hex_color)
    _validate_stroke = field_validator("stroke")(_hex_color)


class ImageNode(NodeBase):
    node_type: Literal["image"] = "image"
    asset_key: str
    source_ref: str
    preserve_aspect: bool = True


GraphicNode = Annotated[TextNode | ShapeNode | ImageNode, Field(discriminator="node_type")]


class GraphicIR(ContractModel):
    schema_version: Literal["graphic-ir.v1"] = "graphic-ir.v1"
    request_id: str
    template_ref: str
    variant: VariantId
    canvas: CanvasSpec
    nodes: list[GraphicNode]
    source_refs: list[str] = Field(default_factory=list)
    assumptions: dict[str, str] = Field(default_factory=dict)


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
