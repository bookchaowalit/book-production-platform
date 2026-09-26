"""Versioned artifact contracts for the scene-to-graphics pipeline.

The published contract ``scene-render-batch.v1`` is the only handoff between
the scene3d render stage and the graphics batch stage. Every path it contains
is a relative POSIX path: ``scene.artifact_dir`` is relative to the pipeline
output root, and the artifact paths below it are relative to that directory.
"""

from __future__ import annotations

import json
from pathlib import Path, PureWindowsPath
from typing import Annotated, Final, Literal

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
)

from book_production_platform.graphics_ir import BrandTokens
from book_production_platform.graphics_ir.models import TemplateId, VariantId

from .errors import PipelineError

CONTRACT_SCHEMA_VERSION: Final = "scene-render-batch.v1"
SPEC_SCHEMA_VERSION: Final = "scene-graphics-spec.v1"
MAX_SPEC_BYTES = 64 * 1024

SceneRenderProfile = Literal["square-1024.v1", "landscape-1920x1080.v1"]


class _ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


def _sha256_digest(value: str) -> str:
    if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
        raise ValueError("value must be a lowercase 64-character sha256 digest")
    return value


def _relative_path(value: str) -> str:
    if (
        not value
        or "\\" in value
        or value.startswith("/")
        or Path(value).is_absolute()
        or PureWindowsPath(value).drive
        or any(part in {"", ".", ".."} for part in value.split("/"))
        or any(ord(character) < 32 for character in value)
    ):
        raise ValueError("path must be a relative posix path without traversal")
    return value


Sha256Digest = Annotated[str, AfterValidator(_sha256_digest)]
RelativePath = Annotated[str, AfterValidator(_relative_path)]


class SceneArtifactRef(_ContractModel):
    """One scene job artifact pinned by its bundle-relative path and hash."""

    path: RelativePath
    sha256: Sha256Digest


class SceneRenderOutput(SceneArtifactRef):
    """The rendered PNG together with its verified pixel dimensions."""

    width: int = Field(ge=1, le=8192)
    height: int = Field(ge=1, le=8192)


class SceneRenderOutputs(_ContractModel):
    """The accepted scene render side of the handoff."""

    fingerprint: Sha256Digest
    render_profile: SceneRenderProfile
    artifact_dir: RelativePath
    render_png: SceneRenderOutput
    scene_blend: SceneArtifactRef
    qa_manifest: SceneArtifactRef


class GraphicsBatchInputs(_ContractModel):
    """The graphics batch side of the handoff: one template plus per-size specs."""

    template_id: TemplateId
    variants: tuple[VariantId, ...] = Field(min_length=1, max_length=3)
    brand: BrandTokens
    content: dict[str, str] = Field(default_factory=dict)

    @field_validator("variants")
    @classmethod
    def _reject_duplicate_variants(cls, value: tuple[VariantId, ...]) -> tuple[VariantId, ...]:
        if len(set(value)) != len(value):
            raise ValueError("variants must not repeat")
        return value

    @field_validator("content")
    @classmethod
    def _reject_blank_content(cls, value: dict[str, str]) -> dict[str, str]:
        if any(not key.strip() or not text.strip() for key, text in value.items()):
            raise ValueError("content keys and values must be non-blank")
        return value


class SceneRenderBatch(_ContractModel):
    """The complete ``scene-render-batch.v1`` handoff artifact."""

    schema_version: Literal["scene-render-batch.v1"]
    scene: SceneRenderOutputs
    graphics: GraphicsBatchInputs


class GraphicsSpecInput(GraphicsBatchInputs):
    """The bounded input file that supplies the graphics half of the contract."""

    schema_version: Literal["scene-graphics-spec.v1"]


def relative_path_or_raise(value: str) -> str:
    """Validate one relative POSIX path for published artifacts."""

    return _relative_path(value)


def parse_graphics_spec(value: object) -> GraphicsSpecInput:
    """Validate an untrusted value against the graphics spec contract."""

    try:
        return GraphicsSpecInput.model_validate(value)
    except (ValidationError, TypeError, ValueError, RecursionError) as error:
        raise PipelineError("invalid_graphics_spec") from error


def read_graphics_spec(path: Path) -> GraphicsSpecInput:
    """Read and validate the bounded graphics spec file."""

    if path.is_symlink() or not path.is_file():
        raise PipelineError("invalid_graphics_spec", "graphics spec file is unavailable")
    try:
        if path.stat().st_size > MAX_SPEC_BYTES:
            raise PipelineError("invalid_graphics_spec", "graphics spec exceeds its size limit")
        value = json.loads(path.read_text(encoding="utf-8"))
    except PipelineError:
        raise
    except (OSError, UnicodeDecodeError, ValueError, RecursionError) as error:
        raise PipelineError("invalid_graphics_spec", "graphics spec cannot be read") from error
    return parse_graphics_spec(value)


def parse_scene_render_batch(value: object) -> SceneRenderBatch:
    """Validate an untrusted value against the published handoff contract."""

    try:
        return SceneRenderBatch.model_validate(value)
    except (ValidationError, TypeError, ValueError, RecursionError) as error:
        raise PipelineError("invalid_scene_render_batch") from error


__all__ = [
    "CONTRACT_SCHEMA_VERSION",
    "MAX_SPEC_BYTES",
    "SPEC_SCHEMA_VERSION",
    "GraphicsBatchInputs",
    "GraphicsSpecInput",
    "SceneArtifactRef",
    "SceneRenderBatch",
    "SceneRenderOutput",
    "SceneRenderOutputs",
    "parse_graphics_spec",
    "parse_scene_render_batch",
    "read_graphics_spec",
    "relative_path_or_raise",
]
