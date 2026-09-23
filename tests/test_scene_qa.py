from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from book_production_platform.scene3d.errors import SceneInputError
from book_production_platform.scene3d.qa import verify_png


def write_render(path: Path, size: tuple[int, int] = (1024, 1024), *, flat: bool = False) -> None:
    image = Image.new("RGB", size, color=(220, 225, 230))
    if not flat:
        ImageDraw.Draw(image).rectangle((size[0] // 4, size[1] // 4, size[0] // 2, size[1] // 2), fill=(20, 70, 140))
    image.save(path, format="PNG")


def test_verify_png_accepts_readable_non_flat_expected_profile(tmp_path: Path) -> None:
    render = tmp_path / "render.png"
    write_render(render)

    assert verify_png(render, (1024, 1024)) is None


def test_verify_png_rejects_empty_and_unreadable_files(tmp_path: Path) -> None:
    for name, content in (("empty.png", b""), ("invalid.png", b"not a png")):
        render = tmp_path / name
        render.write_bytes(content)
        with pytest.raises(SceneInputError) as error:
            verify_png(render, (1024, 1024))
        assert error.value.code == "invalid_render"


def test_verify_png_rejects_wrong_dimensions_and_flat_render(tmp_path: Path) -> None:
    wrong_size = tmp_path / "wrong-size.png"
    write_render(wrong_size, (1024, 768))
    with pytest.raises(SceneInputError) as error:
        verify_png(wrong_size, (1024, 1024))
    assert error.value.code == "invalid_render"

    flat = tmp_path / "flat.png"
    write_render(flat, flat=True)
    with pytest.raises(SceneInputError) as error:
        verify_png(flat, (1024, 1024))
    assert error.value.code == "invalid_render"
