from __future__ import annotations

import base64
import io
import re
from pathlib import Path
from typing import Any

import pytest
from PIL import Image, ImageDraw

from book_production_platform.graphics_ir import (
    GroupNode,
    ImageNode,
    ResolvedImageEffects,
    iter_nodes,
)
from book_production_platform.graphics_production import image_fx
from book_production_platform.graphics_production.art_styles import get_art_style
from book_production_platform.graphics_production.design_edit import (
    apply_change,
    merge_design,
)
from book_production_platform.graphics_production.image_fx import (
    ImageEffectError,
    process_image,
)
from book_production_platform.graphics_production.manual import build_figma_guide
from book_production_platform.graphics_production.render import render_svg
from book_production_platform.graphics_production.templates import (
    TemplateError,
    compile_template,
)
from book_production_platform.graphics_production.themes import VARIANT_SIZES
from book_production_platform.production_trace.guide import render_manual_markdown

CONTENT = {"headline": "ฉลองหน้าหนาว", "discount": "-20%", "detail": "เครื่องดื่มร้อนทุกเมนู", "cta": "แวะมาเลย"}


@pytest.fixture
def photos(tmp_path: Path) -> Path:
    """A red disc on a plain white studio backdrop, and a blue/green photo-like gradient."""

    product = Image.new("RGB", (400, 300), "white")
    ImageDraw.Draw(product).ellipse((120, 70, 280, 230), fill=(200, 30, 40))
    product.save(tmp_path / "product.png")
    scene = Image.new("RGB", (300, 300))
    for y in range(300):
        for x in range(300):
            scene.putpixel((x, y), (x * 255 // 299, 120, y * 255 // 299))
    scene.save(tmp_path / "scene.jpg")
    (tmp_path / "logo.svg").write_text('<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10"/>', encoding="utf-8")
    return tmp_path


def effects(**values: Any) -> ResolvedImageEffects:
    return ResolvedImageEffects.model_validate({"filter": "none", **values})


def png(data: bytes) -> Image.Image:
    return Image.open(io.BytesIO(data)).convert("RGBA")


def request(photos: Path, images: list[dict[str, Any]], art_style: str | None = None, variant: str = "square") -> dict[str, Any]:
    width, height = VARIANT_SIZES[variant]
    design: dict[str, Any] = {"images": images}
    if art_style:
        design["art_style"] = art_style
    return {
        "request_id": "photo-test", "template_id": "promo_sale", "variant": variant, "canvas": {"width": width, "height": height},
        "brand": {"primary": "#3B2A20", "accent": "#C9772E"}, "content": CONTENT,
        "assets": {"product": "product.png", "scene": "scene.jpg", "logo": "logo.svg"}, "asset_root": str(photos), "design": design,
    }


# --- pixels -----------------------------------------------------------------------------------------


def test_output_is_exactly_the_box(photos: Path) -> None:
    image = png(process_image(photos / "scene.jpg", effects(), 320, 180))
    assert image.size == (320, 180)


def test_plain_cutout_removes_the_studio_backdrop(photos: Path) -> None:
    image = png(process_image(photos / "product.png", effects(cutout="plain", fit="contain"), 200, 200))
    assert image.getpixel((2, 2))[3] == 0  # backdrop gone
    assert image.getpixel((100, 100))[3] == 255 and image.getpixel((100, 100))[0] > 150  # subject kept, trimmed to fill


def test_masks_shape_the_alpha(photos: Path) -> None:
    for mask in ("circle", "ellipse", "star", "hexagon", "blob", "arch", "torn", "rounded"):
        image = png(process_image(photos / "scene.jpg", effects(mask=mask), 200, 200))
        assert image.getpixel((1, 1))[3] < 40, mask
        assert image.getpixel((100, 110))[3] == 255, mask


def test_filters_change_the_pixels(photos: Path) -> None:
    base = png(process_image(photos / "scene.jpg", effects(), 120, 120)).getpixel((90, 30))
    gray = png(process_image(photos / "scene.jpg", effects(filter="grayscale"), 120, 120)).getpixel((90, 30))
    assert gray[0] == gray[1] == gray[2] and base[0] != base[2]
    duo = png(process_image(photos / "scene.jpg", effects(filter="duotone", duotone_dark="#000080", duotone_light="#FFFF00"), 120, 120))
    red, green, blue, _alpha = duo.getpixel((60, 60))
    assert abs(red - green) < 40 and blue < max(red, green) + 130
    for name in ("sepia", "posterize", "high_contrast", "blur", "invert", "vintage", "halftone"):
        assert png(process_image(photos / "scene.jpg", effects(filter=name), 120, 120)).size == (120, 120)


def test_outline_and_shadow_grow_the_visible_shape(photos: Path) -> None:
    bare = png(process_image(photos / "product.png", effects(cutout="plain", fit="contain"), 200, 200))
    sticker = png(process_image(photos / "product.png", effects(cutout="plain", fit="contain", outline_width=12,
                                                                 outline_color="#00FF00", shadow=0.8), 200, 200))
    covered = lambda image: sum(1 for pixel in image.getdata() if pixel[3] > 128)
    assert covered(sticker) > 0 and any(pixel[:3] == (0, 255, 0) for pixel in sticker.getdata() if pixel[3] == 255)
    assert bare.size == sticker.size


def test_vector_input_with_effects_is_refused(photos: Path) -> None:
    with pytest.raises(ImageEffectError, match="raster"):
        process_image(photos / "logo.svg", effects(), 100, 100)


def test_ai_cutout_falls_back_to_plain_when_rembg_is_missing(photos: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(image_fx, "ai_cutout_available", lambda: False)
    ir = compile_template(request(photos, [{"id": "p", "asset": "product", "z": "front", "x": 0.6, "y": 0.6, "width": 0.3, "height": 0.3,
                                            "effects": {"cutout": "ai"}}]))
    node = next(node for node in iter_nodes(list(ir.nodes)) if isinstance(node, ImageNode))
    assert node.effects is not None and node.effects.cutout == "plain"
    assert ir.assumptions["cutout"] == "ai_unavailable_used_plain"


@pytest.mark.skipif(not image_fx.ai_cutout_available(), reason="optional rembg (cutout extra) is not installed")
def test_ai_cutout_runs_when_installed(photos: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv(image_fx.CUTOUT_CACHE_ENV, str(tmp_path / "cutouts"))
    try:
        image = png(process_image(photos / "product.png", effects(cutout="ai", fit="contain"), 160, 160))
    except ImageEffectError as exc:
        pytest.skip(f"rembg model not available offline: {exc}")
    assert image.getpixel((1, 1))[3] < 40


def test_ai_cutouts_are_reused_from_disk_by_file_content(photos: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    calls: list[tuple[int, int]] = []

    def fake_cutout(image: Image.Image) -> Image.Image:
        calls.append(image.size)
        return image.convert("RGBA")

    monkeypatch.setattr(image_fx, "_ai_cutout", fake_cutout)
    monkeypatch.setenv(image_fx.CUTOUT_CACHE_ENV, str(tmp_path / "cutouts"))
    copy = tmp_path / "renamed.png"
    copy.write_bytes((photos / "product.png").read_bytes())
    with Image.open(photos / "product.png") as image:
        first = image_fx._cached_ai_cutout(photos / "product.png", image.convert("RGBA"))
        again = image_fx._cached_ai_cutout(copy, image.convert("RGBA"))  # same bytes under another name
    assert len(calls) == 1 and first.size == again.size and len(list((tmp_path / "cutouts").glob("*.png"))) == 1
    monkeypatch.setenv(image_fx.CUTOUT_CACHE_ENV, "off")
    with Image.open(photos / "product.png") as image:
        image_fx._cached_ai_cutout(photos / "product.png", image.convert("RGBA"))
    assert len(calls) == 2


# --- placement ------------------------------------------------------------------------------------


def test_placements_land_in_z_order_layers(photos: Path) -> None:
    ir = compile_template(request(photos, [
        {"id": "bg", "asset": "scene", "x": 0, "y": 0, "width": 1, "height": 1, "z": "background", "opacity": 0.4},
        {"id": "mid", "asset": "scene", "x": 0.1, "y": 0.6, "width": 0.3, "square": True},
        {"id": "top", "asset": "product", "x": 0.7, "y": 0.75, "width": 0.25, "height": 0.2, "z": "front", "rotation": -8},
    ], art_style="retro"))
    order = [group.node_id for group in ir.nodes if isinstance(group, GroupNode)]
    assert order[:3] == ["Background", "ArtBackdrop", "PhotosBack"]
    assert order.index("Photos") < order.index("Text") and order[-2:] == ["PhotosFront", "ArtOverlay"]
    mid = next(node for node in iter_nodes(list(ir.nodes)) if node.node_id == "photo-mid")
    assert isinstance(mid, ImageNode) and mid.width == mid.height == 324
    assert mid.effects is not None and mid.effects.filter == get_art_style("retro").photo["filter"]  # filter=style uses the pack


def test_svg_embeds_a_baked_png_with_rotation(photos: Path) -> None:
    ir = compile_template(request(photos, [{"id": "top", "asset": "product", "x": 0.6, "y": 0.7, "width": 0.3, "height": 0.25,
                                            "z": "front", "rotation": -8, "opacity": 0.9, "effects": {"cutout": "plain"}}]))
    svg = render_svg(ir, asset_root=photos)
    tag = re.search(r'<image id="photo-top"[^>]*>', svg)
    assert tag is not None and 'transform="rotate(-8' in tag.group(0) and 'opacity="0.9"' in tag.group(0)
    data = base64.b64decode(re.search(r'href="data:image/png;base64,([^"]+)"', tag.group(0)).group(1))  # type: ignore[union-attr]
    assert png(data).size == (324, 270)


def test_unknown_photo_asset_is_rejected(photos: Path) -> None:
    with pytest.raises(TemplateError, match="design_image_asset_unknown"):
        compile_template(request(photos, [{"id": "x", "asset": "missing"}]))


def test_duotone_from_role_colors(photos: Path) -> None:
    ir = compile_template(request(photos, [{"id": "p", "asset": "scene", "effects": {"filter": "duotone"}}], art_style="jazz"))
    node = next(node for node in iter_nodes(list(ir.nodes)) if isinstance(node, ImageNode))
    jazz = get_art_style("jazz")
    assert node.effects is not None and node.effects.duotone_dark == jazz.colors["bg"] and node.effects.duotone_light == jazz.colors["highlight"]


# --- editing --------------------------------------------------------------------------------------


def test_photo_change_ops_merge_add_update_remove() -> None:
    design = merge_design({}, {"images": {"a": {"asset": "scene", "x": 0.1}, "b": {"asset": "product"}}})
    design = merge_design(design, {"images": {"a": {"x": 0.3, "effects": {"mask": "circle"}}, "b": None}})
    assert design["images"] == [{"id": "a", "asset": "scene", "x": 0.3, "effects": {"mask": "circle"}}]


def test_front_photo_over_text_asks_first(photos: Path) -> None:
    base = request(photos, [])
    covering = apply_change(base, {"images": {"p": {"asset": "scene", "x": 0, "y": 0, "width": 1, "height": 0.5, "z": "front"}}})
    assert covering.status == "needs_input" and covering.issues[0]["code"] == "photo_covers_text"
    corner = apply_change(base, {"images": {"p": {"asset": "product", "x": 0.78, "y": 0.02, "width": 0.2, "height": 0.12, "z": "front",
                                                  "effects": {"cutout": "plain", "outline_width": 8}}}})
    assert corner.status == "complete", corner.issues
    assert corner.changed == ["image:p"]


def test_invalid_photo_fields_are_rejected(photos: Path) -> None:
    result = apply_change(request(photos, []), {"images": {"p": {"asset": "scene", "effects": {"mask": "octopus"}}}})
    assert result.status == "rejected"


def test_manual_guide_explains_photo_steps(photos: Path) -> None:
    ir = compile_template(request(photos, [{"id": "top", "asset": "product", "x": 0.7, "y": 0.75, "width": 0.25, "height": 0.2,
                                            "z": "front", "rotation": -8, "effects": {"cutout": "ai", "mask": "circle", "outline_width": 10,
                                                                                      "filter": "duotone"}}], art_style="kawaii"))
    text = render_manual_markdown(build_figma_guide([("photo-test", ir)], batch_id="b1"))
    for needle in ("Select Subject", "Ellipse Tool (hold Shift)", "Outline", "Gradient Map", "kawaii"):
        assert needle in text, needle


def test_cli_lists_styles_and_makes_a_styled_collage(photos: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    import json

    from book_production_platform.graphics_production.cli import main

    assert main(["styles"]) == 0
    listed = {style["style_id"] for style in json.loads(capsys.readouterr().out)}
    assert {"retro", "minimal", "jazz", "dystopia"} <= listed
    place = '{"id":"p","asset":"product","x":0.7,"y":0.02,"width":0.25,"height":0.18,"z":"front","effects":{"cutout":"plain"}}'
    code = main(["make", "--theme", "promo_sale", "--variant", "square", "--set", "headline=ลดราคา", "--set", "discount=-20%",
                 "--art-style", "noir", "--photo", f"product={photos / 'product.png'}", "--place", place,
                 "--output", str(tmp_path / "out"), "--idempotency-key", "cli-photo"])
    assert code == 0, capsys.readouterr().err
    saved = json.loads((tmp_path / "out" / "promo_sale-square.request.json").read_text(encoding="utf-8"))
    assert saved["design"]["art_style"] == "noir" and saved["assets"] == {"product": "product.png"}


@pytest.mark.parametrize(("shapes", "ok", "reason"), [
    ([(150, 100, 250, 200)], True, "ok"),
    ([(190, 140, 205, 155)], False, "subject_too_small"),
    ([(120, 0, 280, 180)], False, "subject_cut_by_frame"),
    ([(40, 90, 140, 200), (260, 90, 360, 200)], False, "several_subjects"),
    ([(150, 120, 250, 300)], True, "ok"),  # cropped at the bottom like a bust: fine
])
def test_cutout_suitability_flags_photos_that_do_not_cut_cleanly(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                                 shapes: list[tuple[int, int, int, int]], ok: bool, reason: str) -> None:
    def fake_ai(_source: Path, picture: Image.Image) -> Image.Image:
        cut = picture.convert("RGBA")
        cut.putalpha(picture.convert("L").point(lambda v: 0 if v > 240 else 255))  # the subject is everything not white
        return cut

    monkeypatch.setattr(image_fx, "ai_cutout_available", lambda: True)
    monkeypatch.setattr(image_fx, "_cached_ai_cutout", fake_ai)
    image = Image.new("RGB", (400, 300), "white")
    draw = ImageDraw.Draw(image)
    for box in shapes:
        draw.rectangle(box, fill=(180, 40, 40))
    path = tmp_path / f"{len(shapes)}-{shapes[0][1]}.png"
    image.save(path)
    result, why = image_fx.cutout_suitability(path)
    assert result is ok and why.startswith(reason)
