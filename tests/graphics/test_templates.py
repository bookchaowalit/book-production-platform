from pathlib import Path

import pytest

from book_production_platform.graphics_ir import TextNode
from book_production_platform.graphics_production.render import render_svg
from book_production_platform.graphics_production.templates import (
    LayoutError,
    TemplateError,
    compile_template,
)


def request_for(template_id: str, *, variant: str = "square", content: dict[str, str] | None = None, **extra: object) -> dict[str, object]:
    values: dict[str, object] = {
        "request_id": f"{template_id}-001",
        "template_id": template_id,
        "variant": variant,
        "canvas": {"width": 1080, "height": 1080 if variant == "square" else 1350 if variant == "portrait" else 1920},
        "brand": {"primary": "#14213D", "accent": "#FCA311", "foreground": "#111111", "background": "#FFFFFF"},
        "content": content or {"headline": "ประกาศสำคัญ", "body": "รายละเอียดที่ชัดเจน", "cta": "ดูเพิ่มเติม"},
    }
    values.update(extra)
    return values


@pytest.mark.parametrize("template_id", ["quote_card", "product_card", "announcement"])
def test_compile_template_supports_the_three_v1_templates(template_id: str) -> None:
    request = request_for(template_id, content={"quote": "ทำงานให้ชัด", "author": "ทีมงาน", "title": "สินค้า", "price": "฿99", "headline": "ประกาศ", "body": "ข้อความ", "cta": "เริ่มต้น"})

    ir = compile_template(request)

    assert ir.template_ref == f"{template_id}@2"
    assert ir.canvas.width == 1080
    assert len(ir.nodes) >= 3
    assert ir.assumptions["model_calls"] == "0"


def test_compile_template_wraps_long_thai_text_without_truncating() -> None:
    long_text = "นี่คือข้อความภาษาไทยที่ยาวมากและต้องถูกจัดบรรทัดให้ครบทุกตัวอักษร " * 8

    ir = compile_template(request_for("quote_card", content={"quote": long_text, "author": "ผู้เขียน"}))
    svg = render_svg(ir)

    assert long_text in svg
    assert "overflow" not in svg


def test_compile_template_rejects_invalid_recipe_dimensions() -> None:
    request = request_for("announcement")
    request["canvas"] = {"width": 1200, "height": 1200}

    with pytest.raises(TemplateError, match="canvas_dimensions_unsupported"):
        compile_template(request)


def test_compile_template_rejects_missing_font(tmp_path: Path) -> None:
    with pytest.raises(TemplateError, match="missing_font"):
        compile_template(request_for("announcement", font_ref="fonts/brand.ttf", asset_root=str(tmp_path)))


def test_compile_template_carries_font_reference_to_every_text_node(tmp_path: Path) -> None:
    font = tmp_path / "brand.ttf"
    font.write_bytes(b"font fixture")

    ir = compile_template(request_for("announcement", font_ref="brand.ttf", asset_root=str(tmp_path)))

    text_nodes = [node for node in ir.nodes if isinstance(node, TextNode)]
    assert text_nodes
    assert {node.font_ref for node in text_nodes} == {"brand.ttf"}


def test_render_svg_embeds_custom_font_without_exposing_local_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    font = tmp_path / "brand.ttf"
    font.write_bytes(b"font fixture")

    class FixtureFont:
        pass

    monkeypatch.setattr("book_production_platform.graphics_production.render.ImageFont.truetype", lambda *_args, **_kwargs: FixtureFont())
    ir = compile_template(request_for("announcement", font_ref="brand.ttf", asset_root=str(tmp_path)))

    svg = render_svg(ir, asset_root=tmp_path)

    assert 'font-family="graphics-font-' in svg
    assert "data:font/ttf;base64,Zm9udCBmaXh0dXJl" in svg
    assert str(tmp_path) not in svg


def test_compile_template_rejects_unsafe_svg_asset(tmp_path: Path) -> None:
    unsafe = tmp_path / "unsafe.svg"
    unsafe.write_text('<svg><image href="https://example.com/a.png" /></svg>', encoding="utf-8")

    with pytest.raises(TemplateError, match="unsafe_svg"):
        compile_template(request_for("product_card", content={"title": "สินค้า", "price": "฿9"}, assets={"product_image": unsafe.name}, asset_root=str(tmp_path)))


def test_compile_template_accepts_safe_svg_asset(tmp_path: Path) -> None:
    safe = tmp_path / "safe.svg"
    safe.write_text('<svg xmlns="http://www.w3.org/2000/svg"><rect width="10" height="10" /></svg>', encoding="utf-8")

    ir = compile_template(request_for("product_card", content={"title": "สินค้า", "price": "฿9"}, assets={"product_image": safe.name}, asset_root=str(tmp_path)))

    assert any(node.node_id == "product-image" for node in ir.nodes)


def test_compile_template_raises_for_unfit_reject_policy() -> None:
    with pytest.raises(LayoutError, match="text_does_not_fit"):
        compile_template(request_for("announcement", content={"headline": "x" * 3000, "body": "body", "cta": "go"}))
