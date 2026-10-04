from __future__ import annotations

import json
from pathlib import Path
from xml.etree import ElementTree

import pytest

from book_production_platform.graphics_ir import (
    GraphicIR,
    GroupNode,
    ShapeNode,
    TextNode,
    iter_nodes,
)
from book_production_platform.graphics_production.jobs import run_batch
from book_production_platform.graphics_production.motifs import MOTIFS
from book_production_platform.graphics_production.qa import contrast_report
from book_production_platform.graphics_production.render import render_svg
from book_production_platform.graphics_production.templates import (
    TemplateError,
    compile_template,
)
from book_production_platform.graphics_production.themes import (
    VARIANT_SIZES,
    ThemeError,
    evaluate,
    load_themes,
    parse_theme,
)

from .browser import requires_browser

SAMPLE = {
    "synthwave": {"headline": "SOLO", "headline2": "EMPIRE", "subhead": "สร้างอาณาจักร · คนเดียว", "tag": "EST. 2026", "footer": "EXAMPLE.TEST"},
    "bold_editorial": {"kicker": "ISSUE 07", "headline": "One person. Whole empire.", "body": "ธุรกิจคนเดียว", "footer": "EXAMPLE.TEST"},
    "soft_gradient": {"headline": "ทำงานน้อยลง ได้ผลมากขึ้น", "body": "ให้ระบบช่วยงานซ้ำ ๆ", "cta": "เริ่มเลย"},
    "quote_card": {"quote": "Done is better than perfect.", "author": "Sample"},
    "product_card": {"title": "ร่มพับทดลอง", "price": "฿199"},
    "announcement": {"headline": "เปิดบ้าน", "body": "แวะชมสตูดิโอ", "cta": "จองที่นั่ง"},
}


def request(theme: str, variant: str, content: dict[str, str] | None = None, request_id: str = "theme-test") -> dict[str, object]:
    width, height = VARIANT_SIZES[variant]
    return {
        "request_id": request_id, "template_id": theme, "variant": variant, "canvas": {"width": width, "height": height},
        "brand": {"primary": "#14213D", "accent": "#FCA311"}, "content": content if content is not None else SAMPLE[theme],
    }


def test_every_registered_theme_has_sample_content_and_valid_structure() -> None:
    themes = load_themes()
    assert set(themes) == set(SAMPLE)
    for theme in themes.values():
        assert theme.evidence, theme.theme_id
        assert all(spec.get("labels") for spec in theme.slots.values()), theme.theme_id
        motifs = {item["motif"] for layer in theme.layers for item in layer["items"]}
        assert motifs <= set(MOTIFS), theme.theme_id


@pytest.mark.parametrize(("theme_id", "variant"), [(t, v) for t, theme in load_themes().items() for v in theme.variants])
def test_every_theme_compiles_and_renders_in_every_variant(theme_id: str, variant: str) -> None:
    ir = compile_template(request(theme_id, variant))
    svg = render_svg(ir)
    root = ElementTree.fromstring(svg)
    assert root.attrib["width"] == str(VARIANT_SIZES[variant][0])
    texts = [node.text for node in iter_nodes(list(ir.nodes)) if isinstance(node, TextNode)]
    assert texts and all(text in svg for text in texts)
    if ir.schema_version == "graphic-ir.v2":
        assert all(isinstance(node, GroupNode) for node in ir.nodes)
        # Layer ids become named layers in editors; paint ids never collide with them.
        ids = [element.attrib["id"] for element in root.iter() if "id" in element.attrib]
        assert len(ids) == len(set(ids))
    GraphicIR.model_validate(ir.model_dump())


def test_original_templates_stay_flat_v1() -> None:
    for theme_id in ("quote_card", "product_card", "announcement"):
        ir = compile_template(request(theme_id, "square"))
        assert ir.schema_version == "graphic-ir.v1"
        assert ir.template_ref == f"{theme_id}@2"
        assert ir.assumptions["renderer"] == "graphics-svg-browser-v2"
        assert not ir.paints and not any(isinstance(node, GroupNode) for node in ir.nodes)


def test_compilation_is_deterministic_and_seeded_by_request() -> None:
    first = compile_template(request("synthwave", "portrait"))
    again = compile_template(request("synthwave", "portrait"))
    other = compile_template(request("synthwave", "portrait", request_id="another-job"))
    assert first.model_dump() == again.model_dump()
    stars = lambda ir: [n.d for n in iter_nodes(list(ir.nodes)) if isinstance(n, ShapeNode) and n.node_id.startswith("stars")]
    assert stars(first) != stars(other)


def test_optional_slots_are_left_out_and_required_slots_are_enforced() -> None:
    ir = compile_template(request("synthwave", "square", {"headline": "SOLO"}))
    ids = {node.node_id for node in iter_nodes(list(ir.nodes))}
    assert "headline" in ids and not {"headline2", "subhead", "tag", "tag-pill", "footer"} & ids
    with pytest.raises(TemplateError, match="content_required:headline"):
        compile_template(request("synthwave", "square", {"subhead": "x"}))


def test_original_template_rejects_a_variant_it_does_not_offer() -> None:
    with pytest.raises(TemplateError, match="variant_unsupported"):
        compile_template(request("quote_card", "landscape"))


def test_unknown_template_is_rejected() -> None:
    with pytest.raises(TemplateError, match="template_unknown"):
        compile_template(request("no_such_theme", "square", {"headline": "x"}))


def test_gradient_text_is_left_out_of_the_contrast_report() -> None:
    synthwave = {row["node_id"] for row in contrast_report(compile_template(request("synthwave", "portrait")))}
    assert not {"headline", "subhead"} & synthwave  # gradient text or gradient backdrop: check the PNG instead
    editorial = {row["node_id"]: row for row in contrast_report(compile_template(request("bold_editorial", "portrait")))}
    assert editorial["headline"]["backdrop_node"] == "paper" and editorial["headline"]["passes_aa"]


@pytest.mark.parametrize(
    "expression",
    ["__import__('os')", "W.real", "(lambda: 1)()", "W ** 99", "[1][0]", "open", "x if 1 else 2", "1 / 0"],
)
def test_layout_expressions_allow_only_arithmetic(expression: str) -> None:
    with pytest.raises(ThemeError):
        evaluate(expression, {"W": 100.0})


def test_layout_expressions_compute() -> None:
    assert evaluate("min(W, H) * 0.5 + max(1, 2) - abs(-3)", {"W": 100.0, "H": 50.0}) == 24.0


def test_theme_files_are_validated() -> None:
    good = json.loads((Path(__file__).parents[2] / "src/book_production_platform/graphics_production/themes/soft_gradient.json").read_text(encoding="utf-8"))
    parse_theme(good)
    for broken, code in (
        ({**good, "schema_version": "x"}, "theme_schema_invalid"),
        ({**good, "variants": ["poster"]}, "theme_variants_invalid"),
        ({**good, "surprise": 1}, "theme_unknown_keys"),
        ({**good, "layers": []}, "theme_layers_invalid"),
    ):
        with pytest.raises(ThemeError, match=code):
            parse_theme(broken)


def test_svg_path_data_cannot_carry_markup() -> None:
    with pytest.raises(ValueError):
        ShapeNode(node_id="p", kind="path", d='M0 0 L1 1"/><script>', x=0, y=0, width=1, height=1)


@requires_browser
def test_themed_batch_runs_end_to_end(tmp_path: Path) -> None:
    items = [request("synthwave", "landscape", request_id="sw-1"), request("bold_editorial", "portrait", request_id="ed-1")]
    result = run_batch(items, tmp_path, "themes-1")
    assert result.status == "accepted", result.to_dict()


def test_cli_lists_themes(capsys: pytest.CaptureFixture[str]) -> None:
    from book_production_platform.graphics_production.cli import main

    assert main(["themes"]) == 0
    listed = {theme["theme_id"]: theme for theme in json.loads(capsys.readouterr().out)}
    assert "synthwave" in listed and "landscape" in listed["synthwave"]["variants"]


def test_cli_make_rejects_unknown_slots_and_sizes(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from book_production_platform.graphics_production.cli import main

    assert main(["make", "--theme", "quote_card", "--set", "nope=x", "--output", str(tmp_path), "--idempotency-key", "k"]) == 2
    assert main(["make", "--theme", "quote_card", "--variant", "landscape", "--set", "quote=q", "--set", "author=a",
                 "--output", str(tmp_path), "--idempotency-key", "k2"]) == 2
    assert "does not come in landscape" in capsys.readouterr().err


@requires_browser
def test_cli_make_renders_every_size(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from book_production_platform.graphics_production.cli import main

    code = main(["make", "--theme", "synthwave", "--set", "headline=SOLO", "--set", "subhead=คนเดียว",
                 "--output", str(tmp_path), "--idempotency-key", "make-1"])
    summary = json.loads(capsys.readouterr().out)
    assert code == 0, summary
    assert len(list(tmp_path.rglob("*.svg"))) == 4
