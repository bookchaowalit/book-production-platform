from book_production_platform.graphics_production.qa import run_graphic_qa
from book_production_platform.graphics_production.render import render_png, render_svg
from book_production_platform.graphics_production.templates import compile_template
from tests.graphics.test_templates import request_for


def test_graphic_qa_accepts_valid_svg_and_png() -> None:
    ir = compile_template(request_for("announcement"))

    issues = run_graphic_qa(render_svg(ir), render_png(ir), ir)

    assert issues == ()


def test_graphic_qa_rejects_external_reference() -> None:
    ir = compile_template(request_for("announcement"))
    svg = render_svg(ir).replace(
        ">",
        '><image href="https://evil.example/x.png" />',
        1,
    )

    issues = run_graphic_qa(svg, render_png(ir), ir)

    assert "external_reference_forbidden" in issues
