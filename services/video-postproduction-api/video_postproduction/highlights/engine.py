"""Deterministic highlight engine: segment-id ranges in, an ``EDLProposal`` out.

The model (or the user on the structured route) may only name transcript
segment-id ranges and a short title. This engine converts every range to the
exact ``start`` of its first segment and the exact ``end`` of its last
segment, so every proposed clip is aligned to transcript boundaries by
construction. It rejects unknown ids, reversed ranges, overlaps, clip counts
above the request, durations outside the requested range, and any
model-supplied timestamp (REQ-AI-03).

An ``EDLProposal`` is never an EDL and never renders. ``build_confirmed_edl``
turns it into an ``EDLDocument`` only with an explicit user confirmation whose
fingerprint matches the proposal (REQ-VID-04); persisting or rendering the
EDL stays with the existing EDL and render routes.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Mapping, Sequence
from itertools import pairwise
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from ..edl import EDLDocument, validate_edl
from .brief import MAX_CLIP_SECONDS, MAX_CLIPS, MIN_CLIP_SECONDS

REQUEST_SCHEMA = "video-highlight-request.v1"
PROPOSAL_SCHEMA = "video-edl-proposal.v1"
PrivacyClass = Literal["public", "internal", "private", "restricted"]
DISPATCHABLE_PRIVACY = frozenset({"public", "internal"})
MAX_TITLE_CHARS = 80
_EPSILON = 1e-6
_TIMESTAMP_KEYS = frozenset(
    {
        "start",
        "end",
        "start_time",
        "end_time",
        "start_seconds",
        "end_seconds",
        "timestamp",
        "duration",
    }
)
_CLIP_KEYS = frozenset({"start_segment_id", "end_segment_id", "title"})
_UNSAFE_TITLE = re.compile(r"[\x00-\x1f\x7f<>{}]|https?://", re.IGNORECASE)


class HighlightError(ValueError):
    """A proposal or confirmation violated the engine contract; ``code`` is stable."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class SegmentIn(BaseModel):
    """One transcript segment; extra fields (e.g. ``words``) are ignored."""

    model_config = ConfigDict(extra="ignore", frozen=True)

    id: str = Field(min_length=1, max_length=128)
    start: float = Field(ge=0)
    end: float = Field(gt=0)
    text: str = Field(max_length=4_000)

    @field_validator("start", "end")
    @classmethod
    def _finite(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("timestamps must be finite")
        return value


class ClipRange(BaseModel):
    """A user-selected segment range for the structured route (no model call)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    start_segment_id: str = Field(min_length=1, max_length=128)
    end_segment_id: str = Field(min_length=1, max_length=128)
    title: str = Field(default="", max_length=200)


class HighlightRequest(BaseModel):
    """Input for one highlight proposal job.

    ``privacy_class`` must come from the owning project; ``private`` and
    ``restricted`` transcripts are never dispatched to a model. ``ranges``
    selects the structured route and needs no provider configuration.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["video-highlight-request.v1"] = "video-highlight-request.v1"
    project_id: UUID
    asset_id: UUID
    privacy_class: PrivacyClass
    locale: str = Field(default="th-TH", pattern=r"^[a-z]{2}(?:-[A-Z]{2})?$")
    brief: str = Field(default="", max_length=2_000)
    ranges: list[ClipRange] | None = Field(default=None, min_length=1)
    segments: list[SegmentIn] = Field(min_length=1, max_length=5_000)


class ProposedClip(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    clip_id: str
    start_segment_id: str
    end_segment_id: str
    segment_count: int = Field(ge=1)
    start: float
    end: float
    duration: float
    title: str
    title_source: Literal["model", "user", "engine"]


class ProposalConstraints(BaseModel):
    """Engine-enforced limits; the brief topic is deliberately not stored."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    max_clips: int = Field(ge=1, le=MAX_CLIPS)
    min_seconds: float = Field(ge=0)
    max_seconds: float = Field(gt=0, le=MAX_CLIP_SECONDS)


class EDLProposal(BaseModel):
    """A proposal awaiting user confirmation; it is not an EDL and never renders."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["video-edl-proposal.v1"] = "video-edl-proposal.v1"
    project_id: UUID
    asset_id: UUID
    status: Literal["awaiting_confirmation"] = "awaiting_confirmation"
    requires_confirmation: Literal[True] = True
    route: Literal["structured", "provider"]
    constraints: ProposalConstraints
    clips: list[ProposedClip] = Field(min_length=1, max_length=MAX_CLIPS)
    fingerprint: str


class HighlightConfirmation(BaseModel):
    """The user's explicit decision; ``clip_ids`` may keep a subset of clips."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    proposal_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    confirmed: bool
    clip_ids: list[str] | None = None


def transcript_index(segments: Sequence[SegmentIn]) -> dict[str, int]:
    """Map segment id → position after checking ids are unique and time-ordered."""

    index: dict[str, int] = {}
    previous_start = -1.0
    for position, segment in enumerate(segments):
        if segment.id in index:
            raise HighlightError("transcript_invalid", "transcript segment ids must be unique")
        if segment.end <= segment.start or segment.start < previous_start:
            raise HighlightError(
                "transcript_invalid", "transcript segments must be ordered with end after start"
            )
        previous_start = segment.start
        index[segment.id] = position
    return index


def prompt_alias(position: int) -> str:
    """Compact, model-facing segment id (``s1``, ``s2``, …); real ids never reach the model."""

    return f"s{position + 1}"


def _sanitize_title(
    raw: Any, fallback_index: int, source: Literal["model", "user"]
) -> tuple[str, str]:
    if isinstance(raw, str):
        title = " ".join(raw.split())
        if 0 < len(title) <= MAX_TITLE_CHARS and not _UNSAFE_TITLE.search(title):
            return title, source
    return f"Highlight {fallback_index + 1}", "engine"


def _fingerprint(project_id: UUID, asset_id: UUID, clips: Sequence[ProposedClip]) -> str:
    material = {
        "project_id": str(project_id),
        "asset_id": str(asset_id),
        "clips": [
            [clip.start_segment_id, clip.end_segment_id, clip.start, clip.end] for clip in clips
        ],
    }
    return hashlib.sha256(json.dumps(material, sort_keys=True).encode("utf-8")).hexdigest()


def build_proposal(
    request: HighlightRequest,
    raw_clips: Sequence[Mapping[str, Any]],
    *,
    constraints: ProposalConstraints,
    route: Literal["structured", "provider"],
    id_lookup: Mapping[str, int],
) -> EDLProposal:
    """Convert id ranges into exact segment boundaries and enforce every limit.

    ``id_lookup`` maps the ids the proposer was allowed to use (real ids on
    the structured route, ``s<n>`` aliases on the provider route) to segment
    positions. Raises :class:`HighlightError` on the first violation.
    """

    if not raw_clips:
        raise HighlightError("no_matching_highlight", "no highlight matched the request")
    if len(raw_clips) > constraints.max_clips:
        raise HighlightError(
            "clip_count_exceeded", "more clips were proposed than the request allows"
        )
    segments = request.segments
    title_source: Literal["model", "user"] = "model" if route == "provider" else "user"
    spans: list[tuple[int, int, Any]] = []
    for raw in raw_clips:
        if not isinstance(raw, Mapping):
            raise HighlightError("invalid_model_output", "each clip must be an object")
        keys = set(raw)
        if keys & _TIMESTAMP_KEYS:
            raise HighlightError(
                "timestamps_forbidden", "clips may name segment ids only, never timestamps"
            )
        if keys - _CLIP_KEYS or not {"start_segment_id", "end_segment_id"} <= keys:
            raise HighlightError(
                "invalid_model_output",
                "clips must contain start_segment_id, end_segment_id, and title only",
            )
        start_id, end_id = raw["start_segment_id"], raw["end_segment_id"]
        if not isinstance(start_id, str) or not isinstance(end_id, str):
            raise HighlightError("unknown_segment_id", "segment ids must be strings")
        first, last = id_lookup.get(start_id.strip()), id_lookup.get(end_id.strip())
        if first is None or last is None:
            raise HighlightError(
                "unknown_segment_id", "a clip names a segment id that is not in the transcript"
            )
        if last < first:
            raise HighlightError("segment_range_reversed", "a clip ends before it starts")
        spans.append((first, last, raw.get("title")))

    ordered = sorted(spans, key=lambda span: span[0])
    for (_, previous_last, _), (next_first, _, _) in pairwise(ordered):
        if next_first <= previous_last:
            raise HighlightError("overlapping_clips", "proposed clips overlap")

    clips: list[ProposedClip] = []
    for number, (first, last, raw_title) in enumerate(ordered):
        start, end = segments[first].start, segments[last].end
        duration = end - start
        if (
            duration < constraints.min_seconds - _EPSILON
            or duration > constraints.max_seconds + _EPSILON
        ):
            raise HighlightError(
                "clip_duration_out_of_range", "a clip is outside the requested duration range"
            )
        title, source = _sanitize_title(raw_title, number, title_source)
        clips.append(
            ProposedClip(
                clip_id=f"clip_{number + 1:02d}",
                start_segment_id=segments[first].id,
                end_segment_id=segments[last].id,
                segment_count=last - first + 1,
                start=start,
                end=end,
                duration=round(duration, 3),
                title=title,
                title_source=source,  # type: ignore[arg-type]
            )
        )
    return EDLProposal(
        project_id=request.project_id,
        asset_id=request.asset_id,
        route=route,
        constraints=constraints,
        clips=clips,
        fingerprint=_fingerprint(request.project_id, request.asset_id, clips),
    )


def structured_constraints(count: int) -> ProposalConstraints:
    """Absolute engine bounds for user-selected ranges."""

    return ProposalConstraints(
        max_clips=count, min_seconds=MIN_CLIP_SECONDS, max_seconds=MAX_CLIP_SECONDS
    )


def build_confirmed_edl(
    proposal: EDLProposal,
    *,
    confirmation: HighlightConfirmation,
    base_edl: EDLDocument | Mapping[str, Any],
) -> EDLDocument:
    """Build an EDL from a confirmed proposal; never persists and never renders.

    The first timeline segment of ``base_edl`` supplies visual, caption, audio,
    and colour settings. Clip titles are not copied into EDL provenance.
    """

    if not confirmation.confirmed:
        raise HighlightError("confirmation_required", "the user has not confirmed this proposal")
    if confirmation.proposal_fingerprint != proposal.fingerprint:
        raise HighlightError(
            "confirmation_mismatch", "the confirmation does not match this proposal"
        )
    if proposal.fingerprint != _fingerprint(proposal.project_id, proposal.asset_id, proposal.clips):
        raise HighlightError("proposal_tampered", "the proposal no longer matches its fingerprint")
    base = validate_edl(base_edl)
    if base.project_id != proposal.project_id:
        raise HighlightError("confirmation_mismatch", "the base EDL belongs to another project")
    if proposal.asset_id not in {asset.asset_id for asset in base.source_assets}:
        raise HighlightError(
            "confirmation_mismatch", "the proposal asset is not a source of the base EDL"
        )
    selected = proposal.clips
    if confirmation.clip_ids is not None:
        wanted = set(confirmation.clip_ids)
        if not wanted or not wanted <= {clip.clip_id for clip in proposal.clips}:
            raise HighlightError(
                "confirmation_mismatch", "confirmed clip ids are not in this proposal"
            )
        selected = [clip for clip in proposal.clips if clip.clip_id in wanted]
    template = base.timeline[0].model_dump(mode="json")
    timeline = []
    for clip in selected:
        segment = {
            **template,
            "id": f"highlight_{clip.clip_id}",
            "source": {"asset_id": str(proposal.asset_id), "start": clip.start, "end": clip.end},
            "provenance": {
                "reason": "confirmed highlight proposal",
                "proposal_fingerprint": proposal.fingerprint,
                "route": proposal.route,
                "start_segment_id": clip.start_segment_id,
                "end_segment_id": clip.end_segment_id,
            },
        }
        timeline.append(segment)
    document = {**base.model_dump(mode="json"), "timeline": timeline}
    return validate_edl(document)


__all__ = [
    "DISPATCHABLE_PRIVACY",
    "PROPOSAL_SCHEMA",
    "REQUEST_SCHEMA",
    "ClipRange",
    "EDLProposal",
    "HighlightConfirmation",
    "HighlightError",
    "HighlightRequest",
    "PrivacyClass",
    "ProposalConstraints",
    "ProposedClip",
    "SegmentIn",
    "build_confirmed_edl",
    "build_proposal",
    "prompt_alias",
    "structured_constraints",
    "transcript_index",
]
