"""Stable errors for untrusted GameIR input."""

from __future__ import annotations


class GameInputError(ValueError):
    """A GameIR payload failed a stable input or resource check."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)
