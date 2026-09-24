"""Cost and replay counters for the local Graphics V1A contract."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class BatchTelemetry:
    fresh_count: int = 0
    replay_count: int = 0
    bytes_written: int = 0
    model_calls: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
