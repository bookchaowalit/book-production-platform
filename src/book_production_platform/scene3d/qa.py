"""Artifact QA checks for rendered scene outputs."""

from pathlib import Path

from PIL import Image, ImageStat, UnidentifiedImageError

from book_production_platform.scene3d.errors import INVALID_RENDER, SceneInputError


def verify_png(path: Path, expected_size: tuple[int, int]) -> None:
    """Require a readable, non-flat PNG with the exact output dimensions."""
    if path.is_symlink() or not path.is_file():
        raise SceneInputError(INVALID_RENDER, "render output is missing")
    try:
        if path.stat().st_size == 0:
            raise SceneInputError(INVALID_RENDER, "render output is empty")
        with Image.open(path) as image:
            if image.format != "PNG" or image.size != expected_size:
                raise SceneInputError(INVALID_RENDER, "render format or dimensions are invalid")
            image.load()
            variance = ImageStat.Stat(image.convert("RGB")).var
            if not variance or max(variance) < 1.0:
                raise SceneInputError(INVALID_RENDER, "render output is flat")
    except SceneInputError:
        raise
    except (OSError, ValueError, UnidentifiedImageError, Image.DecompressionBombError) as error:
        raise SceneInputError(INVALID_RENDER, "render output cannot be decoded") from error
