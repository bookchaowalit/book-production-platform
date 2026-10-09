import hashlib
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from video_postpipeline.audio_extract import ExtractionError
from video_postpipeline.cli import main
from video_postpipeline.overlay import MANIFEST_NAME, overlay_graphic

FFMPEG_AVAILABLE = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None
pytestmark = pytest.mark.skipif(not FFMPEG_AVAILABLE, reason="ffmpeg/ffprobe are not on PATH")


def _ffmpeg(*args: str) -> None:
    result = subprocess.run(["ffmpeg", "-y", "-v", "error", *args], capture_output=True, text=True,
                            timeout=60, shell=False, check=False)
    assert result.returncode == 0, result.stderr


@pytest.fixture(scope="module")
def media(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Path]:
    root = tmp_path_factory.mktemp("overlay")
    video = root / "clip.mp4"
    _ffmpeg("-f", "lavfi", "-i", "sine=frequency=440:duration=2", "-f", "lavfi", "-i",
            "color=c=black:s=640x360:d=2", "-shortest", str(video))
    silent = root / "silent.mp4"
    _ffmpeg("-f", "lavfi", "-i", "color=c=black:s=640x360:d=1", str(silent))
    logo = root / "logo.png"
    _ffmpeg("-f", "lavfi", "-i", "color=c=red:s=200x100", "-frames:v", "1", str(logo))
    return {"video": video, "silent": silent, "logo": logo}


def test_overlay_publishes_manifest_and_keeps_audio(media: dict[str, Path], tmp_path: Path) -> None:
    result = overlay_graphic(media["video"], media["logo"], tmp_path, position="top-left", width_ratio=0.25)
    manifest = json.loads((tmp_path / MANIFEST_NAME).read_text(encoding="utf-8"))
    assert manifest["schema_version"] == "video-overlay.v1"
    assert manifest["output"]["sha256"] == hashlib.sha256(result.video_path.read_bytes()).hexdigest()
    assert manifest["source_image"]["sha256"] == hashlib.sha256(media["logo"].read_bytes()).hexdigest()
    assert manifest["qa"]["checks"]["audio_preserved"] is True
    assert abs(result.duration_seconds - 2.0) <= 0.5


def test_overlay_without_audio_and_pixel_is_red(media: dict[str, Path], tmp_path: Path) -> None:
    result = overlay_graphic(media["silent"], media["logo"], tmp_path, position="top-left", width_ratio=0.5, margin_px=0)
    frame = tmp_path / "frame.png"
    _ffmpeg("-i", str(result.video_path), "-frames:v", "1", "-vf", "crop=4:4:10:10,scale=1:1", "-f", "rawvideo",
            "-pix_fmt", "rgb24", str(frame))
    red, green, blue = frame.read_bytes()[:3]
    assert red > 180 and green < 80 and blue < 80


@pytest.mark.parametrize("kwargs", [{"position": "middle"}, {"width_ratio": 2.0}, {"margin_px": -1}])
def test_overlay_rejects_bad_placement(media: dict[str, Path], tmp_path: Path, kwargs: dict) -> None:
    with pytest.raises(ExtractionError):
        overlay_graphic(media["video"], media["logo"], tmp_path, **kwargs)


def test_cli_overlay(media: dict[str, Path], tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code = main(["overlay", "--video", str(media["video"]), "--image", str(media["logo"]),
                 "--output-dir", str(tmp_path), "--overlay-id", "branded"])
    assert code == 0
    assert "branded.mp4" in capsys.readouterr().out
