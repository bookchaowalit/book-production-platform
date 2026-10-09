"""Owner-approved provider configuration for AI highlight proposals (REQ-VID-04).

Video highlights use the shared ``brief_adapter_kit`` configuration contract
with their own schema id and defaults (tool ``video-highlight-v2``, route
``video.highlight``). ``mode`` starts as ``shadow``; the owner switches it to
``live`` only after a passing held-out shadow report. No secret ever lives in
this file: ``token_env`` names the environment variable that holds the
gateway bearer token.

Transcripts are larger than a document brief, so the default total token
budget is the kit ceiling (8,000 input + 2,000 output tokens per call).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from brief_adapter_kit.config import (
    GATEWAY_MAX_OUTPUT_TOKENS,
    MAX_INPUT_TOKENS,
    MAX_OUTPUT_TOKENS,
    ProviderConfigError,
)
from brief_adapter_kit.config import ProviderConfig as KitProviderConfig
from brief_adapter_kit.config import load_provider_config as _load
from brief_adapter_kit.config import parse_provider_config as _parse
from pydantic import Field

PROVIDER_CONFIG_SCHEMA = "video-highlight-provider-config.v1"
TOOL_ID = "video-highlight-v2"
MAX_TOKEN_BUDGET = MAX_INPUT_TOKENS + MAX_OUTPUT_TOKENS + 48
# With a reasoning effort the completion may use the gateway's full output cap.
MAX_REASONING_TOKEN_BUDGET = MAX_INPUT_TOKENS + GATEWAY_MAX_OUTPUT_TOKENS + 48


class HighlightProviderConfig(KitProviderConfig):
    """Video highlight flavour of the kit configuration."""

    schema_version: Literal["video-highlight-provider-config.v1"] = (
        "video-highlight-provider-config.v1"  # type: ignore[assignment]
    )
    tool_id: str = Field(default=TOOL_ID, pattern=r"^video-highlight-v2$")
    route: str = Field(default="video.highlight", pattern=r"^[A-Za-z0-9_.:/-]{1,64}$")
    prompt_version: str = Field(default="video-highlight.v1", pattern=r"^[A-Za-z0-9_.:/-]{1,64}$")
    max_token_budget: int = Field(default=MAX_TOKEN_BUDGET, ge=2_304, le=MAX_REASONING_TOKEN_BUDGET)


def parse_provider_config(raw: Any) -> HighlightProviderConfig:
    return _parse(raw, HighlightProviderConfig)


def load_provider_config(path: str | Path) -> HighlightProviderConfig:
    return _load(path, HighlightProviderConfig)


__all__ = [
    "MAX_OUTPUT_TOKENS",
    "MAX_TOKEN_BUDGET",
    "PROVIDER_CONFIG_SCHEMA",
    "TOOL_ID",
    "HighlightProviderConfig",
    "ProviderConfigError",
    "load_provider_config",
    "parse_provider_config",
]
