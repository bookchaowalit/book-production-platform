"""Versioned contracts for the deterministic Web V1A product."""

from .models import (
    ArtifactRef,
    FaqItem,
    FeatureSpec,
    HeroImageSpec,
    LinkSpec,
    PageIR,
    PageRequest,
    PricingPlan,
    ThemeSpec,
    ValidationIssue,
)
from .validation import compile_page, validate_request

__all__ = [
    "ArtifactRef",
    "FaqItem",
    "FeatureSpec",
    "HeroImageSpec",
    "LinkSpec",
    "PageIR",
    "PageRequest",
    "PricingPlan",
    "ThemeSpec",
    "ValidationIssue",
    "compile_page",
    "validate_request",
]
