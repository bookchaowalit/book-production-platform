"""Metadata-only cost accounting for local document jobs."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

COST_EVENT_SCHEMA_VERSION = "cost-event-v2"


@dataclass(frozen=True)
class CostEvent:
    job_id: str
    attempt_id: str
    status: str
    model_calls: int
    wall_seconds: float
    cpu_seconds: float
    bytes_written: int
    replayed: bool
    cost_status: str
    estimated_cost_usd: float | None
    actual_cost_usd: float | None
    rate_card_version: str
    schema_version: str = COST_EVENT_SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def build_cost_event(
    *,
    job_id: str,
    model_calls: int,
    cpu_seconds: float,
    bytes_written: int,
    status: str,
    attempt_id: str | None = None,
    wall_seconds: float | None = None,
    replayed: bool = False,
) -> CostEvent:
    """Build a cost event without pretending unmetered local compute is free."""

    return CostEvent(
        job_id=job_id,
        attempt_id=attempt_id or job_id,
        status=status,
        model_calls=model_calls,
        wall_seconds=round(cpu_seconds if wall_seconds is None else wall_seconds, 6),
        cpu_seconds=round(cpu_seconds, 6),
        bytes_written=bytes_written,
        replayed=replayed,
        cost_status="unknown",
        estimated_cost_usd=None,
        actual_cost_usd=None,
        rate_card_version="local-v1",
    )
