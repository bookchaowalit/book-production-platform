"""Queue adapters and ingestion job idempotency."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any, Protocol
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import MediaAsset, ProcessingJob, ProcessingStage

if TYPE_CHECKING:
    from .stages import StageRunner


class JobQueue(Protocol):
    """Queue boundary carrying only stable job and stage identifiers."""

    def enqueue(self, job_id: UUID, stage_name: str) -> None:
        """Enqueue a named stage for a persisted job."""


class CeleryJobQueue:
    """Celery adapter that never serializes local paths or shell commands."""

    def __init__(self, app: Any, task_name: str = "video_postproduction.jobs.tasks.run_stage") -> None:
        self.app = app
        self.task_name = task_name

    def enqueue(self, job_id: UUID, stage_name: str) -> None:
        self.app.send_task(self.task_name, args=[str(job_id), stage_name])


class InlineJobQueue:
    """Synchronous queue used by tests and local operator workflows."""

    def __init__(self, runner: StageRunner | None = None) -> None:
        self.runner = runner
        self.calls: list[tuple[UUID, str]] = []

    def enqueue(self, job_id: UUID, stage_name: str) -> None:
        self.calls.append((job_id, stage_name))
        if self.runner is None:
            return
        handler: Callable[[UUID], object]
        if stage_name == "probe":
            handler = self.runner.run_probe
        elif stage_name == "transcribe":
            handler = self.runner.run_transcribe
        else:
            raise ValueError(f"unknown inline stage: {stage_name}")
        handler(job_id)


def enqueue_asset_pipeline(session: Session, queue: JobQueue, asset_id: UUID) -> ProcessingJob:
    """Create one ingest job per asset fingerprint, then enqueue its probe stage."""

    asset = session.get(MediaAsset, asset_id)
    if asset is None:
        raise ValueError("media asset not found")
    idempotency_key = f"ingest:{asset.id}:{asset.sha256}"
    existing = session.scalar(
        select(ProcessingJob).where(ProcessingJob.idempotency_key == idempotency_key)
    )
    if existing is not None:
        return existing

    job = ProcessingJob(
        project_id=asset.project_id,
        asset_id=asset.id,
        kind="ingest",
        status="queued",
        idempotency_key=idempotency_key,
    )
    session.add(job)
    session.flush()
    session.add(ProcessingStage(job_id=job.id, name="probe", status="queued"))
    session.add(ProcessingStage(job_id=job.id, name="transcribe", status="queued"))
    session.commit()
    queue.enqueue(job.id, "probe")
    return job
