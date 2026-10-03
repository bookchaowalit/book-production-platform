"""Audio Master job history, step trace, Audacity guide, and the new presets.

FFmpeg is replaced by a scripted process runner that writes a real PCM24/48k
WAV and returns loudnorm JSON, so these tests run without FFmpeg installed.
Whether the real FFmpeg filters (``highpass``, ``agate``, ``deesser``) hit the
targets is runtime-dependent and covered only by ``test_jobs.py`` when FFmpeg
is available.
"""

from __future__ import annotations

import json
import re
import wave
from pathlib import Path
from typing import Any

import pytest

from book_production_platform.audio_ir import AudioIR
from book_production_platform.audio_production import jobs, master_audio
from book_production_platform.audio_production.manual import (
    build_audacity_guide,
    linear_gain_db,
)
from book_production_platform.audio_production.presets import PLATFORM_TARGETS, PRESETS
from book_production_platform.audio_production.runner import ProcessOutput
from book_production_platform.production_trace import (
    read_history,
    sha256_json,
    verify_trace,
)
from tests.audio_production.test_jobs import _write_synthetic_voice

CANARY = "CANARY-audio-ลับ"
BEFORE = {"input_i": "-26.50", "input_tp": "-9.00", "input_lra": "5.20", "input_thresh": "-37.00", "target_offset": "0.30", "normalization_type": "linear"}


class FakeFfmpeg:
    """Scripted ``run_process``: version, loudnorm measurement, and a real WAV render."""

    def __init__(self, *, qa_offset: float = 0.0) -> None:
        self.graphs: list[str] = []
        self.qa_offset = qa_offset

    def __call__(self, argv: list[str], timeout_seconds: float) -> ProcessOutput:
        if argv[1:] == ["-version"]:
            return ProcessOutput(0, b"ffmpeg version 7.1-scripted Copyright (c) the FFmpeg developers\n", b"", 0.01, False)
        graph = argv[argv.index("-af") + 1]
        self.graphs.append(graph)
        source = Path(argv[argv.index("-i") + 1])
        target = float(re.search(r"loudnorm=I=(-?[0-9.]+)", graph).group(1))  # type: ignore[union-attr]
        ceiling = float(re.search(r":TP=(-?[0-9.]+)", graph).group(1))  # type: ignore[union-attr]
        if argv[-1] != "-":
            with wave.open(str(source), "rb") as reader:
                channels, frames, rate = reader.getnchannels(), reader.getnframes(), reader.getframerate()
            with wave.open(argv[-1], "wb") as writer:
                writer.setnchannels(channels)
                writer.setsampwidth(3)
                writer.setframerate(48_000)
                writer.writeframes(b"\x01\x00\x00" * channels * round(frames * 48_000 / rate))
            stats = dict(BEFORE)
        elif source.name == "master.wav":
            stats = {**BEFORE, "input_i": f"{target + self.qa_offset:.2f}", "input_tp": f"{ceiling - 0.4:.2f}", "input_lra": "4.10"}
        else:
            stats = dict(BEFORE)
        return ProcessOutput(0, b"", ("[Parsed_loudnorm_0]\n" + json.dumps(stats)).encode(), 0.02, False)


def _setup(tmp_path: Path, preset: str = "podcast-mono.v1") -> tuple[dict[str, Any], Path, Path, Path]:
    input_root = tmp_path / "input"
    input_root.mkdir()
    _write_synthetic_voice(input_root / "voice.wav")
    ffmpeg = tmp_path / "ffmpeg"
    ffmpeg.write_bytes(b"scripted")
    ir = {"schema_version": "audio-ir.v1", "audio_id": "trace-demo", "source_path": "voice.wav", "preset": preset}
    return ir, input_root, tmp_path / "jobs", ffmpeg


def test_accepted_master_writes_trace_ir_manual_and_history(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeFfmpeg()
    monkeypatch.setattr(jobs, "run_process", fake)
    ir, input_root, root, ffmpeg = _setup(tmp_path)

    result = master_audio(ir, input_root, root, ffmpeg)

    assert result.status == "accepted", result.error_code
    assert result.artifact_dir is not None
    trace = json.loads((result.artifact_dir / "trace.json").read_text(encoding="utf-8"))
    assert trace["tool_id"] == "audio-master-v1a"
    assert trace["status"] == "accepted"
    assert [step["operation"] for step in trace["steps"]] == [
        "parse_audio_ir", "inspect_source", "ffmpeg_version", "measure_loudness", "ffmpeg_master", "master_qa", "publish_master",
    ]
    render = next(step for step in trace["steps"] if step["operation"] == "ffmpeg_master")
    assert render["params"]["sample_rate"] == 48_000 and render["params"]["codec"] == "pcm_s24le"
    assert "measured_I=-26.500000" in render["params"]["graph"] and "linear=true" in render["params"]["graph"]
    assert {artifact["file"] for artifact in trace["artifacts"]} == {"master.wav", "qa-manifest.json"}
    assert trace["inputs"][1]["file"] == "voice.wav" and len(trace["inputs"][1]["sha256"]) == 64
    assert trace["ir"] == {"schema": "audio-ir.v1", "sha256": sha256_json(json.loads((result.artifact_dir / "ir.json").read_text(encoding="utf-8"))), "file": "ir.json"}
    assert trace["manual_guide"]["program"] == "Audacity"
    assert (result.artifact_dir / "MANUAL-STEPS.md").is_file()
    history = read_history(root)
    assert [(entry["status"], entry["trace"]) for entry in history] == [("accepted", f"{result.fingerprint}/trace.json")]


def test_replay_still_verifies_and_writes_no_second_trace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(jobs, "run_process", FakeFfmpeg())
    ir, input_root, root, ffmpeg = _setup(tmp_path)
    first = master_audio(ir, input_root, root, ffmpeg)

    second = master_audio(ir, input_root, root, ffmpeg)

    assert second.replayed is True and second.artifact_hashes == first.artifact_hashes
    assert len(read_history(root)) == 1


def test_verify_passes_then_fails_after_tamper(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(jobs, "run_process", FakeFfmpeg())
    ir, input_root, root, ffmpeg = _setup(tmp_path)
    result = master_audio(ir, input_root, root, ffmpeg)
    job_id = read_history(root)[0]["job_id"]
    assert verify_trace(root, job_id)["ok"] is True

    assert result.master_path is not None
    result.master_path.write_bytes(result.master_path.read_bytes() + b"x")

    report = verify_trace(root, job_id)
    assert report["ok"] is False
    assert [item["file"] for item in report["artifacts"] if not item["ok"]] == ["master.wav"]


def test_rejected_jobs_are_traced_without_private_text(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(jobs, "run_process", FakeFfmpeg(qa_offset=3.0))
    ir, input_root, root, ffmpeg = _setup(tmp_path)

    qa_failed = master_audio(ir, input_root, root, ffmpeg)
    invalid = master_audio({**ir, "audio_id": "x", "notes": CANARY}, input_root, root, ffmpeg)
    escaped = master_audio({**ir, "source_path": "../voice.wav"}, input_root, root, ffmpeg)

    assert (qa_failed.error_code, invalid.error_code, escaped.error_code) == ("audio_qa_failed", "invalid_audio_ir", "unsafe_audio_path")
    history = read_history(root)
    assert [entry["status"] for entry in history] == ["failed", "failed", "failed"]
    assert all(entry["trace"].startswith("failed/") for entry in history)
    qa_trace = json.loads((root / history[0]["trace"]).read_text(encoding="utf-8"))
    qa_step = next(step for step in qa_trace["steps"] if step["operation"] == "master_qa")
    assert qa_step["status"] == "failed"
    assert qa_trace["steps"][-1]["note"] == "audio_qa_failed"
    assert not list(root.glob("*/master.wav"))
    for entry in history:
        assert CANARY not in (root / entry["trace"]).read_text(encoding="utf-8")
    assert CANARY not in (root / "history.jsonl").read_text(encoding="utf-8")


def test_unsafe_output_root_is_never_traced(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(jobs, "run_process", FakeFfmpeg())
    ir, input_root, _, ffmpeg = _setup(tmp_path)
    not_a_dir = tmp_path / "file-root"
    not_a_dir.write_text("x", encoding="utf-8")

    result = master_audio(ir, input_root, not_a_dir, ffmpeg)

    assert result.error_code == "unsafe_output_path"
    assert not_a_dir.read_text(encoding="utf-8") == "x"


def test_audacity_guide_values_equal_the_preset_and_measurements(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(jobs, "run_process", FakeFfmpeg())
    ir, input_root, root, ffmpeg = _setup(tmp_path)
    result = master_audio(ir, input_root, root, ffmpeg)
    assert result.manifest_path is not None
    manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
    preset = PRESETS["podcast-mono.v1"]

    guide = build_audacity_guide(AudioIR.model_validate(ir), preset, manifest)
    values = [step.value for step in guide.steps]

    assert linear_gain_db(preset, manifest["measurements"]["before"]) == 7.5  # -19.0 - (-26.5)
    assert "Amplification (dB) = +7.50, Allow clipping = off" in values
    assert f"Threshold (dB) = {preset.true_peak_dbtp:g}, Make-up target (dB) = {preset.true_peak_dbtp:g}, Lookahead (ms) = 1" in values
    assert "Channels = Mono, Sample Rate = 48000 Hz, Encoding = Signed 24-bit PCM" in values
    assert "master.wav" in values
    assert any(f"{manifest['measurements']['after']['integrated_lufs']:g} LUFS" in check for check in guide.checks)
    assert any(f"{manifest['measurements']['after']['true_peak_dbtp']:g} dBTP" in check for check in guide.checks)
    tools = [step.tool for step in guide.steps]
    assert tools.index("Effect ▸ Volume and Compression ▸ Amplify…") < tools.index("Effect ▸ Volume and Compression ▸ Limiter…") < tools.index("File ▸ Export Audio… (Ctrl+Shift+E)")


def test_voice_clean_chain_runs_in_order_and_the_guide_mirrors_it(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeFfmpeg()
    monkeypatch.setattr(jobs, "run_process", fake)
    ir, input_root, root, ffmpeg = _setup(tmp_path, "voice-clean-mono.v1")

    result = master_audio(ir, input_root, root, ffmpeg)

    assert result.status == "accepted", result.error_code
    chain = "highpass=f=80:p=2,agate=threshold=0.01:ratio=2:attack=10:release=250:range=0.06,deesser=i=0.4:m=0.5:f=0.5:s=o,loudnorm="
    analysis, render, qa = fake.graphs
    assert analysis.startswith(chain) and render.startswith(chain)
    assert qa.startswith("loudnorm=")  # QA measures the master itself, without the chain
    manifest = json.loads(result.manifest_path.read_text(encoding="utf-8")) if result.manifest_path else {}
    assert [stage["filter"] for stage in manifest["preset"]["filter_chain"]] == ["highpass", "agate", "deesser"]
    guide = build_audacity_guide(AudioIR.model_validate(ir), PRESETS["voice-clean-mono.v1"], manifest)
    values = [step.value for step in guide.steps]
    assert "Frequency (Hz) = 80, Roll-off = 12 dB per octave" in values
    assert "Gate threshold (dB) = -40.0, Level reduction (dB) = -24.4, Attack (ms) = 10, Hold (ms) = 0, Decay (ms) = 250" in values
    tools = [step.tool for step in guide.steps]
    assert tools.index("Effect ▸ EQ and Filters ▸ High-Pass Filter…") < tools.index("Effect ▸ Noise Removal and Repair ▸ Noise Gate…")


def test_chainless_presets_keep_their_fingerprint_payload(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(jobs, "run_process", FakeFfmpeg())
    ir, input_root, root, ffmpeg = _setup(tmp_path)
    plain = master_audio(ir, input_root, root, ffmpeg)
    clean = master_audio({**ir, "preset": "voice-clean-mono.v1"}, input_root, root, ffmpeg)
    assert plain.fingerprint != clean.fingerprint
    assert jobs._filter_graph(PRESETS["podcast-mono.v1"]) == jobs._loudnorm_graph(PRESETS["podcast-mono.v1"])


@pytest.mark.parametrize(
    ("preset", "lufs", "tp", "channels"),
    [
        ("apple-music-stereo.v1", -16.0, -1.0, 2),
        ("broadcast-r128-stereo.v1", -23.0, -1.0, 2),
        ("audiobook-acx-mono.v1", -20.0, -3.0, 1),
        ("voice-clean-mono.v1", -19.0, -1.5, 1),
    ],
)
def test_platform_presets_are_registered(preset: str, lufs: float, tp: float, channels: int) -> None:
    value = PRESETS[preset]
    assert (value.integrated_lufs, value.true_peak_dbtp, value.channels) == (lufs, tp, channels)
    assert preset in PLATFORM_TARGETS.values()
    assert AudioIR.model_validate({"schema_version": "audio-ir.v1", "audio_id": "p", "source_path": "a.wav", "preset": preset}).preset == preset
    assert f"I={lufs:.1f}" in jobs._loudnorm_graph(value) and f"TP={tp:.1f}" in jobs._loudnorm_graph(value)
