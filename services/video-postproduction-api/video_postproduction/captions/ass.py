"""ASS caption rendering with escaped event text."""

from __future__ import annotations

import logging
import re

from pythainlp.tokenize import word_tokenize

from ..transcription.models import NormalizedTranscript, TranscriptSegment

# Noto Sans Thai ships in the runtime image (fonts-noto-core); Latin glyphs fall
# back to Noto Sans through fontconfig.
_FONT = "Noto Sans Thai"
PRESETS: dict[str, tuple[str, str]] = {
    "minimal": (_FONT, "52"),
    "bold": (_FONT, "58"),
    "karaoke": (_FONT, "56"),
    "emphasis": (_FONT, "60"),
}

# Visible columns per caption line: usable width (output width minus 2 x 80 px
# margins) over ~38 px per glyph at up to 60 px font size, i.e. 24 columns on a
# 1080 px wide frame. WrapStyle 2 disables libass wrapping, and Thai has no
# spaces for it to wrap on, so lines are broken here.
_MAX_LINE_COLUMNS = 24
_HORIZONTAL_MARGIN = 80
_COLUMN_WIDTH_PX = 38
_DEFAULT_OUTPUT_SIZE = (1080, 1920)
# Thai above/below vowels and tone marks occupy no column of their own.
_THAI_COMBINING = frozenset(chr(c) for c in (0x0E31, *range(0x0E34, 0x0E3B), *range(0x0E47, 0x0E4F)))
# Thai leading vowels are written before their consonant and must not end a line.
_THAI_LEADING_VOWELS = frozenset("เแโใไ")
_logger = logging.getLogger(__name__)


def _columns(text: str) -> int:
    return sum(1 for ch in text if ch not in _THAI_COMBINING)


def _safe_cut(text: str, index: int) -> bool:
    return 0 < index < len(text) and text[index] not in _THAI_COMBINING and text[index - 1] not in _THAI_LEADING_VOWELS


def _hard_split(text: str, limit: int) -> list[str]:
    pieces: list[str] = []
    while _columns(text) > limit:
        cut, used = 0, 0
        for index, ch in enumerate(text):
            if ch not in _THAI_COMBINING:
                used += 1
            if used > limit:
                break
            cut = index + 1
        while cut > 0 and not _safe_cut(text, cut):
            cut -= 1
        if cut == 0:
            break
        pieces.append(text[:cut])
        text = text[cut:]
    pieces.append(text)
    return pieces


def _word_boundaries(text: str, words: list[str]) -> set[int]:
    boundaries: set[int] = set()
    cursor = 0
    if any("\u0e01" <= character <= "\u0e5b" for character in text):
        try:
            tokens = word_tokenize(text, engine="newmm", keep_whitespace=True)
        except (ValueError, TypeError, RecursionError) as exc:
            _logger.warning("caption_tokenization_fallback reason=%s", type(exc).__name__)
            return boundaries
        if "".join(tokens) != text:
            _logger.warning("caption_tokenization_fallback reason=character_mismatch")
            return boundaries
        for token in tokens:
            cursor += len(token)
            boundaries.add(cursor)
        return boundaries
    for word in words:
        token = word.strip()
        if not token:
            continue
        found = text.find(token, cursor)
        if found < 0:
            continue
        cursor = found + len(token)
        boundaries.add(cursor)
    return boundaries


def _pack(units: list[str], limit: int) -> list[str]:
    lines: list[str] = []
    current = ""
    for unit in units:
        candidate = current + unit
        if current and _columns(candidate) > limit:
            lines.append(current)
            current = unit
        else:
            current = candidate
    if current:
        lines.append(current)
    return lines


def wrap_caption_text(text: str, words: list[str], limit: int = _MAX_LINE_COLUMNS) -> list[str]:
    """Wrap at local Thai tokens or word timings, retaining every source character."""

    boundaries = _word_boundaries(text, words)
    for whitespace in re.finditer(r"\s+", text):
        boundaries.update((whitespace.start(), whitespace.end()))
    cuts = sorted(boundary for boundary in boundaries if _safe_cut(text, boundary))
    units = [
        part
        for start, end in zip([0, *cuts], [*cuts, len(text)], strict=True)
        for part in _hard_split(text[start:end], limit)
    ]
    return _pack(units, limit)


def format_ass_time(seconds: float) -> str:
    centiseconds = max(0, round(seconds * 100))
    hours, remainder = divmod(centiseconds, 360_000)
    minutes, remainder = divmod(remainder, 6_000)
    whole_seconds, centiseconds = divmod(remainder, 100)
    return f"{hours}:{minutes:02d}:{whole_seconds:02d}.{centiseconds:02d}"


def escape_ass_text(text: str) -> str:
    """Escape user/provider text so it cannot inject ASS event syntax."""

    return (
        text.replace("\\", "\\\\")
        .replace("{", "\\{")
        .replace("}", "\\}")
        .replace("\r", " ")
        .replace("\n", "\\N")
    )


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


def line_columns_for_width(width: int) -> int:
    return max(12, (width - 2 * _HORIZONTAL_MARGIN) // _COLUMN_WIDTH_PX)


def render_ass(
    transcript: NormalizedTranscript,
    preset: str = "minimal",
    *,
    clip_start: float = 0.0,
    clip_end: float | None = None,
    output_size: tuple[int, int] = _DEFAULT_OUTPUT_SIZE,
) -> str:
    """Render deterministic ASS headers and dialogue events on the output's own canvas."""

    font, font_size = PRESETS.get(preset, PRESETS["minimal"])
    width, height = output_size
    # The canvas must match the output aspect or libass stretches glyphs.
    margin_bottom = round(120 * height / _DEFAULT_OUTPUT_SIZE[1])
    columns = line_columns_for_width(width)
    lines = [
        "[Script Info]",
        "ScriptType: v4.00+",
        f"PlayResX: {width}",
        f"PlayResY: {height}",
        "WrapStyle: 2",
        "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        # 4 px black outline plus a soft shadow keeps white text legible on bright footage.
        f"Style: Default,{font},{font_size},&H00FFFFFF,&H000000FF,&H00000000,&H80000000,0,0,0,0,100,100,0,0,1,4,1,2,{_HORIZONTAL_MARGIN},{_HORIZONTAL_MARGIN},{margin_bottom},1",
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]
    for segment in transcript.segments:
        text = _segment_text(segment)
        bounds = _relative_bounds(segment, clip_start=clip_start, clip_end=clip_end)
        if text and bounds is not None:
            start, end = bounds
            wrapped = "\\N".join(
                escape_ass_text(line) for line in wrap_caption_text(text, [word.text for word in segment.words], columns)
            )
            lines.append(f"Dialogue: 0,{format_ass_time(start)},{format_ass_time(end)},Default,,0,0,0,,{wrapped}")
    return "\n".join(lines) + "\n"
