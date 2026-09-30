from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "audio_boundary_check.py"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("audio_boundary_check", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


boundary = _load()


def _sibling(root: Path, source: str = "SCHEMA = 'audio-batch.v1'\n") -> Path:
    package = root / "src" / "book_audio_production"
    package.mkdir(parents=True)
    (package / "manifest.py").write_text(source, encoding="utf-8")
    (package / "toolchain.lock.json").write_text(
        json.dumps({"ffmpeg_version": "ffmpeg version 9.0.1-test"}), encoding="utf-8"
    )
    return root


def test_real_platform_audio_modules_respect_the_boundary(tmp_path: Path) -> None:
    sibling = _sibling(tmp_path / "sibling")

    assert boundary.violations(boundary.PLATFORM_AUDIO, sibling / "src" / "book_audio_production") == []


@pytest.mark.parametrize(
    ("side", "source", "needle"),
    [
        ("platform", "SCHEMA = 'audio-batch.v1'\n", "handles audio-batch.v1"),
        ("platform", "import book_audio_production\n", "imports book_audio_production"),
        ("sibling", "SCHEMA = 'audio-ir.v1'\n", "handles audio-ir.v1"),
        ("sibling", "from book_production_platform import audio_ir\n", "imports book_production_platform"),
    ],
)
def test_crossing_the_boundary_is_reported(tmp_path: Path, side: str, source: str, needle: str) -> None:
    platform = tmp_path / "platform"
    platform.mkdir()
    (platform / "jobs.py").write_text("X = 1\n" if side == "sibling" else source, encoding="utf-8")
    sibling = _sibling(tmp_path / "sibling", source if side == "sibling" else "X = 1\n")

    problems = boundary.violations([platform], sibling / "src" / "book_audio_production")

    assert len(problems) == 1 and needle in problems[0]


def test_main_reports_pin_and_exit_codes(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert boundary.main(["--sibling", str(tmp_path / "missing")]) == 2

    sibling = _sibling(tmp_path / "sibling")
    assert boundary.main(["--sibling", str(sibling)]) == 0
    out = capsys.readouterr().out
    assert "ffmpeg version 9.0.1-test" in out and "boundary holds" in out

    (sibling / "src" / "book_audio_production" / "extra.py").write_text("V = 'audio-ir.v1'\n", encoding="utf-8")
    assert boundary.main(["--sibling", str(sibling)]) == 1
    assert boundary.pinned_ffmpeg(tmp_path / "missing") is None
