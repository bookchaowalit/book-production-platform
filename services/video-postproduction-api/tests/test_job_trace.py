"""Render/export job traceability: trace.json, history.jsonl, MANUAL-STEPS.md, interchange."""

from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
from production_trace import (
    frames_to_timecode,
    read_history,
    render_manual_markdown,
    seconds_to_timecode,
    verify_trace,
)

from video_postproduction.captions.ass import render_ass
from video_postproduction.captions.cues import ClipWindow, timeline_cues
from video_postproduction.edl import validate_edl
from video_postproduction.exports import ExportError, ExportService
from video_postproduction.interchange import to_cmx3600, to_fcpxml, to_otio
from video_postproduction.manual import (
    GuideContext,
    build_resolve_guide,
    cut_points,
    removed_ranges,
)
from video_postproduction.media.models import (
    AudioStreamMetadata,
    MediaMetadata,
    VideoStreamMetadata,
)
from video_postproduction.models import EDL, MediaAsset, Project, RenderOutput, Transcript
from video_postproduction.render.base import (
    RenderArtifact,
    RenderCapabilityError,
    RenderProcessError,
)
from video_postproduction.render.ffmpeg import build_ffmpeg_argv, summarize_ffmpeg_argv
from video_postproduction.render.service import RenderService
from video_postproduction.storage.local import LocalFilesystemStorage
from video_postproduction.timeline import (
    FrameRate,
    SourceVideo,
    TimelinePlanError,
    build_timeline_plan,
    frames_label,
)

CANARY = "CANARYลับสุดยอด"
THAI = "สวัสดีครับวันนี้เราจะมาพูดเรื่องราคาของสินค้าใหม่ที่เพิ่งเปิดตัว"
SOURCE_METADATA = {
    "duration": 120.0,
    "format_name": "mov,mp4,m4a,3gp,3g2,mj2",
    "size_bytes": 4096,
    "bitrate": 5_000_000,
    "video": {"codec_name": "h264", "width": 1920, "height": 1080, "fps": 30.0, "pix_fmt": "yuv420p"},
    "audio": {"codec_name": "aac", "sample_rate": 48000, "channels": 2},
}


@dataclass
class FakeEngine:
    calls: list[dict[str, Any]] = field(default_factory=list)
    duration: float = 29.75
    caption_text: str | None = None

    def render(self, edl, *, input_path: Path, output_path: Path, caption_path: Path | None = None):
        self.calls.append({"caption_path": caption_path})
        if caption_path is not None:
            self.caption_text = caption_path.read_text(encoding="utf-8")
        output_path.write_bytes(b"rendered-mp4-bytes")
        return RenderArtifact(
            duration=self.duration,
            metadata=MediaMetadata(
                duration=self.duration,
                format_name="mov,mp4,m4a,3gp,3g2,mj2",
                size_bytes=18,
                bitrate=6_000_000,
                video=VideoStreamMetadata(codec_name="h264", width=1080, height=1920, fps=30.0),
                audio=AudioStreamMetadata(codec_name="aac", sample_rate=48000, channels=2),
            ),
        )


def _segment(asset_id: UUID, start: float, end: float, segment_id: str = "segment_0001", **extra: Any) -> dict[str, Any]:
    return {
        "id": segment_id,
        "source": {"asset_id": str(asset_id), "start": start, "end": end},
        "visual": {"kind": "a_roll", "reframe": {"mode": "center", "aspect_ratio": "9:16"}, "asset_refs": []},
        "captions": {"enabled": True, "source": "transcript", "format": "ass", "preset": "minimal"},
        "audio": {"operations": []},
        "color": {"operations": []},
        "provenance": {"reason": "test"},
        **extra,
    }


def make_document(project_id: UUID, asset_id: UUID, segments: list[dict[str, Any]], **output: Any) -> dict[str, Any]:
    return {
        "version": 1,
        "project_id": str(project_id),
        "source_assets": [{"asset_id": str(asset_id), "storage_key": "source.mp4", "duration": 120.0}],
        "timeline": segments,
        "assets": [],
        "output": {"width": 1080, "height": 1920, "fps": 30, "format": "mp4", **output},
        "capabilities": {"required": ["cut", "captions", "center_reframe"]},
    }


def _transcript_json() -> dict[str, Any]:
    return {
        "schema_version": "transcript.v1",
        "provider": "deterministic",
        "model": "test",
        "language": "th",
        "duration": 120.0,
        "segments": [
            {"id": "s1", "start": 10.0, "end": 16.0, "text": THAI, "words": []},
            {"id": "s2", "start": 16.0, "end": 24.0, "text": f"ข้อความที่สอง {CANARY}", "words": []},
            {"id": "s3", "start": 60.0, "end": 64.0, "text": "ช่วงที่สามของคลิป", "words": []},
        ],
    }


@pytest.fixture
def studio(session, tmp_path):
    storage = LocalFilesystemStorage(tmp_path / "storage")
    project = Project(name="Trace demo", status="ready_for_edit", transcription_quality="accurate")
    session.add(project)
    session.flush()
    storage.put_atomic("projects/demo/source.mp4", b"source-bytes", "video/mp4")
    asset = MediaAsset(
        project_id=project.id,
        storage_key="projects/demo/source.mp4",
        original_filename="interview.mp4",
        media_type="video/mp4",
        size_bytes=4096,
        sha256="a" * 64,
        metadata_json=SOURCE_METADATA,
        status="transcribed",
    )
    session.add(asset)
    session.flush()
    session.add(
        Transcript(
            project_id=project.id,
            asset_id=asset.id,
            provider="faster_whisper",
            model="medium",
            language="th",
            duration=120.0,
            normalized_json=_transcript_json(),
            content_fingerprint="f" * 64,
        )
    )
    session.commit()

    def add_edl(segments: list[dict[str, Any]], **output: Any) -> EDL:
        document = validate_edl(make_document(project.id, asset.id, segments, **output)).model_dump(mode="json")
        version = (session.query(EDL).filter(EDL.project_id == project.id).count()) + 1
        record = EDL(project_id=project.id, source_asset_id=asset.id, version=version, schema_version="edl.v1", document=document)
        session.add(record)
        session.commit()
        return record

    return {"session": session, "storage": storage, "project": project, "asset": asset, "add_edl": add_edl}


def _job_dir(storage: LocalFilesystemStorage, output: RenderOutput) -> Path:
    return storage.local_path(f"projects/{output.project_id}/jobs/render-{output.id}")


def _root(storage: LocalFilesystemStorage, project_id: UUID) -> Path:
    return storage.local_path(f"projects/{project_id}/jobs")


def _render(studio: dict[str, Any], segments: list[dict[str, Any]], engine: FakeEngine | None = None, **output: Any) -> RenderOutput:
    record = studio["add_edl"](segments, **output)
    service = RenderService(studio["session"], storage=studio["storage"], engine=engine or FakeEngine())
    return service.render(studio["project"].id, record.id)


# --- success trace -------------------------------------------------------------


def test_successful_render_writes_trace_manual_history_and_artifacts(studio):
    asset = studio["asset"]
    engine = FakeEngine()
    output = _render(studio, [_segment(asset.id, 12.5, 42.25)], engine)
    storage = studio["storage"]
    job_dir = _job_dir(storage, output)

    assert output.status == "succeeded"
    assert output.storage_key.startswith(f"projects/{output.project_id}/jobs/render-{output.id}/")
    trace = json.loads((job_dir / "trace.json").read_text(encoding="utf-8"))
    assert trace["schema_version"] == "production-trace.v1"
    assert trace["tool_id"] == "video-studio.render"
    assert trace["job_id"] == str(output.id)
    assert trace["status"] == "accepted"
    assert trace["ir"]["schema"] == "video-edl.v1" and trace["ir"]["file"] == "edl.json"
    stages = [step["stage"] for step in trace["steps"]]
    for stage in ("validate", "probe", "plan", "transcribe", "captions", "interchange", "render", "qa", "publish"):
        assert stage in stages, stage
    source = next(item for item in trace["inputs"] if item["name"] == "source_video")
    assert source == {
        "name": "source_video", "role": "source", "file": "interview.mp4", "bytes": 4096, "sha256": "a" * 64,
        "duration_s": 120.0, "fps": 30.0, "width": 1920, "height": 1080, "video_codec": "h264",
        "pix_fmt": "yuv420p", "has_audio": True, "audio_codec": "aac", "sample_rate": 48000, "channels": 2,
    }
    transcribe = next(step for step in trace["steps"] if step["stage"] == "transcribe")
    assert transcribe["params"]["model"] == "medium" and transcribe["params"]["quality"] == "accurate"
    render_step = next(step for step in trace["steps"] if step["stage"] == "render")
    options = render_step["params"]["options"]
    assert options["c:v"] == "libx264" and options["pix_fmt"] == "yuv420p" and options["c:a"] == "aac"
    assert options["ss"] == "12.500" and options["t"] == "29.750"
    assert options["vf"] == ["crop=ih*9/16:ih:(iw-ih*9/16)/2:0", "scale=1080:1920", "subtitles=<captions>"]
    assert str(storage.root) not in json.dumps(trace)

    files = {artifact["file"]: artifact for artifact in trace["artifacts"]}
    mp4_name = Path(output.storage_key).name
    assert set(files) == {
        "edl.json", "timeline.edl", "timeline.fcpxml", "timeline.otio", "captions.srt", "captions-burned.ass", mp4_name,
    }
    from production_trace import sha256_file

    for name, artifact in files.items():
        assert artifact["sha256"] == sha256_file(job_dir / name)
    assert json.loads((job_dir / "edl.json").read_text(encoding="utf-8")) == validate_edl(
        studio["session"].get(EDL, output.edl_id).document
    ).model_dump(mode="json")
    assert trace["qa"] == {"status": "passed", "checks": 3}
    assert trace["manual_guide"]["program"] == "DaVinci Resolve"
    assert (job_dir / "MANUAL-STEPS.md").is_file()
    assert engine.calls[0]["caption_path"] == job_dir / "captions-burned.ass"

    history = read_history(_root(storage, output.project_id))
    assert [entry["job_id"] for entry in history] == [str(output.id)]
    assert history[0]["status"] == "accepted"
    assert history[0]["trace"] == f"render-{output.id}/trace.json"

    meta = output.metadata_json["trace"]
    assert meta["trace"] == f"projects/{output.project_id}/jobs/render-{output.id}/trace.json"
    assert meta["history"] == f"projects/{output.project_id}/jobs/history.jsonl"
    assert {"MANUAL-STEPS.md", "trace.json", "timeline.fcpxml", mp4_name} <= set(meta["files"])


def test_trace_and_history_never_contain_transcript_text(studio):
    asset = studio["asset"]
    engine = FakeEngine()
    output = _render(studio, [_segment(asset.id, 12.5, 42.25)], engine)
    job_dir = _job_dir(studio["storage"], output)
    trace_text = (job_dir / "trace.json").read_text(encoding="utf-8")
    history_text = (_root(studio["storage"], output.project_id) / "history.jsonl").read_text(encoding="utf-8")
    for leaked in (CANARY, THAI[:10], "ข้อความที่สอง"):
        assert leaked not in trace_text
        assert leaked not in history_text
    # Caption text belongs in the caption files and the manual guide only.
    assert CANARY in (job_dir / "captions.srt").read_text(encoding="utf-8")
    assert CANARY in (job_dir / "MANUAL-STEPS.md").read_text(encoding="utf-8")
    assert engine.caption_text is not None and CANARY in engine.caption_text


def test_verify_passes_then_fails_after_tamper(studio):
    asset = studio["asset"]
    output = _render(studio, [_segment(asset.id, 12.5, 42.25)])
    root = _root(studio["storage"], output.project_id)
    assert verify_trace(root, str(output.id))["ok"] is True
    studio["storage"].local_path(output.storage_key).write_bytes(b"tampered")
    result = verify_trace(root, str(output.id))
    assert result["ok"] is False
    assert [item["file"] for item in result["artifacts"] if not item["ok"]] == [Path(output.storage_key).name]


# --- failure trace -------------------------------------------------------------


def test_failed_render_records_trace_history_and_manual(studio):
    session = studio["session"]
    session.query(Transcript).delete()
    session.commit()
    asset = studio["asset"]
    record = studio["add_edl"]([_segment(asset.id, 12.5, 42.25)])
    service = RenderService(session, storage=studio["storage"], engine=FakeEngine())
    with pytest.raises(RenderProcessError):
        service.render(studio["project"].id, record.id)
    output = session.query(RenderOutput).one()
    assert output.status == "failed"
    job_dir = _job_dir(studio["storage"], output)
    trace = json.loads((job_dir / "trace.json").read_text(encoding="utf-8"))
    assert trace["status"] == "failed"
    assert trace["steps"][-1] == {**trace["steps"][-1], "stage": "outcome", "operation": "failed", "note": "transcript_not_ready"}
    assert read_history(_root(studio["storage"], output.project_id))[-1]["status"] == "failed"
    assert output.metadata_json["trace"]["trace"].endswith(f"render-{output.id}/trace.json")
    manual = (job_dir / "MANUAL-STEPS.md").read_text(encoding="utf-8")
    assert "transcript_not_ready" in manual


def test_multi_clip_render_fails_but_leaves_editor_bundle(studio):
    asset = studio["asset"]
    record = studio["add_edl"]([_segment(asset.id, 10.0, 20.0, "a"), _segment(asset.id, 60.0, 64.0, "b")])

    class CapabilityEngine(FakeEngine):
        def render(self, edl, **kwargs):
            raise AssertionError("argv validation must reject multi-clip timelines before rendering")

    service = RenderService(studio["session"], storage=studio["storage"], engine=CapabilityEngine())
    with pytest.raises(RenderCapabilityError):
        service.render(studio["project"].id, record.id)
    output = studio["session"].query(RenderOutput).one()
    job_dir = _job_dir(studio["storage"], output)
    trace = json.loads((job_dir / "trace.json").read_text(encoding="utf-8"))
    assert trace["status"] == "failed"
    render_step = next(step for step in trace["steps"] if step["stage"] == "render")
    assert render_step["status"] == "failed" and render_step["note"] == "RenderCapabilityError"
    assert {"timeline.edl", "timeline.fcpxml", "timeline.otio", "captions.srt", "edl.json"} <= {
        artifact["file"] for artifact in trace["artifacts"]
    }
    manual = (job_dir / "MANUAL-STEPS.md").read_text(encoding="utf-8")
    assert "unsupported_capability" in manual and "timeline.fcpxml" in manual


# --- manual guide --------------------------------------------------------------


def _guide_markdown(plan, cues=None) -> str:
    return render_manual_markdown(build_resolve_guide(GuideContext(plan=plan, cues=cues)))


def test_every_cut_and_clip_appears_with_exact_timecodes(studio):
    asset = studio["asset"]
    output = _render(studio, [_segment(asset.id, 12.5, 42.25)])
    manual = (_job_dir(studio["storage"], output) / "MANUAL-STEPS.md").read_text(encoding="utf-8")
    for seconds in (12.5, 42.25):
        assert f"{frames_to_timecode(seconds, 30)} (= {seconds_to_timecode(seconds)})" in manual
    assert "00:00:12:15 (= 00:00:12.500)" in manual and "00:00:42:08 (= 00:00:42.250)" in manual
    # Ripple delete both removed ranges, last first.
    tail = manual.index("00:00:42:08 (= 00:00:42.250) → 00:02:00:00 (= 00:02:00.000)")
    head = manual.index("00:00:00:00 (= 00:00:00.000) → 00:00:12:15 (= 00:00:12.500)")
    assert tail < head
    assert "Record IN 00:00:00:00 → OUT 00:00:29:23" in manual
    assert "Timeline resolution ▸ Custom" in manual and "1080 x 1920" in manual
    assert "Start Timecode 00:00:00:00" in manual
    assert "Restrict to 6000 Kb/s" in manual
    assert "Zoom (X/Y ลิงก์กัน)" in manual and "`3.160`" in manual


def test_multi_clip_guide_lists_every_segment_in_record_order(studio):
    asset = studio["asset"]
    edl = validate_edl(
        make_document(
            studio["project"].id,
            asset.id,
            [_segment(asset.id, 10.0, 16.25, "a"), _segment(asset.id, 30.0, 33.0, "b"), _segment(asset.id, 60.0, 64.5, "c")],
        )
    )
    source = SourceVideo.from_metadata("interview.mp4", SOURCE_METADATA)
    plan = build_timeline_plan(edl, source)
    manual = _guide_markdown(plan)
    assert cut_points(plan) == [10.0, 16.25, 30.0, 33.0, 60.0, 64.5]
    assert removed_ranges(plan) == [(0.0, 10.0), (16.25, 30.0), (33.0, 60.0), (64.5, 120.0)]
    for point in cut_points(plan):
        assert f"{frames_to_timecode(point, 30)} (= {seconds_to_timecode(point)})" in manual
    record_in = 0
    for clip in plan.clips:
        frames = round(clip.source_end * 30) - round(clip.source_start * 30)
        expected = f"Record IN {frames_label(record_in, 30)} → OUT {frames_label(record_in + frames, 30)}"
        assert expected in manual
        assert f"{clip.index} ({clip.segment_id})" in manual
        record_in += frames
    assert [clip.record_in_frame for clip in plan.clips] == [0, 188, 278]


def test_out_of_order_clips_use_in_out_append_with_inclusive_out(studio):
    asset = studio["asset"]
    edl = validate_edl(
        make_document(studio["project"].id, asset.id, [_segment(asset.id, 60.0, 64.0, "late"), _segment(asset.id, 10.0, 12.0, "early")])
    )
    plan = build_timeline_plan(edl, SourceVideo.from_metadata("interview.mp4", SOURCE_METADATA))
    assert not plan.is_sequential
    manual = _guide_markdown(plan)
    assert "Mark In (I)" in manual and "Append at End (Shift+F12)" in manual
    assert "Razor" not in manual.split("## 3.")[1].split("ทางลัด")[0]
    assert "`00:01:03:29`" in manual  # Out marked one frame before 00:01:04:00


def test_layer_table_matches_tracks_the_renderer_used(studio):
    asset = studio["asset"]
    source = SourceVideo.from_metadata("interview.mp4", SOURCE_METADATA)
    project_id = studio["project"].id

    def layers(segment: dict[str, Any], src: SourceVideo) -> list[str]:
        plan = build_timeline_plan(validate_edl(make_document(project_id, asset.id, [segment])), src)
        return [layer.name for layer in build_resolve_guide(GuideContext(plan=plan)).layers]

    assert layers(_segment(asset.id, 1, 5), source) == ["V1 (Video 1)", "ST1 (Subtitle 1)", "A1 (Audio 1)"]
    no_captions = _segment(asset.id, 1, 5)
    no_captions["captions"]["enabled"] = False
    assert layers(no_captions, source) == ["V1 (Video 1)", "A1 (Audio 1)"]
    silent = SourceVideo.from_metadata("silent.mp4", {**SOURCE_METADATA, "audio": None})
    assert layers(no_captions, silent) == ["V1 (Video 1)"]

    # The render argv uses exactly these tracks: one video map, optional audio, subtitles filter.
    edl = validate_edl(make_document(project_id, asset.id, [_segment(asset.id, 1, 5)]))
    argv = build_ffmpeg_argv(edl, input_path=Path("in.mp4"), output_path=Path("out.mp4"), caption_path=Path("c.ass"))
    summary = summarize_ffmpeg_argv(argv)
    assert summary["map"] == ["0:v:0", "0:a?"]
    assert summary["inputs"] == 1


def test_source_profile_guide_keeps_full_frame(studio):
    asset = studio["asset"]
    edl = validate_edl(
        make_document(
            studio["project"].id,
            asset.id,
            [{**_segment(asset.id, 1, 5), "visual": {"kind": "a_roll", "reframe": None, "asset_refs": []}}],
            width=1920,
            height=1080,
            render_profile="source",
        )
    )
    plan = build_timeline_plan(edl, SourceVideo.from_metadata("interview.mp4", SOURCE_METADATA))
    manual = _guide_markdown(plan)
    assert "1920 x 1080" in manual and "คงภาพเต็มไม่ครอป" in manual
    assert plan.reframe.resolve_zoom == 1.0


def test_caption_cues_match_burned_ass_events(studio):
    from video_postproduction.transcription.models import NormalizedTranscript

    transcript = NormalizedTranscript.model_validate(_transcript_json())
    ass = render_ass(transcript, "minimal", clip_start=12.5, clip_end=42.25, output_size=(1080, 1920))
    dialogues = [line.split(",", 9) for line in ass.splitlines() if line.startswith("Dialogue:")]
    from video_postproduction.captions.ass import line_columns_for_width

    cues = timeline_cues(transcript, [ClipWindow(12.5, 42.25, 0.0)], wrap_columns=line_columns_for_width(1080))
    assert any(len(cue.lines) > 1 for cue in cues)
    assert len(cues) == len(dialogues)
    for cue, dialogue in zip(cues, dialogues, strict=True):
        assert "\\N".join(cue.lines) == dialogue[9]
        from video_postproduction.captions.ass import format_ass_time

        assert format_ass_time(cue.start) == dialogue[1] and format_ass_time(cue.end) == dialogue[2]


def test_ntsc_rate_labels_frames_at_nominal_rate():
    rate = FrameRate.from_fps(30000 / 1001)
    assert rate.nominal == 30 and rate.label == "29.97"
    assert rate.frames(10.0) == 300 - 0  # round(10 * 29.97) = 300
    assert rate.frames(100.0) == 2997
    assert frames_label(rate.frames(100.0), 30) == "00:01:39:27"
    assert FrameRate.from_fps(25).frames(1.5) == 37 + 1  # half up like production_trace
    assert frames_to_timecode(1.5, 25) == frames_label(FrameRate.from_fps(25).frames(1.5), 25)


# --- interchange ---------------------------------------------------------------


def _three_clip_plan(project_id: UUID, asset_id: UUID):
    edl = validate_edl(
        make_document(project_id, asset_id, [_segment(asset_id, 10.0, 16.25, "a"), _segment(asset_id, 30.0, 33.0, "b")])
    )
    return build_timeline_plan(edl, SourceVideo.from_metadata("interview.mp4", SOURCE_METADATA), title="ทดสอบ Export")


def test_cmx3600_events_use_plan_frames(studio):
    plan = _three_clip_plan(studio["project"].id, studio["asset"].id)
    text = to_cmx3600(plan)
    lines = text.split("\r\n")
    assert lines[0].startswith("TITLE: ") and lines[1] == "FCM: NON-DROP FRAME"
    events = [line for line in lines if re.match(r"^\d{3}  ", line)]
    assert events == [
        "001  AX       AA/V  C        00:00:10:00 00:00:16:08 00:00:00:00 00:00:06:08",
        "002  AX       AA/V  C        00:00:30:00 00:00:33:00 00:00:06:08 00:00:09:08",
    ]
    assert text.count("* FROM CLIP NAME: interview.mp4") == 2


def test_fcpxml_has_spine_offsets_and_center_crop_scale(studio):
    plan = _three_clip_plan(studio["project"].id, studio["asset"].id)
    root = ET.fromstring(to_fcpxml(plan).split("\n", 2)[2])
    assert root.tag == "fcpxml" and root.get("version") == "1.10"
    formats = {item.get("id"): item for item in root.iter("format")}
    assert formats["r1"].get("width") == "1080" and formats["r1"].get("height") == "1920"
    assert formats["r1"].get("frameDuration") == "1/30s"
    sequence = root.find("./library/event/project/sequence")
    assert sequence is not None and sequence.get("duration") == "139/15s"  # 278 frames
    clips = sequence.findall("./spine/asset-clip")
    assert [(c.get("offset"), c.get("start"), c.get("duration")) for c in clips] == [
        ("0s", "10s", "94/15s"),
        ("94/15s", "30s", "3s"),
    ]
    transform = clips[0].find("adjust-transform")
    assert transform is not None and transform.get("scale") == "3.16 3.16"
    assert clips[0].find("adjust-conform").get("type") == "fit"
    assert [c.find("marker").get("value") for c in clips] == ["a", "b"]


def test_otio_tracks_and_ranges(studio):
    plan = _three_clip_plan(studio["project"].id, studio["asset"].id)
    document = json.loads(to_otio(plan))
    assert document["OTIO_SCHEMA"] == "Timeline.1"
    tracks = document["tracks"]["children"]
    assert [(track["name"], track["kind"]) for track in tracks] == [("V1", "Video"), ("A1", "Audio")]
    ranges = [
        (clip["source_range"]["start_time"]["value"], clip["source_range"]["duration"]["value"])
        for clip in tracks[0]["children"]
    ]
    assert ranges == [(300.0, 188.0), (900.0, 90.0)]
    assert tracks[0]["children"][0]["OTIO_SCHEMA"] == "Clip.2"
    assert tracks[0]["children"][0]["media_references"]["DEFAULT_MEDIA"]["target_url"] == "interview.mp4"


# --- loudness ------------------------------------------------------------------


def test_loudness_operation_reaches_ffmpeg_argv_and_guide(studio):
    asset = studio["asset"]
    segment = _segment(asset.id, 1, 5, audio={"operations": [{"type": "loudness_normalize", "integrated_lufs": -14, "true_peak_dbtp": -1.5}]})
    edl = validate_edl(make_document(studio["project"].id, asset.id, [segment]))
    argv = build_ffmpeg_argv(edl, input_path=Path("in.mp4"), output_path=Path("out.mp4"))
    assert argv[argv.index("-af") + 1] == "loudnorm=I=-14:TP=-1.5:LRA=11"
    assert argv[argv.index("-ar") + 1] == "48000"
    plan = build_timeline_plan(edl, SourceVideo.from_metadata("interview.mp4", SOURCE_METADATA))
    manual = _guide_markdown(plan)
    assert "Normalize Audio ✓" in manual and "Target Loudness -14 LKFS" in manual


@pytest.mark.parametrize(
    "operations",
    [
        [{"type": "duck_music"}],
        [{"type": "loudness_normalize", "integrated_lufs": -50}],
        [{"type": "loudness_normalize", "extra": 1}],
        [{"type": "loudness_normalize"}, {"type": "loudness_normalize"}],
    ],
)
def test_unsupported_audio_operations_fail_before_render(studio, operations):
    asset = studio["asset"]
    edl = validate_edl(make_document(studio["project"].id, asset.id, [_segment(asset.id, 1, 5, audio={"operations": operations})]))
    with pytest.raises(RenderCapabilityError):
        build_ffmpeg_argv(edl, input_path=Path("in.mp4"), output_path=Path("out.mp4"))


def test_loudness_without_source_audio_is_rejected(studio):
    asset = studio["asset"]
    segment = _segment(asset.id, 1, 5, audio={"operations": [{"type": "loudness_normalize"}]})
    edl = validate_edl(make_document(studio["project"].id, asset.id, [segment]))
    with pytest.raises(TimelinePlanError):
        build_timeline_plan(edl, SourceVideo.from_metadata("silent.mp4", {**SOURCE_METADATA, "audio": None}))


# --- highlights provenance -----------------------------------------------------


def test_confirmed_ai_proposal_metadata_is_traced(studio):
    asset = studio["asset"]
    segment = _segment(
        asset.id,
        12.5,
        42.25,
        "highlight_c1",
        provenance={
            "reason": "confirmed highlight proposal",
            "proposal_fingerprint": "b" * 64,
            "route": "provider",
            "start_segment_id": "s1",
            "end_segment_id": "s2",
        },
    )
    output = _render(studio, [segment])
    trace = json.loads((_job_dir(studio["storage"], output) / "trace.json").read_text(encoding="utf-8"))
    assert trace["ai"]["used"] is True and trace["ai"]["model_calls_exact"] is False
    step = next(step for step in trace["steps"] if step["stage"] == "highlight_proposal")
    assert step["params"]["proposal_fingerprints"] == ["b" * 64]
    assert step["params"]["segment_ranges"] == [["s1", "s2"]]
    assert read_history(_root(studio["storage"], output.project_id))[-1]["ai_used"] is True


# --- export --------------------------------------------------------------------


def test_export_service_writes_bundle_for_multi_clip_edl(studio):
    asset = studio["asset"]
    record = studio["add_edl"]([_segment(asset.id, 10.0, 16.25, "a"), _segment(asset.id, 60.0, 64.0, "b")])
    result = ExportService(studio["session"], storage=studio["storage"]).export(studio["project"].id, record.id)
    job_dir = studio["storage"].local_path(result.trace["job_dir"])
    assert result.status == "accepted"
    for name in ("trace.json", "MANUAL-STEPS.md", "edl.json", "timeline.edl", "timeline.fcpxml", "timeline.otio", "captions.srt"):
        assert (job_dir / name).is_file(), name
    srt = (job_dir / "captions.srt").read_text(encoding="utf-8")
    assert "00:00:06,250 --> 00:00:10,250" in srt  # second clip's cue starts at its record position
    trace_text = (job_dir / "trace.json").read_text(encoding="utf-8")
    assert CANARY not in trace_text
    history = read_history(_root(studio["storage"], studio["project"].id))
    assert history[-1]["tool_id"] == "video-studio.export" and history[-1]["job_id"] == str(result.export_id)
    assert verify_trace(_root(studio["storage"], studio["project"].id), str(result.export_id))["ok"] is True


def test_export_failure_is_traced(studio):
    asset = studio["asset"]
    record = studio["add_edl"]([_segment(asset.id, 10.0, 16.25)])
    record.document = {**record.document, "version": 2}
    studio["session"].commit()
    with pytest.raises(ExportError) as caught:
        ExportService(studio["session"], storage=studio["storage"]).export(studio["project"].id, record.id)
    assert caught.value.code == "edl_invalid"
    history = read_history(_root(studio["storage"], studio["project"].id))
    assert history[-1]["status"] == "failed" and history[-1]["tool_id"] == "video-studio.export"


# --- API -----------------------------------------------------------------------


@pytest.fixture
def studio_api(studio):
    from video_postproduction.api.dependencies import get_session, get_storage
    from video_postproduction.main import app

    app.dependency_overrides.update({get_session: lambda: studio["session"], get_storage: lambda: studio["storage"]})
    try:
        yield studio
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_export_history_and_job_file_routes(client, studio_api):
    asset = studio_api["asset"]
    project_id = studio_api["project"].id
    studio_api["add_edl"]([_segment(asset.id, 10.0, 16.25, "a"), _segment(asset.id, 60.0, 64.0, "b")])

    created = await client.post(f"/api/v1/projects/{project_id}/exports", json={})
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["status"] == "accepted" and body["warnings"] == []
    assert {"timeline.fcpxml", "timeline.edl", "timeline.otio", "MANUAL-STEPS.md", "trace.json"} <= set(body["trace"]["files"])
    job_name = body["trace"]["job_dir"].rsplit("/", 1)[1]

    fcpxml = await client.get(f"/api/v1/projects/{project_id}/jobs/{job_name}/files/timeline.fcpxml")
    assert fcpxml.status_code == 200 and fcpxml.text.startswith('<?xml version="1.0"')
    manual = await client.get(f"/api/v1/projects/{project_id}/jobs/{job_name}/files/MANUAL-STEPS.md")
    assert manual.status_code == 200 and "DaVinci Resolve" in manual.text

    for bad in (f"{job_name}/files/source.mp4", "render-..%2F..%2F/files/trace.json", "other/files/trace.json"):
        response = await client.get(f"/api/v1/projects/{project_id}/jobs/{bad}")
        assert response.status_code == 404, bad

    history = await client.get(f"/api/v1/projects/{project_id}/history")
    assert history.status_code == 200
    items = history.json()["items"]
    assert items[0]["job_id"] == body["id"] and items[0]["tool_id"] == "video-studio.export"
    assert CANARY not in history.text


@pytest.mark.asyncio
async def test_export_route_reports_missing_edl(client, studio_api):
    response = await client.post(f"/api/v1/projects/{studio_api['project'].id}/exports", json={})
    assert response.status_code == 404
