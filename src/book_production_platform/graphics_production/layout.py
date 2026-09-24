"""Small deterministic text layout helpers used by fixed templates."""

from __future__ import annotations

from dataclasses import dataclass


class LayoutError(ValueError):
    """Raised when bounded text layout cannot preserve all input text."""


@dataclass(frozen=True)
class TextLayout:
    lines: tuple[str, ...]
    font_size: float


def wrap_text(text: str, width: float, font_size: float) -> tuple[str, ...]:
    """Wrap by a conservative character-width estimate, including Thai text."""

    max_chars = max(1, int(width / max(font_size * 0.55, 1)))
    lines: list[str] = []
    for paragraph in text.splitlines() or [""]:
        if not paragraph:
            lines.append("")
            continue
        lines.extend(paragraph[index : index + max_chars] for index in range(0, len(paragraph), max_chars))
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
