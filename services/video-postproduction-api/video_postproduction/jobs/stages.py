"""Probe and transcript stage state transitions."""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import asdict
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import Settings
from ..edl import validate_edl
from ..media.ffprobe import FFprobeMetadataReader, MediaProbeError
from ..media.models import AudioStreamMetadata, MediaMetadata, VideoStreamMetadata
from ..models import (
    EDL,
    MediaAsset,
    ProcessingJob,
    ProcessingStage,
    Project,
    Transcript,
    TranscriptSegment,
)
from ..observability import emit_job_event
from ..storage.base import StorageProvider
from ..transcription.base import TranscriptionProvider, TranscriptionProviderError
from ..transcription.models import NormalizedTranscript, TranscriptValidationError
from .queue import JobQueue

_logger = logging.getLogger(__name__)


def _now() -> datetime:
    return datetime.now(UTC)


def _metadata_to_json(metadata: MediaMetadata) -> dict[str, Any]:
    return asdict(metadata)


def _metadata_from_json(raw: dict[str, Any]) -> MediaMetadata:
    video_raw = raw.get("video") or {}
    audio_raw = raw.get("audio")
    audio = None
    if isinstance(audio_raw, dict):
        audio = AudioStreamMetadata(
            codec_name=audio_raw.get("codec_name"),
            sample_rate=audio_raw.get("sample_rate"),
            channels=audio_raw.get("channels"),
        )
    return MediaMetadata(
        duration=float(raw.get("duration", 0)),
        format_name=raw.get("format_name"),
        size_bytes=raw.get("size_bytes"),
        bitrate=raw.get("bitrate"),
        video=VideoStreamMetadata(
            codec_name=video_raw.get("codec_name"),
            width=int(video_raw.get("width", 0)),
            height=int(video_raw.get("height", 0)),
            fps=video_raw.get("fps"),
            pix_fmt=video_raw.get("pix_fmt"),
        ),
        audio=audio,
    )


class StageRunner:
    """Run one persisted stage with safe failure and retry state transitions."""

    def __init__(
        self,
        session: Session,
        *,
        storage: StorageProvider,
        probe_reader: FFprobeMetadataReader,
        transcription_provider: TranscriptionProvider,
        settings: Settings | None = None,
        queue: JobQueue | None = None,
    ) -> None:
        self.session = session
        self.storage = storage
        self.probe_reader = probe_reader
        self.transcription_provider = transcription_provider
        self.settings = settings or Settings()
        self.queue = queue

    def _get_job(self, job_id: UUID) -> ProcessingJob:
        job = self.session.get(ProcessingJob, job_id)
        if job is None:
            raise ValueError("processing job not found")
        return job

    def _get_stage(self, job_id: UUID, stage_name: str) -> ProcessingStage:
        stage = self.session.scalar(
            select(ProcessingStage).where(
                ProcessingStage.job_id == job_id,
                ProcessingStage.name == stage_name,
            )
        )
        if stage is None:
            raise ValueError(f"processing stage not found: {stage_name}")
        return stage

    def _start_stage(self, job: ProcessingJob, stage: ProcessingStage) -> bool:
        if stage.status == "succeeded":
            return False
        stage.status = "running"
        stage.attempt += 1
        stage.started_at = _now()
        stage.finished_at = None
        stage.error_code = None
        stage.error_message = None
        job.status = "running"
        job.started_at = job.started_at or stage.started_at
        job.error_code = None
        job.error_message = None
        self.session.commit()
        emit_job_event("stage_started", job_id=job.id, stage=stage.name, status=stage.status)
        return True

    def _finish_stage(self, job: ProcessingJob, stage: ProcessingStage, *, progress: int) -> None:
        stage.status = "succeeded"
        stage.finished_at = _now()
        stage.error_code = None
        stage.error_message = None
        job.progress = progress
        job.error_code = None
        job.error_message = None
        self.session.commit()
        emit_job_event("stage_finished", job_id=job.id, stage=stage.name, status=stage.status)

    def _fail_stage(
        self,
        job: ProcessingJob,
        stage: ProcessingStage,
        *,
        error_code: str,
        error_message: str,
    ) -> None:
        stage.status = "failed"
        stage.finished_at = _now()
        stage.error_code = error_code
        stage.error_message = error_message
        job.status = "failed"
        job.error_code = error_code
        job.error_message = error_message
        self.session.commit()
        emit_job_event(
            "stage_failed",
            job_id=job.id,
            stage=stage.name,
            status=stage.status,
            fields={"error_code": error_code},
        )

    def run_probe(self, job_id: UUID) -> None:
        job = self._get_job(job_id)
        stage = self._get_stage(job_id, "probe")
        if not self._start_stage(job, stage):
            return
        asset = self.session.get(MediaAsset, job.asset_id)
        if asset is None:
            self._fail_stage(job, stage, error_code="asset_not_found", error_message="Media asset not found")
            return
        try:
            metadata = self.probe_reader.read(self.storage.local_path(asset.storage_key))
            asset.metadata_json = _metadata_to_json(metadata)
            asset.status = "probed"
            self.session.commit()
            self._finish_stage(job, stage, progress=50)
            if self.queue is not None:
                self.queue.enqueue(job.id, "transcribe")
        except MediaProbeError as exc:
            self._fail_stage(job, stage, error_code="media_probe_error", error_message=exc.safe_message)
        except Exception:  # noqa: BLE001 - convert unexpected stage failures to safe state
            self._fail_stage(job, stage, error_code="stage_error", error_message="Media probe stage failed")

    @staticmethod
    def _transcript_fingerprint(asset: MediaAsset, transcript: NormalizedTranscript) -> str:
        material = {
            "asset_sha256": asset.sha256,
            "stage": "transcribe",
            "provider": transcript.provider,
            "model": transcript.model,
            "language": transcript.language,
        }
        return hashlib.sha256(json.dumps(material, sort_keys=True).encode("utf-8")).hexdigest()

    @staticmethod
    def _initial_edl(project_id: UUID, asset: MediaAsset, metadata: MediaMetadata) -> dict[str, Any]:
        duration = metadata.duration
        if duration <= 0:
            raise ValueError("media duration must be positive for an initial EDL")
        end = min(duration, 10.0)
        fps = metadata.video.fps or 30.0
        document: dict[str, Any] = {
            "version": 1,
            "project_id": str(project_id),
            "source_assets": [
                {
                    "asset_id": str(asset.id),
                    "storage_key": asset.storage_key,
                    "duration": duration,
                }
            ],
            "timeline": [
                {
                    "id": "segment_0001",
                    "source": {
                        "asset_id": str(asset.id),
                        "start": 0.0,
                        "end": end,
                    },
                    "visual": {
                        "kind": "a_roll",
                        "reframe": {"mode": "center", "aspect_ratio": "9:16"},
                        "asset_refs": [],
                    },
                    "captions": {
                        "enabled": True,
                        "source": "transcript",
                        "format": "ass",
                        "preset": "minimal",
                    },
                    "audio": {"operations": []},
                    "color": {"operations": []},
                    "provenance": {"reason": "initial source range"},
                }
            ],
            "assets": [],
            "output": {"width": 1080, "height": 1920, "fps": fps, "format": "mp4"},
            "capabilities": {"required": ["cut", "captions", "center_reframe"]},
        }
        return validate_edl(document).model_dump(mode="json")

    def run_transcribe(self, job_id: UUID) -> None:
        job = self._get_job(job_id)
        stage = self._get_stage(job_id, "transcribe")
        if not self._start_stage(job, stage):
            return
        asset = self.session.get(MediaAsset, job.asset_id)
        project = self.session.get(Project, job.project_id)
        if asset is None or project is None:
            self._fail_stage(job, stage, error_code="asset_not_found", error_message="Media asset not found")
            return
        try:
            metadata = _metadata_from_json(asset.metadata_json)
            transcript = self.transcription_provider.transcribe(
                self.storage.local_path(asset.storage_key),
                metadata,
                language=self.settings.transcription_language,
            )
            fingerprint = self._transcript_fingerprint(asset, transcript)
            persisted_transcript = self.session.scalar(
                select(Transcript).where(
                    Transcript.asset_id == asset.id,
                    Transcript.content_fingerprint == fingerprint,
                )
            )
            if persisted_transcript is None:
                persisted_transcript = Transcript(
                    project_id=project.id,
                    asset_id=asset.id,
                    provider=transcript.provider,
                    model=transcript.model,
                    language=transcript.language,
                    duration=transcript.duration,
                    normalized_json=transcript.model_dump(mode="json"),
                    content_fingerprint=fingerprint,
                )
                self.session.add(persisted_transcript)
                self.session.flush()
                for ordinal, segment in enumerate(transcript.segments):
                    self.session.add(
                        TranscriptSegment(
                            transcript_id=persisted_transcript.id,
                            ordinal=ordinal,
                            start=segment.start,
                            end=segment.end,
                            text=segment.text,
                            words_json=[word.model_dump(mode="json") for word in segment.words],
                        )
                    )
            existing_edl = self.session.scalar(
                select(EDL)
                .where(EDL.project_id == project.id, EDL.source_asset_id == asset.id)
                .order_by(EDL.version.desc())
            )
            if existing_edl is None:
                self.session.add(
                    EDL(
                        project_id=project.id,
                        source_asset_id=asset.id,
                        version=1,
                        schema_version="edl.v1",
                        document=self._initial_edl(project.id, asset, metadata),
                        status="draft",
                    )
                )
            asset.status = "transcribed"
            project.status = "ready_for_edit"
            self.session.commit()
            self._finish_stage(job, stage, progress=100)
            job.status = "succeeded"
            job.finished_at = _now()
            self.session.commit()
        except TranscriptionProviderError as exc:
            self._fail_stage(
                job,
                stage,
                error_code="transcription_provider_error",
                error_message=exc.safe_message,
            )
        except TranscriptValidationError as exc:
            # Validation messages describe structure only (ranges, types), never transcript text.
            _logger.warning("transcript validation failed for job %s: %s", job.id, exc)
            self._fail_stage(
                job,
                stage,
                error_code="transcript_invalid",
                error_message=f"Transcript from provider failed validation: {exc}",
            )
        except Exception:  # convert unexpected stage failures to safe state, keeping the traceback in logs
            _logger.exception("transcription stage failed unexpectedly for job %s", job.id)
            self._fail_stage(job, stage, error_code="stage_error", error_message="Transcription stage failed")

    def retry_stage(self, job_id: UUID, stage_name: str) -> ProcessingJob:
        job = self._get_job(job_id)
        stage = self._get_stage(job_id, stage_name)
        if stage.status != "failed":
            raise ValueError("only failed stages can be retried")
        if stage.attempt >= self.settings.max_stage_attempts:
            raise ValueError("stage retry limit reached")
        stage.status = "queued"
        stage.error_code = None
        stage.error_message = None
        stage.finished_at = None
        job.status = "queued"
        job.error_code = None
        job.error_message = None
        self.session.commit()
        if self.queue is not None:
            self.queue.enqueue(job.id, stage_name)
        return job
