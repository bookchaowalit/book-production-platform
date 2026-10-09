"""Validated, versioned edit decision list models for the post-production engine."""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any, Literal
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    NonNegativeFloat,
    PositiveFloat,
    ValidationInfo,
    field_validator,
    model_validator,
)

SUPPORTED_CAPABILITIES = frozenset({"cut", "captions", "center_reframe"})
FUTURE_CAPABILITIES = frozenset(
    {
        "active_speaker",
        "audio_cleanup",
        "color_correction",
        "color_grade",
        "asset_search",
        "graphic_generation",
        "generated_media",
    }
)
KNOWN_CAPABILITIES = SUPPORTED_CAPABILITIES | FUTURE_CAPABILITIES
VISUAL_KINDS = frozenset({"a_roll", "asset", "stock", "graphic", "generated_video"})


class EDLCapabilityError(ValueError):
    """Raised when an EDL requires a renderer capability that is not available."""


class EDLModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SourceAsset(EDLModel):
    asset_id: UUID
    storage_key: str = Field(min_length=1)
    duration: NonNegativeFloat


class SourceRange(EDLModel):
    asset_id: UUID
    start: NonNegativeFloat
    end: PositiveFloat

    @field_validator("start", "end")
    @classmethod
    def require_finite_timestamp(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("timestamps must be finite")
        return value

    @model_validator(mode="after")
    def end_after_start(self) -> SourceRange:
        if self.end <= self.start:
            raise ValueError("source range end must be greater than start")
        return self


class ReframeSettings(EDLModel):
    mode: Literal["center", "active_speaker"]
    aspect_ratio: Literal["9:16", "16:9", "1:1"]


class VisualInstruction(EDLModel):
    kind: Literal["a_roll", "asset", "stock", "graphic", "generated_video"]
    reframe: ReframeSettings | None = None
    asset_refs: list[str] = Field(default_factory=list)


class CaptionSettings(EDLModel):
    enabled: bool = True
    source: Literal["transcript", "manual"] = "transcript"
    format: Literal["srt", "ass"] = "ass"
    preset: Literal["minimal", "bold", "karaoke", "emphasis"] = "minimal"


class OperationList(EDLModel):
    operations: list[dict[str, Any]] = Field(default_factory=list)


class TimelineSegment(EDLModel):
    id: str = Field(min_length=1)
    source: SourceRange
    visual: VisualInstruction
    captions: CaptionSettings
    audio: OperationList
    color: OperationList
    provenance: dict[str, Any] = Field(default_factory=dict)


class EDLAssetReference(EDLModel):
    id: str = Field(min_length=1)
    kind: Literal["asset", "stock", "graphic", "generated_video", "music", "sfx"]
    storage_key: str | None = None
    provenance: dict[str, Any] = Field(default_factory=dict)


class OutputSettings(EDLModel):
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    fps: PositiveFloat
    format: Literal["mp4"] = "mp4"
    render_profile: Literal["vertical_centered", "source"] = "vertical_centered"


class RequiredCapabilities(EDLModel):
    required: list[str] = Field(default_factory=list)

    @field_validator("required")
    @classmethod
    def require_known_capabilities(cls, values: list[str]) -> list[str]:
        unknown = sorted(set(values) - KNOWN_CAPABILITIES)
        if unknown:
            raise ValueError(f"unknown EDL capabilities: {', '.join(unknown)}")
        return list(dict.fromkeys(values))


class EDLDocument(EDLModel):
    version: Literal[1]
    project_id: UUID
    source_assets: list[SourceAsset] = Field(min_length=1)
    timeline: list[TimelineSegment] = Field(min_length=1)
    assets: list[EDLAssetReference]
    output: OutputSettings
    capabilities: RequiredCapabilities

    @model_validator(mode="after")
    def validate_source_references(self, info: ValidationInfo) -> EDLDocument:
        asset_ids = {asset.asset_id for asset in self.source_assets}
        durations = {asset.asset_id: asset.duration for asset in self.source_assets}
        context = info.context or {}
        durations.update(context.get("asset_durations", {}))
        for segment in self.timeline:
            source = segment.source
            if source.asset_id not in asset_ids:
                raise ValueError(f"timeline references unknown source asset: {source.asset_id}")
            duration = durations.get(source.asset_id)
            if duration is not None and source.end > duration:
                raise ValueError(
                    f"timeline range ends at {source.end}, beyond asset duration {duration}"
                )
        return self


def validate_edl(
    document: Mapping[str, Any] | EDLDocument,
    asset_durations: Mapping[UUID, float] | None = None,
) -> EDLDocument:
    """Validate an EDL document and the capabilities available in V1."""

    if isinstance(document, EDLDocument):
        validated = document
    else:
        validated = EDLDocument.model_validate(
            document,
            context={"asset_durations": dict(asset_durations or {})},
        )
    required = set(validated.capabilities.required)
    unsupported = sorted(required - SUPPORTED_CAPABILITIES)
    if unsupported:
        raise EDLCapabilityError(
            "renderer does not support required capabilities: " + ", ".join(unsupported)
        )
    return validated


def schema_is_compatible(schema: Mapping[str, Any]) -> bool:
    """Check the shared JSON Schema's public model contract against Pydantic."""

    generated = EDLDocument.model_json_schema()
    generated_properties = set(generated["properties"])
    schema_properties = set(schema["properties"])
    if generated_properties != schema_properties:
        return False
    if set(generated["required"]) != set(schema["required"]):
        return False
    if generated["properties"]["version"]["const"] != schema["properties"]["version"]["const"]:
        return False

    generated_kinds = set(
        generated["$defs"]["VisualInstruction"]["properties"]["kind"]["enum"]
    )
    schema_kinds = set(schema["$defs"]["visual"]["properties"]["kind"]["enum"])
    return generated_kinds == schema_kinds
