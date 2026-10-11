"""Filesystem locations owned by the Production Hub package."""

from __future__ import annotations

from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
# The Book Production Platform checkout that contains services/ and packages/.
REPO_ROOT = PACKAGE_ROOT.parents[1]
SCHEMAS_DIR = PACKAGE_ROOT / "schemas"
REGISTRY_DIR = PACKAGE_ROOT / "registry"
# The platform owns its production tools catalog.
CATALOG_PATH = REPO_ROOT / "catalog" / "production-tools.yaml"
WORKSPACE_ROOT = REPO_ROOT  # kept for callers; the platform no longer locates any other workspace
