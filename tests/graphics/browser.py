"""Shared skip marker for graphics tests that rasterize through a local browser."""

from __future__ import annotations

import pytest

from book_production_platform.graphics_production.render import _find_browser

requires_browser = pytest.mark.skipif(
    _find_browser() is None,
    reason=(
        "a local Chromium-family browser is required for PNG rendering; "
        "install Edge/Chrome/Chromium or set GRAPHICS_PRODUCTION_BROWSER"
    ),
)
