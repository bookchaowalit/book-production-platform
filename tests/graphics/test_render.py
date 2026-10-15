from io import BytesIO
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

import pytest
from PIL import Image

from book_production_platform.graphics_production import render as render_module
from book_production_platform.graphics_production.render import (
    RenderError,
    render_png,
    render_svg,
)
from book_production_platform.graphics_production.templates import compile_template
from tests.graphics.browser import requires_browser
from tests.graphics.test_templates import request_for


def test_render_svg_is_editable_and_escaped() -> None:
    ir = compile_template(request_for("quote_card", content={"quote": "พูดว่า <ชัดเจน>", "author": "ทีม & เพื่อน"}))

    svg = render_svg(ir)
    root = ElementTree.fromstring(svg)

    assert root.tag.endswith("svg")
    assert root.attrib["width"] == "1080"
    assert "&lt;ชัดเจน&gt;" in svg
    assert "ทีม &amp; เพื่อน" in svg
    assert 'dominant-baseline="text-before-edge"' in svg
    assert "https://" not in svg


@requires_browser
def test_render_png_has_canvas_dimensions() -> None:
    ir = compile_template(request_for("announcement", variant="portrait"))

    png = render_png(ir)
    with Image.open(BytesIO(png)) as image:
        assert image.format == "PNG"
        assert image.size == (1080, 1350)


def test_render_png_fails_clearly_when_no_shaping_browser_is_available(monkeypatch: pytest.MonkeyPatch) -> None:
    ir = compile_template(request_for("announcement"))
    monkeypatch.setattr(render_module, "_find_browser", lambda: None)
    monkeypatch.setattr(render_module, "_resvg_available", lambda: False)

    with pytest.raises(RenderError, match="browser_not_found"):
        render_png(ir)


def test_render_png_rejects_cleanly_when_no_browser_profile_can_be_made(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    ir = compile_template(request_for("announcement"))
    monkeypatch.setattr(render_module, "rasterizer", lambda: "browser")
    monkeypatch.setattr(render_module, "_find_browser", lambda: "chrome")
    monkeypatch.setenv("GRAPHICS_PRODUCTION_BROWSER_PROFILE", str(tmp_path / "profiles"))

    def unwritable(**_kwargs: object) -> str:
        raise PermissionError("profile folder is not writable")

    monkeypatch.setattr(render_module.tempfile, "mkdtemp", unwritable)

    with pytest.raises(RenderError, match="browser_profile_unavailable"):
        render_png(ir)


def test_each_browser_render_gets_its_own_profile_and_removes_it(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    ir = compile_template(request_for("announcement"))
    monkeypatch.setattr(render_module, "rasterizer", lambda: "browser")
    monkeypatch.setattr(render_module, "_find_browser", lambda: "chrome")
    monkeypatch.setenv("GRAPHICS_PRODUCTION_BROWSER_PROFILE", str(tmp_path / "profiles"))
    profiles: list[Path] = []

    def fake_chrome(command: list[str], **_kwargs: Any) -> Any:
        argument = {part.split("=", 1)[0]: part.split("=", 1)[1] for part in command if part.startswith("--") and "=" in part}
        profiles.append(Path(argument["--user-data-dir"]))
        assert profiles[-1].is_dir() and profiles[-1].parent == tmp_path / "profiles"
        Image.new("RGB", (ir.canvas.width, ir.canvas.height)).save(argument["--screenshot"], format="PNG")
        return render_module.subprocess.CompletedProcess(command, 0, b"", b"")

    monkeypatch.setattr(render_module.subprocess, "run", fake_chrome)

    render_png(ir)
    render_png(ir)

    assert len(set(profiles)) == 2 and not any(path.exists() for path in profiles)


def test_render_svg_embeds_local_image_as_data() -> None:
    ir = compile_template(request_for("product_card", content={"title": "สินค้า", "price": "฿99"}))

    svg = render_svg(ir)

    assert 'xmlns="http://www.w3.org/2000/svg"' in svg
    assert '<image ' not in svg or "href=\"data:" in svg
