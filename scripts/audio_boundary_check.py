"""Check the ownership boundary between Audio Master here and book-audio-production.

Unlike graphics, ``audio_production`` is not a fork of ``book-audio-production``:
the two share FFmpeg loudness normalisation but own different contracts.

* This repository owns ``audio-ir.v1`` (Audio Master V1A: one PCM WAV, one of
  the fixed presets, ``book-production audio master``).
* ``book-audio-production`` owns ``audio-batch.v1`` (batch trim + loudness
  targets per track, ``book-audio run``) and pins the FFmpeg build it accepts.

This script fails when either side starts handling the other's contract or
imports the other's package, which is how a silent second implementation would
begin. It also prints the sibling's pinned FFmpeg build for comparison with the
version recorded in Audio Master manifests (``ffmpeg_version``).

Usage::

    python scripts/audio_boundary_check.py --sibling ../book-apps/tools/content-audio-mastering-cli
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLATFORM_AUDIO = (
    ROOT / "src" / "book_production_platform" / "audio_production",
    ROOT / "src" / "book_production_platform" / "audio_ir",
)
SIBLING_PACKAGE = Path("src/book_audio_production")
SIBLING_LOCK = SIBLING_PACKAGE / "toolchain.lock.json"
PLATFORM_CONTRACT = "audio-ir.v1"
SIBLING_CONTRACT = "audio-batch.v1"
PLATFORM_IMPORT = "book_production_platform"
SIBLING_IMPORT = "book_audio_production"


def _sources(directories: tuple[Path, ...] | list[Path]) -> dict[str, str]:
    found: dict[str, str] = {}
    for directory in directories:
        if not directory.is_dir():
            continue
        for path in sorted(directory.rglob("*")):
            if "__pycache__" in path.parts or path.suffix not in {".py", ".json"}:
                continue
            found[path.as_posix()] = path.read_text(encoding="utf-8")
    return found


def violations(platform_dirs: tuple[Path, ...] | list[Path], sibling_package: Path) -> list[str]:
    """Return one line per boundary violation (empty when the boundary holds)."""

    problems: list[str] = []
    for name, text in _sources(platform_dirs).items():
        if SIBLING_CONTRACT in text:
            problems.append(f"{name}: platform handles {SIBLING_CONTRACT}, owned by book-audio-production")
        if SIBLING_IMPORT in text:
            problems.append(f"{name}: platform imports {SIBLING_IMPORT}")
    for name, text in _sources([sibling_package]).items():
        if PLATFORM_CONTRACT in text:
            problems.append(f"{name}: book-audio-production handles {PLATFORM_CONTRACT}, owned by this repository")
        if PLATFORM_IMPORT in text:
            problems.append(f"{name}: book-audio-production imports {PLATFORM_IMPORT}")
    return problems


def pinned_ffmpeg(sibling_root: Path) -> str | None:
    """Return the sibling's pinned ``ffmpeg -version`` first line, if readable."""

    try:
        value = json.loads((sibling_root / SIBLING_LOCK).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    version = value.get("ffmpeg_version") if isinstance(value, dict) else None
    return version if isinstance(version, str) else None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--sibling",
        type=Path,
        default=ROOT.parent / "book-apps" / "tools" / "content-audio-mastering-cli",
        help="path to a book-audio-production checkout (folder content-audio-mastering-cli)",
    )
    args = parser.parse_args(argv)
    package = args.sibling / SIBLING_PACKAGE
    if not package.is_dir():
        print(f"book-audio-production checkout not found: {args.sibling}", file=sys.stderr)
        return 2
    problems = violations(PLATFORM_AUDIO, package)
    print(f"platform contract: {PLATFORM_CONTRACT}; sibling contract: {SIBLING_CONTRACT}")
    print(f"sibling pinned FFmpeg: {pinned_ffmpeg(args.sibling) or 'unreadable'}")
    for problem in problems:
        print(f"  violation: {problem}")
    print("boundary holds" if not problems else f"{len(problems)} boundary violation(s)")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
