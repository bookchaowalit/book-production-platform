"""Design-quality score: deterministic, bounded, and ranks clear hierarchy above flat, crammed, or scattered layouts."""

from __future__ import annotations

from typing import Any

from book_production_platform.graphics_ir import GraphicIR
from book_production_platform.graphics_production.aesthetics import WEIGHTS, score_ir


def ir(texts: list[dict[str, Any]], width: int = 1080, height: int = 1080) -> GraphicIR:
    nodes: list[dict[str, Any]] = [{"node_type": "shape", "node_id": "bg", "kind": "rect", "x": 0, "y": 0, "width": width, "height": height,
                                    "fill": "#F3F0EA"}]
    for index, text in enumerate(texts):
        nodes.append({"node_type": "text", "node_id": f"t{index}", "color": "#151515", "align": "left", **text})
    return GraphicIR.model_validate({"schema_version": "graphic-ir.v2", "request_id": "t", "template_ref": "t@1", "variant": "square", "canvas": {"width": width, "height": height}, "nodes": nodes})


CLEAR = [
    {"text": "Big winter sale", "x": 100, "y": 300, "width": 880, "height": 240, "font_size": 110, "font_weight": "bold"},
    {"text": "Up to 50% off everything", "x": 100, "y": 580, "width": 880, "height": 60, "font_size": 40},
    {"text": "Shop now", "x": 100, "y": 680, "width": 880, "height": 60, "font_size": 36},
]


def test_score_is_bounded_deterministic_and_explained() -> None:
    first, second = score_ir(ir(CLEAR)), score_ir(ir(CLEAR))
    assert 0 <= first.total <= 100 and first.total == second.total
    assert set(first.parts) == set(WEIGHTS) and all(0 <= value <= 1 for value in first.parts.values())
    assert first.to_dict()["notes"]["alignment"] == "1 text axes"


def test_clear_hierarchy_beats_equal_sizes() -> None:
    flat = [{**text, "font_size": 44, "font_weight": "normal"} for text in CLEAR]
    assert score_ir(ir(CLEAR)).parts["hierarchy"] > score_ir(ir(flat)).parts["hierarchy"]


def test_scattered_axes_and_edge_hugging_text_score_lower() -> None:
    scattered = [{**CLEAR[0]}, {**CLEAR[1], "x": 330, "width": 600}, {**CLEAR[2], "x": 4, "width": 300}]
    clean, messy = score_ir(ir(CLEAR)), score_ir(ir(scattered))
    assert messy.parts["alignment"] < clean.parts["alignment"] and messy.parts["margins"] < clean.parts["margins"]
    assert messy.total < clean.total


def test_a_long_empty_band_scores_lower_on_spread() -> None:
    top_heavy = [{**text, "y": text["y"] - 250} for text in CLEAR]
    story_empty = score_ir(ir(top_heavy, height=1920))
    spread = score_ir(ir([{**text, "y": text["y"] + 400 * index} for index, text in enumerate(CLEAR)], height=1920))
    assert story_empty.parts["spread"] < spread.parts["spread"]
    assert "longest empty band" in story_empty.notes["spread"]
