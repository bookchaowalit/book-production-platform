import pytest

from book_production_platform.scene3d.errors import SceneInputError
from book_production_platform.scene3d.models import parse_scene_ir


def valid_scene() -> dict[str, object]:
    return {
        "schema_version": "scene-ir.v1",
        "asset": {"path": "product.glb"},
        "recipe": "product-hero.studio.v1",
        "composition": "three-quarter.v1",
        "lighting": "softbox-three-point.v1",
        "background": "studio-neutral.v1",
        "material_preset": "preserve-source.v1",
        "render_profile": "square-1024.v1",
    }


def test_scene_ir_accepts_the_allowlisted_minimal_scene() -> None:
    parsed = parse_scene_ir(valid_scene())

    assert parsed.render_profile == "square-1024.v1"
    assert parsed.asset.path == "product.glb"


def test_scene_ir_rejects_unknown_fields() -> None:
    with pytest.raises(SceneInputError) as error:
        parse_scene_ir({**valid_scene(), "python": "print(1)"})

    assert error.value.code == "invalid_scene_ir"


def test_scene_ir_rejects_absolute_traversal_and_url_asset_paths() -> None:
    for path in (
        "C:/private/product.glb",
        "../product.glb",
        "./product.glb",
        "folder//product.glb",
        r"..\product.glb",
        "https://host/product.glb",
    ):
        with pytest.raises(SceneInputError) as error:
            parse_scene_ir({**valid_scene(), "asset": {"path": path}})

        assert error.value.code == "invalid_scene_ir"
