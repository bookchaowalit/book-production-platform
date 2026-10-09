"""Filesystem locations owned by the Production Hub package."""

from __future__ import annotations

import os
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
# The Book Production Platform checkout that contains services/ and packages/.
REPO_ROOT = PACKAGE_ROOT.parents[1]
SCHEMAS_DIR = PACKAGE_ROOT / "schemas"
REGISTRY_DIR = PACKAGE_ROOT / "registry"


def _workspace_root() -> Path:
    """Solo Empire workspace that owns catalog/production-tools.yaml.

    The platform checkout normally lives inside that workspace, so the nearest
    ancestor holding the catalog is used; SOLO_EMPIRE_ROOT overrides it.
    """
    override = os.environ.get("SOLO_EMPIRE_ROOT")
    if override:
        return Path(override).resolve()
    for candidate in (REPO_ROOT, *REPO_ROOT.parents):
        if (candidate / "catalog" / "production-tools.yaml").is_file():
            return candidate
    return REPO_ROOT


WORKSPACE_ROOT = _workspace_root()
CATALOG_PATH = WORKSPACE_ROOT / "catalog" / "production-tools.yaml"
