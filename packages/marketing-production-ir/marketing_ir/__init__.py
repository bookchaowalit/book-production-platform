"""Versioned contracts for the deterministic Marketing V1A product."""

from .models import (
    ArtifactRef,
    AssetSpec,
    AudienceRule,
    AudienceSpec,
    CampaignIR,
    CampaignRequest,
    UtmSpec,
    ValidationIssue,
)
from .validation import compile_campaign, validate_request

__all__ = [
    "ArtifactRef",
    "AssetSpec",
    "AudienceRule",
    "AudienceSpec",
    "CampaignIR",
    "CampaignRequest",
    "UtmSpec",
    "ValidationIssue",
    "compile_campaign",
    "validate_request",
]
