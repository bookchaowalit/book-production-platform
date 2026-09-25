"""Stable error boundary for local CAD production jobs."""

from __future__ import annotations


class CadProductionError(ValueError):
    """A CAD job failed without retaining raw dimensions or local paths."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)
