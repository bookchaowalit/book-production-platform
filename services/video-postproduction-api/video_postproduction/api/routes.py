"""Versioned V1A project, asset, job, transcript, EDL, and stream routes."""

from __future__ import annotations

import re
from uuid import UUID

from fastapi import APIRouter, Body, Depends, File, Query, UploadFile
from fastapi.responses import FileResponse
from production_trace import read_history
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import Settings
from ..edl import EDLDocument, validate_edl
from ..exports import ExportError, ExportService
from ..jobs.queue import JobQueue
from ..jobs.stages import StageRunner
from ..jobtrace import JOB_FILES, media_type_for
from ..media.ffprobe import FFprobeMetadataReader
from ..models import (
    EDL,
    MediaAsset,
    ProcessingJob,
    ProcessingStage,
    Project,
    RenderOutput,
    Transcript,
)
from ..render.service import RenderService
from ..schemas import (
    AssetListResponse,
    AssetResponse,
    AssetUploadResponse,
    EDLResponse,
    ExportRequest,
    ExportResponse,
    JobHistoryResponse,
    JobResponse,
    ProjectCreate,
    ProjectListResponse,
    ProjectResponse,
    RenderOutputResponse,
    RenderRequest,
    RetryRequest,
    TranscriptionCapabilities,
    TranscriptResponse,
)
from ..storage.base import StorageProvider
from .dependencies import (
    get_job_queue,
    get_probe_reader,
    get_render_service,
    get_session,
    get_settings,
    get_stage_runner,
    get_storage,
    require_write_access,
)
from .errors import APIError
from .services import (
    create_project,
    enqueue_accurate_retranscription,
    get_asset_or_error,
    get_job_or_error,
    get_project_or_error,
    upload_asset,
)

router = APIRouter(prefix="/api/v1")


def _asset_response(asset: MediaAsset) -> AssetResponse:
    return AssetResponse(
        id=asset.id,
        project_id=asset.project_id,
        original_filename=asset.original_filename,
        media_type=asset.media_type,
        size_bytes=asset.size_bytes,
        status=asset.status,
        metadata=asset.metadata_json,
        created_at=asset.created_at,
    )


def _job_response(session: Session, job: ProcessingJob) -> JobResponse:
    stages = session.scalars(select(ProcessingStage).where(ProcessingStage.job_id == job.id)).all()
    current = next((stage for stage in stages if stage.status in {"running", "failed", "queued"}), None)
    return JobResponse(
        id=job.id,
        project_id=job.project_id,
        asset_id=job.asset_id,
        kind=job.kind,
        status=job.status,
        progress=job.progress,
        error_code=job.error_code,
        error_message=job.error_message,
        current_stage=current.name if current else None,
        stage_status=current.status if current else None,
        created_at=job.created_at,
        started_at=job.started_at,
        finished_at=job.finished_at,
    )


def _edl_response(edl: EDL) -> EDLResponse:
    return EDLResponse(
        id=edl.id,
        project_id=edl.project_id,
        source_asset_id=edl.source_asset_id,
        version=edl.version,
        schema_version=edl.schema_version,
        document=edl.document,
        status=edl.status,
        created_at=edl.created_at,
        updated_at=edl.updated_at,
    )


def _render_output_response(output: RenderOutput) -> RenderOutputResponse:
    return RenderOutputResponse(
        id=output.id,
        project_id=output.project_id,
        edl_id=output.edl_id,
        job_id=output.job_id,
        status=output.status,
        metadata=output.metadata_json,
        error=output.error,
        created_at=output.created_at,
        updated_at=output.updated_at,
    )


@router.get("/projects", response_model=ProjectListResponse)
def list_projects(
    session: Session = Depends(get_session),
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> ProjectListResponse:
    total = session.scalar(select(func.count()).select_from(Project))
    projects = session.scalars(
        select(Project)
        .order_by(Project.created_at.desc())
        .offset(offset)
        .limit(limit)
    ).all()
    return ProjectListResponse(
        items=[ProjectResponse.model_validate(project) for project in projects],
        limit=limit,
        offset=offset,
        total=int(total or 0),
    )


@router.get("/transcription/capabilities", response_model=TranscriptionCapabilities)
def get_transcription_capabilities(
    settings: Settings = Depends(get_settings),
) -> TranscriptionCapabilities:
    return TranscriptionCapabilities(
        supports_project_quality=settings.transcription_provider == "faster_whisper"
    )


@router.post(
    "/projects",
    response_model=ProjectResponse,
    status_code=201,
    dependencies=[Depends(require_write_access)],
)
def create_project_route(
    payload: ProjectCreate,
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> ProjectResponse:
    if (
        settings.transcription_provider != "faster_whisper"
        and "transcription_quality" in payload.model_fields_set
    ):
        raise APIError(
            "transcription_quality_unsupported",
            "Per-project transcription quality is only available with the faster-whisper provider.",
            422,
        )
    return ProjectResponse.model_validate(
        create_project(session, payload.name, payload.instructions, payload.transcription_quality)
    )


@router.get("/projects/{project_id}", response_model=ProjectResponse)
def get_project_route(project_id: UUID, session: Session = Depends(get_session)) -> ProjectResponse:
    return ProjectResponse.model_validate(get_project_or_error(session, project_id))


@router.post(
    "/projects/{project_id}/assets/{asset_id}/transcribe-accurately",
    response_model=JobResponse,
    status_code=202,
    dependencies=[Depends(require_write_access)],
)
def retranscribe_accurately_route(
    project_id: UUID,
    asset_id: UUID,
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
    queue: JobQueue = Depends(get_job_queue),
) -> JobResponse:
    if settings.transcription_provider != "faster_whisper":
        raise APIError(
            "transcription_quality_unsupported",
            "Accurate retranscription is only available with the faster-whisper provider.",
            422,
        )
    job = enqueue_accurate_retranscription(session, queue, project_id, asset_id)
    return _job_response(session, job)


@router.post(
    "/projects/{project_id}/assets",
    response_model=AssetUploadResponse,
    status_code=202,
    dependencies=[Depends(require_write_access)],
)
def upload_asset_route(
    project_id: UUID,
    file: UploadFile = File(...),
    session: Session = Depends(get_session),
    storage: StorageProvider = Depends(get_storage),
    probe_reader: FFprobeMetadataReader = Depends(get_probe_reader),
    queue: JobQueue = Depends(get_job_queue),
    settings: Settings = Depends(get_settings),
) -> AssetUploadResponse:
    asset, job = upload_asset(
        session,
        storage,
        probe_reader,
        queue,
        settings,
        project_id,
        file.filename,
        file.content_type,
        file.file,
    )
    return AssetUploadResponse(
        asset_id=asset.id,
        job_id=job.id,
        status=job.status,
        poll_url=f"/api/v1/jobs/{job.id}",
    )


@router.get("/projects/{project_id}/assets", response_model=AssetListResponse)
def list_assets_route(
    project_id: UUID,
    session: Session = Depends(get_session),
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> AssetListResponse:
    get_project_or_error(session, project_id)
    total = session.scalar(
        select(func.count()).select_from(MediaAsset).where(MediaAsset.project_id == project_id)
    )
    assets = session.scalars(
        select(MediaAsset)
        .where(MediaAsset.project_id == project_id)
        .order_by(MediaAsset.created_at.desc())
        .offset(offset)
        .limit(limit)
    ).all()
    return AssetListResponse(
        items=[_asset_response(asset) for asset in assets],
        limit=limit,
        offset=offset,
        total=int(total or 0),
    )


@router.get("/projects/{project_id}/assets/{asset_id}", response_model=AssetResponse)
def get_asset_route(
    project_id: UUID,
    asset_id: UUID,
    session: Session = Depends(get_session),
) -> AssetResponse:
    return _asset_response(get_asset_or_error(session, project_id, asset_id))


@router.get("/projects/{project_id}/assets/{asset_id}/stream")
def stream_asset_route(
    project_id: UUID,
    asset_id: UUID,
    session: Session = Depends(get_session),
    storage: StorageProvider = Depends(get_storage),
) -> FileResponse:
    asset = get_asset_or_error(session, project_id, asset_id)
    path = storage.local_path(asset.storage_key)
    if not path.is_file():
        raise APIError("asset_file_not_found", "The media file is unavailable.", 404)
    return FileResponse(path, media_type=asset.media_type, filename=asset.original_filename)


@router.get("/projects/{project_id}/jobs/latest", response_model=JobResponse)
def get_latest_project_job_route(
    project_id: UUID,
    session: Session = Depends(get_session),
) -> JobResponse:
    get_project_or_error(session, project_id)
    job = session.scalar(
        select(ProcessingJob)
        .where(ProcessingJob.project_id == project_id)
        .order_by(ProcessingJob.created_at.desc())
    )
    if job is None:
        raise APIError("job_not_ready", "No processing job is available for this project yet.", 409)
    return _job_response(session, job)


@router.get("/jobs/{job_id}", response_model=JobResponse)
def get_job_route(job_id: UUID, session: Session = Depends(get_session)) -> JobResponse:
    return _job_response(session, get_job_or_error(session, job_id))


@router.post(
    "/jobs/{job_id}/retry",
    response_model=JobResponse,
    dependencies=[Depends(require_write_access)],
)
def retry_job_route(
    job_id: UUID,
    payload: RetryRequest | None = Body(default=None),
    session: Session = Depends(get_session),
    runner: StageRunner = Depends(get_stage_runner),
) -> JobResponse:
    job = get_job_or_error(session, job_id)
    stage = payload.stage if payload is not None else "transcribe"
    try:
        retried = runner.retry_stage(job.id, stage)
    except ValueError as exc:
        raise APIError("job_not_retryable", "The requested job stage cannot be retried.", 409) from exc
    return _job_response(session, retried)


@router.get("/projects/{project_id}/transcript", response_model=TranscriptResponse)
def get_transcript_route(project_id: UUID, session: Session = Depends(get_session)) -> TranscriptResponse:
    get_project_or_error(session, project_id)
    transcript = session.scalar(
        select(Transcript)
        .where(Transcript.project_id == project_id)
        .order_by(Transcript.created_at.desc())
    )
    if transcript is None:
        raise APIError("transcript_not_ready", "The transcript is not ready yet.", 409)
    return TranscriptResponse(
        id=transcript.id,
        project_id=transcript.project_id,
        asset_id=transcript.asset_id,
        provider=transcript.provider,
        model=transcript.model,
        language=transcript.language,
        duration=transcript.duration,
        normalized=transcript.normalized_json,
        created_at=transcript.created_at,
    )


@router.get("/projects/{project_id}/edl", response_model=EDLResponse)
def get_edl_route(project_id: UUID, session: Session = Depends(get_session)) -> EDLResponse:
    get_project_or_error(session, project_id)
    edl = session.scalar(select(EDL).where(EDL.project_id == project_id).order_by(EDL.version.desc()))
    if edl is None:
        raise APIError("edl_not_ready", "The edit decision list is not ready yet.", 409)
    return _edl_response(edl)


@router.patch(
    "/projects/{project_id}/edl",
    response_model=EDLResponse,
    dependencies=[Depends(require_write_access)],
)
def update_edl_route(
    project_id: UUID,
    payload: EDLDocument,
    session: Session = Depends(get_session),
) -> EDLResponse:
    get_project_or_error(session, project_id)
    if payload.project_id != project_id:
        raise APIError("edl_project_mismatch", "The EDL project does not match the requested project.", 422)

    source_asset_ids = {source.asset_id for source in payload.source_assets}
    assets = session.scalars(
        select(MediaAsset).where(MediaAsset.project_id == project_id, MediaAsset.id.in_(source_asset_ids))
    ).all()
    if len(assets) != len(source_asset_ids):
        raise APIError("edl_asset_not_found", "The EDL references an asset outside this project.", 422)
    durations = {
        asset.id: float(asset.metadata_json["duration"])
        for asset in assets
        if isinstance(asset.metadata_json.get("duration"), (int, float))
    }
    try:
        validated = validate_edl(payload.model_dump(mode="json"), asset_durations=durations)
    except ValueError as exc:
        raise APIError("edl_invalid", "The edit decision list is invalid.", 422) from exc

    current = session.scalar(select(EDL).where(EDL.project_id == project_id).order_by(EDL.version.desc()))
    source_asset_id = current.source_asset_id if current and current.source_asset_id in source_asset_ids else next(iter(source_asset_ids))
    edl = EDL(
        project_id=project_id,
        source_asset_id=source_asset_id,
        version=(current.version + 1) if current else 1,
        schema_version="edl.v1",
        document=validated.model_dump(mode="json"),
        status="draft",
    )
    session.add(edl)
    session.commit()
    return _edl_response(edl)


@router.post(
    "/projects/{project_id}/renders",
    response_model=RenderOutputResponse,
    status_code=202,
    dependencies=[Depends(require_write_access)],
)
def enqueue_render_route(
    project_id: UUID,
    payload: RenderRequest | None = Body(default=None),
    session: Session = Depends(get_session),
    queue: JobQueue = Depends(get_job_queue),
    render_service: RenderService = Depends(get_render_service),
) -> RenderOutputResponse:
    get_project_or_error(session, project_id)
    edl_id = payload.edl_id if payload is not None else None
    if edl_id is None:
        latest = session.scalar(select(EDL).where(EDL.project_id == project_id).order_by(EDL.version.desc()))
        if latest is None:
            raise APIError("edl_not_ready", "The edit decision list is not ready yet.", 409)
        edl_id = latest.id
    try:
        output = render_service.enqueue(project_id, edl_id, queue)
    except ValueError as exc:
        raise APIError("render_request_invalid", "The render request is invalid.", 422) from exc
    except RuntimeError as exc:
        raise APIError("job_queue_unavailable", "The render job could not be queued.", 503) from exc
    return _render_output_response(output)


def _get_render_output(session: Session, render_id: UUID) -> RenderOutput:
    output = session.get(RenderOutput, render_id)
    if output is None:
        raise APIError("render_not_found", "The render output was not found.", 404)
    return output


@router.get("/renders/{render_id}", response_model=RenderOutputResponse)
def get_render_route(render_id: UUID, session: Session = Depends(get_session)) -> RenderOutputResponse:
    return _render_output_response(_get_render_output(session, render_id))


def _render_file_response(
    render_id: UUID,
    *,
    session: Session,
    storage: StorageProvider,
    download: bool,
) -> FileResponse:
    output = _get_render_output(session, render_id)
    if output.status != "succeeded" or output.storage_key is None:
        raise APIError("render_not_ready", "The rendered video is not ready yet.", 409)
    path = storage.local_path(output.storage_key)
    if not path.is_file():
        raise APIError("render_file_not_found", "The rendered video is unavailable.", 404)
    return FileResponse(
        path,
        media_type="video/mp4",
        filename=f"render-{render_id}.mp4" if download else None,
    )


@router.get("/renders/{render_id}/stream")
def stream_render_route(
    render_id: UUID,
    session: Session = Depends(get_session),
    storage: StorageProvider = Depends(get_storage),
) -> FileResponse:
    return _render_file_response(render_id, session=session, storage=storage, download=False)


@router.get("/renders/{render_id}/download")
def download_render_route(
    render_id: UUID,
    session: Session = Depends(get_session),
    storage: StorageProvider = Depends(get_storage),
) -> FileResponse:
    return _render_file_response(render_id, session=session, storage=storage, download=True)


_JOB_NAME = re.compile(r"^(render|export)-[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


@router.post(
    "/projects/{project_id}/exports",
    response_model=ExportResponse,
    status_code=201,
    dependencies=[Depends(require_write_access)],
)
def export_timeline_route(
    project_id: UUID,
    payload: ExportRequest | None = Body(default=None),
    session: Session = Depends(get_session),
    storage: StorageProvider = Depends(get_storage),
) -> ExportResponse:
    """Write CMX3600 / FCPXML / OTIO timelines, editor captions, a manual guide, and a trace."""

    get_project_or_error(session, project_id)
    try:
        result = ExportService(session, storage=storage).export(
            project_id, payload.edl_id if payload is not None else None
        )
    except ExportError as exc:
        status = 404 if exc.code in {"edl_not_found", "edl_source_missing"} else 422
        raise APIError(exc.code, str(exc), status) from exc
    return ExportResponse(
        id=result.export_id,
        project_id=result.project_id,
        edl_id=result.edl_id,
        status=result.status,
        trace=result.trace,
        warnings=result.warnings,
    )


@router.get("/projects/{project_id}/history", response_model=JobHistoryResponse)
def project_history_route(
    project_id: UUID,
    session: Session = Depends(get_session),
    storage: StorageProvider = Depends(get_storage),
) -> JobHistoryResponse:
    """Metadata-only job history (production-history.v1), newest first."""

    get_project_or_error(session, project_id)
    items = list(reversed(read_history(storage.local_path(f"projects/{project_id}/jobs"))))
    return JobHistoryResponse(items=items, total=len(items))


@router.get("/projects/{project_id}/jobs/{job_name}/files/{filename}")
def job_file_route(
    project_id: UUID,
    job_name: str,
    filename: str,
    session: Session = Depends(get_session),
    storage: StorageProvider = Depends(get_storage),
) -> FileResponse:
    """Download one allow-listed job file (trace, manual, EDL, timelines, captions, render)."""

    get_project_or_error(session, project_id)
    if not _JOB_NAME.fullmatch(job_name) or filename not in JOB_FILES:
        raise APIError("job_file_not_found", "The job file was not found.", 404)
    path = storage.local_path(f"projects/{project_id}/jobs/{job_name}/{filename}")
    if not path.is_file():
        raise APIError("job_file_not_found", "The job file was not found.", 404)
    return FileResponse(path, media_type=media_type_for(filename), filename=filename)
