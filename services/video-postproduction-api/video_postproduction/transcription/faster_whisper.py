"""Lazy faster-whisper provider integration."""

from __future__ import annotations

from collections.abc import Callable
from importlib import import_module
from pathlib import Path
from threading import Lock
from typing import Any

from ..config import Settings
from ..media.models import MediaMetadata
from .base import TranscriptionProviderError
from .models import NormalizedTranscript
from .normalize import normalize_provider_result

_MODEL_CACHE: dict[tuple[str, str], Any] = {}
_MODEL_CACHE_LOCK = Lock()


def _cached_model(model_name: str, device: str) -> Any:
    key = (model_name, device)
    with _MODEL_CACHE_LOCK:
        if key in _MODEL_CACHE:
            return _MODEL_CACHE[key]
        try:
            whisper_module = import_module("faster_whisper")
        except ImportError as exc:
            raise TranscriptionProviderError(
                "faster_whisper_unavailable",
                "The faster-whisper package is not installed",
            ) from exc
        try:
            model = whisper_module.WhisperModel(model_name, device=device)
        except Exception as exc:
            raise TranscriptionProviderError(
                "faster_whisper_configuration_error",
                "The faster-whisper model could not be loaded",
            ) from exc
        _MODEL_CACHE[key] = model
        return model


class FasterWhisperProvider:
    """Load faster-whisper only when the first transcription job runs."""

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        model_name: str | None = None,
        device: str | None = None,
        model_factory: Callable[[str, str], Any] | None = None,
    ) -> None:
        runtime_settings = settings or Settings()
        self.model_name = model_name or runtime_settings.whisper_model
        self.device = device or runtime_settings.whisper_device
        self._model_factory = model_factory
        self._model: Any | None = None

    def _load_model(self) -> Any:
        if self._model is not None:
            return self._model
        if self._model_factory is not None:
            self._model = self._model_factory(self.model_name, self.device)
            return self._model
        self._model = _cached_model(self.model_name, self.device)
        return self._model

    def transcribe(
        self,
        audio_path: str | Path,
        metadata: MediaMetadata,
        language: str | None = None,
    ) -> NormalizedTranscript:
        model = self._load_model()
        try:
            provider_segments, info = model.transcribe(
                str(audio_path),
                language=language,
                word_timestamps=True,
            )
            raw_segments: list[dict[str, Any]] = []
            for segment in provider_segments:
                raw_words = []
                for word in getattr(segment, "words", None) or []:
                    raw_words.append(
                        {
                            "word": getattr(word, "word", ""),
                            "start": getattr(word, "start", None),
                            "end": getattr(word, "end", None),
                            "probability": getattr(word, "probability", None),
                        }
                    )
                raw_segments.append(
                    {
                        "start": getattr(segment, "start", None),
                        "end": getattr(segment, "end", None),
                        "text": getattr(segment, "text", ""),
                        "words": raw_words,
                    }
                )
        except TranscriptionProviderError:
            raise
        except Exception as exc:
            raise TranscriptionProviderError(
                "faster_whisper_transcription_failed",
                "Local transcription failed",
            ) from exc

        detected_language = getattr(info, "language", None)
        return normalize_provider_result(
            {"segments": raw_segments, "language": detected_language},
            metadata.duration,
            provider="faster_whisper",
            model=self.model_name,
            language=language,
        )
