"""Deterministic copy rules for Marketing campaign copy (REQ-MKT-01).

These are the engine's own checks; they run on every model-proposed copy and
on structured asset text, and the shadow report re-runs them on accepted
output. They never rewrite or truncate copy: a violation is a flag for a
person (``needs_input``).

- per-channel length limits (characters after NFC normalization);
- forbidden words: a baseline brand-safety list plus the brand's own list;
- claim markers (superlatives, rankings, awards, guarantees, proof) that need
  evidence in the brief before they may appear in copy;
- numbers and links, which :mod:`marketing_production.provider` must ground
  in the brief.

This module has no AI or network dependency. Messages are static and never
echo copy, brief text, or brand words.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable

from marketing_ir import ValidationIssue

CHANNEL_LIMITS: dict[str, int] = {
    "facebook": 500,
    "instagram": 500,
    "linkedin": 700,
    "email": 1_200,
    "line": 400,
    "web": 300,
}
DEFAULT_FORBIDDEN_WORDS: tuple[str, ...] = (
    "guaranteed results", "risk-free", "miracle", "cure", "รับประกันผล", "หายขาด", "ปาฏิหาริย์",
)
CLAIM_MARKERS: dict[str, re.Pattern[str]] = {
    "best": re.compile(r"\bbest\b|ที่สุด", re.IGNORECASE),
    "rank_first": re.compile(r"#\s?1\b|\bnumber\s+one\b|\bno\.\s?1\b|อันดับ\s?(?:1|๑|หนึ่ง)", re.IGNORECASE),
    "fastest": re.compile(r"\bfastest\b|เร็วที่สุด", re.IGNORECASE),
    "cheapest": re.compile(r"\bcheapest\b|\blowest\s+price\b|ถูกที่สุด", re.IGNORECASE),
    "proven": re.compile(r"\bproven\b|\bclinically\b|พิสูจน์|ทางคลินิก", re.IGNORECASE),
    "award": re.compile(r"\baward(?:s|ed|-winning)?\b|รางวัล", re.IGNORECASE),
    "certified": re.compile(r"\bcertified\b|\bofficial\b|รับรอง|ของแท้", re.IGNORECASE),
    "guarantee": re.compile(r"\bguarantee[ds]?\b|การันตี|รับประกัน", re.IGNORECASE),
}
_THAI_DIGITS = str.maketrans("๐๑๒๓๔๕๖๗๘๙", "0123456789")
_NUMBER = re.compile(r"(?<![\w.])\d{1,3}(?:,\d{3})+(?:\.\d+)?|(?<![\w.])\d+(?:\.\d+)?")
_LINK = re.compile(r"(?:https?://|www\.)[^\s<>\"']+", re.IGNORECASE)
_UNSAFE = re.compile(r"<\s*script|javascript:|[\x00-\x08\x0b\x0c\x0e-\x1f]", re.IGNORECASE)


def _issue(code: str, path: str, message: str) -> ValidationIssue:
    return ValidationIssue(code=code, path=path, message=message)


def normalize(text: str) -> str:
    return unicodedata.normalize("NFC", text).translate(_THAI_DIGITS)


def copy_length(text: str) -> int:
    return len(unicodedata.normalize("NFC", text))


def forbidden_hits(text: str, forbidden_words: Iterable[str] = ()) -> list[str]:
    haystack = normalize(text).casefold()
    words = {*DEFAULT_FORBIDDEN_WORDS, *(word for word in forbidden_words if word.strip())}
    return sorted(word for word in words if normalize(word).casefold().strip() in haystack)


def claim_markers(text: str) -> set[str]:
    normalized = normalize(text)
    return {name for name, pattern in CLAIM_MARKERS.items() if pattern.search(normalized)}


def numbers_in(text: str) -> list[str]:
    return [match.replace(",", "") for match in _NUMBER.findall(normalize(text))]


def links_in(text: str) -> list[str]:
    return [match.rstrip(".,)") for match in _LINK.findall(text)]


def copy_issues(channel: str, text: str, *, forbidden_words: Iterable[str] = (), path: str = "copy") -> list[ValidationIssue]:
    """Length, forbidden-word, and safety checks that need no brief."""

    issues: list[ValidationIssue] = []
    if not text.strip():
        return [_issue("copy_empty", path, "copy text is empty")]
    limit = CHANNEL_LIMITS.get(channel)
    if limit is None:
        issues.append(_issue("channel_not_allowed", path, "the channel has no copy rules"))
    elif copy_length(text) > limit:
        issues.append(_issue("copy_too_long", path, "copy exceeds the channel length limit"))
    if forbidden_hits(text, forbidden_words):
        issues.append(_issue("forbidden_word", path, "copy contains a forbidden word"))
    if _UNSAFE.search(text):
        issues.append(_issue("unsafe_copy", path, "copy contains markup or control characters"))
    return issues


__all__ = [
    "CHANNEL_LIMITS",
    "CLAIM_MARKERS",
    "DEFAULT_FORBIDDEN_WORDS",
    "claim_markers",
    "copy_issues",
    "copy_length",
    "forbidden_hits",
    "links_in",
    "normalize",
    "numbers_in",
]
