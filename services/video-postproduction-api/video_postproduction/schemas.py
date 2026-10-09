"""Public API schemas for the V1 foundation."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

TranscriptionQuality = Literal["fast", "accurate"]


class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    instructions: str | None = Field(default=None, max_length=10_000)
    transcription_quality: TranscriptionQuality = "fast"


class TranscriptionCapabilities(BaseModel):
    supports_project_quality: bool


class ProjectResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    instructions: str | None
    transcription_quality: TranscriptionQuality
    status: str
    created_at: datetime
    updated_at: datetime


class ProjectListResponse(BaseModel):
    items: list[ProjectResponse]
    limit: int
    offset: int
    total: int


class AssetResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    project_id: UUID
    original_filename: str
    media_type: str
    size_bytes: int
    status: str
    metadata: dict[str, Any]
    created_at: datetime


class AssetListResponse(BaseModel):
    items: list[AssetResponse]
    limit: int
    offset: int
    total: int


class AssetUploadResponse(BaseModel):
    asset_id: UUID
    job_id: UUID
    status: str
    poll_url: str


class JobResponse(BaseModel):
    id: UUID
    project_id: UUID
    asset_id: UUID
    kind: str
    status: str
    progress: int
    error_code: str | None = None
    error_message: str | None = None
    current_stage: str | None = None
    stage_status: str | None = None
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None


class RetryRequest(BaseModel):
    stage: str = Field(default="transcribe", min_length=1, max_length=60)


class TranscriptResponse(BaseModel):
    id: UUID
    project_id: UUID
    asset_id: UUID
    provider: str
    model: str
    language: str | None
    duration: float | None
    normalized: dict[str, Any]
    created_at: datetime


class EDLResponse(BaseModel):
    id: UUID
    project_id: UUID
    source_asset_id: UUID | None
    version: int
    schema_version: str
    document: dict[str, Any]
    status: str
    created_at: datetime
    updated_at: datetime


class RenderRequest(BaseModel):
    edl_id: UUID | None = None


class RenderOutputResponse(BaseModel):
    id: UUID
    project_id: UUID
    edl_id: UUID
    job_id: UUID | None
    status: str
    metadata: dict[str, Any]
    error: str | None
    created_at: datetime
    updated_at: datetime


class ExportRequest(BaseModel):
    edl_id: UUID | None = None


class ExportResponse(BaseModel):
    id: UUID
    project_id: UUID
    edl_id: UUID
    status: str
    trace: dict[str, Any]
    warnings: list[str]


class JobHistoryResponse(BaseModel):
    items: list[dict[str, Any]]
    total: int


class HealthResponse(BaseModel):
    service: str
    status: str
    version: str
