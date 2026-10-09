"""Application services for project creation, upload, and response lookup."""

from __future__ import annotations

import os
import tempfile
from dataclasses import asdict
from pathlib import Path
from typing import BinaryIO
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import Settings
from ..jobs.queue import JobQueue
from ..media.ffprobe import FFprobeMetadataReader, MediaProbeError
from ..media.validation import UploadValidationError, stream_upload, validate_media_type
from ..models import MediaAsset, ProcessingJob, ProcessingStage, Project, Transcript
from ..storage.base import StoragePathError, StorageProvider
from .errors import APIError


def create_project(
    session: Session,
    name: str,
    instructions: str | None,
    transcription_quality: str = "fast",
) -> Project:
    project = Project(
        name=name,
        instructions=instructions,
        transcription_quality=transcription_quality,
        status="created",
    )
    session.add(project)
    session.commit()
    return project


def enqueue_accurate_retranscription(
    session: Session,
    queue: JobQueue,
    project_id: UUID,
    asset_id: UUID,
) -> ProcessingJob:
    project = get_project_or_error(session, project_id)
    asset = get_asset_or_error(session, project_id, asset_id)
    if project.transcription_quality != "fast":
        raise APIError("transcription_not_fast", "The project is not using fast transcription.", 409)
    if asset.status != "transcribed":
        raise APIError("transcript_not_ready", "The asset does not have a ready transcript.", 409)
    transcript = session.scalar(
        select(Transcript)
        .where(Transcript.project_id == project_id, Transcript.asset_id == asset_id)
        .order_by(Transcript.created_at.desc())
    )
    if transcript is None or transcript.provider != "faster_whisper" or transcript.model != "small":
        raise APIError("fast_transcript_not_found", "The asset has no fast Whisper transcript to improve.", 409)

    active_job = session.scalar(
        select(ProcessingJob).where(
            ProcessingJob.project_id == project_id,
            ProcessingJob.asset_id == asset_id,
            ProcessingJob.kind == "retranscription",
            ProcessingJob.status.in_(("queued", "running", "retry_queued")),
        )
    )
    if active_job is not None:
        raise APIError("transcription_in_progress", "A transcription job is already in progress.", 409)

    previous_quality = project.transcription_quality
    previous_status = project.status
    job = ProcessingJob(
        project_id=project_id,
        asset_id=asset_id,
        kind="retranscription",
        status="queued",
        idempotency_key=f"retranscribe:{asset_id}:{uuid4()}",
    )
    session.add(job)
    session.flush()
    session.add(ProcessingStage(job_id=job.id, name="transcribe", status="queued"))
    project.transcription_quality = "accurate"
    project.status = "processing"
    session.commit()
    try:
        queue.enqueue(job.id, "transcribe")
    except Exception as exc:
        job.status = "failed"
        job.error_code = "queue_error"
        job.error_message = "The transcription job could not be queued."
        project.transcription_quality = previous_quality
        project.status = previous_status
        session.commit()
        raise APIError("job_queue_unavailable", "The transcription job could not be queued.", 503) from exc
    return job


def get_project_or_error(session: Session, project_id: UUID) -> Project:
    project = session.get(Project, project_id)
    if project is None:
        raise APIError("project_not_found", "The project was not found.", 404)
    return project


def get_asset_or_error(session: Session, project_id: UUID, asset_id: UUID) -> MediaAsset:
    asset = session.scalar(
        select(MediaAsset).where(MediaAsset.id == asset_id, MediaAsset.project_id == project_id)
    )
    if asset is None:
        raise APIError("asset_not_found", "The media asset was not found.", 404)
    return asset


def get_job_or_error(session: Session, job_id: UUID) -> ProcessingJob:
    job = session.get(ProcessingJob, job_id)
    if job is None:
        raise APIError("job_not_found", "The processing job was not found.", 404)
    return job


def upload_asset(
    session: Session,
    storage: StorageProvider,
    probe_reader: FFprobeMetadataReader,
    queue: JobQueue,
    settings: Settings,
    project_id: UUID,
    filename: str | None,
    content_type: str | None,
    source: BinaryIO,
) -> tuple[MediaAsset, ProcessingJob]:
    get_project_or_error(session, project_id)
    if filename is None:
        raise APIError("asset_filename_required", "A filename is required.", 400)
    try:
        normalized_type = validate_media_type(content_type, filename, settings.allowed_mime_types)
    except UploadValidationError as exc:
        raise APIError("asset_type_not_supported", "The uploaded file type is not supported.", 415) from exc

    suffix = Path(filename).suffix.lower()
    asset_id = uuid4()
    storage_key = f"projects/{project_id}/original/{asset_id}{suffix}"
    temporary_path: Path | None = None
    stored = None
    try:
        with tempfile.NamedTemporaryFile(mode="w+b", prefix="video-upload-", suffix=suffix, delete=False) as temporary:
            temporary_path = Path(temporary.name)
            try:
                stream_upload(source, temporary, settings.max_upload_bytes)
            except UploadValidationError as exc:
                raise APIError("asset_too_large", "The uploaded file exceeds the maximum size.", 413) from exc
            temporary.flush()
            os.fsync(temporary.fileno())

        with temporary_path.open("rb") as temporary_source:
            stored = storage.put_atomic(storage_key, temporary_source, normalized_type)
        try:
            metadata = probe_reader.read(storage.local_path(storage_key))
        except MediaProbeError as exc:
            storage.delete(storage_key)
            raise APIError("asset_not_readable", "The uploaded file is not a readable video.", 422) from exc

        asset = MediaAsset(
            id=asset_id,
            project_id=project_id,
            storage_key=stored.storage_key,
            original_filename=filename,
            media_type=normalized_type,
            size_bytes=stored.size_bytes,
            sha256=stored.sha256,
            metadata_json=asdict(metadata),
            status="uploaded",
        )
        job = ProcessingJob(
            project_id=project_id,
            asset_id=asset.id,
            kind="ingest",
            status="queued",
            idempotency_key=f"ingest:{asset.id}:{asset.sha256}",
        )
        session.add(asset)
        session.add(job)
        session.flush()
        session.add(ProcessingStage(job_id=job.id, name="probe", status="queued"))
        session.add(ProcessingStage(job_id=job.id, name="transcribe", status="queued"))
        session.commit()
        try:
            queue.enqueue(job.id, "probe")
        except Exception as exc:
            job.status = "failed"
            job.error_code = "queue_error"
            job.error_message = "The processing job could not be queued."
            session.commit()
            raise APIError("job_queue_unavailable", "The processing job could not be queued.", 503) from exc
        return asset, job
    except APIError:
        raise
    except (OSError, StoragePathError) as exc:
        if stored is not None:
            storage.delete(storage_key)
        raise APIError("asset_storage_error", "The uploaded file could not be stored.", 500) from exc
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
