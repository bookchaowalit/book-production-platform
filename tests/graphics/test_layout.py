from __future__ import annotations

import pytest

from book_production_platform.graphics_production.layout import (
    visible_length,
    wrap_text,
)
from book_production_platform.graphics_production.templates import (
    VARIANT_SIZES,
    compile_template,
)

THAI_COMBINING = {chr(code) for code in (0x0E31, *range(0x0E34, 0x0E3B), *range(0x0E47, 0x0E4F))}
THAI_LEADING = {chr(code) for code in range(0x0E40, 0x0E45)}


def _chars(lines: tuple[str, ...]) -> str:
    return "".join("".join(line.split()) for line in lines)


def test_wraps_at_spaces_and_keeps_number_with_unit() -> None:
    text = "ลายใหม่มาแล้ว พิมพ์สดทุกตัว ส่งไวภายใน 3 วัน"
    lines = wrap_text(text, width=936, font_size=42)
    assert all(line == line.strip() for line in lines)
    assert _chars(lines) == "".join(text.split())
    assert any("3 วัน" in line for line in lines)


def test_long_thai_word_never_splits_marks_or_leading_vowels() -> None:
    text = "กิ่งไม้ที่เคลื่อนไหวตามแรงลมเย็นยามเช้าอันแสนสดชื่นเหลือเกิน" * 2
    lines = wrap_text(text, width=300, font_size=42)
    assert len(lines) > 1
    assert _chars(lines) == text
    for line in lines:
        assert line[0] not in THAI_COMBINING
        assert line[-1] not in THAI_LEADING
        assert visible_length(line) <= int(300 / (42 * 0.55))


def test_visible_length_ignores_combining_marks() -> None:
    assert visible_length("ที่") == 1
    assert visible_length("abc") == 3


def test_blank_lines_and_english_words_are_preserved() -> None:
    lines = wrap_text("Hello world\n\nsecond paragraph here", width=200, font_size=40)
    assert "" in lines
    assert _chars(lines) == "Helloworldsecondparagraphhere"


@pytest.mark.parametrize("variant", sorted(VARIANT_SIZES))
def test_announcement_cta_stays_inside_canvas(variant: str) -> None:
    width, height = VARIANT_SIZES[variant]
    ir = compile_template({
        "request_id": f"a-{variant}", "template_id": "announcement", "variant": variant,
        "canvas": {"width": width, "height": height},
        "brand": {"primary": "#14213D", "accent": "#FCA311"},
        "content": {"headline": "ลายใหม่มาแล้ว!", "body": "พิมพ์สดทุกตัว ส่งไวภายใน 3 วัน", "cta": "สั่งเลยวันนี้"},
    })
    nodes = {node.node_id: node for node in ir.nodes}
    for node_id in ("cta-bg", "cta", "body", "headline"):
        node = nodes[node_id]
        assert node.y + node.height <= height, node_id
    assert nodes["body"].y + nodes["body"].height <= nodes["cta-bg"].y
    assert nodes["headline"].y + nodes["headline"].height <= nodes["body"].y
