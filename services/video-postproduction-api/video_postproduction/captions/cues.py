"""Caption cues on the record timeline, identical to what the renderer burns in.

The ASS renderer wraps Thai text with PyThaiNLP ``newmm`` at a column limit
derived from the output width. A person rebuilding the edit in DaVinci
Resolve or Premiere cannot import ASS, so this module produces the same cues
(same start/end, same line breaks) as an SRT the editors can import. For a
multi-clip timeline each clip's cues are shifted to that clip's record
position.

Cue text is transcript content: it belongs in the caption artifact and the
manual guide only, never in ``trace.json`` or ``history.jsonl``.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from ..transcription.models import NormalizedTranscript
from .ass import _relative_bounds, _segment_text, wrap_caption_text
from .srt import format_srt_time


@dataclass(frozen=True, slots=True)
class CaptionCue:
    index: int
    start: float
    end: float
    lines: tuple[str, ...]
    clip_index: int


@dataclass(frozen=True, slots=True)
class ClipWindow:
    """One timeline clip: source range plus where it starts on the record timeline."""

    source_start: float
    source_end: float
    record_start: float


def timeline_cues(
    transcript: NormalizedTranscript,
    clips: Sequence[ClipWindow],
    *,
    wrap_columns: int | None,
) -> list[CaptionCue]:
    """Return cues per clip in record time; ``wrap_columns=None`` keeps one line (SRT burn-in)."""

    cues: list[CaptionCue] = []
    for clip_index, clip in enumerate(clips, start=1):
        for segment in transcript.segments:
            text = _segment_text(segment)
            bounds = _relative_bounds(segment, clip_start=clip.source_start, clip_end=clip.source_end)
            if not text or bounds is None:
                continue
            start, end = bounds
            lines = (
                tuple(wrap_caption_text(text, [word.text for word in segment.words], wrap_columns))
                if wrap_columns is not None
                else (text,)
            )
            cues.append(
                CaptionCue(
                    index=len(cues) + 1,
                    start=clip.record_start + start,
                    end=clip.record_start + end,
                    lines=lines,
                    clip_index=clip_index,
                )
            )
    return cues


def render_editor_srt(cues: Sequence[CaptionCue]) -> str:
    """SRT for editors: the burned-in line breaks are kept as real line breaks."""

    blocks = [
        "\n".join(
            [str(cue.index), f"{format_srt_time(cue.start)} --> {format_srt_time(cue.end)}", *cue.lines]
        )
        for cue in cues
    ]
    return "\n\n".join(blocks) + ("\n" if blocks else "")


__all__ = ["CaptionCue", "ClipWindow", "render_editor_srt", "timeline_cues"]
