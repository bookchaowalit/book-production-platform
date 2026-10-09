"""Small, explicit telemetry envelope for Data V1A jobs."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class JobTelemetry:
    model_calls: int = 0
    bytes_written: int = 0
    replayed: bool = False

    def to_dict(self) -> dict[str, int | bool]:
        return {"model_calls": self.model_calls, "bytes_written": self.bytes_written, "replayed": self.replayed}


__all__ = ["JobTelemetry"]
