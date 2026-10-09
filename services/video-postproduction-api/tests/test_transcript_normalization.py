from __future__ import annotations

import pytest

from video_postproduction.transcription.models import TranscriptValidationError
from video_postproduction.transcription.normalize import normalize_provider_result


def test_normalizer_rejects_invalid_ranges():
    with pytest.raises(TranscriptValidationError):
        normalize_provider_result(
            {"segments": [{"start": 3, "end": 2, "text": "bad"}]},
            duration=10,
        )


def test_provider_preserves_missing_word_timestamps():
    result = normalize_provider_result(
        {"segments": [{"start": 0, "end": 2, "text": "hello"}]},
        duration=2,
    )

    assert result.segments[0].words == []


def test_normalizer_assigns_stable_segment_ids_and_orders_words():
    result = normalize_provider_result(
        {
            "segments": [
                {
                    "start": 5,
                    "end": 7,
                    "text": "second",
                    "words": [
                        {"start": 6.5, "end": 7, "word": "two"},
                        {"start": 5, "end": 5.5, "word": "one"},
                    ],
                },
                {"start": 1, "end": 3, "text": "first", "words": []},
            ]
        },
        duration=10,
    )

    assert [segment.id for segment in result.segments] == ["segment-0001", "segment-0002"]
    assert result.segments[0].text == "first"
    assert result.segments[1].words[0].start <= result.segments[1].words[1].start


def test_normalizer_merges_zero_length_thai_subword_tokens_into_neighbours():
    # faster-whisper splits Thai into sub-character tokens (vowels, tone marks)
    # that carry start == end; the text must survive, attached to a neighbour.
    result = normalize_provider_result(
        {
            "segments": [
                {
                    "start": 5.0,
                    "end": 9.0,
                    "text": "ก่อนเล็ก",
                    "words": [
                        {"start": 5.0, "end": 5.0, "word": "ก"},
                        {"start": 5.0, "end": 5.4, "word": "่อน"},
                        {"start": 5.4, "end": 6.0, "word": "เล"},
                        {"start": 6.0, "end": 6.0, "word": "็"},
                        {"start": 6.0, "end": 6.0, "word": "ก"},
                    ],
                }
            ]
        },
        duration=10,
    )

    words = result.segments[0].words
    assert "".join(word.text for word in words) == "ก่อนเล็ก"
    assert [word.text for word in words] == ["ก่อน", "เล็ก"]
    assert all(word.start < word.end for word in words)


def test_normalizer_keeps_segment_when_every_word_is_zero_length():
    result = normalize_provider_result(
        {"segments": [{"start": 1, "end": 2, "text": "่", "words": [{"start": 1.5, "end": 1.5, "word": "่"}]}]},
        duration=5,
    )

    assert result.segments[0].text == "่"
    assert result.segments[0].words == []


def test_normalizer_clamps_words_that_drift_outside_their_segment():
    # faster-whisper medium emits Thai word timings that overshoot the segment edges.
    result = normalize_provider_result(
        {
            "segments": [
                {
                    "start": 2.0,
                    "end": 4.0,
                    "text": "ครับผม",
                    "words": [
                        {"start": 1.2, "end": 2.6, "word": "ครับ"},
                        {"start": 3.1, "end": 4.9, "word": "ผม"},
                        {"start": 4.4, "end": 4.8, "word": "นะ"},
                    ],
                }
            ]
        },
        duration=10,
    )

    words = result.segments[0].words
    assert [(word.text, word.start, word.end) for word in words] == [("ครับ", 2.0, 2.6), ("ผมนะ", 3.1, 4.0)]


def test_normalizer_still_rejects_reversed_word_ranges():
    with pytest.raises(TranscriptValidationError):
        normalize_provider_result(
            {"segments": [{"start": 0, "end": 4, "text": "x", "words": [{"start": 3, "end": 2, "word": "x"}]}]},
            duration=5,
        )


def test_normalizer_clamps_small_duration_overshoot():
    result = normalize_provider_result(
        {"segments": [{"start": 0, "end": 2.00001, "text": "hello"}]},
        duration=2,
    )

    assert result.segments[0].end == 2


def test_normalizer_rejects_material_duration_overshoot():
    with pytest.raises(TranscriptValidationError, match="duration"):
        normalize_provider_result(
            {"segments": [{"start": 0, "end": 3, "text": "hello"}]},
            duration=2,
        )
