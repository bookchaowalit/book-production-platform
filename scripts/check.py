"""Run repository tests and focused static checks for the shared platform."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
GRAPHICS = ROOT / "src" / "book_production_platform" / "graphics_production"
GRAPHICS_IR = ROOT / "src" / "book_production_platform" / "graphics_ir"
GRAPHICS_TESTS = ROOT / "tests" / "graphics"


def main() -> int:
    commands = (
        [sys.executable, "-m", "pytest", "-q"],
        [sys.executable, "-m", "ruff", "check", str(GRAPHICS_IR), str(GRAPHICS), str(GRAPHICS_TESTS)],
        [sys.executable, "-m", "mypy", "--strict", str(GRAPHICS_IR), str(GRAPHICS)],
    )
    for command in commands:
        result = subprocess.run(command, cwd=ROOT, check=False)
        if result.returncode:
            return result.returncode
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
