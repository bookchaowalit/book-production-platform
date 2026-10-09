"""OpenAI-compatible transcription adapter with injectable HTTP transport."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx

from ..config import Settings
from ..media.models import MediaMetadata
from .base import TranscriptionProviderError
from .models import NormalizedTranscript
from .normalize import normalize_provider_result


class OpenAICompatibleTranscriptionProvider:
    """Send only the selected audio file to an OpenAI-compatible endpoint."""

    def __init__(
        self,
        settings: Settings,
        *,
        client: httpx.Client | None = None,
        endpoint: str = "/audio/transcriptions",
    ) -> None:
        if not settings.openai_compatible_base_url:
            raise TranscriptionProviderError(
                "transcription_provider_not_configured",
                "The OpenAI-compatible transcription endpoint is not configured",
            )
        self.model_name = settings.openai_compatible_transcription_model
        self.endpoint = endpoint
        self._owns_client = client is None
        self.client = client or httpx.Client(
            base_url=settings.openai_compatible_base_url.rstrip("/"),
            timeout=60,
        )

    def close(self) -> None:
        if self._owns_client:
            self.client.close()

    def transcribe(
        self,
        audio_path: str | Path,
        metadata: MediaMetadata,
        language: str | None = None,
    ) -> NormalizedTranscript:
        path = Path(audio_path)
        form_data: dict[str, str | list[str]] = {
            "model": self.model_name,
            "response_format": "json",
            "timestamp_granularities[]": ["segment", "word"],
        }
        if language:
            form_data["language"] = language
        try:
            with path.open("rb") as audio_file:
                response = self.client.post(
                    self.endpoint,
                    data=form_data,
                    files={"file": (path.name, audio_file, "application/octet-stream")},
                )
        except FileNotFoundError as exc:
            raise TranscriptionProviderError(
                "transcription_audio_unavailable",
                "The selected audio file is unavailable",
            ) from exc
        except (OSError, httpx.HTTPError) as exc:
            raise TranscriptionProviderError(
                "transcription_transport_error",
                "The transcription provider could not be reached",
            ) from exc

        if not 200 <= response.status_code < 300:
            raise TranscriptionProviderError(
                "transcription_http_error",
                "The transcription provider returned an error",
                diagnostics=f"status={response.status_code}",
            )
        try:
            payload: Any = response.json()
        except (ValueError, json.JSONDecodeError) as exc:
            raise TranscriptionProviderError(
                "transcription_invalid_response",
                "The transcription provider returned invalid JSON",
            ) from exc
        if not isinstance(payload, dict):
            raise TranscriptionProviderError(
                "transcription_invalid_response",
                "The transcription provider returned an invalid payload",
            )
        return normalize_provider_result(
            payload,
            metadata.duration,
            provider="openai-compatible",
            model=self.model_name,
            language=language,
        )
