from __future__ import annotations

import pytest

from video_postproduction.captions import ass as ass_module
from video_postproduction.captions.ass import render_ass
from video_postproduction.captions.srt import render_srt
from video_postproduction.transcription.models import NormalizedTranscript, TranscriptSegment, Word


def sample_transcript() -> NormalizedTranscript:
    return NormalizedTranscript(
        provider="test",
        model="deterministic",
        language="th",
        duration=20,
        segments=[
            TranscriptSegment(
                id="segment-0001",
                start=10.25,
                end=14.72,
                text="hello {world}",
                words=[Word(text="hello", start=10.25, end=11.2)],
            )
        ],
    )


def test_srt_uses_word_or_segment_boundaries_without_invalid_timecodes():
    srt = render_srt(sample_transcript())

    assert "00:00:10,250 --> 00:00:14,720" in srt
    assert "[object Object]" not in srt


def test_ass_escapes_event_text_and_is_deterministic():
    ass = render_ass(sample_transcript(), preset="minimal")

    assert "Dialogue: 0,0:00:10.25,0:00:14.72,Default,,0,0,0,,hello \\{world\\}" in ass
    assert "Dialogue:" in ass
    assert "[object Object]" not in ass


_THAI_MARKS = {chr(c) for c in (0x0E31, *range(0x0E34, 0x0E3B), *range(0x0E47, 0x0E4F))}


def _dialogue_lines(ass: str) -> list[str]:
    event = next(line for line in ass.splitlines() if line.startswith("Dialogue:"))
    return event.split(",", 9)[9].split("\\N")


def _columns(text: str) -> int:
    return sum(1 for ch in text if ch not in _THAI_MARKS)


def thai_transcript(text: str, words: list[str] | None = None) -> NormalizedTranscript:
    timed = [Word(text=w, start=1 + i * 0.1, end=1.05 + i * 0.1) for i, w in enumerate(words or [])]
    return NormalizedTranscript(
        provider="test",
        model="deterministic",
        language="th",
        duration=20,
        segments=[TranscriptSegment(id="segment-0001", start=1, end=9, text=text, words=timed)],
    )


def test_ass_uses_a_thai_capable_font_for_every_preset():
    for preset in ("minimal", "bold", "karaoke", "emphasis"):
        style = next(line for line in render_ass(thai_transcript("สวัสดี"), preset).splitlines() if line.startswith("Style:"))
        assert style.split(",")[1] == "Noto Sans Thai"


def test_ass_style_keeps_captions_readable_on_bright_footage():
    style = next(line for line in render_ass(thai_transcript("สวัสดี")).splitlines() if line.startswith("Style:"))
    fields = style.split(",")
    # Format: ... BackColour(6) ... BorderStyle(15), Outline(16), Shadow(17)
    assert fields[15] == "1"
    assert float(fields[16]) >= 4
    assert float(fields[17]) >= 1


def test_ass_wraps_long_thai_lines_without_losing_or_splitting_characters():
    text = "ส่วนเรื่องพวกคิดน์เชียวอะไรอย่างนี้ ก็ขอเขาได้เลยใช่มั้ย ว่าเราต้องไปสร้างเองเลยครับ แรกเขาต้องจัด"
    lines = _dialogue_lines(render_ass(thai_transcript(text)))

    assert len(lines) > 1
    assert all(_columns(line) <= 24 for line in lines)
    assert "".join(lines).replace(" ", "") == text.replace(" ", "")
    assert all(line and line[0] not in _THAI_MARKS for line in lines)


def test_ass_prefers_word_boundaries_inside_unspaced_thai_runs():
    words = ["ก็", "ตอนนี้", "ทำ", "หลังบ้าน", "อะไร", "แล้ว", "เรา", "ก็", "ส่ง", "งาน", "ให้", "เขา", "ตรวจ", "แล้ว"]
    text = "".join(words)
    lines = _dialogue_lines(render_ass(thai_transcript(text, words)))

    assert len(lines) > 1
    boundaries = set()
    cursor = 0
    for word in words:
        cursor += len(word)
        boundaries.add(cursor)
    position = 0
    for line in lines[:-1]:
        position += len(line)
        assert position in boundaries


def test_caption_times_are_rebased_to_the_rendered_clip():
    srt = render_srt(sample_transcript(), clip_start=10.0, clip_end=15.0)
    ass = render_ass(sample_transcript(), clip_start=10.0, clip_end=15.0)

    assert "00:00:00,250 --> 00:00:04,720" in srt
    assert "Dialogue: 0,0:00:00.25,0:00:04.72" in ass
    assert "00:00:10,250" not in srt
    assert "0:00:10.25" not in ass


def test_ass_canvas_matches_landscape_output_and_widens_lines():
    text = "ส่วนเรื่องพวกคิดน์เชียวอะไรอย่างนี้ ก็ขอเขาได้เลยใช่มั้ย ว่าเราต้องไปสร้างเองเลยครับ แรกเขาต้องจัด"
    ass = render_ass(thai_transcript(text), output_size=(1920, 1080))

    assert "PlayResX: 1920" in ass
    assert "PlayResY: 1080" in ass
    lines = _dialogue_lines(ass)
    assert all(_columns(line) <= 46 for line in lines)
    assert len(lines) < len(_dialogue_lines(render_ass(thai_transcript(text))))


@pytest.mark.parametrize(
    "tokens",
    [
        ["เรา", "ไป", "เรียน", "แล้ว", "แวะ", "โรงเรียน", "ใกล้", "บ้าน", "ได้", "เลย"],
        ["วันนี้", "มี", "ประชุม", "เพื่อ", "วางแผน", "งาน", "ร่วมกัน"],
        ["สวัสดี", "ครับ", "!", "วันนี้", "ประชุม", "เรื่อง", "งาน", "?", "จบ"],
        ["วันนี้", "ประชุม", "เรื่อง", "API", "และ", "Docker", "ครับ"],
    ],
)
def test_newmm_overrides_character_sized_whisper_boundaries(tokens):
    text = "".join(tokens)
    lines = ass_module.wrap_caption_text(text, list(text), limit=12)
    allowed = set()
    position = 0
    for token in tokens:
        position += len(token)
        allowed.add(position)
    position = 0
    assert "".join(lines) == text
    assert all(_columns(line) <= 12 for line in lines)
    for line in lines[:-1]:
        position += len(line)
        assert position in allowed
        assert line[-1] not in "เแโใไ"
    assert all(line[0] not in _THAI_MARKS for line in lines)


def test_caption_wrapping_preserves_spaces_and_overlong_mixed_language_tokens():
    text = "  วันนี้  Supercalifragilisticexpialidocious ประชุม\tเรื่องงาน  "
    lines = ass_module.wrap_caption_text(text, [], limit=12)
    assert "".join(lines) == text
    assert all(_columns(line) <= 12 for line in lines)
    assert all(not line or line[0] not in _THAI_MARKS for line in lines)


@pytest.mark.parametrize("text", ["เก่งขึ้น", "แป้ง", "โต๊ะ", "ใกล้", "ไม่ได้", "กุ้งน้ำจิ้ม"])
def test_hard_split_keeps_leading_vowels_and_combining_marks_attached(text):
    lines = ass_module._hard_split(text, 2)
    assert "".join(lines) == text
    assert all(line[0] not in _THAI_MARKS for line in lines)
    assert all(line[-1] not in "เแโใไ" for line in lines[:-1])


@pytest.mark.parametrize("failure", ["exception", "lossy"])
def test_tokenizer_failure_is_logged_and_falls_back_without_losing_text(
    monkeypatch, caplog, failure
):
    text = "วันนี้เราประชุมเพื่อวางแผนงานร่วมกัน"

    def broken_tokenizer(*args, **kwargs):
        if failure == "exception":
            raise ValueError("private input must not appear in logs")
        return ["วันนี้"]

    monkeypatch.setattr(ass_module, "word_tokenize", broken_tokenizer)
    lines = ass_module.wrap_caption_text(text, [], limit=12)
    assert "".join(lines) == text
    assert all(_columns(line) <= 12 for line in lines)
    assert "caption_tokenization_fallback" in caplog.text
    assert text not in caplog.text
    assert "private input" not in caplog.text
