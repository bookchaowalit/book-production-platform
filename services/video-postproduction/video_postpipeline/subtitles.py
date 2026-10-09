"""Deterministic SRT and WebVTT subtitle emitters for transcript artifacts.

Pinned formatting rules (identical output for identical input):

- Timestamps are ``HH:MM:SS`` plus exactly three millisecond digits. SRT uses
  a comma decimal separator (``00:00:01,500``), WebVTT uses a dot
  (``00:00:01.500``). Cue ranges use `` --> `` between start and end.
- Hours are a plain unpadded decimal integer, so media longer than 99 hours
  still formats deterministically.
- Cue text is hard-wrapped at 42 code points with a greedy word rule: words
  join a line with single spaces while they fit; when a word does not fit the
  current line is closed; a word longer than the limit (e.g. Thai text without
  spaces) is broken into chunks of exactly the limit. Existing newlines in
  segment text are normalized to spaces before wrapping.
"""

from __future__ import annotations

from pathlib import Path

from .contract import TranscriptArtifact, TranscriptSegment

SRT_EXTENSION = ".srt"
VTT_EXTENSION = ".vtt"
CUE_LINE_WIDTH = 42
_TIMESTAMP_DECIMAL_SEPARATOR = {"srt": ",", "vtt": "."}


def format_timestamp(seconds: float, *, dialect: str) -> str:
    """Format seconds as ``HH:MM:SS<sep>mmm`` for the given dialect."""

    try:
        separator = _TIMESTAMP_DECIMAL_SEPARATOR[dialect]
    except KeyError as exc:
        raise ValueError(f"unknown timestamp dialect: {dialect}") from exc
    if seconds < 0:
        raise ValueError("timestamp seconds must not be negative")
    total_milliseconds = round(seconds * 1000)
    hours, remainder = divmod(total_milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    whole_seconds, milliseconds = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{whole_seconds:02d}{separator}{milliseconds:03d}"


def wrap_cue_text(text: str, width: int = CUE_LINE_WIDTH) -> list[str]:
    """Wrap text into deterministic cue lines of at most ``width`` code points."""

    normalized = " ".join(text.split())
    lines: list[str] = []
    current = ""
    for word in normalized.split(" ") if normalized else []:
        candidate = f"{current} {word}" if current else word
        if len(candidate) <= width:
            current = candidate
            continue
        if current:
            lines.append(current)
        while len(word) > width:
            lines.append(word[:width])
            word = word[width:]
        current = word
    if current:
        lines.append(current)
    return lines or [""]


def _cue_text_lines(segment: TranscriptSegment) -> list[str]:
    label = f"{segment.speaker}: " if segment.speaker else ""
    return wrap_cue_text(f"{label}{segment.text}")


def render_srt(artifact: TranscriptArtifact) -> str:
    """Render the artifact as a deterministic SRT document."""

    blocks: list[str] = []
    for index, segment in enumerate(artifact.segments, start=1):
        start = format_timestamp(segment.start_seconds, dialect="srt")
        end = format_timestamp(segment.end_seconds, dialect="srt")
        body = "\n".join(_cue_text_lines(segment))
        blocks.append(f"{index}\n{start} --> {end}\n{body}")
    return "\n\n".join(blocks) + "\n"


def render_vtt(artifact: TranscriptArtifact) -> str:
    """Render the artifact as a deterministic WebVTT document."""

    blocks = ["WEBVTT"]
    for segment in artifact.segments:
        start = format_timestamp(segment.start_seconds, dialect="vtt")
        end = format_timestamp(segment.end_seconds, dialect="vtt")
        body = "\n".join(_cue_text_lines(segment))
        blocks.append(f"{start} --> {end}\n{body}")
    return "\n\n".join(blocks) + "\n"


def write_subtitles(artifact: TranscriptArtifact, output_dir: str | Path, *, formats: tuple[str, ...] = ("srt", "vtt")) -> list[Path]:
    """Write subtitle files named ``<transcript_id>.<ext>`` and return their paths."""

    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for fmt in formats:
        if fmt == "srt":
            content, extension = render_srt(artifact), SRT_EXTENSION
        elif fmt == "vtt":
            content, extension = render_vtt(artifact), VTT_EXTENSION
        else:
            raise ValueError(f"unknown subtitle format: {fmt}")
        target = output_path / f"{artifact.transcript_id}{extension}"
        target.write_text(content, encoding="utf-8", newline="\n")
        written.append(target)
    return written
