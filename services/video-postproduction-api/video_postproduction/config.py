"""Environment-backed runtime settings."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    database_url: str = "sqlite:///services/video-postproduction-api/.runtime/video-postproduction.db"
    redis_url: str = "redis://localhost:6379/0"
    storage_root: Path = Path("services/video-postproduction-api/.runtime/storage")
    max_upload_bytes: int = Field(default=2 * 1024 * 1024 * 1024, gt=0)
    allowed_video_mime_types: str = "video/mp4,video/quicktime,video/webm,video/x-matroska"
    cors_allowed_origins: str = "http://127.0.0.1:3400,http://localhost:3400"
    transcription_provider: str = "faster_whisper"
    transcription_language: str | None = None
    whisper_model: str = "small"
    whisper_device: str = "cpu"
    openai_compatible_base_url: str | None = None
    openai_compatible_transcription_model: str = "gpt-4o-mini-transcribe"
    video_editor_api_token: str | None = None
    ffprobe_executable: str = "ffprobe"
    ffmpeg_executable: str = "ffmpeg"
    job_lease_seconds: int = Field(default=900, gt=0)
    max_stage_attempts: int = Field(default=3, ge=1, le=10)

    @field_validator("transcription_language", mode="before")
    @classmethod
    def normalize_transcription_language(cls, value: object) -> object:
        if isinstance(value, str):
            normalized = value.strip()
            return normalized or None
        return value

    @field_validator("video_editor_api_token", mode="before")
    @classmethod
    def normalize_video_editor_api_token(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            return None
        return value

    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="",
        extra="ignore",
        case_sensitive=False,
    )

    @property
    def allowed_mime_types(self) -> frozenset[str]:
        return frozenset(item.strip().lower() for item in self.allowed_video_mime_types.split(",") if item.strip())

    @property
    def cors_origins(self) -> list[str]:
        return [item.strip() for item in self.cors_allowed_origins.split(",") if item.strip()]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
