import pytest
from synthetic_fixtures import synthetic_artifact_payload

from video_postpipeline.contract import parse_transcript_artifact
from video_postpipeline.subtitles import (
    CUE_LINE_WIDTH,
    format_timestamp,
    render_srt,
    render_vtt,
    wrap_cue_text,
    write_subtitles,
)

THAI_CUE = "สวัสดีครับ นี่คือการทดสอบระบบถอดเสียง"
HOST_LINE_1 = "Host: Hello from the synthetic transcript"
LONG_LINE_1 = "x" * CUE_LINE_WIDTH
LONG_LINE_2 = "x" * 8


def _artifact():
    return parse_transcript_artifact(synthetic_artifact_payload())


def test_srt_golden_output_is_deterministic():
    expected = (
        f"1\n00:00:00,000 --> 00:00:02,500\n{THAI_CUE}\n\n"
        f"2\n00:00:02,500 --> 00:00:06,000\n{HOST_LINE_1}\nfixture\n\n"
        f"3\n00:00:06,000 --> 00:00:09,500\n{LONG_LINE_1}\n{LONG_LINE_2}\n"
    )
    assert render_srt(_artifact()) == expected


def test_vtt_golden_output_is_deterministic():
    expected = (
        f"WEBVTT\n\n00:00:00.000 --> 00:00:02.500\n{THAI_CUE}\n\n"
        f"00:00:02.500 --> 00:00:06.000\n{HOST_LINE_1}\nfixture\n\n"
        f"00:00:06.000 --> 00:00:09.500\n{LONG_LINE_1}\n{LONG_LINE_2}\n"
    )
    assert render_vtt(_artifact()) == expected


def test_repeated_rendering_is_byte_identical():
    assert render_srt(_artifact()) == render_srt(_artifact())
    assert render_vtt(_artifact()) == render_vtt(_artifact())


@pytest.mark.parametrize(
    ("seconds", "dialect", "expected"),
    [
        (0.0, "srt", "00:00:00,000"),
        (3661.5, "srt", "01:01:01,500"),
        (3661.5, "vtt", "01:01:01.500"),
        (359999.9994, "srt", "99:59:59,999"),
        (360000.0, "vtt", "100:00:00.000"),
    ],
)
def test_timestamp_formatting_rules(seconds, dialect, expected):
    assert format_timestamp(seconds, dialect=dialect) == expected


def test_timestamp_rejects_negative_and_unknown_dialect():
    with pytest.raises(ValueError):
        format_timestamp(-0.001, dialect="srt")
    with pytest.raises(ValueError):
        format_timestamp(1.0, dialect="ass")


def test_wrap_rule_joins_words_within_width():
    assert wrap_cue_text("one two three", width=12) == ["one two", "three"]


def test_wrap_rule_hard_breaks_unbroken_runs():
    lines = wrap_cue_text("ก" * 50, width=42)
    assert lines == ["ก" * 42, "ก" * 8]


def test_wrap_rule_normalizes_existing_whitespace():
    assert wrap_cue_text("a\nb\t c", width=42) == ["a b c"]


def test_wrap_rule_keeps_empty_text_as_one_empty_line():
    assert wrap_cue_text("") == [""]


def test_write_subtitles_uses_transcript_id_filenames(tmp_path):
    written = write_subtitles(_artifact(), tmp_path, formats=("srt", "vtt"))
    assert [path.name for path in written] == ["demo-001.srt", "demo-001.vtt"]
    assert all(path.stat().st_size > 0 for path in written)


def test_write_subtitles_rejects_unknown_format(tmp_path):
    with pytest.raises(ValueError, match="unknown subtitle format"):
        write_subtitles(_artifact(), tmp_path, formats=("ass",))
