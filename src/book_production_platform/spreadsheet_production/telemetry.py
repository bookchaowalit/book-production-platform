"""Replay and cost counters for Spreadsheet V1A jobs."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class JobTelemetry:
    replayed: bool = False
    bytes_written: int = 0
    model_calls: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
