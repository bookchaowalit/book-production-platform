"""Per-job production trace for Video Studio render and export jobs.

Every render or export job gets a directory under the project in storage::

    projects/<project_id>/jobs/history.jsonl            # one line per job (production-history.v1)
    projects/<project_id>/jobs/render-<render_id>/      # or export-<export_id>/
        trace.json        production-trace.v1: inputs (hash/size/duration/fps/resolution),
                          every stage, the EDL hash, artifacts with sha256, QA, AI use
        MANUAL-STEPS.md   DaVinci Resolve 19 click-by-click guide from the same timeline plan
        edl.json          the exact EDL the engine executed (the job IR)
        timeline.edl / timeline.fcpxml / timeline.otio   open the edit in a real editor
        captions.srt      editor captions (same cues and line breaks as the burn-in)
        captions-burned.ass|srt, render.mp4               render jobs only

``trace.json`` and ``history.jsonl`` hold names, parameters, hashes, sizes, and
timings only - never transcript or caption text (canary-tested). Caption text
lives only in the caption files and the manual guide, next to the video it
describes.
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import UUID

from production_trace import MANUAL_FILENAME, TRACE_FILENAME, ManualGuide, TraceRecorder

from .captions.cues import CaptionCue, ClipWindow, render_editor_srt, timeline_cues
from .edl import EDLDocument
from .interchange import write_interchange
from .manual import EDITOR_CAPTIONS
from .storage.base import StorageProvider
from .timeline import SourceVideo, TimelinePlan
from .transcription.models import NormalizedTranscript

ENGINE_VERSION = "video-postproduction-api 0.1.0"
RENDER_TOOL_ID = "video-studio.render"
EXPORT_TOOL_ID = "video-studio.export"
RENDER_RECIPE = "render-v1b.2"
EXPORT_RECIPE = "export-interchange.v1"
IR_SCHEMA = "video-edl.v1"
IR_FILENAME = "edl.json"
RENDER_FILENAME = "render.mp4"
HISTORY_KEY = "history.jsonl"
JOB_FILES = frozenset(
    {
        TRACE_FILENAME,
        MANUAL_FILENAME,
        IR_FILENAME,
        "timeline.edl",
        "timeline.fcpxml",
        "timeline.otio",
        EDITOR_CAPTIONS,
        "captions-burned.ass",
        "captions-burned.srt",
        RENDER_FILENAME,
    }
)
_MEDIA_TYPES = {
    ".json": "application/json",
    ".md": "text/markdown",
    ".edl": "text/plain",
    ".fcpxml": "application/xml",
    ".otio": "application/json",
    ".srt": "application/x-subrip",
    ".ass": "text/x-ssa",
    ".mp4": "video/mp4",
}


def media_type_for(name: str) -> str:
    return _MEDIA_TYPES.get(Path(name).suffix.lower(), "application/octet-stream")


@dataclass(frozen=True, slots=True)
class JobLocation:
    """Storage keys (relative, safe to return from the API) plus local paths."""

    project_id: UUID
    name: str
    key_prefix: str
    root_key: str
    job_dir: Path
    output_root: Path

    def key(self, filename: str) -> str:
        return f"{self.key_prefix}/{filename}"

    def describe(self, files: Sequence[str]) -> dict[str, Any]:
        return {
            "job_dir": self.key_prefix,
            "trace": self.key(TRACE_FILENAME),
            "manual": self.key(MANUAL_FILENAME),
            "history": f"{self.root_key}/{HISTORY_KEY}",
            "files": sorted(set(files)),
        }


def job_location(storage: StorageProvider, project_id: UUID, kind: str, job_id: UUID) -> JobLocation:
    root_key = f"projects/{project_id}/jobs"
    name = f"{kind}-{job_id}"
    key_prefix = f"{root_key}/{name}"
    job_dir = storage.local_path(key_prefix)
    job_dir.mkdir(parents=True, exist_ok=True)
    return JobLocation(project_id, name, key_prefix, root_key, job_dir, storage.local_path(root_key))


def new_recorder(*, tool_id: str, job_id: UUID, recipe: str, idempotency_key: str | None = None) -> TraceRecorder:
    from production_trace import sha256_json

    return TraceRecorder(
        tool_id=tool_id,
        job_id=str(job_id),
        engine_version=ENGINE_VERSION,
        recipe_version=recipe,
        schema_version=IR_SCHEMA,
        idempotency_key_sha256=sha256_json(idempotency_key) if idempotency_key else None,
    )


def write_text(path: Path, text: str) -> Path:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(text, encoding="utf-8")
    os.replace(temporary, path)
    return path


def record_source(trace: TraceRecorder, source: SourceVideo) -> None:
    """Source media facts from the ingest probe and upload hash; no re-hash of large media."""

    entry = {"name": "source_video", "role": "source", **source.structural()}
    trace.inputs.append(entry)


def record_transcript(
    trace: TraceRecorder,
    *,
    provider: str,
    model: str,
    language: str | None,
    quality: str | None,
    fingerprint: str,
    normalized: NormalizedTranscript,
) -> None:
    """Which transcript fed the captions: provider/model/quality and hashes, never text."""

    trace.input_json("transcript", normalized.model_dump(mode="json"), role="transcript")
    trace.record_step(
        "transcribe",
        "use_persisted_transcript",
        provider=provider,
        model=model,
        language=language,
        quality=quality,
        segments=len(normalized.segments),
        words=sum(len(segment.words) for segment in normalized.segments),
        content_fingerprint=fingerprint,
    )


def record_highlight_provenance(trace: TraceRecorder, edl: EDLDocument) -> None:
    """Record AI highlight proposal metadata carried in EDL provenance (ids and route only)."""

    confirmed = [segment for segment in edl.timeline if segment.provenance.get("proposal_fingerprint")]
    if not confirmed:
        return
    routes = sorted({str(segment.provenance.get("route", "unknown")) for segment in confirmed})
    fingerprints = sorted({str(segment.provenance.get("proposal_fingerprint")) for segment in confirmed})
    trace.record_step(
        "highlight_proposal",
        "confirmed_highlight_proposal",
        routes=routes,
        proposal_fingerprints=fingerprints,
        clips=len(confirmed),
        segment_ranges=[
            [str(segment.provenance.get("start_segment_id")), str(segment.provenance.get("end_segment_id"))]
            for segment in confirmed
        ],
        note="the user confirmed the proposal before this job; the model chose segment-id ranges only",
    )
    if "provider" in routes:
        # The EDL keeps the route but not the exact call count; one call is the lower bound.
        trace.ai_proposal(
            model_calls=1,
            adapter="video-highlight-v2",
            mode="provider",
            provided=["segment_id_ranges", "titles"],
            inferred=["clip_start_end_from_segment_boundaries"],
        )
        trace.ai["model_calls_exact"] = False
    else:
        trace.ai = {"used": False, "model_calls": 0, "adapter": "video-highlight-v2", "mode": "structured"}


def editor_cues(
    plan: TimelinePlan, normalized: NormalizedTranscript | None
) -> list[CaptionCue] | None:
    if plan.captions is None or normalized is None:
        return None
    windows = [ClipWindow(clip.source_start, clip.source_end, clip.record_start) for clip in plan.clips]
    return timeline_cues(normalized, windows, wrap_columns=plan.captions.wrap_columns)


def write_editor_bundle(
    trace: TraceRecorder,
    location: JobLocation,
    *,
    edl: EDLDocument,
    plan: TimelinePlan,
    cues: Sequence[CaptionCue] | None,
) -> list[str]:
    """Write the IR, interchange timelines, and editor captions; record each as an artifact."""

    written: list[str] = []
    ir = edl.model_dump(mode="json")
    write_text(location.job_dir / IR_FILENAME, json.dumps(ir, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    trace.ir_document(ir, schema=IR_SCHEMA, path=location.job_dir / IR_FILENAME)
    written.append(IR_FILENAME)
    with trace.step("interchange", "export_timelines", formats=["cmx3600", "fcpxml-1.10", "otio-json"], clips=len(plan.clips)) as step:
        for name, content in write_interchange(plan).items():
            write_text(location.job_dir / name, content)
            written.append(name)
        step.outputs = [name for name in written if name != IR_FILENAME]
    if cues is not None:
        with trace.step("captions", "write_editor_srt", cues=len(cues), wrap_columns=plan.captions.wrap_columns if plan.captions else None) as step:
            write_text(location.job_dir / EDITOR_CAPTIONS, render_editor_srt(cues))
            step.outputs = [EDITOR_CAPTIONS]
        written.append(EDITOR_CAPTIONS)
    for name in written:
        trace.artifact(location.job_dir / name, media_type=media_type_for(name), role="editor" if name != IR_FILENAME else "ir")
    return written


def finish_failure(
    trace: TraceRecorder,
    location: JobLocation,
    code: str,
    *,
    guide: ManualGuide | None = None,
) -> dict[str, Any] | None:
    """Record failed jobs in the history too; tracing must never mask the real failure."""

    try:
        trace.record_step("outcome", "failed", note=code)
        return trace.finish(status="failed", job_dir=location.job_dir, output_root=location.output_root, guide=guide)
    except OSError:
        return None


def qa_summary(checks: Mapping[str, bool]) -> dict[str, Any]:
    return {
        "status": "passed" if all(checks.values()) else "warning",
        "checks": [{"name": name, "ok": ok} for name, ok in checks.items()],
    }


__all__ = [
    "ENGINE_VERSION",
    "EXPORT_RECIPE",
    "EXPORT_TOOL_ID",
    "IR_FILENAME",
    "IR_SCHEMA",
    "JOB_FILES",
    "RENDER_FILENAME",
    "RENDER_RECIPE",
    "RENDER_TOOL_ID",
    "JobLocation",
    "editor_cues",
    "finish_failure",
    "job_location",
    "media_type_for",
    "new_recorder",
    "qa_summary",
    "record_highlight_provenance",
    "record_source",
    "record_transcript",
    "write_editor_bundle",
    "write_text",
]
