"""Recovery of jobs left running after a process interruption."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import Settings
from ..models import ProcessingJob, ProcessingStage


def recover_stale_jobs(
    session: Session,
    settings: Settings | None = None,
    *,
    now: datetime | None = None,
) -> int:
    """Move stale running jobs/stages back to a retryable queued state."""

    runtime_settings = settings or Settings()
    current = now or datetime.now(UTC)
    threshold = current - timedelta(seconds=runtime_settings.job_lease_seconds)
    jobs = session.scalars(select(ProcessingJob).where(ProcessingJob.status == "running")).all()
    recovered = 0
    for job in jobs:
        if job.started_at is None:
            continue
        started_at = job.started_at
        if started_at.tzinfo is None:
            started_at = started_at.replace(tzinfo=UTC)
        if started_at > threshold:
            continue
        job.status = "retry_queued"
        job.error_code = "job_lease_expired"
        job.error_message = "Job lease expired before the stage completed"
        stages = session.scalars(select(ProcessingStage).where(ProcessingStage.job_id == job.id)).all()
        for stage in stages:
            if stage.status == "running":
                stage.status = "queued"
                stage.error_code = "job_lease_expired"
                stage.error_message = "Stage lease expired before completion"
                stage.finished_at = current
        recovered += 1
    if recovered:
        session.commit()
    return recovered
