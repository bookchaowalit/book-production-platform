"""Small literal GLB fixtures for bounded parser tests."""

import json
import struct
import zlib
from io import BytesIO
from pathlib import Path
from typing import Any

from PIL import Image

JSON_CHUNK = 0x4E4F534A
BIN_CHUNK = 0x004E4942
TRIANGLE_BINARY = struct.pack(
    "<9f3H2x",
    -1.0,
    -1.0,
    0.0,
    1.0,
    -1.0,
    0.0,
    0.0,
    1.0,
    0.0,
    0,
    1,
    2,
)


def triangle_document() -> dict[str, Any]:
    return {
        "asset": {"version": "2.0"},
        "scene": 0,
        "scenes": [{"nodes": [0]}],
        "nodes": [{"mesh": 0}],
        "meshes": [
            {
                "primitives": [
                    {
                        "attributes": {"POSITION": 0},
                        "indices": 1,
                        "mode": 4,
                    }
                ]
            }
        ],
        "buffers": [{"byteLength": len(TRIANGLE_BINARY)}],
        "bufferViews": [
            {"buffer": 0, "byteOffset": 0, "byteLength": 36},
            {"buffer": 0, "byteOffset": 36, "byteLength": 6},
        ],
        "accessors": [
            {
                "bufferView": 0,
                "componentType": 5126,
                "count": 3,
                "type": "VEC3",
            },
            {
                "bufferView": 1,
                "componentType": 5123,
                "count": 3,
                "type": "SCALAR",
            },
        ],
    }


def png_bytes(width: int = 1, height: int = 1) -> bytes:
    """Return a small valid RGBA PNG fixture."""
    raw = (b"\x00" + bytes(width * 4)) * height
    compressed = zlib.compress(raw)

    def chunk(kind: bytes, payload: bytes) -> bytes:
        return (
            struct.pack(">I", len(payload))
            + kind
            + payload
            + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)
        )

    header = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", compressed) + chunk(b"IEND", b"")


def jpeg_bytes(width: int = 1, height: int = 1) -> bytes:
    output = BytesIO()
    Image.new("RGB", (width, height), color=(20, 40, 60)).save(output, format="JPEG")
    return output.getvalue()


def build_glb(
    document: dict[str, Any] | None = None,
    binary: bytes = TRIANGLE_BINARY,
    *,
    json_chunk_length: int | None = None,
    header_length: int | None = None,
    magic: bytes = b"glTF",
) -> bytes:
    doc = triangle_document() if document is None else document
    json_data = json.dumps(doc, separators=(",", ":")).encode("utf-8")
    json_data += b" " * (-len(json_data) % 4)
    binary_data = binary + b"\x00" * (-len(binary) % 4)
    json_length = len(json_data) if json_chunk_length is None else json_chunk_length
    chunks = [struct.pack("<II", json_length, JSON_CHUNK) + json_data]
    if binary:
        chunks.append(struct.pack("<II", len(binary_data), BIN_CHUNK) + binary_data)
    payload = b"".join(chunks)
    actual_length = 12 + len(payload)
    declared_length = actual_length if header_length is None else header_length
    if len(magic) != 4:
        raise ValueError("GLB magic must be four bytes")
    return struct.pack("<4sII", magic, 2, declared_length) + payload


def write_glb(
    path: Path,
    document: dict[str, Any] | None = None,
    binary: bytes = TRIANGLE_BINARY,
    **kwargs: Any,
) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(build_glb(document, binary, **kwargs))
    return path


def with_texture(
    width: int = 1, height: int = 1, *, mime_type: str = "image/png"
) -> tuple[dict[str, Any], bytes]:
    image = jpeg_bytes(width, height) if mime_type == "image/jpeg" else png_bytes(width, height)
    binary = TRIANGLE_BINARY + image
    document = triangle_document()
    document["buffers"][0]["byteLength"] = len(binary)
    document["bufferViews"].append(
        {"buffer": 0, "byteOffset": len(TRIANGLE_BINARY), "byteLength": len(image)}
    )
    document["images"] = [{"bufferView": 2, "mimeType": mime_type}]
    document["textures"] = [{"source": 0}]
    document["materials"] = [
        {"pbrMetallicRoughness": {"baseColorTexture": {"index": 0}}}
    ]
    document["meshes"][0]["primitives"][0]["material"] = 0
    return document, binary
