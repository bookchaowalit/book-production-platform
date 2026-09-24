from __future__ import annotations

import json
from pathlib import Path

import pytest

from book_production_platform.cli import main
from tests.audio_production.test_jobs import _ffmpeg, _write_synthetic_voice


def test_top_level_cli_masters_audio_ir_and_emits_json(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    input_root = tmp_path / "input"
    input_root.mkdir()
    _write_synthetic_voice(input_root / "voice.wav")
    ir_path = tmp_path / "audio-ir.json"
    ir_path.write_text(
        json.dumps({
            "schema_version": "audio-ir.v1",
            "audio_id": "cli-demo",
            "source_path": "voice.wav",
            "preset": "podcast-mono.v1",
        }),
        encoding="utf-8",
    )

    exit_code = main([
        "audio", "master", "--audio-ir", str(ir_path), "--input-root", str(input_root),
        "--output-root", str(tmp_path / "jobs"), "--ffmpeg", str(_ffmpeg()),
    ])

    output = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert output["status"] == "accepted"
    assert output["model_calls"] == 0
    assert Path(output["master_path"]).is_file()
