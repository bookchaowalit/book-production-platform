"""Pydantic models for the versioned Marketing V1A contract."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

CampaignChannel = Literal["facebook", "instagram", "linkedin", "email", "line", "web"]
CampaignObjective = Literal["awareness", "consideration", "conversion", "retention"]
AssetKind = Literal["copy", "image", "video", "landing_page", "email"]
AudienceField = Literal["location", "language", "interest", "lifecycle", "custom"]
RuleOperator = Literal["equals", "contains", "in"]


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=False)


class AudienceRule(ContractModel):
    field: AudienceField
    operator: RuleOperator
    value: str = Field(min_length=1, max_length=500)


class AudienceSpec(ContractModel):
    name: str = Field(min_length=1, max_length=120)
    rules: list[AudienceRule] = Field(min_length=1, max_length=20)


class AssetSpec(ContractModel):
    asset_id: str = Field(min_length=1, max_length=80)
    kind: AssetKind
    channel: CampaignChannel
    uri: str = Field(min_length=1, max_length=2_000)
    text: str | None = Field(default=None, max_length=10_000)
    # A/B variant label (added in campaign-package-v2). Omitted from hashes and
    # packages while unset, so v1 requests keep their exact bytes.
    variant: str | None = Field(default=None, pattern=r"^[A-Z]$")


class UtmSpec(ContractModel):
    """Tracked-link builder input; the engine appends utm_* per channel and asset."""

    landing_url: str = Field(min_length=9, max_length=2_000)
    campaign: str | None = Field(default=None, min_length=1, max_length=100, pattern=r"^[A-Za-z0-9._-]+$")
    term: str | None = Field(default=None, min_length=1, max_length=100, pattern=r"^[A-Za-z0-9._-]+$")


class CampaignRequest(ContractModel):
    schema_version: Literal["campaign-request.v1"] = "campaign-request.v1"
    campaign_id: str = Field(min_length=1, max_length=80)
    name: str = Field(min_length=1, max_length=200)
    objective: CampaignObjective
    channels: list[CampaignChannel] = Field(min_length=1, max_length=6)
    audience: AudienceSpec
    assets: list[AssetSpec] = Field(min_length=1, max_length=100)
    asset_root: str | None = None
    utm: UtmSpec | None = None


class CampaignIR(ContractModel):
    schema_version: Literal["campaign-ir.v1"] = "campaign-ir.v1"
    campaign_id: str
    name: str
    objective: CampaignObjective
    channels: list[CampaignChannel]
    audience: AudienceSpec
    assets: list[AssetSpec]
    source_refs: list[str] = Field(default_factory=list)
    assumptions: dict[str, str] = Field(default_factory=dict)
    utm: UtmSpec | None = None
    publish_enabled: Literal[False] = False
    spend_enabled: Literal[False] = False


class ArtifactRef(ContractModel):
    filename: str
    media_type: str
    content_hash: str
    byte_size: int = Field(ge=0)


class ValidationIssue(ContractModel):
    code: str
    path: str
    message: str
