from __future__ import annotations

import hashlib
import json
import math
import shutil
import struct
import wave
from pathlib import Path

import pytest

from book_production_platform.audio_production import master_audio
from book_production_platform.audio_production.runner import ProcessOutput


def _write_synthetic_voice(path: Path) -> None:
    sample_rate = 44_100
    frames = bytearray()
    for index in range(sample_rate * 3):
        time_s = index / sample_rate
        syllable = 0.25 + 0.75 * (0.5 + 0.5 * math.sin(2 * math.pi * 2.2 * time_s))
        sample = syllable * (0.11 * math.sin(2 * math.pi * 173 * time_s) + 0.06 * math.sin(2 * math.pi * 281 * time_s))
        frames.extend(struct.pack("<h", round(sample * 32767)))
    with wave.open(str(path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(sample_rate)
        output.writeframes(frames)


def _write_silence(path: Path) -> None:
    with wave.open(str(path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(44_100)
        output.writeframes(b"\0\0" * 44_100)


def _audio_ir(source_path: str = "voice.wav") -> dict[str, object]:
    return {
        "schema_version": "audio-ir.v1",
        "audio_id": "voice-demo",
        "source_path": source_path,
        "preset": "podcast-mono.v1",
    }


def _ffmpeg() -> Path:
    configured = shutil.which("ffmpeg")
    if configured:
        return Path(configured)
    candidate = Path(r"C:\Users\bookc\AppData\Local\Microsoft\WinGet\Links\ffmpeg.exe")
    if candidate.is_file():
        return candidate
    pytest.skip("FFmpeg is not installed")


def test_master_audio_writes_lossless_qa_artifact_and_replays(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    input_root = tmp_path / "input"
    input_root.mkdir()
    source = input_root / "voice.wav"
    _write_synthetic_voice(source)
    original_hash = hashlib.sha256(source.read_bytes()).hexdigest()

    first = master_audio(_audio_ir(), input_root, tmp_path / "jobs", _ffmpeg())
    manifest = json.loads(first.manifest_path.read_text(encoding="utf-8"))

    def version_only(argv: list[str], timeout_seconds: float) -> ProcessOutput:
        assert argv[1:] == ["-version"]
        return ProcessOutput(0, f"{manifest['ffmpeg_version']}\n".encode(), b"", 0.01, False)

    monkeypatch.setattr("book_production_platform.audio_production.jobs.run_process", version_only)
    monkeypatch.setattr(
        "book_production_platform.audio_production.jobs._measure",
        lambda *args: pytest.fail("exact replay must skip FFmpeg analysis and rendering"),
    )
    second = master_audio(_audio_ir(), input_root, tmp_path / "jobs", _ffmpeg())

    assert first.status == "accepted", first.error_code
    assert first.model_calls == 0
    assert first.replayed is False
    assert first.master_path is not None and first.master_path.is_file()
    assert first.manifest_path is not None and first.manifest_path.is_file()
    assert first.artifact_hashes == second.artifact_hashes
    assert second.replayed is True
    assert source.is_file()
    assert hashlib.sha256(source.read_bytes()).hexdigest() == original_hash
    assert manifest["model_calls"] == 0
    assert manifest["qa"]["passed"] is True
    with wave.open(str(first.master_path), "rb") as output:
        assert (output.getnchannels(), output.getsampwidth(), output.getframerate()) == (1, 3, 48_000)


def test_stereo_podcast_and_music_presets_master_real_synthetic_wav(tmp_path: Path) -> None:
    input_root = tmp_path / "input"
    input_root.mkdir()
    sample_rate = 44_100
    frames = bytearray()
    for index in range(sample_rate * 3):
        time_s = index / sample_rate
        envelope = 0.25 + 0.75 * (0.5 + 0.5 * math.sin(2 * math.pi * 1.7 * time_s))
        left = envelope * (0.12 * math.sin(2 * math.pi * 217 * time_s) + 0.04 * math.sin(2 * math.pi * 403 * time_s))
        right = envelope * (0.10 * math.sin(2 * math.pi * 223 * time_s) + 0.05 * math.sin(2 * math.pi * 397 * time_s))
        frames.extend(struct.pack("<hh", round(left * 32767), round(right * 32767)))
    with wave.open(str(input_root / "stereo.wav"), "wb") as output:
        output.setnchannels(2)
        output.setsampwidth(2)
        output.setframerate(sample_rate)
        output.writeframes(frames)

    for preset, target_lufs in (("podcast-stereo.v1", -16.0), ("music-stereo.v1", -14.0)):
        result = master_audio(
            {
                "schema_version": "audio-ir.v1",
                "audio_id": preset.replace(".", "-"),
                "source_path": "stereo.wav",
                "preset": preset,
            },
            input_root,
            tmp_path / "jobs",
            _ffmpeg(),
        )

        assert result.status == "accepted", result.error_code
        assert result.master_path is not None
        manifest = json.loads(result.manifest_path.read_text(encoding="utf-8")) if result.manifest_path else {}
        assert manifest["preset"]["integrated_lufs"] == target_lufs
        assert manifest["qa"]["passed"] is True
        with wave.open(str(result.master_path), "rb") as master:
            assert (master.getnchannels(), master.getsampwidth(), master.getframerate()) == (2, 3, 48_000)


def test_master_audio_rejects_path_escape_and_wrong_channel_preset(tmp_path: Path) -> None:
    input_root = tmp_path / "input"
    input_root.mkdir()
    _write_synthetic_voice(input_root / "voice.wav")

    escaped = master_audio(_audio_ir("../voice.wav"), input_root, tmp_path / "jobs", _ffmpeg())
    windows_parent = master_audio(_audio_ir(".. /voice.wav"), input_root, tmp_path / "jobs", _ffmpeg())
    device_name = master_audio(_audio_ir("NUL.wav"), input_root, tmp_path / "jobs", _ffmpeg())
    wrong_preset = master_audio(
        {**_audio_ir(), "preset": "podcast-stereo.v1"},
        input_root,
        tmp_path / "jobs",
        _ffmpeg(),
    )

    assert escaped.status == "rejected" and escaped.error_code == "unsafe_audio_path"
    assert windows_parent.status == "rejected" and windows_parent.error_code == "unsafe_audio_path"
    assert device_name.status == "rejected" and device_name.error_code == "unsafe_audio_path"
    assert wrong_preset.status == "rejected" and wrong_preset.error_code == "preset_channel_mismatch"


def test_master_audio_rejects_tampered_replay(tmp_path: Path) -> None:
    input_root = tmp_path / "input"
    input_root.mkdir()
    _write_synthetic_voice(input_root / "voice.wav")
    first = master_audio(_audio_ir(), input_root, tmp_path / "jobs", _ffmpeg())
    assert first.master_path is not None, first.error_code
    first.master_path.write_bytes(first.master_path.read_bytes() + b"tampered")

    replay = master_audio(_audio_ir(), input_root, tmp_path / "jobs", _ffmpeg())
    assert replay.status == "rejected"
    assert replay.error_code == "replay_integrity_error"


def test_master_audio_rejects_malformed_replay_receipt(tmp_path: Path) -> None:
    input_root = tmp_path / "input"
    input_root.mkdir()
    _write_synthetic_voice(input_root / "voice.wav")
    first = master_audio(_audio_ir(), input_root, tmp_path / "jobs", _ffmpeg())
    assert first.artifact_dir is not None
    (first.artifact_dir / ".receipt.json").write_text("[]", encoding="utf-8")

    replay = master_audio(_audio_ir(), input_root, tmp_path / "jobs", _ffmpeg())

    assert replay.status == "rejected"
    assert replay.error_code == "replay_integrity_error"


def test_master_audio_rejects_silence_without_publishing(tmp_path: Path) -> None:
    input_root = tmp_path / "input"
    input_root.mkdir()
    _write_silence(input_root / "voice.wav")

    result = master_audio(_audio_ir(), input_root, tmp_path / "jobs", _ffmpeg())

    assert result.status == "rejected"
    assert result.error_code == "silent_audio"
    jobs_root = tmp_path / "jobs"
    if jobs_root.exists():
        assert not list(jobs_root.glob("*/master.wav"))
