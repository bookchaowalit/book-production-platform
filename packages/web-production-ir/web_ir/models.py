"""Pydantic models for the versioned Web V1A contract."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=False)


def _hex_color(value: str) -> str:
    if len(value) != 7 or not value.startswith("#"):
        raise ValueError("color must be a six-digit hex value")
    try:
        int(value[1:], 16)
    except ValueError as exc:
        raise ValueError("color must be a six-digit hex value") from exc
    return value.upper()


class ThemeSpec(ContractModel):
    primary: str = "#2563EB"
    background: str = "#FFFFFF"
    foreground: str = "#111827"

    _validate_colors = field_validator("primary", "background", "foreground")(_hex_color)


class LinkSpec(ContractModel):
    label: str = Field(min_length=1, max_length=120)
    href: str = Field(min_length=1, max_length=500)


class HeroImageSpec(ContractModel):
    """A local raster image shipped inside the page bundle (never a URL)."""

    filename: str = Field(min_length=5, max_length=120, pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*\.(png|jpg|jpeg|webp)$")
    content_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    alt: str = Field(min_length=1, max_length=300)


class FeatureSpec(ContractModel):
    title: str = Field(min_length=1, max_length=160)
    body: str = Field(min_length=1, max_length=2_000)


class PricingPlan(ContractModel):
    """One plan card in the optional ``pricing`` section; the price is display text, never computed."""

    name: str = Field(min_length=1, max_length=80)
    price: str = Field(min_length=1, max_length=40)
    description: str = Field(default="", max_length=300)
    features: list[str] = Field(default_factory=list, max_length=8)


class FaqItem(ContractModel):
    """One question/answer pair in the optional ``faq`` section (rendered as ``<details>``)."""

    question: str = Field(min_length=1, max_length=200)
    answer: str = Field(min_length=1, max_length=1_000)


class PageRequest(ContractModel):
    schema_version: Literal["page-request.v1"] = "page-request.v1"
    page_id: str = Field(min_length=1, max_length=80)
    language: Literal["th", "en"] = "th"
    slug: str = Field(min_length=1, max_length=80, pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(min_length=1, max_length=500)
    hero_heading: str = Field(min_length=1, max_length=200)
    hero_body: str = Field(min_length=1, max_length=2_000)
    hero_cta: LinkSpec
    features: list[FeatureSpec] = Field(max_length=6)
    pricing: list[PricingPlan] = Field(default_factory=list, max_length=3)
    faq: list[FaqItem] = Field(default_factory=list, max_length=8)
    secondary_cta: LinkSpec | None = None
    footer_text: str = Field(default="", max_length=500)
    theme: ThemeSpec = Field(default_factory=ThemeSpec)
    hero_image: HeroImageSpec | None = None


class PageIR(ContractModel):
    schema_version: Literal["page-ir.v1"] = "page-ir.v1"
    template_ref: Literal["landing-v1"] = "landing-v1"
    page_id: str
    language: Literal["th", "en"]
    slug: str
    title: str
    description: str
    hero_heading: str
    hero_body: str
    hero_cta: LinkSpec
    features: list[FeatureSpec]
    pricing: list[PricingPlan] = Field(default_factory=list)
    faq: list[FaqItem] = Field(default_factory=list)
    secondary_cta: LinkSpec | None = None
    footer_text: str = ""
    theme: ThemeSpec
    hero_image: HeroImageSpec | None = None
    model_calls: Literal[0] = 0
    source_refs: list[str] = Field(default_factory=list)


class ArtifactRef(ContractModel):
    filename: str
    media_type: str
    content_hash: str
    byte_size: int = Field(ge=0)


class ValidationIssue(ContractModel):
    code: str
    path: str
    message: str
