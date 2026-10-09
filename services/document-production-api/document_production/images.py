"""Verified local images for documents (report charts, spec-sheet previews).

A request names each image by ``name``, ``media_type``, ``content_hash`` and
``byte_size``; the caller supplies the folder that holds the files. Every
PNG/JPEG reference must exist there with exactly that sha256 and size or the
request is rejected, so a document never embeds a different image than the
one the request describes. Other media types stay text references.
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from pathlib import Path
from typing import Literal, Protocol

from document_ir import DocumentImage, ValidationIssue

from .recipe import RequestValidationError

EMBEDDABLE_IMAGE_TYPES = frozenset({"image/png", "image/jpeg"})


class ImageRef(Protocol):
    name: str
    media_type: str
    content_hash: str
    byte_size: int


def resolve_images(
    refs: Sequence[ImageRef], root: str | Path, *, field: str, caption_label: str
) -> list[DocumentImage]:
    folder = Path(root)
    images: list[DocumentImage] = []
    issues: list[ValidationIssue] = []
    for index, ref in enumerate(refs):
        if ref.media_type not in EMBEDDABLE_IMAGE_TYPES:
            continue
        path = folder / ref.name
        if not path.is_file():
            issues.append(ValidationIssue(code=f"{field.rstrip('s')}_file_missing", path=f"{field}[{index}]",
                                          message=f"{ref.name} is not in the image folder"))
            continue
        data = path.read_bytes()
        if len(data) != ref.byte_size or hashlib.sha256(data).hexdigest() != ref.content_hash:
            issues.append(ValidationIssue(code=f"{field.rstrip('s')}_file_mismatch", path=f"{field}[{index}]",
                                          message=f"{ref.name} does not match its content_hash or byte_size"))
            continue
        media_type: Literal["image/png", "image/jpeg"] = "image/png" if ref.media_type == "image/png" else "image/jpeg"
        images.append(DocumentImage(path=str(path.resolve()), media_type=media_type, content_hash=ref.content_hash,
                                    caption=f"{caption_label} {index + 1}: {ref.name}"))
    if issues:
        raise RequestValidationError(issues)
    return images
