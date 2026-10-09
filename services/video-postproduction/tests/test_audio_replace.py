import hashlib
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from video_postpipeline.audio_extract import ExtractionError
from video_postpipeline.audio_replace import MANIFEST_NAME, replace_audio
from video_postpipeline.cli import main

FFMPEG_AVAILABLE = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None
pytestmark = pytest.mark.skipif(not FFMPEG_AVAILABLE, reason="ffmpeg/ffprobe are not on PATH")


def _ffmpeg(*args: str) -> None:
    result = subprocess.run(["ffmpeg", "-y", "-v", "error", *args], capture_output=True, text=True,
                            timeout=60, shell=False, check=False)
    assert result.returncode == 0, result.stderr


@pytest.fixture(scope="module")
def media(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Path]:
    root = tmp_path_factory.mktemp("remux")
    video = root / "clip.mp4"
    _ffmpeg("-f", "lavfi", "-i", "sine=frequency=440:duration=2", "-f", "lavfi", "-i",
            "color=c=black:s=320x240:d=2", "-shortest", str(video))
    master = root / "master.wav"
    _ffmpeg("-f", "lavfi", "-i", "sine=frequency=660:duration=3", "-c:a", "pcm_s24le", "-ar", "48000", str(master))
    short = root / "short.wav"
    _ffmpeg("-f", "lavfi", "-i", "sine=frequency=660:duration=0.5", "-c:a", "pcm_s24le", "-ar", "48000", str(short))
    silent = root / "silent.mp4"
    _ffmpeg("-f", "lavfi", "-i", "color=c=black:s=320x240:d=1", str(silent))
    return {"video": video, "master": master, "short": short, "silent": silent}


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_replace_audio_publishes_verified_manifest(media: dict[str, Path], tmp_path: Path) -> None:
    result = replace_audio(media["video"], media["master"], tmp_path)
    assert result.video_path.name == "clip-remastered.mp4"
    assert abs(result.duration_seconds - 2.0) <= 0.5
    manifest = json.loads((tmp_path / MANIFEST_NAME).read_text(encoding="utf-8"))
    assert manifest["schema_version"] == "video-remux.v1"
    assert manifest["status"] == "accepted"
    assert manifest["output"]["sha256"] == _sha(result.video_path)
    assert manifest["source_audio"]["sha256"] == _sha(media["master"])
    assert manifest["source_video"]["sha256"] == _sha(media["video"])
    assert all(manifest["qa"]["checks"].values())
    assert not list(tmp_path.glob(".*partial*"))


def test_short_master_is_padded_to_video_length(media: dict[str, Path], tmp_path: Path) -> None:
    result = replace_audio(media["video"], media["short"], tmp_path)
    assert abs(result.duration_seconds - 2.0) <= 0.5


def test_rejects_inputs_without_required_streams(media: dict[str, Path], tmp_path: Path) -> None:
    with pytest.raises(ExtractionError, match="audio_stream_missing"):
        replace_audio(media["video"], media["silent"], tmp_path)
    with pytest.raises(ExtractionError, match="video_stream_missing"):
        replace_audio(media["master"], media["master"], tmp_path)
    with pytest.raises(ExtractionError, match="input_missing"):
        replace_audio(tmp_path / "none.mp4", media["master"], tmp_path)
    assert not (tmp_path / MANIFEST_NAME).exists()


def test_cli_replace_audio(media: dict[str, Path], tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code = main(["replace-audio", "--video", str(media["video"]), "--audio", str(media["master"]),
                 "--output-dir", str(tmp_path), "--remux-id", "final cut"])
    assert code == 0
    assert "final-cut.mp4" in capsys.readouterr().out
    assert main(["replace-audio", "--video", str(tmp_path / "x.mp4"), "--audio", str(media["master"]),
                 "--output-dir", str(tmp_path)]) == 4
