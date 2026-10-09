from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from video_postproduction.media.ffprobe import FFprobeMetadataReader, MediaProbeError


@pytest.fixture
def sample_video(tmp_path: Path) -> Path:
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        pytest.skip("ffmpeg is not installed")

    output = tmp_path / "sample.mp4"
    result = subprocess.run(
        [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=c=black:s=320x240:d=0.5",
            "-an",
            "-pix_fmt",
            "yuv420p",
            "-y",
            str(output),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        pytest.skip(f"ffmpeg fixture generation unavailable: {result.stderr[:200]}")
    return output


def test_ffprobe_reader_parses_duration_and_streams(sample_video):
    ffprobe = shutil.which("ffprobe")
    if ffprobe is None:
        pytest.skip("ffprobe is not installed")

    reader = FFprobeMetadataReader(executable=ffprobe)
    metadata = reader.read(sample_video)

    assert metadata.duration > 0
    assert metadata.video is not None
    assert metadata.video.width > 0
    assert metadata.video.height > 0


def test_ffprobe_reader_uses_typed_invocation(monkeypatch, tmp_path):
    captured: dict[str, object] = {}

    def fake_run(argv, *, capture_output, check, shell, text, timeout):
        captured["argv"] = argv
        captured["capture_output"] = capture_output
        captured["check"] = check
        captured["shell"] = shell
        captured["text"] = text
        captured["timeout"] = timeout
        return subprocess.CompletedProcess(argv, 0, stdout=json.dumps({
            "format": {"duration": "1.25", "format_name": "mov,mp4"},
            "streams": [{
                "codec_type": "video",
                "codec_name": "h264",
                "width": 320,
                "height": 240,
                "avg_frame_rate": "30/1",
            }],
        }), stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    path = tmp_path / "clip.mp4"

    metadata = FFprobeMetadataReader(executable="ffprobe", timeout_seconds=17).read(path)

    assert captured["argv"] == [
        "ffprobe",
        "-v",
        "error",
        "-print_format",
        "json",
        "-show_format",
        "-show_streams",
        str(path),
    ]
    assert captured["shell"] is False
    assert captured["check"] is False
    assert captured["timeout"] == 17
    assert metadata.duration == 1.25
    assert metadata.video is not None
    assert metadata.video.fps == 30.0


def test_ffprobe_reader_redacts_local_path_from_probe_error(monkeypatch, tmp_path):
    path = tmp_path / "private-clip.mp4"
    secret_path = str(path)

    def fake_run(*args, **kwargs):
        return subprocess.CompletedProcess(
            args[0],
            1,
            stdout="",
            stderr=f"unable to open {secret_path}",
        )

    monkeypatch.setattr(subprocess, "run", fake_run)

    with pytest.raises(MediaProbeError) as error:
        FFprobeMetadataReader().read(path)

    assert error.value.code == "media_probe_failed"
    assert secret_path not in error.value.diagnostics
    assert "<media>" in error.value.diagnostics
