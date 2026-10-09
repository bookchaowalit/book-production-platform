"""Normalize heterogeneous provider payloads into transcript.v1."""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any

from .models import NormalizedTranscript, TranscriptSegment, TranscriptValidationError, Word

_OVERSHOOT_TOLERANCE = 0.05


def _number(value: Any, field_name: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise TranscriptValidationError(f"{field_name} must be a finite number") from exc
    if not math.isfinite(number):
        raise TranscriptValidationError(f"{field_name} must be a finite number")
    return number


def _bounded_range(start: Any, end: Any, duration: float, kind: str) -> tuple[float, float]:
    normalized_start = _number(start, f"{kind} start")
    normalized_end = _number(end, f"{kind} end")
    if normalized_start < 0:
        if normalized_start >= -_OVERSHOOT_TOLERANCE:
            normalized_start = 0.0
        else:
            raise TranscriptValidationError(f"{kind} start is outside the media duration")
    if normalized_end > duration:
        if normalized_end <= duration + _OVERSHOOT_TOLERANCE:
            normalized_end = duration
        else:
            raise TranscriptValidationError(f"{kind} end exceeds the media duration")
    if normalized_end < 0 or normalized_start > duration or normalized_start >= normalized_end:
        raise TranscriptValidationError(f"{kind} range is invalid")
    return normalized_start, normalized_end


def _optional_confidence(value: Any) -> float | None:
    if value is None or value == "":
        return None
    confidence = _number(value, "word confidence")
    if not 0 <= confidence <= 1:
        raise TranscriptValidationError("word confidence must be between 0 and 1")
    return confidence


def _word_text(raw_word: Mapping[str, Any]) -> str:
    return str(raw_word.get("word", raw_word.get("text", "")))


def _normalize_words(
    raw_words: Any,
    segment_start: float,
    segment_end: float,
    duration: float,
) -> list[Word]:
    if raw_words is None:
        return []
    if not isinstance(raw_words, list):
        raise TranscriptValidationError("words must be a list when provided")

    pending_prefix: list[str] = []
    timed: list[tuple[Mapping[str, Any], float, float]] = []
    for raw_word in raw_words:
        if not isinstance(raw_word, Mapping):
            raise TranscriptValidationError("word entries must be objects")
        if raw_word.get("start") is None or raw_word.get("end") is None:
            continue
        raw_start = _number(raw_word.get("start"), "word start")
        raw_end = _number(raw_word.get("end"), "word end")
        if raw_start != raw_end:
            raw_start, raw_end = _bounded_range(raw_start, raw_end, duration, "word")
            # Word timings drift past segment edges (notably faster-whisper medium on
            # Thai); segment boundaries win, so clamp the word to its segment.
            raw_start = max(raw_start, segment_start)
            raw_end = min(raw_end, segment_end)
        if raw_start >= raw_end:
            # Whisper splits Thai (and other scripts without spaces) into sub-character
            # tokens such as vowels and tone marks with zero duration, and clamping can
            # leave no time for a drifted word. Keep their text by attaching it to the
            # previous timed word, or to the next one if none.
            text = str(raw_word.get("word", raw_word.get("text", "")))
            if timed:
                previous, start, end = timed[-1]
                timed[-1] = ({**previous, "word": _word_text(previous) + text}, start, end)
            else:
                pending_prefix.append(text)
            continue
        if pending_prefix:
            raw_word = {**raw_word, "word": "".join(pending_prefix) + _word_text(raw_word)}
            pending_prefix.clear()
        timed.append((raw_word, raw_start, raw_end))

    normalized: list[tuple[float, int, Word]] = []
    for ordinal, (raw_word, start, end) in enumerate(timed):
        text = _word_text(raw_word)
        normalized.append(
            (
                start,
                ordinal,
                Word(
                    text=str(text),
                    start=start,
                    end=end,
                    confidence=_optional_confidence(
                        raw_word.get("probability", raw_word.get("confidence"))
                    ),
                ),
            )
        )
    normalized.sort(key=lambda item: (item[0], item[1]))
    return [word for _, _, word in normalized]


def normalize_provider_result(
    raw_result: Mapping[str, Any],
    duration: float,
    *,
    provider: str = "unknown",
    model: str = "unknown",
    language: str | None = None,
) -> NormalizedTranscript:
    """Validate, sort, and normalize a provider's segment payload."""

    if not isinstance(raw_result, Mapping):
        raise TranscriptValidationError("provider result must be an object")
    normalized_duration = _number(duration, "duration")
    if normalized_duration < 0:
        raise TranscriptValidationError("duration must not be negative")
    raw_segments = raw_result.get("segments", [])
    if not isinstance(raw_segments, list):
        raise TranscriptValidationError("segments must be a list")

    ordered_segments: list[tuple[float, float, int, TranscriptSegment]] = []
    for ordinal, raw_segment in enumerate(raw_segments):
        if not isinstance(raw_segment, Mapping):
            raise TranscriptValidationError("segment entries must be objects")
        start, end = _bounded_range(
            raw_segment.get("start"),
            raw_segment.get("end"),
            normalized_duration,
            "segment",
        )
        segment = TranscriptSegment(
            id="pending",
            start=start,
            end=end,
            text=str(raw_segment.get("text", "")),
            words=_normalize_words(raw_segment.get("words"), start, end, normalized_duration),
        )
        ordered_segments.append((start, end, ordinal, segment))

    ordered_segments.sort(key=lambda item: (item[0], item[1], item[2]))
    segments = [
        segment.model_copy(update={"id": f"segment-{index:04d}"})
        for index, (_, _, _, segment) in enumerate(ordered_segments, start=1)
    ]
    result_language = language or raw_result.get("language")
    result_model = str(raw_result.get("model") or model)
    return NormalizedTranscript(
        provider=provider,
        model=result_model,
        language=str(result_language) if result_language is not None else None,
        duration=normalized_duration,
        segments=segments,
    )
