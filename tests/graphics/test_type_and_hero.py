"""Display typography (inline markup, giant bleed type, outline, blur), photo fade/glow, and the photo-led themes."""

from __future__ import annotations

import io
import re
from pathlib import Path
from typing import Any

import pytest
from PIL import Image

from book_production_platform.graphics_ir import (
    GroupNode,
    ImageNode,
    ResolvedImageEffects,
    TextNode,
    iter_nodes,
)
from book_production_platform.graphics_production import fonts, image_fx
from book_production_platform.graphics_production.image_fx import process_image
from book_production_platform.graphics_production.layout import (
    line_runs,
    parse_markup,
    wrap_text,
)
from book_production_platform.graphics_production.provider import TEMPLATE_IDS
from book_production_platform.graphics_production.render import render_svg
from book_production_platform.graphics_production.templates import (
    TemplateError,
    compile_template,
)
from book_production_platform.graphics_production.themes import VARIANT_SIZES

PHOTOS = Path(__file__).parent / "fixtures" / "photos"
STAR = "*"


@pytest.fixture(autouse=True)
def _plain_cutout(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(image_fx, "ai_cutout_available", lambda: False)


def request(theme: str, content: dict[str, str], variant: str = "portrait", **extra: Any) -> dict[str, Any]:
    width, height = VARIANT_SIZES[variant]
    return {"request_id": f"t-{theme}", "template_id": theme, "variant": variant, "canvas": {"width": width, "height": height},
            "brand": {"primary": "#9D2235", "accent": "#FFC72C"}, "content": content, **extra}


def photo_request(theme: str, content: dict[str, str], assets: dict[str, str], variant: str = "portrait") -> dict[str, Any]:
    return request(theme, content, variant, assets=assets, asset_root=str(PHOTOS))


# --- inline markup -------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "plain", "runs"),
    [
        (f"CRAFTING {STAR}DESIGNS{STAR} THAT", "CRAFTING DESIGNS THAT", [(9, 16, "accent")]),
        (f"{STAR}{STAR}LEADER{STAR}{STAR}HEALTH", "LEADERHEALTH", [(0, 6, "heavy")]),
        ("AR~DI~AN", "ARDIAN", [(2, 4, "outline")]),
        (f"ลด {STAR}50%{STAR} ทุกเมนู", "ลด 50% ทุกเมนู", [(3, 6, "accent")]),
        (f"ราคา 99.- {STAR}เฉพาะวันนี้", f"ราคา 99.- {STAR}เฉพาะวันนี้", []),
        (f"x {STAR} y {STAR} z", f"x {STAR} y {STAR} z", []),
        ("\\" + STAR + "note\\" + STAR, f"{STAR}note{STAR}", []),
        ("~5 km", "~5 km", []),
    ],
)
def test_markup_is_parsed_only_when_balanced(text: str, plain: str, runs: list[tuple[int, int, str]]) -> None:
    assert parse_markup(text) == (plain, runs)


def test_runs_follow_wrapped_lines() -> None:
    plain, runs = parse_markup(f"CRAFTING {STAR}DESIGNS{STAR} THAT CONNECT {STAR}PEOPLE{STAR}")
    lines = wrap_text(plain, 500, 60)
    pieces = line_runs(plain, lines, [(start, end, index) for index, (start, end, _kind) in enumerate(runs)])
    assert "".join(segment for line in pieces for segment, _ in line).replace(" ", "") == plain.replace(" ", "")
    assert ("DESIGNS", 0) in pieces[1] and ("PEOPLE", 1) in pieces[-1]


def test_accent_words_render_as_sibling_tspans() -> None:
    ir = compile_template(request("numbered_list", {"title": f"{STAR}Design{STAR} that works", "item1": "A", "item2": "B"}))
    svg = render_svg(ir)
    title = re.search(r'<text id="title".*?</text>', svg, re.DOTALL)
    assert title is not None and "<tspan><tspan" not in title.group(0)
    assert re.search(r'<tspan x="[^"]+" dy="0" fill="#EE5A24">DESIGN</tspan><tspan> THAT', title.group(0))
    assert STAR not in title.group(0)


def test_plain_copy_without_markers_is_unchanged() -> None:
    ir = compile_template(request("quote_card", {"quote": "Done is better than perfect.", "author": "Sample"}, "square"))
    assert ir.schema_version == "graphic-ir.v1"
    assert all(not node.runs for node in iter_nodes(list(ir.nodes)) if isinstance(node, TextNode))


# --- display type -------------------------------------------------------------------------------------


def test_big_type_spans_and_may_bleed() -> None:
    ir = compile_template(photo_request("editorial_hero", {"name": "AR~DI~AN", "tagline": "Hi"}, {"subject": "dog.png"}))
    names = [node for node in iter_nodes(list(ir.nodes)) if isinstance(node, TextNode) and node.node_id.startswith("name")]
    assert len(names) == 2 and all(node.bleed and node.max_lines == 1 for node in names)
    behind, front = names
    assert not behind.outline_only and front.outline_only and behind.runs[0].outline
    assert behind.width == pytest.approx(front.width) and behind.font_size > 150


def test_outline_and_motion_blur_reach_the_svg() -> None:
    ir = compile_template(photo_request("hero_poster", {"name": "KORGI", "headline": "MVP"}, {"subject": "dog.png"}))
    svg = render_svg(ir, asset_root=PHOTOS)
    assert re.search(r'<filter id="blur-name"[^>]*><feGaussianBlur stdDeviation="[0-9.]+ 0(\.0)?"', svg)
    assert 'filter="url(#blur-name)"' in svg
    ir2 = compile_template(photo_request("product_hero", {"word": "FRESH", "headline": "New"}, {"product": "dog.png"}))
    assert re.search(r'<text id="word"[^>]* fill="none"[^>]* stroke=', render_svg(ir2, asset_root=PHOTOS))


def test_measured_width_falls_back_without_font_files(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(fonts, "_font_dirs", list)
    assert fonts.text_width_em("HELLO", "builtin:display") is None
    ir = compile_template(photo_request("editorial_hero", {"name": "ARDIAN", "tagline": "Hi"}, {"subject": "dog.png"}))
    assert any(isinstance(node, TextNode) and node.bleed for node in iter_nodes(list(ir.nodes)))


# --- photo-led themes --------------------------------------------------------------------------------


def test_photo_themes_need_their_photo_and_are_not_offered_to_briefs() -> None:
    with pytest.raises(TemplateError, match="asset_required:subject"):
        compile_template(request("hero_poster", {"name": "A", "headline": "B"}))
    assert not {"hero_poster", "editorial_hero", "product_hero"} & set(TEMPLATE_IDS)
    assert {"brand_board", "numbered_list"} <= set(TEMPLATE_IDS)


def test_hero_poster_layers_sandwich_the_subject() -> None:
    ir = compile_template(photo_request("hero_poster", {"name": "KORGI", "headline": "MVP", "kicker": "Go!"}, {"subject": "dog.png"}))
    order = [group.node_id for group in ir.nodes if isinstance(group, GroupNode)]
    assert order.index("Ghost") < order.index("Name") < order.index("Subject") < order.index("Type")
    ghost = next(node for node in iter_nodes(list(ir.nodes)) if node.node_id == "ghost")
    assert isinstance(ghost, ImageNode) and ghost.effects is not None and ghost.effects.filter == "grayscale" and ghost.effects.fade == "bottom"
    kicker = next(node for node in iter_nodes(list(ir.nodes)) if node.node_id == "kicker")
    assert isinstance(kicker, TextNode) and kicker.rotation == -6 and kicker.font_ref == "builtin:script"


@pytest.mark.parametrize("variant", ["square", "portrait", "story", "landscape"])
def test_brand_board_lays_out_every_size(variant: str) -> None:
    ir = compile_template(photo_request("brand_board", {"brand": f"{STAR}{STAR}LEADER{STAR}{STAR}HEALTH", "tagline": "Guided"},
                                        {"photo1": "dog.png", "photo2": "shop.jpg"}, variant))
    width, height = VARIANT_SIZES[variant]
    cards = [node for node in iter_nodes(list(ir.nodes)) if node.node_id.endswith("-card")]
    assert len(cards) == 6 and all(card.x + card.width <= width + 1 and card.y + card.height <= height + 1 for card in cards)
    wordmark = next(node for node in iter_nodes(list(ir.nodes)) if node.node_id == "wordmark")
    assert isinstance(wordmark, TextNode) and wordmark.text == "LEADERHEALTH" and wordmark.runs[0].font_weight == "black"


def test_art_styles_restyle_photo_themes() -> None:
    ir = compile_template({**photo_request("hero_poster", {"name": "KORGI", "headline": "MVP"}, {"subject": "dog.png"}),
                           "design": {"art_style": "noir"}})
    assert ir.assumptions["art_style"] == "noir"


# --- photo effects -------------------------------------------------------------------------------------


def effects(**values: Any) -> ResolvedImageEffects:
    return ResolvedImageEffects.model_validate({"filter": "none", **values})


def alpha_at(data: bytes, x: int, y: int) -> int:
    return int(Image.open(io.BytesIO(data)).convert("RGBA").getpixel((x, y))[3])


def test_fade_ramps_alpha_toward_the_named_edge() -> None:
    data = process_image(PHOTOS / "shop.jpg", effects(fade="bottom", fade_amount=0.5), 200, 200)
    assert alpha_at(data, 100, 10) == 255 and alpha_at(data, 100, 198) < 20 and 60 < alpha_at(data, 100, 150) < 200
    edges = process_image(PHOTOS / "shop.jpg", effects(fade="edges", fade_amount=0.3), 200, 200)
    assert alpha_at(edges, 100, 100) == 255 and alpha_at(edges, 1, 100) < 20
    radial = process_image(PHOTOS / "shop.jpg", effects(fade="radial", fade_amount=0.5), 200, 200)
    assert alpha_at(radial, 100, 100) == 255 and alpha_at(radial, 2, 2) < 20


def test_glow_adds_a_colored_halo() -> None:
    bare = process_image(PHOTOS / "dog.png", effects(cutout="plain", fit="contain"), 240, 240)
    lit = process_image(PHOTOS / "dog.png", effects(cutout="plain", fit="contain", glow=0.8, glow_color="#00FF00"), 240, 240)
    halo = [pixel for pixel in Image.open(io.BytesIO(lit)).convert("RGBA").getdata() if pixel[3] > 30 and pixel[1] > 200 and pixel[0] < 80]
    assert halo and bare != lit
