"""Trace + manual guide for overlay and replace-audio, with ffprobe/ffmpeg faked (no media tools needed)."""

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest
from production_trace import read_history, verify_trace

from video_postpipeline import audio_replace, job_trace, overlay
from video_postpipeline.audio_extract import ExtractionError

VIDEO_PROBE = {
    "streams": [
        {"codec_type": "video", "codec_name": "h264", "width": 1920, "height": 1080, "avg_frame_rate": "30/1", "bit_rate": "8000000"},
        {"codec_type": "audio", "codec_name": "aac"},
    ],
    "format": {"duration": "10.0"},
}


def _fake_probe(path: Path, timeout: float) -> dict[str, Any]:
    name = Path(path).name
    if name.endswith(".png"):
        return {"streams": [{"codec_type": "video", "width": 400, "height": 200}], "format": {}}
    if name.endswith(".wav"):
        return {"streams": [{"codec_type": "audio", "codec_name": "pcm_s24le"}], "format": {"duration": "12.0"}}
    return VIDEO_PROBE


def _fake_run(returncode: int = 0):
    def run(argv: list[str], timeout: float, *, code_prefix: str) -> subprocess.CompletedProcess[str]:
        if returncode == 0:
            Path(argv[-1]).write_bytes(b"fake-mp4-output")
        return subprocess.CompletedProcess(argv, returncode, "", "")

    return run


@pytest.fixture
def inputs(tmp_path: Path) -> dict[str, Path]:
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"video-bytes")
    logo = tmp_path / "logo.png"
    logo.write_bytes(b"png-bytes")
    master = tmp_path / "master.wav"
    master.write_bytes(b"wav-bytes")
    return {"video": video, "logo": logo, "master": master}


@pytest.fixture
def fake_tools(monkeypatch: pytest.MonkeyPatch) -> None:
    for module in (overlay, audio_replace):
        monkeypatch.setattr(module, "_probe", _fake_probe)
        monkeypatch.setattr(module, "_run_tool", _fake_run())


def test_overlay_writes_trace_history_and_resolve_guide(inputs: dict[str, Path], tmp_path: Path, fake_tools: None) -> None:
    out = tmp_path / "out"
    result = overlay.overlay_graphic(inputs["video"], inputs["logo"], out, position="bottom-right", width_ratio=0.2, margin_px=24)
    trace = json.loads((out / "trace.json").read_text(encoding="utf-8"))
    assert trace["tool_id"] == "video-studio.overlay" and trace["status"] == "accepted"
    assert [item["name"] for item in trace["inputs"]] == ["source_video", "graphic"]
    assert {step["stage"] for step in trace["steps"]} >= {"validate", "probe", "render", "qa", "publish"}
    render = next(step for step in trace["steps"] if step["stage"] == "render")
    assert render["params"]["options"]["crf"] == "20" and render["params"]["inputs"] == 2
    assert str(tmp_path) not in json.dumps(trace)
    assert {artifact["file"] for artifact in trace["artifacts"]} == {result.video_path.name, "video-overlay.json"}
    manual = (out / "MANUAL-STEPS.md").read_text(encoding="utf-8")
    assert "| 2 | V2 (Video 2) |" in manual
    assert "bottom-right: 384x192 px ที่ x=1512, y=864" in manual
    assert "`0.200`" in manual and "`X 744, Y -420`" in manual
    assert "00:00:10:00 (= 00:00:10.000)" in manual
    history = read_history(out)
    assert history[-1]["status"] == "accepted" and history[-1]["artifacts"] == [result.video_path.name, "video-overlay.json"]
    assert verify_trace(out, trace["job_id"])["ok"] is True
    result.video_path.write_bytes(b"tampered")
    assert verify_trace(out, trace["job_id"])["ok"] is False


@pytest.mark.parametrize(
    ("position", "x", "y"),
    [("top-left", 24, 24), ("top-right", 1512, 24), ("bottom-left", 24, 864), ("bottom-center", 768, 864)],
)
def test_overlay_placement_mirrors_ffmpeg(position: str, x: int, y: int) -> None:
    facts = job_trace.video_facts(VIDEO_PROBE)
    placement = job_trace.overlay_placement(facts, (400, 200), target_width=384, position=position, margin=24)
    assert (placement["x"], placement["y"], placement["height"]) == (x, y, 192)


def test_overlay_failure_is_traced(inputs: dict[str, Path], tmp_path: Path, fake_tools: None, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(overlay, "_run_tool", _fake_run(returncode=1))
    out = tmp_path / "out"
    with pytest.raises(ExtractionError):
        overlay.overlay_graphic(inputs["video"], inputs["logo"], out)
    trace = json.loads((out / "trace.json").read_text(encoding="utf-8"))
    assert trace["status"] == "failed" and trace["steps"][-1]["note"] == "overlay_failed"
    assert read_history(out)[-1]["status"] == "failed"


def test_bad_placement_failure_is_traced(inputs: dict[str, Path], tmp_path: Path, fake_tools: None) -> None:
    out = tmp_path / "out"
    with pytest.raises(ExtractionError):
        overlay.overlay_graphic(inputs["video"], inputs["logo"], out, position="middle")
    assert read_history(out)[-1]["status"] == "failed"


def test_replace_audio_writes_trace_and_trim_steps(inputs: dict[str, Path], tmp_path: Path, fake_tools: None) -> None:
    out = tmp_path / "out"
    result = audio_replace.replace_audio(inputs["video"], inputs["master"], out)
    trace = json.loads((out / "trace.json").read_text(encoding="utf-8"))
    assert trace["tool_id"] == "video-studio.replace-audio" and trace["status"] == "accepted"
    assert [item["name"] for item in trace["inputs"]] == ["source_video", "mastered_audio"]
    manual = (out / "MANUAL-STEPS.md").read_text(encoding="utf-8")
    assert "| 1 | V1 (Video 1) |" in manual and "| 2 | A1 (Audio 1) |" in manual
    assert "Link Clips (Ctrl+Alt+L)" in manual
    assert "ตัดเสียงส่วนที่ยาวเกินวิดีโอ" in manual and "`00:00:10:00 (= 00:00:10.000)`" in manual
    assert "Restrict to 8000 Kb/s" in manual
    assert verify_trace(out, trace["job_id"])["ok"] is True
    assert result.manifest_path.name == "video-remux.json"


def test_tools_still_work_without_production_trace(inputs: dict[str, Path], tmp_path: Path, fake_tools: None, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(job_trace, "TraceRecorder", None)
    monkeypatch.setattr(job_trace, "ManualGuide", None)
    out = tmp_path / "out"
    result = audio_replace.replace_audio(inputs["video"], inputs["master"], out)
    assert result.video_path.is_file()
    assert not (out / "trace.json").exists()
