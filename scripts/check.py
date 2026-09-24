"""Run repository tests and focused static checks for the shared platform."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GRAPHICS = ROOT / "src" / "book_production_platform" / "graphics_production"
GRAPHICS_IR = ROOT / "src" / "book_production_platform" / "graphics_ir"
GRAPHICS_TESTS = ROOT / "tests" / "graphics"
SPREADSHEET_IR = ROOT / "src" / "book_production_platform" / "spreadsheet_ir"
SPREADSHEET = ROOT / "src" / "book_production_platform" / "spreadsheet_production"
GAME_IR = ROOT / "src" / "book_production_platform" / "game_ir"
GAME = ROOT / "src" / "book_production_platform" / "game_production"
AUDIO_IR = ROOT / "src" / "book_production_platform" / "audio_ir"
AUDIO = ROOT / "src" / "book_production_platform" / "audio_production"
SPREADSHEET_IR_TESTS = ROOT / "tests" / "spreadsheet_ir"
SPREADSHEET_TESTS = ROOT / "tests" / "spreadsheet_production"
GAME_IR_TESTS = ROOT / "tests" / "game_ir"
GAME_TESTS = ROOT / "tests" / "game_production"
AUDIO_IR_TESTS = ROOT / "tests" / "audio_ir"
AUDIO_TESTS = ROOT / "tests" / "audio_production"
PLATFORM_CLI = ROOT / "src" / "book_production_platform" / "cli.py"
RUFF_TARGETS = (
    GRAPHICS_IR,
    GRAPHICS,
    GRAPHICS_TESTS,
    SPREADSHEET_IR,
    SPREADSHEET,
    SPREADSHEET_IR_TESTS,
    SPREADSHEET_TESTS,
    GAME_IR_TESTS,
    GAME_TESTS,
    AUDIO_IR_TESTS,
    AUDIO_TESTS,
    AUDIO_IR,
    AUDIO,
    PLATFORM_CLI,
    GAME_IR,
    GAME,
)
MYPY_TARGETS = (GRAPHICS_IR, GRAPHICS, SPREADSHEET_IR, SPREADSHEET, GAME_IR, GAME, AUDIO_IR, AUDIO)


def main() -> int:
    commands = (
        [sys.executable, "-m", "pytest", "-q"],
        [sys.executable, "-m", "ruff", "check", *(str(path) for path in RUFF_TARGETS)],
        [sys.executable, "-m", "mypy", "--strict", *(str(path) for path in MYPY_TARGETS)],
    )
    for command in commands:
        result = subprocess.run(command, cwd=ROOT, check=False)
        if result.returncode:
            return result.returncode
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
