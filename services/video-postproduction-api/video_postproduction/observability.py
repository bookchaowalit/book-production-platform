"""Small structured event helper for job lifecycle observability."""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from typing import Any
from uuid import UUID

logger = logging.getLogger("video_postproduction.jobs")


def emit_job_event(
    event: str,
    *,
    job_id: UUID,
    stage: str,
    status: str,
    fields: Mapping[str, Any] | None = None,
) -> None:
    """Emit a bounded JSON event without paths, tokens, or provider payloads."""

    payload: dict[str, Any] = {
        "event": event,
        "job_id": str(job_id),
        "stage": stage,
        "status": status,
    }
    if fields:
        payload.update(fields)
    logger.info("job_event %s", json.dumps(payload, sort_keys=True, default=str))
