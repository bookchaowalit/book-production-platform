"""Visual regression: render a fixed set of designs and compare them with approved reference images.

The rule-based checks prove a layout is *valid*; they cannot see that a code change made
it look different. These goldens can. Every built-in theme (square) and a few composed
layouts (with a real photo and a logo) are rendered at quarter size and compared pixel by
pixel with ``tests/graphics/golden/*.png``.

Rendering depends on the installed fonts, so the test only runs where the pinned font set
is configured (``GRAPHICS_PRODUCTION_FONT_DIRS``) and resvg is the rasterizer; elsewhere it
is skipped with the reason. After an intended visual change, look at the diff images, then
refresh the references with ``GRAPHICS_UPDATE_GOLDENS=1 pytest tests/graphics/test_golden.py``.
"""

from __future__ import annotations

import io
import os
from pathlib import Path
from typing import Any

import pytest
from PIL import Image, ImageChops

from book_production_platform.graphics_production import image_fx
from book_production_platform.graphics_production.composer import compose
from book_production_platform.graphics_production.layouts import (
    LAYOUT_DIR_ENV,
    save_layout,
)
from book_production_platform.graphics_production.render import rasterizer, render_png
from book_production_platform.graphics_production.templates import compile_template
from book_production_platform.graphics_production.themes import (
    VARIANT_SIZES,
    load_themes,
)

from .test_themes import SAMPLE, request

GOLDEN = Path(__file__).parent / "golden"
PHOTOS = Path(__file__).parent / "fixtures" / "photos"
MEAN_TOLERANCE = 2.5  # mean absolute difference per channel, 0-255
CHANGED_PIXELS = 0.02  # share of pixels allowed to differ by more than 48 (anti-aliasing, tiny shifts)
UPDATE = os.environ.get("GRAPHICS_UPDATE_GOLDENS") == "1"

pytestmark = pytest.mark.skipif(
    not os.environ.get("GRAPHICS_PRODUCTION_FONT_DIRS") or rasterizer() != "resvg",
    reason="goldens need the pinned fonts (GRAPHICS_PRODUCTION_FONT_DIRS) and the resvg rasterizer",
)

COMPOSED = {
    "compose-coffee": ({"kicker": "LOM YEN COFFEE", "headline": "ลาเต้ร้อน *แก้วละ 55*", "cta": "สั่งเลย", "footer": "lomyen.example"},
                       "real/coffee-red.jpg", ("#3B2A20", "#C9772E"), 11),
    "compose-dog": ({"kicker": "NOVA VET", "headline": "อาบน้ำตัดขน *น้องหมา*", "price": "-30%", "cta": "จองคิว"},
                    "real/dog.jpg", ("#0F4C5C", "#F2A541"), 11),
    "compose-portrait": ({"headline": "Your daily ritual", "cta": "Try it"}, "real/woman-cup.jpg", ("#2D3142", "#EF8354"), 11),
}


def _compare(name: str, png: bytes, tmp_path: Path) -> None:
    image = Image.open(io.BytesIO(png)).convert("RGB")
    image = image.resize((image.width // 4, image.height // 4), Image.Resampling.LANCZOS)
    reference = GOLDEN / f"{name}.png"
    if UPDATE or not reference.is_file():
        GOLDEN.mkdir(exist_ok=True)
        image.save(reference, optimize=True)
        if not UPDATE:
            pytest.fail(f"no reference for {name}; wrote one, review it and commit it")
        return
    expected = Image.open(reference).convert("RGB")
    assert expected.size == image.size, f"{name}: size {image.size} != reference {expected.size}"
    diff = ImageChops.difference(expected, image)
    pixels = list(diff.convert("L").get_flattened_data())
    mean = sum(pixels) / len(pixels)
    changed = sum(1 for value in pixels if int(value) > 48) / len(pixels)  # type: ignore[arg-type]
    if mean > MEAN_TOLERANCE or changed > CHANGED_PIXELS:
        out = tmp_path / f"{name}-diff.png"
        sheet = Image.new("RGB", (image.width * 3, image.height), "white")
        sheet.paste(expected, (0, 0))
        sheet.paste(image, (image.width, 0))
        sheet.paste(diff.point(lambda v: min(255, v * 4)), (image.width * 2, 0))
        sheet.save(out)
        pytest.fail(f"{name} changed visually (mean {mean:.2f}, {changed:.1%} of pixels); reference | now | diff: {out}")


@pytest.fixture(autouse=True)
def _isolated(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(image_fx, "ai_cutout_available", lambda: False)  # plain cut-outs: no model download, deterministic
    monkeypatch.setenv(LAYOUT_DIR_ENV, str(tmp_path / "layouts"))


@pytest.mark.parametrize("theme", sorted(SAMPLE))
def test_theme_looks_like_its_reference(theme: str, tmp_path: Path) -> None:
    variant = "square" if "square" in load_themes()[theme].variants else load_themes()[theme].variants[0]
    req = request(theme, variant)
    _compare(f"theme-{theme}-{variant}", render_png(compile_template(req), asset_root=req.get("asset_root")), tmp_path)


@pytest.mark.parametrize("name", sorted(COMPOSED))
def test_composed_layout_looks_like_its_reference(name: str, tmp_path: Path) -> None:
    content, photo, (primary, accent), seed = COMPOSED[name]
    base: dict[str, Any] = {"request_id": name, "brand": {"primary": primary, "accent": accent}, "content": content,
                            "assets": {"hero": photo, "logo": "logo.png"}, "asset_root": str(PHOTOS)}
    result = compose(base, ["square", "story"], count=1, seed=seed, avoid_history=False)
    assert result.specs, result.rejected
    save_layout(result.specs[0], result.fingerprints[0])
    for variant in ("square", "story"):
        width, height = VARIANT_SIZES[variant]
        req = {**base, "request_id": f"{name}-{variant}", "template_id": result.specs[0]["layout_id"], "variant": variant,
               "canvas": {"width": width, "height": height}}
        _compare(f"{name}-{variant}", render_png(compile_template(req), asset_root=str(PHOTOS)), tmp_path)
