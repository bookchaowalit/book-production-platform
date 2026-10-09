"""Deterministic parsing of a highlight brief into engine constraints.

The model never decides how many clips to cut or how long they are: the clip
count, the duration range, and the presence of a topic are read from the
user's brief here, in Thai or English. When any of the three cannot be
resolved the request stops with ``needs_input`` before any model call
(REQ-AI-02). Requests outside the engine limits are rejected, not clamped.

The parsed topic is only used to confirm that the brief names one; it is
never stored in a proposal, issue, report, or log.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

MAX_CLIPS = 10
MIN_CLIP_SECONDS = 1.0
MAX_CLIP_SECONDS = 180.0

_THAI_DIGITS = str.maketrans("๐๑๒๓๔๕๖๗๘๙", "0123456789")
_EN_NUMBERS = {
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "eleven": 11,
    "twelve": 12,
    "fifteen": 15,
    "twenty": 20,
}
_TH_NUMBERS = {
    "หนึ่ง": 1,
    "สอง": 2,
    "สาม": 3,
    "สี่": 4,
    "ห้า": 5,
    "หก": 6,
    "เจ็ด": 7,
    "แปด": 8,
    "เก้า": 9,
    "สิบ": 10,
    "ยี่สิบ": 20,
}
_EN_WORDS = "|".join(sorted(_EN_NUMBERS, key=len, reverse=True))
_TH_WORDS = "|".join(sorted(_TH_NUMBERS, key=len, reverse=True))
_COUNT = re.compile(
    rf"(?<![\w.\-–—])(\d{{1,3}}|{_EN_WORDS})\s+"
    r"(?:(?!(?:seconds?|secs?|minutes?|mins?)\b)[a-z-]+\s+){0,2}?"
    r"(?:clips?|highlights?|shorts?|moments?|segments?|reels?)\b"
    rf"|(?<![\d.\-–—])(\d{{1,3}}|{_TH_WORDS})\s*(?:คลิป|ช่วง|ไฮไลต์|ไฮไลท์)",
    re.IGNORECASE,
)
_UNIT = r"(seconds?|secs?|s(?![a-z])|minutes?|mins?|m(?![a-z])|วินาที|วิ|นาที)"
_NUMBER = r"(\d{1,4}(?:\.\d+)?)"
_RANGE = re.compile(
    rf"(?:between\s+|ระหว่าง\s*)?{_NUMBER}\s*{_UNIT}?\s*(?:-|–|—|to|and|ถึง|และ)\s*{_NUMBER}\s*{_UNIT}",
    re.IGNORECASE,
)
_UPPER = re.compile(
    rf"(?:up\s+to|under|at\s+most|no\s+more\s+than|max(?:imum)?|ไม่เกิน|ไม่ถึง|สูงสุด)\s*{_NUMBER}\s*{_UNIT}",
    re.IGNORECASE,
)
_TOPIC = re.compile(
    r"\b(?:about|regarding|covering|related\s+to|focused\s+on|on\s+the\s+topic\s+of|on)\s+"
    r"(?!\d)([^\n.,;!?()]{2,80})"
    r"|(?:เกี่ยวกับ|เรื่อง|หัวข้อ|ประเด็น)\s*([^\n.,;!?()0-9]{2,80})",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class BriefConstraints:
    """Constraints the engine enforces; ``topic_present`` never carries the topic text."""

    count: int | None = None
    min_seconds: float | None = None
    max_seconds: float | None = None
    topic_present: bool = False
    missing: list[str] = field(default_factory=list)
    issue_code: str | None = None


def _number(token: str) -> int | None:
    token = token.strip().lower()
    if token.isdigit():
        return int(token)
    return _EN_NUMBERS.get(token) or _TH_NUMBERS.get(token)


def _seconds(value: str, unit: str | None) -> float:
    number = float(value)
    unit = (unit or "s").lower()
    if unit.startswith("m") or unit == "นาที":
        return number * 60.0
    return number


def parse_brief(brief: str) -> BriefConstraints:
    """Read clip count, duration range, and topic presence from ``brief``."""

    text = brief.translate(_THAI_DIGITS)
    count: int | None = None
    count_match = _COUNT.search(text)
    if count_match:
        count = _number(count_match.group(1) or count_match.group(2) or "")

    min_seconds: float | None = None
    max_seconds: float | None = None
    range_match = _RANGE.search(text)
    if range_match:
        low, low_unit, high, high_unit = range_match.groups()
        min_seconds = _seconds(low, low_unit or high_unit)
        max_seconds = _seconds(high, high_unit)
    else:
        upper_match = _UPPER.search(text)
        if upper_match:
            min_seconds = MIN_CLIP_SECONDS
            max_seconds = _seconds(upper_match.group(1), upper_match.group(2))

    topic_present = False
    for match in _TOPIC.finditer(text):
        topic = (match.group(1) or match.group(2) or "").strip()
        if re.search(r"[^\W\d_]{2,}", topic):
            topic_present = True
            break

    if count is not None and (count < 1 or count > MAX_CLIPS):
        return BriefConstraints(
            count, min_seconds, max_seconds, topic_present, issue_code="clip_count_limit_exceeded"
        )
    if min_seconds is not None and max_seconds is not None:
        if min_seconds > max_seconds or max_seconds <= 0:
            return BriefConstraints(
                count, min_seconds, max_seconds, topic_present, issue_code="duration_range_invalid"
            )
        if min_seconds < MIN_CLIP_SECONDS or max_seconds > MAX_CLIP_SECONDS:
            return BriefConstraints(
                count, min_seconds, max_seconds, topic_present, issue_code="duration_limit_exceeded"
            )

    missing: list[str] = []
    if count is None:
        missing.append("clip_count")
    if min_seconds is None or max_seconds is None:
        missing.append("duration_range")
    if not topic_present:
        missing.append("topic")
    return BriefConstraints(count, min_seconds, max_seconds, topic_present, missing)


__all__ = [
    "MAX_CLIPS",
    "MAX_CLIP_SECONDS",
    "MIN_CLIP_SECONDS",
    "BriefConstraints",
    "parse_brief",
]
