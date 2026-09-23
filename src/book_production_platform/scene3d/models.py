"""Strict SceneIR v1 contract and parser."""

from pathlib import PurePosixPath, PureWindowsPath
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, ValidationError, field_validator

from book_production_platform.scene3d.errors import SceneInputError


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class AssetIR(_StrictModel):
    path: str

    @field_validator("path")
    @classmethod
    def validate_local_glb_path(cls, value: str) -> str:
        windows_path = PureWindowsPath(value)
        parts = value.split("/")
        parsed_url = urlsplit(value)
        if (
            not value
            or "\\" in value
            or windows_path.drive
            or value.startswith("/")
            or parsed_url.scheme
            or any(part in {"", ".", ".."} for part in parts)
            or any(ord(character) < 32 for character in value)
            or PurePosixPath(value).suffix.lower() != ".glb"
        ):
            raise ValueError("asset path must be a relative local .glb path")
        return value


class SceneIR(_StrictModel):
    schema_version: Literal["scene-ir.v1"]
    asset: AssetIR
    recipe: Literal["product-hero.studio.v1"]
    composition: Literal["three-quarter.v1"]
    lighting: Literal["softbox-three-point.v1"]
    background: Literal["studio-neutral.v1"]
    material_preset: Literal["preserve-source.v1"]
    render_profile: Literal["square-1024.v1", "landscape-1920x1080.v1"]


def parse_scene_ir(value: object) -> SceneIR:
    """Validate an untrusted JSON-like value against the strict SceneIR v1 contract."""
    try:
        return SceneIR.model_validate(value)
    except (ValidationError, TypeError, ValueError) as error:
        raise SceneInputError("invalid_scene_ir") from error
