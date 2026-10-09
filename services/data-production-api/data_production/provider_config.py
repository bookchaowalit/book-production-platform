"""Owner-approved provider configuration for the Data Viz V1B brief adapter.

Data Viz uses the shared ``brief_adapter_kit`` configuration contract with its
own schema id and defaults (tool ``data-viz-v1a``, route ``data-viz.brief``).
``mode`` starts as ``shadow``; the owner switches it to ``live`` only after a
passing held-out shadow report. No secret ever lives in this file:
``token_env`` names the environment variable holding the gateway bearer token.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from brief_adapter_kit.config import ProviderConfig as KitProviderConfig
from brief_adapter_kit.config import ProviderConfigError
from brief_adapter_kit.config import load_provider_config as _load
from brief_adapter_kit.config import parse_provider_config as _parse
from pydantic import Field

PROVIDER_CONFIG_SCHEMA = "data-viz-provider-config.v1"


class ProviderConfig(KitProviderConfig):
    """Data Viz flavour of the kit configuration."""

    schema_version: Literal["data-viz-provider-config.v1"] = "data-viz-provider-config.v1"  # type: ignore[assignment]
    tool_id: str = Field(default="data-viz-v1a", pattern=r"^data-viz-v1a$")
    route: str = Field(default="data-viz.brief", pattern=r"^[A-Za-z0-9_.:/-]{1,64}$")
    prompt_version: str = Field(default="data-viz-brief.v2", pattern=r"^[A-Za-z0-9_.:/-]{1,64}$")


def parse_provider_config(raw: Any) -> ProviderConfig:
    return _parse(raw, ProviderConfig)


def load_provider_config(path: str | Path) -> ProviderConfig:
    return _load(path, ProviderConfig)


__all__ = [
    "PROVIDER_CONFIG_SCHEMA",
    "ProviderConfig",
    "ProviderConfigError",
    "load_provider_config",
    "parse_provider_config",
]
