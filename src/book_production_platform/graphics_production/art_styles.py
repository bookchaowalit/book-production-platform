"""Art style packs: re-skin any theme (retro, minimal, jazz, dystopia, ...) without touching its layout.

A theme decides *where* things go; an art style decides *how they look*:

- ``colors``: one color per role (``bg``, ``bg2``, ``surface``, ``ink``,
  ``accent``, ``accent2``, ``highlight``, ``on_accent``, ``muted``). Every theme
  maps its palette names to these roles (``roles`` in the theme file), so a
  style recolors every theme the same way. Text contrast is checked when the
  style is loaded (WCAG AA for body text), so no style — built-in or written
  by the AI style designer — can make text unreadable.
- ``fonts``: the face for display (big titles), body, and label (small caps,
  dates, prices) text, from :data:`ART_FONTS` (Thai-capable fallbacks included).
- ``text``: title treatment (weight, uppercase for Latin, letter spacing, an
  echo shadow).
- ``backdrop`` / ``overlay``: decoration and texture items from the shared
  motif library, drawn just above the background and over everything.
- ``hide_decor`` / ``decor_opacity``: tone down or drop the theme's own
  decoration layers (``"decor": true`` in the theme).
- ``photo``: the default photo treatment for placed images (duotone, grain,
  posterize, ...).

Styles are JSON (``graphic-art-style.v1``). Built-in packs live in
``art_styles/``; styles the AI designs from a description are saved to the
style library (``GRAPHICS_PRODUCTION_STYLE_DIR``) and are then used exactly
like built-in ones.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from book_production_platform.graphics_ir import ImageEffects

ART_STYLE_SCHEMA = "graphic-art-style.v1"
ART_STYLES_DIR = Path(__file__).with_name("art_styles")
STYLE_DIR_ENV = "GRAPHICS_PRODUCTION_STYLE_DIR"
ROLES = ("bg", "bg2", "surface", "ink", "accent", "accent2", "highlight", "on_accent", "muted")
FONT_ROLES = ("display", "body", "label")
# Font id -> (CSS font stack with Thai-capable fallbacks, average Latin advance in em).
ART_FONTS: dict[str, tuple[str, float]] = {
    # The first five stacks are unchanged from V1A so existing SVGs stay byte-identical.
    "builtin:sans": ("Arial, sans-serif", 0.55),
    "builtin:thai": ("Sarabun, 'Noto Sans Thai', 'Leelawadee UI', Tahoma, sans-serif", 0.55),
    "builtin:display": ("Anton, 'Bebas Neue', Impact, 'Arial Black', Sarabun, sans-serif", 0.5),
    "builtin:serif": ("'Playfair Display', Georgia, 'Times New Roman', serif", 0.58),
    "builtin:mono": ("'Space Mono', Menlo, Consolas, 'Courier New', monospace", 0.62),
    "builtin:kanit": ("Kanit, Sarabun, sans-serif", 0.58),
    "builtin:mitr": ("Mitr, Kanit, Sarabun, sans-serif", 0.6),
    "builtin:chakra": ("'Chakra Petch', Kanit, Sarabun, sans-serif", 0.58),
    "builtin:trirong": ("Trirong, 'Playfair Display', Georgia, serif", 0.58),
    "builtin:itim": ("Itim, Sriracha, Sarabun, sans-serif", 0.55),
    "builtin:sriracha": ("Sriracha, Itim, Sarabun, cursive", 0.55),
    "builtin:anton": ("Anton, Impact, Kanit, Sarabun, sans-serif", 0.5),
    "builtin:bungee": ("Bungee, 'Arial Black', Kanit, Sarabun, sans-serif", 0.78),
    "builtin:limelight": ("Limelight, 'Playfair Display', Trirong, serif", 0.72),
    "builtin:monoton": ("Monoton, Bungee, Kanit, Sarabun, sans-serif", 0.95),
    "builtin:pixel": ("'Press Start 2P', 'Courier New', 'Chakra Petch', Sarabun, monospace", 1.0),
    "builtin:typewriter": ("'Special Elite', 'Courier New', Trirong, Sarabun, monospace", 0.62),
    "builtin:bebas": ("'Bebas Neue', Anton, Kanit, Sarabun, sans-serif", 0.42),
    "builtin:archivo": ("'Archivo Black', 'Arial Black', Kanit, Sarabun, sans-serif", 0.72),
    "builtin:dm_serif": ("'DM Serif Display', Trirong, Georgia, serif", 0.55),
    "builtin:script": ("'Kaushan Script', Sriracha, cursive", 0.52),
    "builtin:marker": ("'Permanent Marker', Sriracha, Itim, cursive", 0.62),
    "builtin:poppins": ("Poppins, Prompt, Kanit, Sarabun, sans-serif", 0.6),
    "builtin:prompt": ("Prompt, Kanit, Sarabun, sans-serif", 0.6),
    "builtin:anuphan": ("Anuphan, Sarabun, sans-serif", 0.55),
    "builtin:serif_thai": ("'Noto Serif Thai', Trirong, Georgia, serif", 0.58),
}
MIN_TEXT_CONTRAST = 4.5
MIN_ACCENT_CONTRAST = 3.0
_HEX = re.compile(r"^#[0-9A-Fa-f]{6}$")
_STYLE_KEYS = frozenset({
    "schema_version", "style_id", "title", "description", "evidence", "colors", "fonts", "text", "hide_decor",
    "decor_opacity", "backdrop", "overlay", "photo", "source",
})
_TEXT_KEYS = frozenset({"display_weight", "display_case", "display_letter_spacing", "body_weight", "label_case", "display_shadow"})


class ArtStyleError(ValueError):
    """Raised when an art style file is malformed or would make text unreadable."""


@dataclass(frozen=True)
class ArtStyle:
    style_id: str
    title: str
    description: str
    evidence: tuple[str, ...]
    colors: dict[str, str]
    fonts: dict[str, str]
    text: dict[str, Any]
    hide_decor: bool
    decor_opacity: float
    backdrop: tuple[dict[str, Any], ...]
    overlay: tuple[dict[str, Any], ...]
    photo: dict[str, Any] = field(default_factory=dict)
    source: str = "builtin"

    def summary(self) -> dict[str, Any]:
        return {
            "style_id": self.style_id,
            "title": self.title,
            "description": self.description,
            "evidence": list(self.evidence),
            "source": self.source,
        }

    def to_json(self) -> dict[str, Any]:
        return {
            "schema_version": ART_STYLE_SCHEMA,
            "style_id": self.style_id,
            "title": self.title,
            "description": self.description,
            "evidence": list(self.evidence),
            "colors": dict(self.colors),
            "fonts": dict(self.fonts),
            "text": dict(self.text),
            "hide_decor": self.hide_decor,
            "decor_opacity": self.decor_opacity,
            "backdrop": [dict(item) for item in self.backdrop],
            "overlay": [dict(item) for item in self.overlay],
            "photo": dict(self.photo),
            "source": self.source,
        }


def _luminance(color: str) -> float:
    def channel(value: int) -> float:
        srgb = value / 255
        return srgb / 12.92 if srgb <= 0.04045 else ((srgb + 0.055) / 1.055) ** 2.4

    red, green, blue = (int(color[index:index + 2], 16) for index in (1, 3, 5))
    return 0.2126 * channel(red) + 0.7152 * channel(green) + 0.0722 * channel(blue)


def contrast(first: str, second: str) -> float:
    high, low = sorted((_luminance(first), _luminance(second)), reverse=True)
    return round((high + 0.05) / (low + 0.05), 2)


# (foreground role, background role, minimum ratio): text and accents every theme puts on these backgrounds.
CONTRAST_PAIRS = (
    ("ink", "bg", MIN_TEXT_CONTRAST),
    ("ink", "bg2", MIN_ACCENT_CONTRAST),
    ("ink", "surface", MIN_TEXT_CONTRAST),
    ("accent", "bg", MIN_ACCENT_CONTRAST),
    ("accent2", "bg", MIN_ACCENT_CONTRAST),
    ("on_accent", "accent", MIN_ACCENT_CONTRAST),
    ("bg", "ink", MIN_TEXT_CONTRAST),
)


def contrast_problems(colors: Mapping[str, str]) -> list[str]:
    """Role pairs whose contrast is below the minimum, as ``"fg/bg:ratio<min"``."""

    problems = []
    for foreground, background, minimum in CONTRAST_PAIRS:
        ratio = contrast(colors[foreground], colors[background])
        if ratio < minimum:
            problems.append(f"{foreground}/{background}:{ratio}<{minimum}")
    return problems


def _items(raw: Any, style_id: str, key: str) -> tuple[dict[str, Any], ...]:
    from .motifs import (
        DECOR_MOTIFS,  # local import: motifs import themes, which imports this module
    )

    if raw is None:
        return ()
    if not isinstance(raw, list) or len(raw) > 16:
        raise ArtStyleError(f"art_style_items_invalid:{style_id}:{key}")
    items = []
    ids: set[str] = set()
    for item in raw:
        if not isinstance(item, Mapping) or item.get("motif") not in DECOR_MOTIFS:
            raise ArtStyleError(f"art_style_motif_invalid:{style_id}:{key}")
        item_id = item.get("id")
        if not isinstance(item_id, str) or not re.fullmatch(r"[a-z][a-z0-9_-]{0,31}", item_id) or item_id in ids:
            raise ArtStyleError(f"art_style_item_id_invalid:{style_id}:{key}")
        ids.add(item_id)
        if len(json.dumps(item)) > 4000:
            raise ArtStyleError(f"art_style_item_too_large:{style_id}:{key}")
        items.append(dict(item))
    return tuple(items)


def parse_art_style(raw: Mapping[str, Any], *, source: str = "builtin") -> ArtStyle:
    """Validate an art style (structure, fonts, colors, contrast, decoration motifs)."""

    if not isinstance(raw, Mapping) or raw.get("schema_version") != ART_STYLE_SCHEMA:
        raise ArtStyleError("art_style_schema_invalid")
    unknown = set(raw) - _STYLE_KEYS
    if unknown:
        raise ArtStyleError(f"art_style_unknown_keys:{','.join(sorted(unknown))}")
    style_id = raw.get("style_id")
    if not isinstance(style_id, str) or not re.fullmatch(r"[a-z][a-z0-9_]{1,47}", style_id):
        raise ArtStyleError("art_style_id_invalid")
    colors = raw.get("colors")
    if not isinstance(colors, Mapping) or set(colors) != set(ROLES) or not all(isinstance(v, str) and _HEX.match(v) for v in colors.values()):
        raise ArtStyleError(f"art_style_colors_invalid:{style_id}")
    colors = {role: str(colors[role]).upper() for role in ROLES}
    problems = contrast_problems(colors)
    if problems:
        raise ArtStyleError(f"art_style_low_contrast:{style_id}:{';'.join(problems)}")
    fonts = raw.get("fonts")
    if not isinstance(fonts, Mapping) or set(fonts) != set(FONT_ROLES) or not all(font in ART_FONTS for font in fonts.values()):
        raise ArtStyleError(f"art_style_fonts_invalid:{style_id}")
    text = dict(raw.get("text") or {})
    if set(text) - _TEXT_KEYS:
        raise ArtStyleError(f"art_style_text_invalid:{style_id}")
    if text.get("display_weight", "bold") not in ("normal", "bold", "black") or text.get("body_weight", "normal") not in ("normal", "bold"):
        raise ArtStyleError(f"art_style_text_invalid:{style_id}")
    if text.get("display_case", "none") not in ("none", "upper") or text.get("label_case", "none") not in ("none", "upper"):
        raise ArtStyleError(f"art_style_text_invalid:{style_id}")
    spacing = text.get("display_letter_spacing", 0)
    if not isinstance(spacing, int | float) or isinstance(spacing, bool) or not -0.05 <= spacing <= 0.4:
        raise ArtStyleError(f"art_style_text_invalid:{style_id}")
    shadow = text.get("display_shadow")
    if shadow is not None:
        if not isinstance(shadow, Mapping) or set(shadow) - {"dx", "dy", "color", "opacity"}:
            raise ArtStyleError(f"art_style_text_invalid:{style_id}")
        if not str(shadow.get("color", "role.accent")).startswith(("role.", "#")):
            raise ArtStyleError(f"art_style_text_invalid:{style_id}")
    decor_opacity = raw.get("decor_opacity", 1)
    if not isinstance(decor_opacity, int | float) or isinstance(decor_opacity, bool) or not 0 <= decor_opacity <= 1:
        raise ArtStyleError(f"art_style_decor_opacity_invalid:{style_id}")
    photo = dict(raw.get("photo") or {})
    try:
        ImageEffects.model_validate(photo)
    except ValidationError as exc:
        raise ArtStyleError(f"art_style_photo_invalid:{style_id}") from exc
    if photo.get("filter") == "style":
        raise ArtStyleError(f"art_style_photo_invalid:{style_id}")
    evidence = raw.get("evidence") or []
    if not isinstance(evidence, list) or not all(isinstance(word, str) for word in evidence) or len(evidence) > 40:
        raise ArtStyleError(f"art_style_evidence_invalid:{style_id}")
    return ArtStyle(
        style_id=style_id,
        title=str(raw.get("title", style_id))[:80],
        description=str(raw.get("description", ""))[:400],
        evidence=tuple(word[:40] for word in evidence),
        colors=colors,
        fonts={role: str(fonts[role]) for role in FONT_ROLES},
        text=text,
        hide_decor=bool(raw.get("hide_decor", False)),
        decor_opacity=float(decor_opacity),
        backdrop=_items(raw.get("backdrop"), style_id, "backdrop"),
        overlay=_items(raw.get("overlay"), style_id, "overlay"),
        photo=photo,
        source=str(raw.get("source", source))[:40] or source,
    )


def style_library_dir() -> Path | None:
    configured = os.environ.get(STYLE_DIR_ENV, "").strip()
    return Path(configured) if configured else None


def _read_dir(root: Path, source: str) -> dict[str, ArtStyle]:
    styles: dict[str, ArtStyle] = {}
    if not root.is_dir():
        return styles
    for path in sorted(root.glob("*.json")):
        try:
            style = parse_art_style(json.loads(path.read_text(encoding="utf-8")), source=source)
        except (OSError, json.JSONDecodeError) as exc:
            raise ArtStyleError(f"art_style_unreadable:{path.name}") from exc
        if style.style_id != path.stem:
            raise ArtStyleError(f"art_style_id_mismatch:{path.name}")
        styles[style.style_id] = style
    return styles


@lru_cache(maxsize=4)
def _cached_styles(builtin: str, library: str | None, library_mtime: float) -> dict[str, ArtStyle]:
    del library_mtime  # part of the cache key only: a newly saved style invalidates the cache
    styles = _read_dir(Path(builtin), "builtin")
    if library:
        for style_id, style in _read_dir(Path(library), "library").items():
            if style_id in styles:
                continue  # built-in packs cannot be replaced from the library
            styles[style_id] = style
    return styles


def load_art_styles() -> dict[str, ArtStyle]:
    """Built-in packs plus the style library (built-in ids win)."""

    library = style_library_dir()
    mtime = 0.0
    if library is not None and library.is_dir():
        mtime = max([library.stat().st_mtime, *(path.stat().st_mtime for path in library.glob("*.json"))])
    return _cached_styles(str(ART_STYLES_DIR), str(library) if library else None, mtime)


_CANDIDATE: ContextVar[ArtStyle | None] = ContextVar("art_style_candidate", default=None)


@contextmanager
def candidate_style(style: ArtStyle) -> Iterator[None]:
    """Make an unsaved style resolvable (for test-compiling an AI proposal) within this context only."""

    marker = _CANDIDATE.set(style)
    try:
        yield
    finally:
        _CANDIDATE.reset(marker)


def get_art_style(style_id: str) -> ArtStyle:
    candidate = _CANDIDATE.get()
    if candidate is not None and candidate.style_id == style_id:
        return candidate
    styles = load_art_styles()
    if style_id not in styles:
        raise ArtStyleError(f"art_style_unknown:{style_id}")
    return styles[style_id]


def save_to_library(style: ArtStyle) -> Path:
    """Write a validated style into the library directory and return its path."""

    library = style_library_dir()
    if library is None:
        raise ArtStyleError(f"style_library_not_configured: set {STYLE_DIR_ENV}")
    if style.style_id in _read_dir(ART_STYLES_DIR, "builtin"):
        raise ArtStyleError(f"art_style_id_taken:{style.style_id}")
    library.mkdir(parents=True, exist_ok=True)
    path = library / f"{style.style_id}.json"
    path.write_text(json.dumps(style.to_json(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def match_art_styles(text: str, *, limit: int = 3) -> list[str]:
    """Style ids whose evidence words appear in ``text`` (best first)."""

    lowered = text.lower()
    scores = []
    for style_id, style in load_art_styles().items():
        hits = sum(1 for word in style.evidence if word.lower() in lowered)
        if hits:
            scores.append((-hits, style_id))
    return [style_id for _, style_id in sorted(scores)[:limit]]


__all__ = [
    "ART_FONTS",
    "ART_STYLES_DIR",
    "ART_STYLE_SCHEMA",
    "FONT_ROLES",
    "ROLES",
    "STYLE_DIR_ENV",
    "ArtStyle",
    "ArtStyleError",
    "candidate_style",
    "contrast",
    "contrast_problems",
    "get_art_style",
    "load_art_styles",
    "match_art_styles",
    "parse_art_style",
    "save_to_library",
    "style_library_dir",
]
