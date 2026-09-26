"""Stable, safe error type shared by the cross-product pipelines."""

from __future__ import annotations


class PipelineError(ValueError):
    """A rejected pipeline request with a machine-readable failure code."""

    def __init__(self, code: str, message: str = "Pipeline request was rejected") -> None:
        self.code = code
        super().__init__(message)
