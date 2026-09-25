from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image

from book_production_platform.cad_production.errors import CadProductionError
from book_production_platform.cad_production.geometry import TriangleMesh
from book_production_platform.cad_production.preview import (
    PREVIEW_SIZE,
    render_isometric_preview,
    verify_preview,
)


@pytest.fixture
def valid_triangle_mesh() -> TriangleMesh:
    return TriangleMesh(
        vertices=(
            (0.0, 0.0, 0.0),
            (20.0, 0.0, 0.0),
            (20.0, 10.0, 0.0),
            (0.0, 10.0, 0.0),
            (0.0, 0.0, 4.0),
            (20.0, 0.0, 4.0),
            (20.0, 10.0, 4.0),
            (0.0, 10.0, 4.0),
        ),
        triangles=(
            (0, 2, 1), (0, 3, 2), (4, 5, 6), (4, 6, 7),
            (0, 1, 5), (0, 5, 4), (1, 2, 6), (1, 6, 5),
            (2, 3, 7), (2, 7, 6), (3, 0, 4), (3, 4, 7),
        ),
    )


def test_preview_is_deterministic_for_same_mesh(
    valid_triangle_mesh: TriangleMesh, tmp_path: Path
) -> None:
    first = tmp_path / "first.png"
    second = tmp_path / "second.png"

    render_isometric_preview(valid_triangle_mesh, first)
    render_isometric_preview(valid_triangle_mesh, second)

    assert first.read_bytes() == second.read_bytes()
    verify_preview(first)
    with Image.open(first) as image:
        assert image.size == PREVIEW_SIZE
        assert image.mode == "RGB"


def test_verify_preview_rejects_white_image_and_wrong_dimensions(tmp_path: Path) -> None:
    blank = tmp_path / "blank.png"
    Image.new("RGB", PREVIEW_SIZE, "white").save(blank)
    with pytest.raises(CadProductionError) as blank_error:
        verify_preview(blank)
    assert blank_error.value.code == "preview_invalid"

    wrong_size = tmp_path / "wrong.png"
    Image.new("RGB", (100, 100), "white").save(wrong_size)
    with pytest.raises(CadProductionError) as size_error:
        verify_preview(wrong_size)
    assert size_error.value.code == "preview_invalid"


def test_renderer_rejects_destination_link(
    valid_triangle_mesh: TriangleMesh, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    link = tmp_path / "preview.png"
    monkeypatch.setattr(
        "book_production_platform.cad_production.preview._is_link",
        lambda candidate: candidate == link,
    )

    with pytest.raises(CadProductionError) as error:
        render_isometric_preview(valid_triangle_mesh, link)
    assert error.value.code == "preview_invalid"
