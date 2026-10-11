"""Filesystem locations owned by the Production Hub package."""

from __future__ import annotations

import os
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
# The Book Production Platform checkout that contains services/ and packages/.
REPO_ROOT = PACKAGE_ROOT.parents[1]
SCHEMAS_DIR = PACKAGE_ROOT / "schemas"
REGISTRY_DIR = PACKAGE_ROOT / "registry"


CATALOG_RELATIVE = (
    Path("repository-catalog") / "registries" / "production-tools.yaml",  # since 2026-10-11
    Path("catalog") / "production-tools.yaml",  # before 2026-10-11
)


def _catalog_in(root: Path) -> Path | None:
    return next((root / rel for rel in CATALOG_RELATIVE if (root / rel).is_file()), None)


def _workspace_root() -> Path:
    """Solo Empire workspace that owns the production tools catalog.

    The platform checkout normally lives inside that workspace, so the nearest
    ancestor holding the catalog is used; SOLO_EMPIRE_ROOT overrides it.
    """
    override = os.environ.get("SOLO_EMPIRE_ROOT")
    if override:
        return Path(override).resolve()
    for candidate in (REPO_ROOT, *REPO_ROOT.parents):
        if _catalog_in(candidate) is not None:
            return candidate
    return REPO_ROOT


WORKSPACE_ROOT = _workspace_root()
CATALOG_PATH = _catalog_in(WORKSPACE_ROOT) or WORKSPACE_ROOT / CATALOG_RELATIVE[0]
