import json
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
from synthetic_fixtures import synthetic_artifact_payload

from video_postpipeline.cli import main

FFMPEG_AVAILABLE = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


def run_cli(*args) -> SimpleNamespace:
    try:
        return_code = main(list(args))
        return SimpleNamespace(returncode=return_code, stdout="", stderr="")
    except SystemExit as exc:  # argparse-level failures
        return SimpleNamespace(returncode=int(exc.code or 0), stdout="", stderr="")


def write_artifact(tmp_path: Path) -> Path:
    path = tmp_path / "artifact.json"
    path.write_text(json.dumps(synthetic_artifact_payload()), encoding="utf-8")
    return path


def test_validate_accepts_a_valid_artifact(tmp_path, capsys):
    assert run_cli("validate", "--input", str(write_artifact(tmp_path))).returncode == 0
    assert "demo-001" in capsys.readouterr().out


def test_validate_rejects_an_invalid_artifact_with_exit_code_2(tmp_path, capsys):
    path = tmp_path / "invalid.json"
    path.write_text(json.dumps({"schema_version": "transcript-artifact.v2"}), encoding="utf-8")
    assert run_cli("validate", "--input", str(path)).returncode == 2
    assert "input error" in capsys.readouterr().err


def test_validate_rejects_unreadable_json_with_exit_code_2(tmp_path, capsys):
    path = tmp_path / "broken.json"
    path.write_text("{not json", encoding="utf-8")
    assert run_cli("validate", "--input", str(path)).returncode == 2
    assert "input error" in capsys.readouterr().err


def test_subtitles_command_writes_requested_formats(tmp_path, capsys):
    output_dir = tmp_path / "subs"
    assert run_cli("subtitles", "--input", str(write_artifact(tmp_path)), "--output-dir", str(output_dir), "--format", "srt").returncode == 0
    assert (output_dir / "demo-001.srt").stat().st_size > 0
    assert not (output_dir / "demo-001.vtt").exists()
    capsys.readouterr()


def test_transcript_request_command_emits_a_valid_payload(tmp_path, capsys):
    target = tmp_path / "request.json"
    assert run_cli(
        "transcript-request",
        "--input", str(write_artifact(tmp_path)),
        "--output", str(target),
        "--issue-date", "2026-09-26",
    ).returncode == 0
    payload = json.loads(target.read_text(encoding="utf-8"))
    assert payload["schema_version"] == "transcript-document-request.v1"
    assert payload["issue_date"] == "2026-09-26"
    capsys.readouterr()


def test_transcript_request_rejects_a_bad_date_with_exit_code_2(tmp_path, capsys):
    assert run_cli(
        "transcript-request",
        "--input", str(write_artifact(tmp_path)),
        "--output", str(tmp_path / "request.json"),
        "--issue-date", "26-09-2026",
    ).returncode == 2
    assert "input error" in capsys.readouterr().err


def test_missing_required_cli_argument_exits_with_usage_code_2(tmp_path):
    assert run_cli("transcript-request", "--input", str(write_artifact(tmp_path))).returncode == 2


@pytest.mark.skipif(not FFMPEG_AVAILABLE, reason="ffmpeg/ffprobe are not on PATH")
def test_extract_audio_command_writes_manifest(tmp_path, capsys):
    video = tmp_path / "clip.mp4"
    argv = [
        "ffmpeg", "-y", "-v", "error",
        "-f", "lavfi", "-i", "sine=frequency=440:duration=1",
        "-f", "lavfi", "-i", "color=c=black:s=320x240:d=1",
        "-shortest", str(video),
    ]
    result = subprocess.run(argv, capture_output=True, text=True, timeout=60, shell=False, check=False)
    assert result.returncode == 0, result.stderr
    output_dir = tmp_path / "audio"
    assert run_cli("extract-audio", "--input", str(video), "--output-dir", str(output_dir), "--format", "wav").returncode == 0
    captured = capsys.readouterr().out
    assert "codec=pcm_s24le" in captured
    assert (output_dir / "audio-batch.v1.json").is_file()


@pytest.mark.skipif(not FFMPEG_AVAILABLE, reason="ffmpeg/ffprobe are not on PATH")
def test_extract_audio_missing_input_exits_with_media_error_code(tmp_path, capsys):
    assert run_cli("extract-audio", "--input", str(tmp_path / "missing.mp4"), "--output-dir", str(tmp_path / "out")).returncode == 4
    assert "media error" in capsys.readouterr().err
