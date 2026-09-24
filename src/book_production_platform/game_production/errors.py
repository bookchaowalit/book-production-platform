"""Stable errors for the local Unity game production pipeline."""

from __future__ import annotations


class GameJobError(RuntimeError):
    """A job or Unity stage failed with a stable machine-readable code."""

    def __init__(self, code: str, message: str = "Game production job failed") -> None:
        self.code = code
        super().__init__(message)
