"""Owner-approved provider configuration for Spreadsheet V1B.

Spreadsheet uses the shared ``brief_adapter_kit`` configuration contract with
its own schema id and defaults (tool ``spreadsheet-v1a``, route
``spreadsheet.brief``). ``mode`` starts as ``shadow``; the owner switches it
to ``live`` only after a passing held-out shadow report. No secret ever lives
in this file: ``token_env`` names the environment variable holding the
gateway bearer token.
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

PROVIDER_CONFIG_SCHEMA = "spreadsheet-provider-config.v1"


class ProviderConfig(KitProviderConfig):
    """Spreadsheet flavour of the kit configuration."""

    schema_version: Literal["spreadsheet-provider-config.v1"] = "spreadsheet-provider-config.v1"  # type: ignore[assignment]
    tool_id: str = Field(default="spreadsheet-v1a", pattern=r"^spreadsheet-v1a$")
    route: str = Field(default="spreadsheet.brief", pattern=r"^[A-Za-z0-9_.:/-]{1,64}$")
    prompt_version: str = Field(default="spreadsheet-brief.v1", pattern=r"^[A-Za-z0-9_.:/-]{1,64}$")


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
