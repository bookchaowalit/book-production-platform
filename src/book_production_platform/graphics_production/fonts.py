"""Measure text with the real font files, so display type is sized to its true width.

The theme engine estimates line width from an average advance per font. That is
fine for body copy but off by 20-40% for capitals in wide or display faces, which
makes giant type spill or wrap. When the font files are installed (system font
folders or ``GRAPHICS_PRODUCTION_FONT_DIRS``) this module measures the actual
advance with Pillow: Latin with the font itself, Thai with the stack's Thai
fallback (Sarabun unless the font covers Thai). Without the files, callers keep
the estimate.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from PIL import ImageFont

from .art_styles import ART_FONTS

THAI_FAMILIES = ("Sarabun", "Kanit", "Prompt", "Mitr", "Chakra Petch", "Trirong", "Itim", "Sriracha", "Anuphan", "Noto Serif Thai")
_WEIGHT_STYLES = {
    "black": ("black", "heavy", "extrabold", "bold"),
    "bold": ("bold", "semibold", "black"),
    "normal": ("regular", "book", "medium", "normal"),
}
_MEASURE_SIZE = 100


def _font_dirs() -> list[Path]:
    extra = [part for part in os.environ.get("GRAPHICS_PRODUCTION_FONT_DIRS", "").split(os.pathsep) if part]
    candidates = [*extra, "/usr/share/fonts", "/usr/local/share/fonts", str(Path.home() / ".fonts"), str(Path.home() / ".local/share/fonts")]
    return [Path(path) for path in candidates if Path(path).is_dir()]


@lru_cache(maxsize=4)
def _index(dirs: tuple[str, ...]) -> dict[str, list[tuple[str, str]]]:
    """Family (lower case) -> [(style lower case, file path)] for every readable font file."""

    found: dict[str, list[tuple[str, str]]] = {}
    for root in dirs:
        for path in sorted(Path(root).rglob("*")):
            if path.suffix.lower() not in (".ttf", ".otf"):
                continue
            try:
                family, style = ImageFont.truetype(str(path), 12).getname()
            except (OSError, ValueError):
                continue
            found.setdefault(str(family).lower(), []).append((str(style or "regular").lower(), str(path)))
    return found


def _families(font_ref: str) -> list[str]:
    stack = ART_FONTS.get(font_ref, ("Arial, sans-serif", 0.55))[0]
    return [name.strip().strip("'\"") for name in stack.split(",")]


def _file(family: str, weight: str) -> str | None:
    entries = _index(tuple(str(path) for path in _font_dirs())).get(family.lower(), [])
    if not entries:
        return None
    for wanted in _WEIGHT_STYLES.get(weight, ("regular",)):
        for style, path in entries:
            if wanted in style.replace(" ", "") and "italic" not in style:
                return path
    return next((path for style, path in entries if "italic" not in style), entries[0][1])


@lru_cache(maxsize=256)
def _font(path: str) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(path, _MEASURE_SIZE)


def _is_thai(char: str) -> bool:
    return "฀" <= char <= "๿"


def text_width_em(text: str, font_ref: str, weight: str = "normal") -> float | None:
    """Advance width of ``text`` in em (font size 1), or ``None`` when the font files are not installed."""

    families = _families(font_ref)
    primary = next((_file(family, weight) for family in families if _file(family, weight)), None)
    if primary is None:
        return None
    thai_family = next((family for family in families if family in THAI_FAMILIES and _file(family, weight)), None)
    thai_path = _file(thai_family, weight) if thai_family else _file("Sarabun", weight)
    primary_is_thai = any(family in THAI_FAMILIES and _file(family, weight) == primary for family in families)
    total = 0.0
    run, run_thai = "", False
    for char in [*text, None]:
        is_thai = char is not None and _is_thai(char) and not primary_is_thai
        if char is not None and (not run or is_thai == run_thai):
            run += char
            run_thai = is_thai
            continue
        if run:
            path = thai_path if run_thai and thai_path else primary
            total += _font(path).getlength(run) / _MEASURE_SIZE
        run, run_thai = (char or ""), is_thai
    return total


__all__ = ["text_width_em"]
