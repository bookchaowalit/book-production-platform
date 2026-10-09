"""Filesystem locations for the video post-production pipeline tools."""

from __future__ import annotations

from pathlib import Path

SERVICE_ROOT = Path(__file__).resolve().parents[1]
SCHEMAS_DIR = SERVICE_ROOT / "schemas"

__all__ = ["SCHEMAS_DIR", "SERVICE_ROOT"]
