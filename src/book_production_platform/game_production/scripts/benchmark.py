"""Run the Game Scene Builder acceptance benchmark as a Python module."""

from __future__ import annotations

import sys

from book_production_platform.game_production.cli import game_main


def main(argv: list[str] | None = None) -> int:
    """Run `book-production game benchmark` with the provided arguments."""
    arguments = sys.argv[1:] if argv is None else argv
    return game_main(["benchmark", *arguments])


if __name__ == "__main__":
    raise SystemExit(main())
