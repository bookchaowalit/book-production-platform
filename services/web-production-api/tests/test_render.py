from __future__ import annotations

from web_ir import compile_page
from web_production.qa import run_page_qa
from web_production.render import render_css, render_html


def _page() -> dict:
    return {
        "page_id": "render-01",
        "slug": "render-page",
        "title": "Render page",
        "description": "A test page",
        "hero_heading": "Hello <world>",
        "hero_body": "Text & details",
        "hero_cta": {"label": "Start", "href": "#signup"},
        "features": [{"title": "One", "body": "First"}],
    }


def test_render_html_escapes_text_and_contains_accessibility_metadata() -> None:
    ir = compile_page(_page())

    html = render_html(ir, "render-page.css")

    assert "Hello &lt;world&gt;" in html
    assert "Text &amp; details" in html
    assert "<script" not in html.lower()
    assert '<meta name="viewport"' in html
    assert '<main id="content">' in html
    assert html.count("<h1>") == 1
    assert 'href="render-page.css"' in html


def test_render_css_has_responsive_layout_and_keyboard_focus() -> None:
    ir = compile_page(_page())

    css = render_css(ir)

    assert "@media" in css
    assert ":focus-visible" in css
    assert "--primary: #2563EB" in css


def test_english_page_uses_english_labels_and_text_urls_are_not_external_refs() -> None:
    page = _page()
    page["language"] = "en"
    page["hero_body"] = "Read more at https://example.com"
    ir = compile_page(page)
    html = render_html(ir, "render-page.css")
    css = render_css(ir)

    assert '<html lang="en">' in html
    assert ">Features</h2>" in html
    assert "Ready to get started?" in html
    assert run_page_qa(html, css, "render-page.css") == []
