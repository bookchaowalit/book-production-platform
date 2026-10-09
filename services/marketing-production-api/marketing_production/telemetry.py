"""Small explicit telemetry envelope for Marketing V1A jobs."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class JobTelemetry:
    model_calls: int = 0
    replayed: bool = False
    bytes_written: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "model_calls": self.model_calls,
            "replayed": self.replayed,
            "bytes_written": self.bytes_written,
        }
