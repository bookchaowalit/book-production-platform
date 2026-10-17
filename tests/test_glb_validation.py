import struct
from pathlib import Path

import pytest

from book_production_platform.scene3d import glb
from book_production_platform.scene3d.errors import SceneInputError
from tests.helpers.glb_factory import (
    triangle_document,
    with_texture,
    write_glb,
)


def test_inspect_glb_reports_one_embedded_triangle_mesh(tmp_path: Path) -> None:
    asset = write_glb(tmp_path / "triangle.glb")

    summary = glb.inspect_glb(asset)

    assert summary.mesh_object_count == 1
    assert summary.triangle_count == 1
    assert summary.texture_bytes == 0
    assert summary.texture_dimensions == ()
    assert summary.extensions == ()


@pytest.mark.parametrize(
    ("kwargs", "expected_code"),
    [
        ({"magic": b"nope"}, "invalid_glb"),
        ({"header_length": 999}, "invalid_glb"),
        ({"json_chunk_length": 0x7FFFFFFC}, "invalid_glb"),
    ],
)
def test_inspect_glb_rejects_malformed_container(
    tmp_path: Path, kwargs: dict[str, object], expected_code: str
) -> None:
    asset = write_glb(tmp_path / "broken.glb", **kwargs)

    with pytest.raises(SceneInputError) as error:
        glb.inspect_glb(asset)

    assert error.value.code == expected_code


@pytest.mark.parametrize(
    ("mutate", "expected_code"),
    [
        (lambda doc: doc["buffers"][0].update(uri="https://example.invalid/model.bin"), "unsafe_glb_resource"),
        (lambda doc: doc.update(extensionsUsed=["EXT_unknown_feature"]), "unsupported_glb_feature"),
        (lambda doc: doc.update(animations=[{}]), "unsupported_glb_feature"),
        (lambda doc: doc.update(skins=[{}]), "unsupported_glb_feature"),
        (lambda doc: doc["meshes"][0]["primitives"][0].update(mode=1), "unsupported_glb_feature"),
        (lambda doc: doc["buffers"][0].update(byteLength=1000), "invalid_glb"),
        (lambda doc: doc["accessors"][0].update(type=[]), "invalid_glb"),
        (lambda doc: doc["accessors"][0].update(type={}), "invalid_glb"),
    ],
)
def test_inspect_glb_rejects_unsafe_or_unsupported_features(
    tmp_path: Path,
    mutate: object,
    expected_code: str,
) -> None:
    document = triangle_document()
    mutate(document)  # type: ignore[operator]
    asset = write_glb(tmp_path / "unsupported.glb", document)

    with pytest.raises(SceneInputError) as error:
        glb.inspect_glb(asset)

    assert error.value.code == expected_code


def test_inspect_glb_rejects_external_image_uri(tmp_path: Path) -> None:
    document, binary = with_texture()
    document["images"] = [{"uri": "textures/product.png"}]
    asset = write_glb(tmp_path / "external-image.glb", document, binary)

    with pytest.raises(SceneInputError) as error:
        glb.inspect_glb(asset)

    assert error.value.code == "unsafe_glb_resource"


def test_inspect_glb_counts_mesh_nodes_and_triangles_before_caps(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    document = triangle_document()
    document["scenes"][0]["nodes"] = [0, 1]
    document["nodes"].append({"mesh": 0})
    monkeypatch.setattr(glb, "MAX_IMPORTED_OBJECTS", 1)
    asset = write_glb(tmp_path / "too-many-objects.glb", document)

    with pytest.raises(SceneInputError) as error:
        glb.inspect_glb(asset)

    assert error.value.code == "resource_cap_exceeded"


def test_inspect_glb_rejects_triangle_budget_exceeded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(glb, "MAX_TRIANGLES", 0)
    asset = write_glb(tmp_path / "over-triangle-budget.glb")

    with pytest.raises(SceneInputError) as error:
        glb.inspect_glb(asset)

    assert error.value.code == "resource_cap_exceeded"


def test_inspect_glb_reports_embedded_png_dimensions(tmp_path: Path) -> None:
    document, binary = with_texture()
    asset = write_glb(tmp_path / "textured.glb", document, binary)

    summary = glb.inspect_glb(asset)

    assert summary.texture_dimensions == ((1, 1),)
    assert summary.texture_bytes > 0


def test_inspect_glb_reports_embedded_jpeg_dimensions(tmp_path: Path) -> None:
    document, binary = with_texture(mime_type="image/jpeg")
    asset = write_glb(tmp_path / "textured-jpeg.glb", document, binary)

    summary = glb.inspect_glb(asset)

    assert summary.texture_dimensions == ((1, 1),)


def test_inspect_glb_rejects_texture_dimension_and_byte_budgets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    document, binary = with_texture(width=3, height=1)
    monkeypatch.setattr(glb, "MAX_TEXTURE_DIMENSION", 2)
    asset = write_glb(tmp_path / "large-texture-dimension.glb", document, binary)

    with pytest.raises(SceneInputError) as error:
        glb.inspect_glb(asset)

    assert error.value.code == "resource_cap_exceeded"

    document, binary = with_texture()
    monkeypatch.setattr(glb, "MAX_TEXTURE_DIMENSION", 4096)
    monkeypatch.setattr(glb, "MAX_TEXTURE_BYTES", 1)
    asset = write_glb(tmp_path / "large-texture-bytes.glb", document, binary)

    with pytest.raises(SceneInputError) as error:
        glb.inspect_glb(asset)

    assert error.value.code == "resource_cap_exceeded"


def test_inspect_glb_checks_file_cap_before_reading_contents(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    asset = write_glb(tmp_path / "over-file-cap.glb")
    monkeypatch.setattr(glb, "MAX_GLB_BYTES", 12)

    def fail_read_bytes(_: Path) -> bytes:
        raise AssertionError("oversized file must be rejected before it is read")

    monkeypatch.setattr(Path, "read_bytes", fail_read_bytes)
    with pytest.raises(SceneInputError) as error:
        glb.inspect_glb(asset)

    assert error.value.code == "resource_cap_exceeded"


def test_inspect_glb_rejects_buffer_view_outside_declared_buffer(tmp_path: Path) -> None:
    document = triangle_document()
    document["bufferViews"][0]["byteLength"] = 1000
    asset = write_glb(tmp_path / "bad-view.glb", document)

    with pytest.raises(SceneInputError) as error:
        glb.inspect_glb(asset)

    assert error.value.code == "invalid_glb"


def test_inspect_glb_rejects_excessively_nested_json_with_stable_error(tmp_path: Path) -> None:
    json_data = b'{"asset":{"version":"2.0"},"nested":' + b"[" * 1100 + b"null" + b"]" * 1100 + b"}"
    json_data += b" " * (-len(json_data) % 4)
    chunk = struct.pack("<II", len(json_data), glb.JSON_CHUNK) + json_data
    total_length = 12 + len(chunk)
    asset = tmp_path / "deeply-nested.glb"
    asset.write_bytes(struct.pack("<4sII", b"glTF", 2, total_length) + chunk)

    with pytest.raises(SceneInputError) as error:
        glb.inspect_glb(asset)

    assert error.value.code == "invalid_glb"


def test_inspect_glb_rejects_integer_beyond_json_parser_limit(tmp_path: Path) -> None:
    json_data = b'{"oversized":' + b"9" * 5000 + b"}"
    json_data += b" " * (-len(json_data) % 4)
    chunk = struct.pack("<II", len(json_data), glb.JSON_CHUNK) + json_data
    total_length = 12 + len(chunk)
    asset = tmp_path / "oversized-integer.glb"
    asset.write_bytes(struct.pack("<4sII", b"glTF", 2, total_length) + chunk)

    with pytest.raises(SceneInputError) as error:
        glb.inspect_glb(asset)

    assert error.value.code == "invalid_glb"
