"""Render orchestration, queueing, atomic output storage, and persistence."""

from __future__ import annotations

import tempfile
from dataclasses import asdict
from pathlib import Path
from uuid import UUID

from production_trace import MANUAL_FILENAME, TRACE_FILENAME, TraceRecorder
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..captions.ass import render_ass
from ..captions.cues import CaptionCue
from ..captions.srt import render_srt
from ..edl import EDLDocument, validate_edl
from ..jobs.queue import JobQueue
from ..jobtrace import (
    RENDER_FILENAME,
    RENDER_RECIPE,
    RENDER_TOOL_ID,
    JobLocation,
    editor_cues,
    finish_failure,
    job_location,
    media_type_for,
    new_recorder,
    qa_summary,
    record_highlight_provenance,
    record_source,
    record_transcript,
    write_editor_bundle,
    write_text,
)
from ..manual import EDITOR_CAPTIONS, DeliveredOutput, GuideContext, GuideFiles, build_resolve_guide
from ..models import (
    EDL,
    MediaAsset,
    ProcessingJob,
    ProcessingStage,
    Project,
    RenderOutput,
    Transcript,
)
from ..storage.base import StorageProvider
from ..timeline import SourceVideo, TimelinePlan, TimelinePlanError, build_timeline_plan
from ..transcription.models import NormalizedTranscript
from .base import RenderCapabilityError, RenderEngine, RenderProcessError
from .ffmpeg import build_ffmpeg_argv, summarize_ffmpeg_argv


class RenderService:
    """Render an EDL to a temp artifact, validate it, then persist it atomically."""

    def __init__(self, session: Session, *, storage: StorageProvider, engine: RenderEngine) -> None:
        self.session = session
        self.storage = storage
        self.engine = engine

    def _context(self, project_id: UUID, edl_id: UUID) -> tuple[EDL, MediaAsset, EDLDocument]:
        edl_record = self.session.scalar(select(EDL).where(EDL.id == edl_id, EDL.project_id == project_id))
        if edl_record is None:
            raise ValueError("edit decision list not found")
        if edl_record.source_asset_id is None:
            raise ValueError("edit decision list has no source asset")
        asset = self.session.get(MediaAsset, edl_record.source_asset_id)
        if asset is None:
            raise ValueError("source asset not found")
        return edl_record, asset, validate_edl(edl_record.document)

    def _transcript(self, project_id: UUID, asset_id: UUID) -> Transcript | None:
        return self.session.scalar(
            select(Transcript)
            .where(Transcript.project_id == project_id, Transcript.asset_id == asset_id)
            .order_by(Transcript.created_at.desc())
        )

    def _caption_transcript(
        self, project_id: UUID, asset_id: UUID, edl: EDLDocument, trace: TraceRecorder
    ) -> NormalizedTranscript | None:
        """Load the transcript the captions use and trace provider/model/quality (never text)."""

        segment = edl.timeline[0]
        if not segment.captions.enabled:
            return None
        if segment.captions.source != "transcript":
            raise RenderCapabilityError("V1B captions must use the normalized transcript")
        transcript = self._transcript(project_id, asset_id)
        if transcript is None:
            return None
        normalized = NormalizedTranscript.model_validate(transcript.normalized_json)
        project = self.session.get(Project, project_id)
        record_transcript(
            trace,
            provider=transcript.provider,
            model=transcript.model,
            language=transcript.language,
            quality=project.transcription_quality if project is not None else None,
            fingerprint=transcript.content_fingerprint,
            normalized=normalized,
        )
        return normalized

    @staticmethod
    def _burned_caption_file(
        edl: EDLDocument, normalized: NormalizedTranscript, *, directory: Path, trace: TraceRecorder
    ) -> Path:
        """Write the burned-in caption file into the job directory (it is a job artifact)."""

        segment = edl.timeline[0]
        suffix = ".ass" if segment.captions.format == "ass" else ".srt"
        with trace.step(
            "captions",
            "build_burn_in_captions",
            format=suffix[1:],
            preset=segment.captions.preset,
            clip_start=segment.source.start,
            clip_end=segment.source.end,
        ) as step:
            content = (
                render_ass(
                    normalized,
                    segment.captions.preset,
                    clip_start=segment.source.start,
                    clip_end=segment.source.end,
                    output_size=(edl.output.width, edl.output.height),
                )
                if suffix == ".ass"
                else render_srt(
                    normalized,
                    clip_start=segment.source.start,
                    clip_end=segment.source.end,
                )
            )
            path = write_text(directory / f"captions-burned{suffix}", content)
            step.outputs = [path.name]
        return path

    def _render_existing(self, output: RenderOutput, edl_record: EDL, asset: MediaAsset, edl: EDLDocument) -> RenderOutput:
        output.status = "rendering"
        output.error = None
        self.session.commit()
        location = job_location(self.storage, output.project_id, "render", output.id)
        trace = new_recorder(
            tool_id=RENDER_TOOL_ID,
            job_id=output.id,
            recipe=RENDER_RECIPE,
            idempotency_key=f"render:{asset.id}:{edl_record.id}:{edl_record.version}",
        )
        output_name = RENDER_FILENAME
        output_key = location.key(output_name)
        temp_output: Path | None = None
        plan: TimelinePlan | None = None
        cues: list[CaptionCue] | None = None
        files: list[str] = []
        try:
            trace.record_step(
                "validate",
                "validate_edl",
                render_id=str(output.id),
                processing_job_id=str(output.job_id) if output.job_id else None,
                edl_id=str(edl_record.id),
                edl_version=edl_record.version,
                segments=len(edl.timeline),
                capabilities=list(edl.capabilities.required),
            )
            source = SourceVideo.from_metadata(
                asset.original_filename, asset.metadata_json, size_bytes=asset.size_bytes, sha256=asset.sha256
            )
            record_source(trace, source)
            trace.record_step("probe", "read_ingest_probe", **{k: v for k, v in source.structural().items() if k not in {"file", "sha256"}})
            record_highlight_provenance(trace, edl)
            with trace.step("plan", "build_timeline_plan", render_profile=edl.output.render_profile) as plan_step:
                try:
                    plan = build_timeline_plan(edl, source, title=f"Render {output.id}")
                except TimelinePlanError as exc:
                    raise RenderCapabilityError(str(exc)) from exc
                plan_step.params = {**plan_step.params, **plan.structural()}
            with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as temporary:
                temp_output = Path(temporary.name)
            normalized = self._caption_transcript(output.project_id, asset.id, edl, trace)
            cues = editor_cues(plan, normalized)
            # The editor bundle is written before rendering so a failed job still hands over the edit.
            files += write_editor_bundle(trace, location, edl=edl, plan=plan, cues=cues)
            caption_path: Path | None = None
            if edl.timeline[0].captions.enabled:
                if normalized is None:
                    raise RenderProcessError("transcript_not_ready", "The transcript is not ready for captions")
                caption_path = self._burned_caption_file(edl, normalized, directory=location.job_dir, trace=trace)
                trace.artifact(caption_path, media_type=media_type_for(caption_path.name), role="captions")
                files.append(caption_path.name)
            input_path = self.storage.local_path(asset.storage_key)
            with trace.step("render", "ffmpeg_render") as render_step:
                argv = build_ffmpeg_argv(edl, input_path=input_path, output_path=temp_output, caption_path=caption_path)
                render_step.params = summarize_ffmpeg_argv(argv)
                artifact = self.engine.render(
                    edl,
                    input_path=input_path,
                    output_path=temp_output,
                    caption_path=caption_path,
                )
                render_step.outputs = [output_name]
            qa = qa_summary(
                {
                    "mp4_container": bool(artifact.metadata.format_name and "mp4" in artifact.metadata.format_name.split(",")),
                    "dimensions_match": (artifact.metadata.video.width, artifact.metadata.video.height) == (plan.width, plan.height),
                    "duration_within_0_5s": abs(artifact.duration - plan.record_duration) <= 0.5,
                }
            )
            trace.record_step("qa", "probe_output", status=qa["status"], checks=qa["checks"], duration_s=artifact.duration)
            with temp_output.open("rb") as rendered_file:
                stored = self.storage.put_atomic(output_key, rendered_file, "video/mp4")
            trace.record_step("publish", "store_render", outputs=[output_name], bytes=stored.size_bytes)
            trace.artifact(location.job_dir / output_name, media_type="video/mp4", role="output")
            files.append(output_name)
            guide = build_resolve_guide(
                GuideContext(
                    plan=plan,
                    files=GuideFiles(
                        video=output_name,
                        burned_captions=caption_path.name if caption_path else None,
                        editor_captions=EDITOR_CAPTIONS if cues is not None else None,
                    ),
                    cues=cues,
                    output=DeliveredOutput(
                        bitrate=artifact.metadata.bitrate,
                        duration=artifact.duration,
                        audio_sample_rate=artifact.metadata.audio.sample_rate if artifact.metadata.audio else None,
                    ),
                )
            )
            trace.finish(status="accepted", job_dir=location.job_dir, output_root=location.output_root, guide=guide, qa=qa)
            output.storage_key = stored.storage_key
            output.status = "succeeded"
            output.metadata_json = asdict(artifact.metadata) | {
                "duration": artifact.duration,
                "trace": location.describe([*files, TRACE_FILENAME, MANUAL_FILENAME]),
            }
            self.session.commit()
            return output
        except RenderProcessError as exc:
            self._record_failure(output, trace, location, exc.code, plan=plan, cues=cues, files=files)
            output.status = "failed"
            output.error = exc.safe_message
            self.session.commit()
            raise
        except Exception as exc:
            self._record_failure(output, trace, location, type(exc).__name__, plan=plan, cues=cues, files=files)
            self.session.commit()
            raise
        finally:
            if temp_output is not None:
                temp_output.unlink(missing_ok=True)

    @staticmethod
    def _record_failure(
        output: RenderOutput,
        trace: TraceRecorder,
        location: JobLocation,
        code: str,
        *,
        plan: TimelinePlan | None,
        cues: list[CaptionCue] | None,
        files: list[str],
    ) -> None:
        guide = None
        if plan is not None:
            guide = build_resolve_guide(
                GuideContext(
                    plan=plan,
                    files=GuideFiles(
                        editor_captions=EDITOR_CAPTIONS if EDITOR_CAPTIONS in files else None,
                        interchange=tuple(name for name in files if name.startswith("timeline.")),
                    ),
                    cues=cues,
                    engine_outcome=code,
                )
            )
        finished = finish_failure(trace, location, code, guide=guide)
        names = [*files, TRACE_FILENAME] + ([MANUAL_FILENAME] if guide is not None else [])
        if finished is not None:
            output.metadata_json = {**(output.metadata_json or {}), "trace": location.describe(names)}

    def render(self, project_id: UUID, edl_id: UUID, job_id: UUID | None = None) -> RenderOutput:
        """Render immediately, primarily for deterministic unit/integration workflows."""

        edl_record, asset, edl = self._context(project_id, edl_id)
        output = RenderOutput(
            project_id=project_id,
            edl_id=edl_record.id,
            job_id=job_id,
            status="queued",
            metadata_json={},
        )
        self.session.add(output)
        self.session.flush()
        return self._render_existing(output, edl_record, asset, edl)

    def enqueue(self, project_id: UUID, edl_id: UUID, queue: JobQueue) -> RenderOutput:
        """Persist a render job before dispatching only its stable identifiers."""

        edl_record, asset, _ = self._context(project_id, edl_id)
        idempotency_key = f"render:{asset.id}:{edl_record.id}:{edl_record.version}"
        existing_job = self.session.scalar(
            select(ProcessingJob).where(ProcessingJob.idempotency_key == idempotency_key)
        )
        if existing_job is not None:
            existing_output = self.session.scalar(
                select(RenderOutput).where(RenderOutput.job_id == existing_job.id)
            )
            if existing_output is not None:
                return existing_output

        job = ProcessingJob(
            project_id=project_id,
            asset_id=asset.id,
            kind="render",
            status="queued",
            idempotency_key=idempotency_key,
        )
        self.session.add(job)
        self.session.flush()
        self.session.add(ProcessingStage(job_id=job.id, name="render", status="queued"))
        output = RenderOutput(
            project_id=project_id,
            edl_id=edl_record.id,
            job_id=job.id,
            status="queued",
            metadata_json={},
        )
        self.session.add(output)
        self.session.commit()
        try:
            queue.enqueue(job.id, "render")
        except Exception as exc:
            job.status = "failed"
            job.error_code = "queue_error"
            job.error_message = "The render job could not be queued."
            output.status = "failed"
            output.error = "The render job could not be queued."
            self.session.commit()
            raise RuntimeError("render job could not be queued") from exc
        return output

    def run_job(self, job_id: UUID) -> RenderOutput:
        """Execute a queued render job from a worker task."""

        job = self.session.get(ProcessingJob, job_id)
        if job is None:
            raise ValueError("render job not found")
        output = self.session.scalar(select(RenderOutput).where(RenderOutput.job_id == job.id))
        if output is None:
            raise ValueError("render output not found")
        stage = self.session.scalar(
            select(ProcessingStage)
            .where(ProcessingStage.job_id == job.id, ProcessingStage.name == "render")
            .with_for_update()
        )
        if stage is None:
            raise ValueError("render stage not found")
        if output.status == "succeeded" and output.storage_key:
            stage.status = "succeeded"
            job.status = "succeeded"
            job.progress = 100
            self.session.commit()
            return output
        if stage.status == "running":
            self.session.commit()
            return output
        edl_record, asset, edl = self._context(job.project_id, output.edl_id)
        stage.status = "running"
        stage.attempt += 1
        job.status = "running"
        self.session.commit()
        try:
            result = self._render_existing(output, edl_record, asset, edl)
            stage.status = "succeeded"
            job.status = "succeeded"
            job.progress = 100
            self.session.commit()
            return result
        except RenderProcessError as exc:
            stage.status = "failed"
            stage.error_code = exc.code
            stage.error_message = exc.safe_message
            job.status = "failed"
            job.error_code = exc.code
            job.error_message = exc.safe_message
            self.session.commit()
            raise
