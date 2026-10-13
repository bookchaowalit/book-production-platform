"""Compare the canonical graphics module with the older book-graphics-production fork.

This repository owns graphics production (``graphics_production`` and
``graphics_ir``). ``book-graphics-production`` is a frozen fork kept only until
it is archived; safety fixes are mirrored there, features are not. This script
reports how far the fork has drifted so that a fix made here is not forgotten
there (and so nothing that exists only in the fork is lost on archive).

Usage::

    python scripts/graphics_fork_parity.py --fork ../book-apps/tools/content-graphics-cli

Exit status is 1 when the fork has a module this repository lacks, or, with
``--strict``, when any shared module differs. Import paths are normalised
(``book_production_platform.graphics_ir`` -> ``graphics_ir``) and import-block
ordering is ignored before comparing.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLATFORM_PAIRS = (
    (ROOT / "src" / "book_production_platform" / "graphics_production", Path("services/graphics-production-api/graphics_production")),
    (ROOT / "src" / "book_production_platform" / "graphics_ir", Path("packages/graphics-production-ir/graphics_ir")),
)
_IMPORT = re.compile(r"^(from \S+ import .*|import \S+.*)$")


def normalise(source: str) -> list[str]:
    """Return comparable lines: platform import paths rewritten, imports sorted, blanks dropped."""

    text = source.replace("book_production_platform.graphics_ir", "graphics_ir")
    # Join parenthesised multi-line imports so formatting differences vanish.
    text = re.sub(
        r"^(from \S+ import )\(\s*([^()]*?)\s*,?\s*\)",
        lambda m: m.group(1) + " ".join(m.group(2).replace(",", ", ").split()),
        text,
        flags=re.MULTILINE,
    )
    lines = [line.rstrip() for line in text.splitlines() if line.strip()]
    imports = sorted(line for line in lines if _IMPORT.match(line))
    body = [line for line in lines if not _IMPORT.match(line)]
    return imports + body


def _modules(directory: Path) -> set[str]:
    if not directory.is_dir():
        return set()
    return {path.relative_to(directory).as_posix() for path in directory.rglob("*.py") if "__pycache__" not in path.parts}


def compare(platform_dir: Path, fork_dir: Path) -> dict[str, list[str]]:
    ours, theirs = _modules(platform_dir), _modules(fork_dir)
    report: dict[str, list[str]] = {"identical": [], "diverged": [], "only_in_platform": [], "only_in_fork": []}
    for name in sorted(ours | theirs):
        if name not in theirs:
            report["only_in_platform"].append(name)
        elif name not in ours:
            report["only_in_fork"].append(name)
        elif normalise((platform_dir / name).read_text(encoding="utf-8")) == normalise((fork_dir / name).read_text(encoding="utf-8")):
            report["identical"].append(name)
        else:
            report["diverged"].append(name)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--fork", type=Path, default=ROOT.parent / "book-apps" / "tools" / "content-graphics-cli", help="path to a book-graphics-production checkout (folder content-graphics-cli)")
    parser.add_argument("--strict", action="store_true", help="also fail when a shared module differs")
    args = parser.parse_args(argv)
    if not args.fork.is_dir():
        print(f"fork checkout not found: {args.fork}", file=sys.stderr)
        return 2
    failed = False
    for platform_dir, fork_relative in PLATFORM_PAIRS:
        report = compare(platform_dir, args.fork / fork_relative)
        print(f"{platform_dir.name} <-> {fork_relative.as_posix()}")
        for status, names in report.items():
            print(f"  {status}: {', '.join(names) if names else '-'}")
        failed |= bool(report["only_in_fork"]) or (args.strict and bool(report["diverged"]))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
