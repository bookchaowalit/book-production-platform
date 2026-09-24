from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from book_production_platform.audio_ir import AudioIR


def request(**changes: object) -> dict[str, object]:
    return {
        "schema_version": "audio-ir.v1",
        "audio_id": "demo-voice",
        "source_path": "voice.wav",
        "preset": "podcast-mono.v1",
        **changes,
    }


def test_audio_ir_is_versioned_and_allows_only_named_presets() -> None:
    ir = AudioIR.model_validate(request())

    assert ir.schema_version == "audio-ir.v1"
    assert ir.preset == "podcast-mono.v1"

    with pytest.raises(ValidationError):
        AudioIR.model_validate(request(preset="custom-filter.v1"))


def test_audio_ir_rejects_unknown_fields_and_non_wav_source() -> None:
    with pytest.raises(ValidationError):
        AudioIR.model_validate(request(ffmpeg_filter="volume=100"))
    with pytest.raises(ValidationError):
        AudioIR.model_validate(request(source_path="voice.mp3"))


def test_json_schema_matches_the_allowlisted_audio_ir_contract() -> None:
    schema_path = Path(__file__).parents[2] / "src" / "book_production_platform" / "audio_ir" / "audio-ir.v1.schema.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))

    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == {"schema_version", "audio_id", "source_path", "preset"}
    assert schema["properties"]["preset"]["enum"] == [
        "podcast-mono.v1",
        "podcast-stereo.v1",
        "music-stereo.v1",
    ]
