"""SRT caption rendering from normalized transcript segments."""

from __future__ import annotations

from ..transcription.models import NormalizedTranscript, TranscriptSegment


def format_srt_time(seconds: float) -> str:
    """Format seconds as a valid ``HH:MM:SS,mmm`` timestamp."""

    milliseconds = max(0, round(seconds * 1000))
    hours, remainder = divmod(milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    whole_seconds, milliseconds = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{whole_seconds:02d},{milliseconds:03d}"


def _segment_text(segment: TranscriptSegment) -> str:
    text = segment.text if isinstance(segment.text, str) else ""
    if not text.strip() and segment.words:
        text = " ".join(word.text for word in segment.words)
    return " ".join(text.replace("\r", " ").replace("\n", " ").split())


def _relative_bounds(
    segment: TranscriptSegment,
    *,
    clip_start: float,
    clip_end: float | None,
) -> tuple[float, float] | None:
    visible_start = max(segment.start, clip_start)
    visible_end = min(segment.end, clip_end) if clip_end is not None else segment.end
    if visible_end <= visible_start:
        return None
    return visible_start - clip_start, visible_end - clip_start


def render_srt(
    transcript: NormalizedTranscript,
    *,
    clip_start: float = 0.0,
    clip_end: float | None = None,
) -> str:
    """Render one deterministic SRT cue per normalized segment."""

    cues: list[str] = []
    for segment in transcript.segments:
        text = _segment_text(segment)
        bounds = _relative_bounds(segment, clip_start=clip_start, clip_end=clip_end)
        if not text or bounds is None:
            continue
        start, end = bounds
        cues.append(
            "\n".join(
                [
                    str(len(cues) + 1),
                    f"{format_srt_time(start)} --> {format_srt_time(end)}",
                    text,
                ]
            )
        )
    return "\n\n".join(cues) + ("\n" if cues else "")
