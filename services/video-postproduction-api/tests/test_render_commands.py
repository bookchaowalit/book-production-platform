from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest

from video_postproduction.edl import validate_edl
from video_postproduction.render.base import RenderCapabilityError
from video_postproduction.render.ffmpeg import FFmpegRenderEngine, build_ffmpeg_argv


def sample_edl():
    asset_id = uuid4()
    return validate_edl(
        {
            "version": 1,
            "project_id": uuid4(),
            "source_assets": [{"asset_id": asset_id, "storage_key": "source.mp4", "duration": 20}],
            "timeline": [
                {
                    "id": "segment_0001",
                    "source": {"asset_id": asset_id, "start": 0, "end": 10},
                    "visual": {"kind": "a_roll", "reframe": {"mode": "center", "aspect_ratio": "9:16"}, "asset_refs": []},
                    "captions": {"enabled": True, "source": "transcript", "format": "ass", "preset": "minimal"},
                    "audio": {"operations": []},
                    "color": {"operations": []},
                    "provenance": {},
                }
            ],
            "assets": [],
            "output": {"width": 1080, "height": 1920, "fps": 30, "format": "mp4"},
            "capabilities": {"required": ["cut", "captions", "center_reframe"]},
        }
    )


def test_ffmpeg_builder_returns_argv_without_shell_interpolation():
    argv = build_ffmpeg_argv(
        sample_edl(),
        input_path=Path("source.mp4"),
        output_path=Path("out.mp4"),
        caption_path=Path("captions.ass"),
    )

    assert argv[0] == "ffmpeg"
    assert all(";" not in item for item in argv)
    assert "-vf" in argv
    assert any("crop=ih*9/16:ih:(iw-ih*9/16)/2:0,scale=1080:1920" in item for item in argv)
    assert argv[-1] == "out.mp4"


def test_renderer_fails_before_process_for_unknown_capability(monkeypatch):
    edl = sample_edl()
    edl.capabilities.required.append("active_speaker")

    def fail_if_called(*args, **kwargs):
        raise AssertionError("ffmpeg must not start for unsupported capabilities")

    monkeypatch.setattr("subprocess.run", fail_if_called)
    engine = FFmpegRenderEngine()

    with pytest.raises(RenderCapabilityError, match="active_speaker"):
        engine.render(edl, input_path=Path("source.mp4"), output_path=Path("out.mp4"))


def landscape_edl():
    document = sample_edl().model_dump(mode="json")
    document["timeline"][0]["visual"]["reframe"] = None
    document["output"] = {"width": 1920, "height": 1080, "fps": 30, "format": "mp4", "render_profile": "source"}
    document["capabilities"] = {"required": ["cut", "captions"]}
    return validate_edl(document)


def test_ffmpeg_builder_renders_landscape_without_crop_or_distortion():
    argv = build_ffmpeg_argv(landscape_edl(), input_path=Path("source.mp4"), output_path=Path("out.mp4"))
    video_filter = argv[argv.index("-vf") + 1]

    assert "crop=" not in video_filter
    assert video_filter.startswith(
        "scale=1920:1080:force_original_aspect_ratio=decrease,pad=1920:1080:(ow-iw)/2:(oh-ih)/2,setsar=1"
    )
