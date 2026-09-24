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
SPREADSHEET_IR_TESTS = ROOT / "tests" / "spreadsheet_ir"
SPREADSHEET_TESTS = ROOT / "tests" / "spreadsheet_production"
RUFF_TARGETS = (
    GRAPHICS_IR,
    GRAPHICS,
    GRAPHICS_TESTS,
    SPREADSHEET_IR,
    SPREADSHEET,
    SPREADSHEET_IR_TESTS,
    SPREADSHEET_TESTS,
)
MYPY_TARGETS = (GRAPHICS_IR, GRAPHICS, SPREADSHEET_IR, SPREADSHEET)


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
