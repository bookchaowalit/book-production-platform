from __future__ import annotations

from types import SimpleNamespace

import httpx
import pytest

from video_postproduction.config import Settings
from video_postproduction.media.models import MediaMetadata, VideoStreamMetadata
from video_postproduction.transcription.base import TranscriptionProviderError
from video_postproduction.transcription.faster_whisper import FasterWhisperProvider
from video_postproduction.transcription.openai_compatible import (
    OpenAICompatibleTranscriptionProvider,
)


def sample_metadata() -> MediaMetadata:
    return MediaMetadata(
        duration=2,
        format_name="mp4",
        size_bytes=10,
        bitrate=None,
        video=VideoStreamMetadata(codec_name="h264", width=320, height=240),
    )


def test_openai_compatible_provider_sends_audio_and_timestamp_options(tmp_path):
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "language": "th",
                "segments": [{"start": 0, "end": 2, "text": "สวัสดี"}],
            },
            request=request,
        )

    audio_path = tmp_path / "audio.wav"
    audio_path.write_bytes(b"audio")
    client = httpx.Client(
        transport=httpx.MockTransport(handler),
        base_url="https://gateway.test/v1",
    )
    settings = Settings(
        openai_compatible_base_url="https://gateway.test/v1",
        openai_compatible_transcription_model="local-whisper",
    )
    provider = OpenAICompatibleTranscriptionProvider(settings, client=client)

    result = provider.transcribe(audio_path, sample_metadata(), language="th")

    assert result.provider == "openai-compatible"
    assert result.model == "local-whisper"
    assert result.language == "th"
    assert result.segments[0].text == "สวัสดี"
    assert len(requests) == 1
    request = requests[0]
    assert request.method == "POST"
    assert request.url.path == "/v1/audio/transcriptions"
    body = request.content.decode("utf-8", errors="ignore")
    assert "local-whisper" in body
    assert "word" in body
    assert "audio.wav" in body
    client.close()


def test_openai_compatible_provider_hides_error_body(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(502, content=b"private provider response", request=request)

    audio_path = tmp_path / "audio.wav"
    audio_path.write_bytes(b"audio")
    client = httpx.Client(
        transport=httpx.MockTransport(handler),
        base_url="https://gateway.test/v1",
    )
    provider = OpenAICompatibleTranscriptionProvider(
        Settings(openai_compatible_base_url="https://gateway.test/v1"),
        client=client,
    )

    with pytest.raises(TranscriptionProviderError) as error:
        provider.transcribe(audio_path, sample_metadata())

    assert error.value.code == "transcription_http_error"
    assert "private provider response" not in error.value.diagnostics
    client.close()


def test_faster_whisper_loads_lazily_and_maps_optional_words(tmp_path):
    calls: list[tuple[str, str]] = []

    class FakeModel:
        def transcribe(self, path, **kwargs):
            assert path == str(tmp_path / "audio.wav")
            assert kwargs["word_timestamps"] is True
            return iter(
                [
                    SimpleNamespace(
                        start=0,
                        end=2,
                        text="hello",
                        words=[SimpleNamespace(word="hello", start=0, end=2, probability=0.9)],
                    )
                ]
            ), SimpleNamespace(language="en")

    def factory(model_name: str, device: str):
        calls.append((model_name, device))
        return FakeModel()

    audio_path = tmp_path / "audio.wav"
    audio_path.write_bytes(b"audio")
    provider = FasterWhisperProvider(
        model_name="tiny",
        device="cpu",
        model_factory=factory,
    )

    assert calls == []
    result = provider.transcribe(audio_path, sample_metadata())

    assert calls == [("tiny", "cpu")]
    assert result.language == "en"
    assert result.segments[0].words[0].text == "hello"


def test_faster_whisper_reuses_loaded_model_across_provider_instances(monkeypatch, tmp_path):
    from video_postproduction.transcription import faster_whisper

    calls: list[tuple[str, str]] = []

    class FakeModel:
        def transcribe(self, path, **kwargs):
            return iter([]), SimpleNamespace(language="en")

    class FakeWhisperModule:
        @staticmethod
        def WhisperModel(model_name, *, device):
            calls.append((model_name, device))
            return FakeModel()

    monkeypatch.setattr(faster_whisper, "_MODEL_CACHE", {})
    monkeypatch.setattr(faster_whisper, "import_module", lambda _: FakeWhisperModule)
    audio_path = tmp_path / "audio.wav"
    audio_path.write_bytes(b"audio")
    providers = [
        FasterWhisperProvider(model_name="small", device="cpu"),
        FasterWhisperProvider(model_name="small", device="cpu"),
        FasterWhisperProvider(model_name="medium", device="cpu"),
    ]

    for provider in providers:
        provider.transcribe(audio_path, sample_metadata())

    assert calls == [("small", "cpu"), ("medium", "cpu")]


@pytest.mark.parametrize(("quality", "model_name"), [("fast", "small"), ("accurate", "medium")])
def test_worker_selects_faster_whisper_model_for_project_quality(monkeypatch, quality, model_name):
    from video_postproduction.jobs import tasks

    configured: list[str] = []
    monkeypatch.setattr(tasks, "get_settings", lambda: Settings(transcription_provider="faster_whisper"))

    def provider_factory(settings, *, model_name):
        configured.append(model_name)
        return object()

    monkeypatch.setattr(tasks, "FasterWhisperProvider", provider_factory)

    tasks._provider(quality)

    assert configured == [model_name]
