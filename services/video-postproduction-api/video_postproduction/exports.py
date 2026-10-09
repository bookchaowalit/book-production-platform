"""Export an EDL as an editor project bundle without rendering.

The export job writes ``edl.json``, ``timeline.edl`` (CMX3600),
``timeline.fcpxml`` (FCPXML 1.10), ``timeline.otio`` (OpenTimelineIO),
``captions.srt`` (when captions are enabled and a transcript exists), the
DaVinci Resolve ``MANUAL-STEPS.md``, and ``trace.json``, and appends the
project history. It works for timelines the V1B renderer cannot render yet
(for example several confirmed highlight clips), so a person can finish the
edit in DaVinci Resolve or Premiere Pro.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from uuid import UUID, uuid4

from production_trace import MANUAL_FILENAME, TRACE_FILENAME
from sqlalchemy import select
from sqlalchemy.orm import Session

from .edl import EDLCapabilityError, EDLDocument, validate_edl
from .jobtrace import (
    EXPORT_RECIPE,
    EXPORT_TOOL_ID,
    editor_cues,
    finish_failure,
    job_location,
    new_recorder,
    record_highlight_provenance,
    record_source,
    record_transcript,
    write_editor_bundle,
)
from .manual import EDITOR_CAPTIONS, GuideContext, GuideFiles, build_resolve_guide
from .models import EDL, MediaAsset, Project, Transcript
from .storage.base import StorageProvider
from .timeline import SourceVideo, TimelinePlanError, build_timeline_plan
from .transcription.models import NormalizedTranscript


class ExportError(ValueError):
    """The export could not be produced; ``code`` is stable and safe to show."""

    def __init__(self, code: str, message: str, trace: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.trace = trace


@dataclass(frozen=True, slots=True)
class ExportResult:
    export_id: UUID
    project_id: UUID
    edl_id: UUID
    status: str
    trace: dict[str, Any]
    warnings: list[str] = field(default_factory=list)


class ExportService:
    def __init__(self, session: Session, *, storage: StorageProvider) -> None:
        self.session = session
        self.storage = storage

    def _edl(self, project_id: UUID, edl_id: UUID | None) -> EDL:
        query = select(EDL).where(EDL.project_id == project_id)
        query = query.where(EDL.id == edl_id) if edl_id is not None else query.order_by(EDL.version.desc())
        record = self.session.scalar(query)
        if record is None:
            raise ExportError("edl_not_found", "The edit decision list was not found.")
        return record

    def export(self, project_id: UUID, edl_id: UUID | None = None) -> ExportResult:
        record = self._edl(project_id, edl_id)
        if record.source_asset_id is None:
            raise ExportError("edl_source_missing", "The edit decision list has no source asset.")
        asset = self.session.get(MediaAsset, record.source_asset_id)
        if asset is None:
            raise ExportError("edl_source_missing", "The source asset was not found.")
        export_id = uuid4()
        location = job_location(self.storage, project_id, "export", export_id)
        trace = new_recorder(tool_id=EXPORT_TOOL_ID, job_id=export_id, recipe=EXPORT_RECIPE)
        warnings: list[str] = []
        try:
            with trace.step("validate", "validate_edl", edl_id=str(record.id), edl_version=record.version) as step:
                edl: EDLDocument = validate_edl(record.document)
                step.params = {**step.params, "segments": len(edl.timeline), "capabilities": list(edl.capabilities.required)}
            source = SourceVideo.from_metadata(
                asset.original_filename, asset.metadata_json, size_bytes=asset.size_bytes, sha256=asset.sha256
            )
            record_source(trace, source)
            trace.record_step("probe", "read_ingest_probe", **{k: v for k, v in source.structural().items() if k not in {"file", "sha256"}})
            record_highlight_provenance(trace, edl)
            with trace.step("plan", "build_timeline_plan", render_profile=edl.output.render_profile) as step:
                plan = build_timeline_plan(edl, source, title=f"Export {export_id}")
                step.params = {**step.params, **plan.structural()}
            normalized: NormalizedTranscript | None = None
            if plan.captions is not None:
                transcript = self.session.scalar(
                    select(Transcript)
                    .where(Transcript.project_id == project_id, Transcript.asset_id == asset.id)
                    .order_by(Transcript.created_at.desc())
                )
                if transcript is None:
                    warnings.append("captions_skipped_transcript_not_ready")
                    trace.record_step("captions", "skip_editor_captions", note="transcript_not_ready")
                else:
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
            cues = editor_cues(plan, normalized)
            files = write_editor_bundle(trace, location, edl=edl, plan=plan, cues=cues)
            guide = build_resolve_guide(
                GuideContext(
                    plan=plan,
                    files=GuideFiles(editor_captions=EDITOR_CAPTIONS if cues is not None else None),
                    cues=cues,
                    job_kind="export",
                )
            )
            trace.finish(status="accepted", job_dir=location.job_dir, output_root=location.output_root, guide=guide)
        except (EDLCapabilityError, TimelinePlanError, ValueError) as exc:
            code = "edl_invalid" if not isinstance(exc, TimelinePlanError) else "timeline_unsupported"
            finish_failure(trace, location, code)
            raise ExportError(code, "The edit decision list cannot be exported.", location.describe([TRACE_FILENAME])) from exc
        except OSError as exc:
            finish_failure(trace, location, type(exc).__name__)
            raise ExportError("export_write_failed", "The export files could not be written.") from exc
        return ExportResult(
            export_id=export_id,
            project_id=project_id,
            edl_id=record.id,
            status="accepted",
            trace=location.describe([*files, TRACE_FILENAME, MANUAL_FILENAME]),
            warnings=warnings,
        )


__all__ = ["ExportError", "ExportResult", "ExportService"]
