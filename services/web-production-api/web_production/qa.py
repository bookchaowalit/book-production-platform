"""Deterministic build, accessibility, SEO, and responsive checks for Web V1A artifacts."""

from __future__ import annotations

import re
from html.parser import HTMLParser

from .render import BREAKPOINTS

# Meta tags every page must carry (name/property → issue code when missing or empty).
REQUIRED_META: tuple[tuple[str, str, str], ...] = (
    ("name", "description", "meta_description_missing"),
    ("property", "og:type", "og_type_missing"),
    ("property", "og:title", "og_title_missing"),
    ("property", "og:description", "og_description_missing"),
    ("property", "og:locale", "og_locale_missing"),
    ("name", "twitter:card", "twitter_card_missing"),
)


class _MarkupParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.tags: list[str] = []
        self.attrs: list[tuple[str, dict[str, str]]] = []
        self.title_text = ""
        self._in_title = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.tags.append(tag)
        self.attrs.append((tag, {key: value or "" for key, value in attrs}))
        self._in_title = tag == "title"

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self._in_title = False

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title_text += data


# Names of the check groups ``run_page_qa`` applies (recorded in the job trace).
QA_CHECKS: tuple[str, ...] = (
    "doctype", "no_script", "viewport", "html_parse", "required_landmarks", "single_h1", "bundle_local_src",
    "image_alt", "safe_href", "stylesheet_reference", "title", "meta_description", "open_graph", "twitter_card",
    "focus_visible", *(f"breakpoint_{name}" for name, _ in BREAKPOINTS), "no_external_css",
)


def run_page_qa(
    html_text: str,
    css_text: str,
    expected_css_filename: str | None = None,
    bundle_assets: frozenset[str] = frozenset(),
) -> list[str]:
    """Return stable QA issue codes without executing page content.

    ``src`` is allowed only for files shipped in the same bundle
    (``bundle_assets``); every image must carry a non-empty ``alt``. The head
    must carry a non-empty ``<title>``, meta description, and OpenGraph /
    Twitter card meta, and the CSS must define every named breakpoint.
    """

    issues: list[str] = []
    lowered = html_text.lower()
    if not lowered.startswith("<!doctype html>"):
        issues.append("doctype_missing")
    if "<script" in lowered:
        issues.append("unsafe_markup")
    if '<meta name="viewport"' not in lowered:
        issues.append("viewport_missing")
    parser = _MarkupParser()
    try:
        parser.feed(html_text)
    except (TypeError, ValueError):
        issues.append("html_parse_failed")
    for required in ("html", "head", "main", "footer", "h1"):
        if required not in parser.tags:
            issues.append(f"{required}_missing")
    if parser.tags.count("h1") != 1:
        issues.append("h1_count_invalid")
    for tag, attrs in parser.attrs:
        href = attrs.get("href")
        src = attrs.get("src")
        if src and src not in bundle_assets:
            issues.append("external_asset_reference")
        if tag == "img" and not (attrs.get("alt") or "").strip():
            issues.append("image_alt_missing")
        if href and (href.lower().startswith(("http://", "https://", "javascript:", "data:", "//"))):
            issues.append("unsafe_href")
        if expected_css_filename and tag == "link" and href != expected_css_filename:
            issues.append("stylesheet_reference_invalid")
    if not parser.title_text.strip():
        issues.append("title_missing")
    metas = [attrs for tag, attrs in parser.attrs if tag == "meta"]
    for attribute, name, code in REQUIRED_META:
        if not any(meta.get(attribute) == name and meta.get("content", "").strip() for meta in metas):
            issues.append(code)
    if "@media" not in css_text or ":focus-visible" not in css_text:
        issues.append("responsive_or_focus_css_missing")
    for name, width in BREAKPOINTS:
        if not re.search(rf"@media\s*\(\s*max-width:\s*{width}px\s*\)", css_text):
            issues.append(f"breakpoint_{name}_missing")
    if "url(" in css_text.lower() or "@import" in css_text.lower():
        issues.append("external_css_reference")
    return sorted(set(issues))
