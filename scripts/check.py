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
DATA_PIPELINE_IR = ROOT / "src" / "book_production_platform" / "data_pipeline_ir"
DATA_PIPELINE = ROOT / "src" / "book_production_platform" / "data_pipeline"
SPREADSHEET_IR_TESTS = ROOT / "tests" / "spreadsheet_ir"
SPREADSHEET_TESTS = ROOT / "tests" / "spreadsheet_production"
GAME_IR_TESTS = ROOT / "tests" / "game_ir"
GAME_TESTS = ROOT / "tests" / "game_production"
AUDIO_IR_TESTS = ROOT / "tests" / "audio_ir"
AUDIO_TESTS = ROOT / "tests" / "audio_production"
DATA_PIPELINE_IR_TESTS = ROOT / "tests" / "data_pipeline_ir"
DATA_PIPELINE_TESTS = ROOT / "tests" / "data_pipeline"
CAD_IR = ROOT / "src" / "book_production_platform" / "cad_ir"
BIM_IR = ROOT / "src" / "book_production_platform" / "bim_ir"
BIM_TESTS = ROOT / "tests" / "bim_ir"
CAD_PRODUCTION = ROOT / "src" / "book_production_platform" / "cad_production"
CAD_IR_TESTS = ROOT / "tests" / "cad_ir"
CAD_PRODUCTION_TESTS = ROOT / "tests" / "cad_production"
BIM_PRODUCTION = ROOT / "src" / "book_production_platform" / "bim_production"
BIM_PRODUCTION_TESTS = ROOT / "tests" / "bim_production"
PIPELINE = ROOT / "src" / "book_production_platform" / "pipeline"
PIPELINE_TESTS = ROOT / "tests" / "pipeline"
PLATFORM_CLI = ROOT / "src" / "book_production_platform" / "cli.py"
DOCTOR = ROOT / "src" / "book_production_platform" / "doctor.py"
DOCTOR_TESTS = ROOT / "tests" / "test_doctor.py"
SCRIPTS = ROOT / "scripts"
PARITY_TESTS = ROOT / "tests" / "test_graphics_fork_parity.py"
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
    DATA_PIPELINE_IR_TESTS,
    DATA_PIPELINE_TESTS,
    DATA_PIPELINE_IR,
    DATA_PIPELINE,
    CAD_IR,
    CAD_PRODUCTION,
    CAD_IR_TESTS,
    CAD_PRODUCTION_TESTS,
    BIM_PRODUCTION,
    BIM_PRODUCTION_TESTS,
    BIM_IR,
    BIM_TESTS,
    PIPELINE,
    PIPELINE_TESTS,
    PLATFORM_CLI,
    DOCTOR,
    DOCTOR_TESTS,
    GAME_IR,
    GAME,
    SCRIPTS,
    PARITY_TESTS,
    ROOT / "tests" / "test_audio_boundary_check.py",
)
MYPY_TARGETS = (
    GRAPHICS_IR,
    GRAPHICS,
    SPREADSHEET_IR,
    SPREADSHEET,
    GAME_IR,
    GAME,
    AUDIO_IR,
    AUDIO,
    DATA_PIPELINE_IR,
    DATA_PIPELINE,
    CAD_IR,
    CAD_PRODUCTION,
    BIM_IR,
    BIM_PRODUCTION,
    PIPELINE,
    DOCTOR,
    SCRIPTS / "graphics_fork_parity.py",
    SCRIPTS / "audio_boundary_check.py",
)


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
