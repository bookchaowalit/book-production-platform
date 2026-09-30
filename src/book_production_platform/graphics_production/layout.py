"""Small deterministic text layout helpers used by fixed templates."""

from __future__ import annotations

from dataclasses import dataclass


class LayoutError(ValueError):
    """Raised when bounded text layout cannot preserve all input text."""


@dataclass(frozen=True)
class TextLayout:
    lines: tuple[str, ...]
    font_size: float


# Thai above/below vowels and tone marks take no horizontal space and must stay
# with the consonant before them; leading vowels must stay with the consonant
# after them.
_THAI_COMBINING = frozenset(chr(code) for code in (0x0E31, *range(0x0E34, 0x0E3B), *range(0x0E47, 0x0E4F)))
_THAI_LEADING = frozenset(chr(code) for code in range(0x0E40, 0x0E45))


def visible_length(text: str) -> int:
    """Characters that take horizontal space (Thai combining marks count as zero)."""

    return sum(1 for char in text if char not in _THAI_COMBINING)


def _clusters(word: str) -> list[str]:
    clusters: list[str] = []
    pending = ""
    for char in word:
        if char in _THAI_COMBINING and clusters and not pending:
            clusters[-1] += char
        elif char in _THAI_LEADING:
            pending += char
        else:
            clusters.append(pending + char)
            pending = ""
    if pending:
        clusters.append(pending)
    return clusters


def _hard_split(word: str, max_chars: int) -> list[str]:
    pieces: list[str] = []
    current = ""
    for cluster in _clusters(word):
        if current and visible_length(current + cluster) > max_chars:
            pieces.append(current)
            current = ""
        current += cluster
    if current:
        pieces.append(current)
    return pieces


def wrap_text(text: str, width: float, font_size: float) -> tuple[str, ...]:
    """Wrap by a conservative width estimate, preferring spaces and never splitting a Thai cluster.

    Lines break at whitespace first; a word longer than one line is split between
    character clusters so a vowel or tone mark never starts a line. Every
    non-space character of the input is kept in order.
    """

    max_chars = max(1, int(width / max(font_size * 0.55, 1)))
    lines: list[str] = []
    for paragraph in text.splitlines() or [""]:
        words = paragraph.split()
        if not words:
            lines.append("")
            continue
        current = ""
        for word in words:
            candidate = f"{current} {word}" if current else word
            if visible_length(candidate) <= max_chars:
                current = candidate
                continue
            if current:
                lines.append(current)
                current = ""
            if visible_length(word) <= max_chars:
                current = word
            else:
                *full, current = _hard_split(word, max_chars)
                lines.extend(full)
        lines.append(current)
    return tuple(lines)


def fit_text(
    text: str,
    *,
    width: float,
    height: float,
    font_size: float,
    min_font_size: float,
    max_lines: int,
    line_height: float = 1.2,
    overflow_policy: str = "reject",
) -> TextLayout:
    """Reduce font size within a fixed bound; never silently truncate text."""

    candidate = font_size
    while candidate >= min_font_size:
        lines = wrap_text(text, width, candidate)
        if len(lines) <= max_lines and len(lines) * candidate * line_height <= height:
            return TextLayout(lines=lines, font_size=round(candidate, 2))
        candidate -= 1
    if overflow_policy == "wrap" and len(wrap_text(text, width, min_font_size)) <= max_lines:
        return TextLayout(lines=wrap_text(text, width, min_font_size), font_size=round(min_font_size, 2))
    raise LayoutError("text_does_not_fit")
