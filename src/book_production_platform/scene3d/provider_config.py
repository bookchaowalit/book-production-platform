"""Owner-approved provider configuration for the Scene3D V1B brief adapter.

Scene3D uses the shared ``brief_adapter_kit`` configuration contract with its
own schema id and defaults (tool ``scene3d-v1a``, route ``scene3d.brief``).
``mode`` starts as ``shadow``; the owner switches it to ``live`` only after a
passing held-out ``brief-shadow`` report *and* the pending Blender render gate
(AC-S3D-01a). No secret ever lives in this file: ``token_env`` names the
environment variable holding the gateway token.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from pydantic import Field

from book_production_platform.brief_adapter_kit.config import (
    OwnerApproval,
    ProviderConfigError,
    RateCard,
    SpendingCaps,
)
from book_production_platform.brief_adapter_kit.config import (
    ProviderConfig as KitProviderConfig,
)
from book_production_platform.brief_adapter_kit.config import (
    load_provider_config as _load,
)
from book_production_platform.brief_adapter_kit.config import (
    parse_provider_config as _parse,
)

PROVIDER_CONFIG_SCHEMA = "scene3d-provider-config.v1"


class ProviderConfig(KitProviderConfig):
    """Scene3D flavour of the kit configuration."""

    schema_version: Literal["scene3d-provider-config.v1"] = "scene3d-provider-config.v1"  # type: ignore[assignment]
    tool_id: str = Field(default="scene3d-v1a", pattern=r"^scene3d-v1a$")
    route: str = Field(default="scene3d.brief", pattern=r"^[A-Za-z0-9_.:/-]{1,64}$")
    prompt_version: str = Field(default="scene3d-brief.v3", pattern=r"^[A-Za-z0-9_.:/-]{1,64}$")


def parse_provider_config(raw: Any) -> ProviderConfig:
    return _parse(raw, ProviderConfig)


def load_provider_config(path: str | Path) -> ProviderConfig:
    return _load(path, ProviderConfig)


__all__ = [
    "PROVIDER_CONFIG_SCHEMA",
    "OwnerApproval",
    "ProviderConfig",
    "ProviderConfigError",
    "RateCard",
    "SpendingCaps",
    "load_provider_config",
    "parse_provider_config",
]
