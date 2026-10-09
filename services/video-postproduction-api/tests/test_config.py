from __future__ import annotations

from video_postproduction.config import Settings


def test_empty_transcription_language_means_auto_detect():
    assert Settings(transcription_language="").transcription_language is None
    assert Settings(transcription_language=" th ").transcription_language == "th"


def test_empty_video_editor_api_token_means_authentication_is_unconfigured():
    assert Settings(video_editor_api_token="").video_editor_api_token is None


def test_empty_video_editor_api_token_from_environment_is_unconfigured(monkeypatch):
    monkeypatch.setenv("VIDEO_EDITOR_API_TOKEN", "")

    assert Settings().video_editor_api_token is None
