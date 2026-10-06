"""Labels sit optically centred on their buttons and boxes; Thai words are not cut to fit a narrow box."""

from __future__ import annotations

from typing import Any

import pytest

from book_production_platform.graphics_ir import ShapeNode, TextNode
from book_production_platform.graphics_production import box_text
from book_production_platform.graphics_production.box_text import center_text_on_boxes
from book_production_platform.graphics_production.layout import fit_text, longest_word


def shape(node_id: str, x: float, y: float, w: float, h: float) -> ShapeNode:
    return ShapeNode(node_id=node_id, kind="rect", x=x, y=y, width=w, height=h, fill="#D7000F")


def text(node_id: str, value: str, x: float, y: float, w: float, h: float, size: float = 32, align: Any = "center") -> TextNode:
    return TextNode(node_id=node_id, text=value, x=x, y=y, width=w, height=h, font_size=size, align=align, font_ref="builtin:thai")


@pytest.fixture(autouse=True)
def _known_ink(monkeypatch: pytest.MonkeyPatch) -> None:
    # Ink 0.3-1.0 em below the line top, as for digits in many faces; real fonts are measured the same way.
    monkeypatch.setattr(box_text, "ink_extent_em", lambda *_args: (0.3, 1.0))


def test_label_is_centred_on_its_button_by_ink() -> None:
    nodes = [shape("cta-bg", 100, 700, 240, 80), text("cta", "Order", 90, 705, 240, 50)]
    moved = center_text_on_boxes(nodes, 1080, 1080)[1]
    ink_top, ink_bottom = moved.y + 0.3 * 32, moved.y + 1.0 * 32
    assert (ink_top + ink_bottom) / 2 == pytest.approx(740, abs=0.01)  # button centre
    assert moved.x + moved.width / 2 == pytest.approx(220, abs=0.01)  # centred text follows the button horizontally


def test_left_aligned_label_keeps_its_x_and_its_echo_moves_with_it() -> None:
    nodes = [shape("tag", 50, 50, 300, 60), text("t", "NEW", 60, 52, 280, 40, align="left"),
             text("t-shadow", "NEW", 63, 55, 280, 40, align="left")]
    out = center_text_on_boxes(nodes, 1080, 1080)
    assert out[1].x == 60 and out[2].y - out[1].y == pytest.approx(3)


def test_panels_holding_a_stack_and_text_off_shapes_are_left_alone() -> None:
    panel = [shape("panel", 40, 40, 400, 200), text("a", "Headline", 60, 60, 360, 50), text("b", "Body copy", 60, 130, 360, 40)]
    assert center_text_on_boxes(panel, 1080, 1080) == panel
    loose = [shape("dot", 900, 900, 40, 40), text("a", "Hello", 60, 60, 300, 50)]
    assert center_text_on_boxes(loose, 1080, 1080) == loose
    big = [shape("card", 0, 0, 900, 900), text("a", "Hello", 60, 60, 300, 50)]
    assert center_text_on_boxes(big, 1080, 1080) == big  # a big card is a layout region, not a button


def test_thai_words_stay_whole_when_a_smaller_size_fits() -> None:
    pytest.importorskip("pythainlp")
    assert longest_word("ฉลองหน้าหนาว ลด 20%") < len("ฉลองหน้าหนาว")
    layout = fit_text("ฉลองหน้าหนาว ลด 20%", width=200, height=600, font_size=60, min_font_size=20, max_lines=6)
    assert "หน้าหนาว" in layout.lines and not any(line.endswith("หน้าหนา") for line in layout.lines)
