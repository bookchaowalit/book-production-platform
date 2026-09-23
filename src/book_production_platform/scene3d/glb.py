"""Bounded structural preflight for self-contained GLB 2.0 assets."""

from __future__ import annotations

import json
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Any, NoReturn

from book_production_platform.scene3d.errors import (
    INVALID_GLB,
    RESOURCE_CAP_EXCEEDED,
    UNSAFE_GLB_RESOURCE,
    UNSUPPORTED_GLB_FEATURE,
    SceneInputError,
)

GLB_MAGIC = b"glTF"
JSON_CHUNK = 0x4E4F534A
BIN_CHUNK = 0x004E4942
MAX_GLB_BYTES = 256 * 1024 * 1024
MAX_IMPORTED_OBJECTS = 100
MAX_TRIANGLES = 1_000_000
MAX_TEXTURE_DIMENSION = 4096
MAX_TEXTURE_BYTES = 128 * 1024 * 1024

_COMPONENT_BYTES = {5120: 1, 5121: 1, 5122: 2, 5123: 2, 5125: 4, 5126: 4}
_TYPE_COMPONENTS = {
    "SCALAR": 1,
    "VEC2": 2,
    "VEC3": 3,
    "VEC4": 4,
    "MAT2": 4,
    "MAT3": 9,
    "MAT4": 16,
}
_JPEG_START_OF_FRAME = {
    0xC0,
    0xC1,
    0xC2,
    0xC3,
    0xC5,
    0xC6,
    0xC7,
    0xC9,
    0xCA,
    0xCB,
    0xCD,
    0xCE,
    0xCF,
}


@dataclass(frozen=True)
class GlbSummary:
    mesh_object_count: int
    triangle_count: int
    texture_bytes: int
    texture_dimensions: tuple[tuple[int, int], ...]
    extensions: tuple[str, ...]


def _reject(code: str, message: str) -> NoReturn:
    raise SceneInputError(code, message)


def _mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        _reject(INVALID_GLB, f"{label} must be an object")
    return value


def _array(value: Any, label: str) -> list[Any]:
    if not isinstance(value, list):
        _reject(INVALID_GLB, f"{label} must be an array")
    return value


def _integer(value: Any, label: str, *, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        _reject(INVALID_GLB, f"{label} must be an integer at least {minimum}")
    return value


def _index(value: Any, size: int, label: str) -> int:
    index = _integer(value, label)
    if index >= size:
        _reject(INVALID_GLB, f"{label} is out of range")
    return index


def _read_chunks(data: bytes) -> tuple[dict[str, Any], bytes]:
    if len(data) < 20:
        _reject(INVALID_GLB, "GLB header or JSON chunk is truncated")
    magic, version, declared_length = struct.unpack_from("<4sII", data, 0)
    if magic != GLB_MAGIC or declared_length != len(data):
        _reject(INVALID_GLB, "GLB header is invalid")
    if version != 2:
        _reject(UNSUPPORTED_GLB_FEATURE, "only GLB version 2 is supported")

    chunks: list[tuple[int, bytes]] = []
    offset = 12
    while offset < len(data):
        if len(data) - offset < 8:
            _reject(INVALID_GLB, "GLB chunk header is truncated")
        chunk_length, chunk_type = struct.unpack_from("<II", data, offset)
        offset += 8
        end = offset + chunk_length
        if chunk_length % 4 != 0 or end > len(data):
            _reject(INVALID_GLB, "GLB chunk length is invalid")
        chunks.append((chunk_type, data[offset:end]))
        offset = end

    if not chunks or chunks[0][0] != JSON_CHUNK:
        _reject(INVALID_GLB, "GLB must begin with a JSON chunk")
    if sum(kind == JSON_CHUNK for kind, _ in chunks) != 1:
        _reject(INVALID_GLB, "GLB must contain exactly one JSON chunk")
    if sum(kind == BIN_CHUNK for kind, _ in chunks) > 1:
        _reject(INVALID_GLB, "GLB contains duplicate binary chunks")
    if any(kind not in {JSON_CHUNK, BIN_CHUNK} for kind, _ in chunks):
        _reject(UNSUPPORTED_GLB_FEATURE, "GLB contains an unsupported chunk type")
    json_data = chunks[0][1]
    try:
        document_value = json.loads(json_data.decode("utf-8").rstrip("\x00 \t\r\n"))
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError):
        _reject(INVALID_GLB, "GLB JSON chunk is invalid")
    document = _mapping(document_value, "GLB JSON")
    binary_chunks = [payload for kind, payload in chunks if kind == BIN_CHUNK]
    return document, binary_chunks[0] if binary_chunks else b""


def _reject_external_or_extension_features(document: dict[str, Any]) -> None:
    for field in ("extensionsUsed", "extensionsRequired"):
        values = document.get(field, [])
        if not isinstance(values, list):
            _reject(INVALID_GLB, f"{field} must be an array")
        if values:
            _reject(UNSUPPORTED_GLB_FEATURE, "GLB extensions are not supported")

    pending = [document]
    while pending:
        value = pending.pop()
        if isinstance(value, dict):
            if "uri" in value:
                _reject(UNSAFE_GLB_RESOURCE, "external GLB resources are not supported")
            if "extensions" in value:
                extension_value = value["extensions"]
                if not isinstance(extension_value, dict):
                    _reject(INVALID_GLB, "GLB extensions field is invalid")
                if extension_value:
                    _reject(UNSUPPORTED_GLB_FEATURE, "GLB extensions are not supported")
            pending.extend(value.values())
        elif isinstance(value, list):
            pending.extend(value)
    for field in ("animations", "skins"):
        values = document.get(field, [])
        if not isinstance(values, list):
            _reject(INVALID_GLB, f"{field} must be an array")
        if values:
            _reject(UNSUPPORTED_GLB_FEATURE, f"GLB {field} are not supported")


def _png_dimensions(data: bytes) -> tuple[int, int]:
    if (
        len(data) < 24
        or data[:8] != b"\x89PNG\r\n\x1a\n"
        or struct.unpack_from(">I", data, 8)[0] != 13
        or data[12:16] != b"IHDR"
    ):
        _reject(INVALID_GLB, "embedded PNG header is invalid")
    width, height = struct.unpack_from(">II", data, 16)
    if width == 0 or height == 0:
        _reject(INVALID_GLB, "embedded PNG dimensions are invalid")
    return width, height


def _jpeg_dimensions(data: bytes) -> tuple[int, int]:
    if len(data) < 4 or data[:2] != b"\xff\xd8":
        _reject(INVALID_GLB, "embedded JPEG header is invalid")
    offset = 2
    while offset < len(data):
        if data[offset] != 0xFF:
            _reject(INVALID_GLB, "embedded JPEG marker is invalid")
        while offset < len(data) and data[offset] == 0xFF:
            offset += 1
        if offset >= len(data):
            break
        marker = data[offset]
        offset += 1
        if marker in {0xD8, 0xD9, 0x01} or 0xD0 <= marker <= 0xD7:
            continue
        if offset + 2 > len(data):
            _reject(INVALID_GLB, "embedded JPEG segment is truncated")
        segment_length = struct.unpack_from(">H", data, offset)[0]
        if segment_length < 2 or offset + segment_length > len(data):
            _reject(INVALID_GLB, "embedded JPEG segment length is invalid")
        if marker in _JPEG_START_OF_FRAME:
            if segment_length < 7:
                _reject(INVALID_GLB, "embedded JPEG frame header is truncated")
            height, width = struct.unpack_from(">HH", data, offset + 3)
            if width == 0 or height == 0:
                _reject(INVALID_GLB, "embedded JPEG dimensions are invalid")
            return width, height
        if marker == 0xDA:
            break
        offset += segment_length
    _reject(INVALID_GLB, "embedded JPEG dimensions are missing")


def _inspect_document(document: dict[str, Any], binary_chunk: bytes) -> GlbSummary:
    asset = _mapping(document.get("asset"), "asset")
    if asset.get("version") != "2.0":
        _reject(UNSUPPORTED_GLB_FEATURE, "only glTF asset version 2.0 is supported")
    _reject_external_or_extension_features(document)

    buffers = _array(document.get("buffers"), "buffers")
    if len(buffers) != 1:
        _reject(INVALID_GLB, "GLB must contain one embedded buffer")
    buffer = _mapping(buffers[0], "buffer")
    declared_buffer_length = _integer(buffer.get("byteLength"), "buffer.byteLength")
    if not binary_chunk or declared_buffer_length > len(binary_chunk):
        _reject(INVALID_GLB, "GLB binary chunk does not contain its declared buffer")
    if len(binary_chunk) - declared_buffer_length > 3:
        _reject(INVALID_GLB, "GLB binary padding is invalid")

    views = _array(document.get("bufferViews", []), "bufferViews")
    view_ranges: list[tuple[int, int, int | None]] = []
    for item in views:
        view = _mapping(item, "bufferView")
        if _integer(view.get("buffer"), "bufferView.buffer") != 0:
            _reject(INVALID_GLB, "bufferView references an unavailable buffer")
        start = _integer(view.get("byteOffset", 0), "bufferView.byteOffset")
        length = _integer(view.get("byteLength"), "bufferView.byteLength", minimum=1)
        end = start + length
        if end > declared_buffer_length:
            _reject(INVALID_GLB, "bufferView exceeds the declared binary buffer")
        stride_value = view.get("byteStride")
        stride = None if stride_value is None else _integer(stride_value, "bufferView.byteStride", minimum=4)
        if stride is not None and (stride > 252 or stride % 4 != 0):
            _reject(INVALID_GLB, "bufferView byteStride is invalid")
        view_ranges.append((start, end, stride))

    accessors = _array(document.get("accessors", []), "accessors")
    accessor_details: list[tuple[int, str, int, int]] = []
    for item in accessors:
        accessor = _mapping(item, "accessor")
        if accessor.get("sparse"):
            _reject(UNSUPPORTED_GLB_FEATURE, "sparse accessors are not supported")
        view_index = _index(accessor.get("bufferView"), len(view_ranges), "accessor.bufferView")
        component_type = _integer(accessor.get("componentType"), "accessor.componentType")
        component_size = _COMPONENT_BYTES.get(component_type)
        accessor_type = accessor.get("type")
        component_count = _TYPE_COMPONENTS.get(accessor_type)
        count = _integer(accessor.get("count"), "accessor.count")
        relative_offset = _integer(accessor.get("byteOffset", 0), "accessor.byteOffset")
        if component_size is None or component_count is None:
            _reject(INVALID_GLB, "accessor type or componentType is invalid")
        start, end, stride = view_ranges[view_index]
        view_length = end - start
        element_bytes = component_size * component_count
        actual_stride = element_bytes if stride is None else stride
        if actual_stride < element_bytes:
            _reject(INVALID_GLB, "accessor stride is smaller than its element")
        required_bytes = 0 if count == 0 else relative_offset + actual_stride * (count - 1) + element_bytes
        if required_bytes > view_length:
            _reject(INVALID_GLB, "accessor exceeds its bufferView")
        accessor_details.append((count, accessor_type, component_type, view_index))

    meshes = _array(document.get("meshes", []), "meshes")
    nodes = _array(document.get("nodes", []), "nodes")
    object_count = 0
    triangle_count = 0
    for item in nodes:
        node = _mapping(item, "node")
        if "mesh" not in node:
            continue
        mesh_index = _index(node["mesh"], len(meshes), "node.mesh")
        object_count += 1
        if object_count > MAX_IMPORTED_OBJECTS:
            _reject(RESOURCE_CAP_EXCEEDED, "GLB mesh object limit exceeded")
        mesh = _mapping(meshes[mesh_index], "mesh")
        primitives = _array(mesh.get("primitives"), "mesh.primitives")
        if not primitives:
            _reject(INVALID_GLB, "mesh must contain at least one primitive")
        for primitive_value in primitives:
            primitive = _mapping(primitive_value, "primitive")
            mode = _integer(primitive.get("mode", 4), "primitive.mode")
            if mode != 4:
                _reject(UNSUPPORTED_GLB_FEATURE, "only triangle primitives are supported")
            if primitive.get("targets"):
                _reject(UNSUPPORTED_GLB_FEATURE, "morph targets are not supported")
            attributes = _mapping(primitive.get("attributes"), "primitive.attributes")
            if "POSITION" not in attributes:
                _reject(INVALID_GLB, "triangle primitive is missing POSITION")
            position_index = _index(attributes["POSITION"], len(accessor_details), "POSITION accessor")
            position_count, position_type, position_component, _ = accessor_details[position_index]
            if position_type != "VEC3" or position_component != 5126 or position_count == 0:
                _reject(INVALID_GLB, "POSITION accessor has an unsupported format")
            if "indices" in primitive:
                index_accessor = _index(primitive["indices"], len(accessor_details), "indices accessor")
                index_count, index_type, index_component, _ = accessor_details[index_accessor]
                if index_type != "SCALAR" or index_component not in {5121, 5123, 5125}:
                    _reject(INVALID_GLB, "index accessor has an unsupported format")
                if index_count % 3 != 0:
                    _reject(INVALID_GLB, "triangle index count is not divisible by three")
                primitive_triangles = index_count // 3
            else:
                if position_count % 3 != 0:
                    _reject(INVALID_GLB, "vertex count is not divisible by three")
                primitive_triangles = position_count // 3
            triangle_count += primitive_triangles
            if triangle_count > MAX_TRIANGLES:
                _reject(RESOURCE_CAP_EXCEEDED, "GLB triangle limit exceeded")

    if object_count == 0 or triangle_count == 0:
        _reject(INVALID_GLB, "GLB contains no renderable triangle mesh")

    images = _array(document.get("images", []), "images")
    texture_bytes = 0
    texture_dimensions: list[tuple[int, int]] = []
    for item in images:
        image = _mapping(item, "image")
        if "bufferView" not in image:
            _reject(UNSAFE_GLB_RESOURCE, "image data must be embedded in a GLB bufferView")
        view_index = _index(image["bufferView"], len(view_ranges), "image.bufferView")
        start, end, _ = view_ranges[view_index]
        image_bytes = end - start
        texture_bytes += image_bytes
        if texture_bytes > MAX_TEXTURE_BYTES:
            _reject(RESOURCE_CAP_EXCEEDED, "GLB embedded texture byte limit exceeded")
        data = binary_chunk[start:end]
        mime_type = image.get("mimeType")
        if mime_type == "image/png":
            dimensions = _png_dimensions(data)
        elif mime_type == "image/jpeg":
            dimensions = _jpeg_dimensions(data)
        else:
            _reject(UNSUPPORTED_GLB_FEATURE, "only embedded PNG and JPEG textures are supported")
        width, height = dimensions
        if width > MAX_TEXTURE_DIMENSION or height > MAX_TEXTURE_DIMENSION:
            _reject(RESOURCE_CAP_EXCEEDED, "GLB texture dimension limit exceeded")
        texture_dimensions.append(dimensions)

    return GlbSummary(
        mesh_object_count=object_count,
        triangle_count=triangle_count,
        texture_bytes=texture_bytes,
        texture_dimensions=tuple(texture_dimensions),
        extensions=(),
    )


def inspect_glb(path: Path) -> GlbSummary:
    """Inspect a local GLB without trusting declared sizes or loading over-budget files."""
    try:
        if path.is_symlink():
            _reject(UNSAFE_GLB_RESOURCE, "symlinked GLB assets are not supported")
        size = path.stat().st_size
    except OSError as error:
        raise SceneInputError(INVALID_GLB, "GLB asset cannot be read") from error
    if size > MAX_GLB_BYTES:
        _reject(RESOURCE_CAP_EXCEEDED, "GLB file byte limit exceeded")
    try:
        data = path.read_bytes()
    except OSError as error:
        raise SceneInputError(INVALID_GLB, "GLB asset cannot be read") from error
    if len(data) != size:
        _reject(INVALID_GLB, "GLB asset changed while being inspected")
    document, binary_chunk = _read_chunks(data)
    return _inspect_document(document, binary_chunk)
