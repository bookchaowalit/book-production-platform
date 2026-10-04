"""Shared skip marker for graphics tests that rasterize PNG previews.

PNG rendering needs a local Chromium-family browser or the optional
``resvg-py`` package (``pip install .[preview]``).
"""

from __future__ import annotations

import pytest

from book_production_platform.graphics_production.render import RenderError, rasterizer


def _rasterizer_available() -> bool:
    try:
        rasterizer()
    except RenderError:
        return False
    return True


requires_browser = pytest.mark.skipif(
    not _rasterizer_available(),
    reason=(
        "a local PNG rasterizer is required: install Edge/Chrome/Chromium (or set GRAPHICS_PRODUCTION_BROWSER) "
        "or the optional resvg-py package"
    ),
)
