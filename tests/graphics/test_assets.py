from pathlib import Path

import pytest

from book_production_platform.graphics_production.assets import (
    AssetReferenceError,
    validate_local_reference,
)


def test_local_reference_accepts_existing_file(tmp_path: Path) -> None:
    (tmp_path / "logo.svg").write_text("<svg/>", encoding="utf-8")

    resolved = validate_local_reference("logo.svg", tmp_path, field="assets.logo")

    assert resolved == (tmp_path / "logo.svg").resolve()


@pytest.mark.parametrize("reference", ["https://example.com/logo.svg", "data:image/svg+xml,abc", "../outside.svg"])
def test_local_reference_rejects_network_and_traversal(tmp_path: Path, reference: str) -> None:
    with pytest.raises(AssetReferenceError):
        validate_local_reference(reference, tmp_path, field="assets.logo")


def test_local_reference_rejects_missing_file(tmp_path: Path) -> None:
    with pytest.raises(AssetReferenceError, match="missing_local_asset"):
        validate_local_reference("missing.svg", tmp_path, field="assets.logo")
