"""Small deterministic text layout helpers used by fixed templates."""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from functools import lru_cache


class LayoutError(ValueError):
    """Raised when bounded text layout cannot preserve all input text.

    The message stays ``text_does_not_fit``. The attributes describe the bound
    that was exceeded so callers can say which text node overflowed and by how
    much: ``max_lines`` and ``line_chars`` are the line count and characters per
    line that fit at the minimum font size, ``length`` is the visible length of
    the text, and ``node_id`` is set by the template that laid it out.
    """

    def __init__(
        self,
        message: str = "text_does_not_fit",
        *,
        node_id: str | None = None,
        max_lines: int | None = None,
        line_chars: int | None = None,
        length: int | None = None,
    ) -> None:
        super().__init__(message)
        self.node_id = node_id
        self.max_lines = max_lines
        self.line_chars = line_chars
        self.length = length

    @property
    def max_chars(self) -> int | None:
        """Upper bound on visible characters for the node (word wrap may fit fewer)."""

        if self.max_lines is None or self.line_chars is None:
            return None
        return self.max_lines * self.line_chars


@dataclass(frozen=True)
class TextLayout:
    lines: tuple[str, ...]
    font_size: float


# Thai above/below vowels and tone marks take no horizontal space and must stay
# with the consonant before them; leading vowels must stay with the consonant
# after them.
_THAI_COMBINING = frozenset(chr(code) for code in (0x0E31, *range(0x0E34, 0x0E3B), *range(0x0E47, 0x0E4F)))
_THAI_LEADING = frozenset(chr(code) for code in range(0x0E40, 0x0E45))
# Following vowels (sara a, sara aa, sara am, lakkhangyao) and the repetition
# mark mai yamok take horizontal space but can never begin a line, so they stay
# with the cluster before them.
_THAI_FOLLOWING = frozenset(chr(code) for code in (0x0E30, 0x0E32, 0x0E33, 0x0E45, 0x0E46))


def visible_length(text: str) -> int:
    """Characters that take horizontal space (Thai combining marks count as zero)."""

    return sum(1 for char in text if char not in _THAI_COMBINING)


def _clusters(word: str) -> list[str]:
    clusters: list[str] = []
    pending = ""
    for char in word:
        if (char in _THAI_COMBINING or char in _THAI_FOLLOWING) and clusters and not pending:
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


_THAI_CHAR = re.compile("[\u0e00-\u0e7f]")


@lru_cache(maxsize=1)
def _thai_tokenizer() -> Callable[[str], list[str]] | None:
    """PyThaiNLP ``newmm`` (dictionary-based, no model download) when installed; optional."""

    try:
        from pythainlp.tokenize import (
            word_tokenize,  # type: ignore[import-not-found,import-untyped,unused-ignore]
        )
    except ImportError:
        return None

    def tokenize(word: str) -> list[str]:
        return [token for token in word_tokenize(word, engine="newmm", keep_whitespace=False) if token]

    return tokenize


def _split_long_word(word: str, max_chars: int) -> list[str]:
    """Split a word longer than a line, at Thai word boundaries when possible.

    Thai is written without spaces, so a long phrase is one "word" here. With the
    optional Thai tokenizer the pieces break between words (ถึงเวลาฉีดวัคซีน | ประจำปีแล้ว);
    otherwise, or for a single over-long token, between character clusters.
    """

    tokenize = _thai_tokenizer() if _THAI_CHAR.search(word) else None
    if tokenize is None:
        return _hard_split(word, max_chars)
    tokens = tokenize(word)
    if "".join(tokens) != word:
        return _hard_split(word, max_chars)
    # Keep the cluster rules: a token may not start with a combining or following
    # mark, and a leading vowel stays with the token after it.
    merged: list[str] = []
    for token in tokens:
        if merged and (token[0] in _THAI_COMBINING or token[0] in _THAI_FOLLOWING or merged[-1][-1] in _THAI_LEADING):
            merged[-1] += token
        else:
            merged.append(token)
    # An unknown loanword ("ดริป") comes back as one-letter fragments (ด|ริ|ป); keep each such run together.
    tokens = []
    in_run = False
    for token in merged:
        fragment = visible_length(token) <= 1 and bool(_THAI_CHAR.search(token))
        if fragment and in_run:
            tokens[-1] += token
        else:
            tokens.append(token)
        in_run = fragment
    pieces: list[str] = []
    current = ""
    for token in tokens:
        if current and visible_length(current + token) > max_chars:
            pieces.append(current)
            current = ""
        if visible_length(token) > max_chars:
            *full, current = _hard_split(current + token, max_chars)
            pieces.extend(full)
            continue
        current += token
    if current:
        pieces.append(current)
    return pieces


def wrap_text(text: str, width: float, font_size: float, char_width: float = 0.55) -> tuple[str, ...]:
    """Wrap by a conservative width estimate, preferring spaces and never splitting a Thai cluster.

    Lines break at whitespace first; a word longer than one line is split at Thai
    word boundaries when the optional PyThaiNLP tokenizer is installed, else between
    character clusters, so a vowel, tone mark or mai yamok never starts a line. Every
    non-space character of the input is kept in order.
    """

    max_chars = max(1, int(width / max(font_size * char_width, 1)))
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
                *full, current = _split_long_word(word, max_chars)
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
    char_width: float = 0.55,
) -> TextLayout:
    """Reduce font size within a fixed bound; never silently truncate text."""

    candidate = font_size
    while candidate >= min_font_size:
        lines = wrap_text(text, width, candidate, char_width)
        if len(lines) <= max_lines and len(lines) * candidate * line_height <= height:
            return TextLayout(lines=lines, font_size=round(candidate, 2))
        candidate -= 1
    if overflow_policy == "wrap" and len(wrap_text(text, width, min_font_size, char_width)) <= max_lines:
        return TextLayout(lines=wrap_text(text, width, min_font_size, char_width), font_size=round(min_font_size, 2))
    line_chars = max(1, int(width / max(min_font_size * char_width, 1)))
    fitting_lines = max_lines if overflow_policy == "wrap" else min(max_lines, int(height / (min_font_size * line_height)))
    raise LayoutError(
        "text_does_not_fit", max_lines=max(1, fitting_lines), line_chars=line_chars, length=visible_length(text)
    )


# --- inline markup -------------------------------------------------------------------------------

_MARKERS = (("**", "heavy"), ("*", "accent"), ("~", "outline"))


def parse_markup(text: str) -> tuple[str, list[tuple[int, int, str]]]:
    """Strip inline markup and return the plain text and its styled runs.

    ``*word*`` gives the accent color, ``**word**`` heavy weight, ``~word~`` outline-only letters.
    A marker without a closing partner stays literal text, and a backslash before ``*`` or ``~``
    writes the character itself, so ordinary copy (footnote stars, ``~5 km``) is unchanged.
    """

    plain: list[str] = []
    runs: list[tuple[int, int, str]] = []
    index = 0
    while index < len(text):
        char = text[index]
        if char == "\\" and index + 1 < len(text) and text[index + 1] in "*~":
            plain.append(text[index + 1])
            index += 2
            continue
        matched = False
        for marker, kind in _MARKERS:
            if not text.startswith(marker, index):
                continue
            close = text.find(marker, index + len(marker))
            # A single "*" never closes on half of a "**".
            while marker == "*" and close != -1 and text.startswith("**", close):
                close = text.find(marker, close + 2)
            if close <= index + len(marker):
                break
            inner = text[index + len(marker):close]
            # Like Markdown: no space just inside the markers ("x * y * z" stays as written).
            if "\n" in inner or inner[0].isspace() or inner[-1].isspace():
                break
            start = len(plain)
            plain.extend(inner)
            runs.append((start, len(plain), kind))
            index = close + len(marker)
            matched = True
            break
        if not matched:
            plain.append(char)
            index += 1
    return "".join(plain), runs


def line_runs(text: str, lines: tuple[str, ...], runs: list[tuple[int, int, int]]) -> list[list[tuple[str, int]]]:
    """Split each rendered line into ``(segment, run_index)`` pieces (-1 = unstyled).

    ``runs`` are ``(start, end, run_index)`` ranges over ``text``. :func:`wrap_text` keeps every
    non-space character in order, so line characters are matched back to ``text`` by walking both.
    """

    owner = [-1] * len(text)
    for start, end, run_index in runs:
        for position in range(start, min(end, len(text))):
            owner[position] = run_index
    pieces: list[list[tuple[str, int]]] = []
    cursor = 0
    for line in lines:
        segments: list[tuple[str, int]] = []
        for char in line:
            if char.isspace():
                style = -1
                if cursor < len(text) and text[cursor].isspace():
                    style = owner[cursor]
                    cursor += 1
            else:
                while cursor < len(text) and text[cursor] != char:
                    cursor += 1
                style = owner[cursor] if cursor < len(text) else -1
                cursor += 1
            if segments and segments[-1][1] == style:
                segments[-1] = (segments[-1][0] + char, style)
            else:
                segments.append((char, style))
        pieces.append(segments)
    return pieces
