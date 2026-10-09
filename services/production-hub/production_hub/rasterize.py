"""SVG -> PNG through a local Chromium-based browser (Edge or Chrome).

Used by handoffs whose target accepts raster images only (Documents embeds PNG
charts). The flags match the Graphics tool's renderer: headless, no network
services, fixed device scale, and a dedicated profile folder.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

_SIZE = re.compile(r'<svg\b[^>]*?\bwidth="([\d.]+)(?:px)?"[^>]*?\bheight="([\d.]+)(?:px)?"', re.DOTALL)
_VIEWBOX = re.compile(r'<svg\b[^>]*?\bviewBox="[\d.\-]+\s+[\d.\-]+\s+([\d.]+)\s+([\d.]+)"', re.DOTALL)
DEFAULT_SIZE = (800, 480)
MAX_SIDE = 4096


class RasterizeError(RuntimeError):
    """Raised when no browser is available or the screenshot fails."""


def find_browser() -> str | None:
    for variable in ("PRODUCTION_HUB_BROWSER", "GRAPHICS_PRODUCTION_BROWSER"):
        configured = os.environ.get(variable)
        if configured:
            return shutil.which(configured) or (configured if Path(configured).is_file() else None)
    for name in ("msedge", "google-chrome", "chrome", "chromium", "chromium-browser"):
        found = shutil.which(name)
        if found:
            return found
    if os.name == "nt":
        program_files = [os.environ.get("PROGRAMFILES(X86)", "C:/Program Files (x86)"),
                         os.environ.get("PROGRAMFILES", "C:/Program Files")]
        for base in program_files:
            for relative in ("Microsoft/Edge/Application/msedge.exe", "Google/Chrome/Application/chrome.exe"):
                candidate = Path(base) / relative
                if candidate.is_file():
                    return str(candidate)
    return None


def svg_size(svg_text: str) -> tuple[int, int]:
    match = _SIZE.search(svg_text) or _VIEWBOX.search(svg_text)
    if not match:
        return DEFAULT_SIZE
    width, height = (min(MAX_SIDE, max(1, round(float(value)))) for value in match.groups())
    return width, height


def svg_to_png(svg_path: Path, png_path: Path, *, timeout_seconds: int = 60) -> tuple[int, int]:
    browser = find_browser()
    if browser is None:
        raise RasterizeError("no Edge/Chrome browser found; set PRODUCTION_HUB_BROWSER")
    text = svg_path.read_text(encoding="utf-8", errors="replace")
    if re.search(r"<script\b|\son[a-z]+\s*=", text, re.IGNORECASE):
        raise RasterizeError("svg contains script or event handlers")
    width, height = svg_size(text)
    png_path = png_path.resolve()
    png_path.parent.mkdir(parents=True, exist_ok=True)
    png_path.unlink(missing_ok=True)
    # A fresh profile per call: a second Chromium launched on a profile that an
    # earlier (possibly still running) instance holds hands off and exits 0
    # without writing the screenshot.
    profile = Path(tempfile.mkdtemp(prefix="production-hub-browser-"))
    command = [
        browser, "--headless", "--disable-gpu", "--disable-background-networking", "--disable-extensions",
        "--disable-sync", "--no-first-run", "--hide-scrollbars", "--force-device-scale-factor=1",
        f"--window-size={width},{height}", f"--user-data-dir={profile}", f"--screenshot={png_path}",
        svg_path.resolve().as_uri(),
    ]
    try:
        try:
            result = subprocess.run(command, capture_output=True, check=False, timeout=timeout_seconds)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise RasterizeError(f"browser screenshot failed: {exc}") from exc
        if result.returncode != 0:
            raise RasterizeError(f"browser screenshot failed with exit {result.returncode}")
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if png_path.is_file() and png_path.stat().st_size > 0:
                return width, height
            time.sleep(0.1)
        raise RasterizeError("browser did not write the screenshot")
    finally:
        shutil.rmtree(profile, ignore_errors=True)
