"""Vendored copies under src/ must match the standalone packages/ sources.

Covers packages/brief-adapter-kit, packages/production-trace, and
packages/file-intake, which services/ install directly while the platform
CLI imports the vendored copy.
"""

from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PLATFORM_SRC = ROOT / "src" / "book_production_platform"
PAIRS = [
    (ROOT / "packages" / "brief-adapter-kit" / "brief_adapter_kit", PLATFORM_SRC / "brief_adapter_kit"),
    (ROOT / "packages" / "production-trace" / "production_trace", PLATFORM_SRC / "production_trace"),
    (ROOT / "packages" / "file-intake" / "file_intake", PLATFORM_SRC / "file_intake"),
]


@pytest.mark.parametrize(("package", "vendored"), PAIRS, ids=lambda path: path.name)
def test_vendored_copy_is_identical(package: Path, vendored: Path) -> None:
    names = sorted(path.name for path in package.glob("*.py"))
    assert names, f"{package} has no modules"
    assert names == sorted(path.name for path in vendored.glob("*.py"))
    for name in names:
        assert (package / name).read_text(encoding="utf-8") == (vendored / name).read_text(
            encoding="utf-8"
        ), f"{name} drifted; port the change to both copies"
