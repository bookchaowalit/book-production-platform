"""Module entry point: ``python -m production_pipelines <command>``."""

from .cli import main

if __name__ == "__main__":
    raise SystemExit(main())
