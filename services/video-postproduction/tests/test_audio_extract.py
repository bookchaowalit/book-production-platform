import json
import shutil
import subprocess
from pathlib import Path

import jsonschema
import pytest

from video_postpipeline.audio_extract import (
    AUDIO_BATCH_SCHEMA_PATH,
    ExtractionError,
    build_audio_batch_manifest,
    extract_audio,
    safe_pipeline_id,
)

FFMPEG_AVAILABLE = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None
pytestmark = pytest.mark.skipif(not FFMPEG_AVAILABLE, reason="ffmpeg/ffprobe are not on PATH")


@pytest.fixture(scope="module")
def synthetic_video(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Generate a bounded one-second synthetic MP4 (sine beep on black video)."""

    target = tmp_path_factory.mktemp("media") / "synth.mp4"
    argv = [
        "ffmpeg", "-y", "-v", "error",
        "-f", "lavfi", "-i", "sine=frequency=440:duration=1",
        "-f", "lavfi", "-i", "color=c=black:s=320x240:d=1",
        "-shortest", str(target),
    ]
    result = subprocess.run(argv, capture_output=True, text=True, timeout=60, shell=False, check=False)
    assert result.returncode == 0, result.stderr
    return target


def test_extract_wav_publishes_conforming_audio_batch_manifest(synthetic_video: Path, tmp_path: Path):
    result = extract_audio(synthetic_video, tmp_path, audio_format="wav")
    assert result.audio_path.name == "synth.wav"
    assert result.codec_name == "pcm_s24le"
    assert 0.5 <= result.duration_seconds <= 2.0
    assert result.manifest_path is not None and result.manifest_path.name == "audio-batch.v1.json"

    manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
    assert manifest == build_audio_batch_manifest(batch_id="synth", track_id="synth", source_name="synth.wav")
    schema = json.loads(AUDIO_BATCH_SCHEMA_PATH.read_text(encoding="utf-8"))
    jsonschema.validate(manifest, schema)
    track = manifest["tracks"][0]
    assert track["output_format"] == "wav_pcm24"
    assert track["source"] == "synth.wav"
    assert track["targets"] == {"integrated_lufs": -16.0, "true_peak_dbtp": -1.0, "lra_lu": None}


def test_extract_mp3_is_a_preview_without_batch_manifest(synthetic_video: Path, tmp_path: Path):
    result = extract_audio(synthetic_video, tmp_path, audio_format="mp3")
    assert result.codec_name == "mp3"
    assert result.manifest_path is None
    assert not (tmp_path / "audio-batch.v1.json").exists()


def test_missing_input_has_a_stable_error_code(tmp_path):
    with pytest.raises(ExtractionError) as excinfo:
        extract_audio(tmp_path / "missing.mp4", tmp_path)
    assert excinfo.value.code == "input_missing"


def test_unsupported_format_has_a_stable_error_code(synthetic_video: Path, tmp_path: Path):
    with pytest.raises(ExtractionError) as excinfo:
        extract_audio(synthetic_video, tmp_path, audio_format="aac")
    assert excinfo.value.code == "unsupported_format"


def test_invalid_timeout_has_a_stable_error_code(synthetic_video: Path, tmp_path: Path):
    with pytest.raises(ExtractionError) as excinfo:
        extract_audio(synthetic_video, tmp_path, timeout_seconds=0)
    assert excinfo.value.code == "invalid_timeout"


def test_safe_pipeline_id_sanitizes_and_bounds():
    assert safe_pipeline_id("My Episode #1") == "My-Episode-1"
    assert safe_pipeline_id("///") == "track"
    assert len(safe_pipeline_id("a" * 100)) == 64
